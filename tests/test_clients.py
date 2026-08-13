"""Clients, projects, and the one record behind the timeline and the diary.

The half of the product that starts after a company says yes. What is tested
here is mostly the decisions taken against how the established
professional-services tools do it, because those are the parts that would
otherwise quietly rot back into the common shape:

  one event log       the audit trail and the client timeline are the same
                      rows. Tools that build them separately drift, and the
                      drift is discovered during an audit.
  nothing is deleted  a cancelled meeting keeps its row.
  no handoff gap      promoting a prospect keeps the file rather than copying
                      the name into a fresh record.
  no duplicates       correspondence carries its Message-ID.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.kernel.db import Connection, Row
from arpenteur.config import Territory
from arpenteur.console.app import create_app


@pytest.fixture
def settings(db_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        database_url=db_path,
        territory=Territory(cantons=("VD",), legal_forms=("0110",), name="vaud"),
        segments={"sme-owner": "A small owner-run business."},
    )


@pytest.fixture
def conn(settings) -> Connection:
    connection = db.open_db(settings.database_url)
    db.upsert_company(
        connection,
        {"uid": "CHE103895771", "name": "Fondation Bois-Gentil", "canton": "VD",
         "legal_seat": "Lausanne", "legal_form_label": "Fondation",
         "status": "ACTIVE", "purpose": "Venir en aide à toute personne défavorisée.",
         "hydrated": True, "method": "zefix"},
    )
    connection.commit()
    return connection


@pytest.fixture
def client(settings, conn) -> TestClient:
    return TestClient(create_app(settings))


# --- codes --------------------------------------------------------------------


def test_a_client_gets_a_quotable_reference(conn) -> None:
    """It ends up on invoices, so it is generated rather than left to whatever
    an operator types into a blank required field."""
    client_id = db.promote_to_client(conn, 1)
    assert conn.execute(
        "SELECT code FROM crm.client WHERE id = %s", (client_id,)
    ).fetchone()[0] == "BOIS-GENTIL"


def test_codes_do_not_collide(conn) -> None:
    db.upsert_company(
        conn, {"uid": "CHE2", "name": "Fondation Bois-Gentil", "method": "zefix"}
    )
    first = db.promote_to_client(conn, 1)
    second = db.promote_to_client(conn, 2)
    codes = {
        row[0] for row in conn.execute(
            "SELECT code FROM crm.client WHERE id IN (%s, %s)", (first, second)
        )
    }
    assert codes == {"BOIS-GENTIL", "BOIS-GENTIL-2"}


def test_a_project_is_numbered_within_its_client(conn) -> None:
    """A project outlives the conversation that started it and has to be
    nameable in a contract, an invoice and a file name."""
    client_id = db.promote_to_client(conn, 1)
    first = db.add_project(conn, client_id, "Diagnostic")
    second = db.add_project(conn, client_id, "Migration")
    codes = [
        conn.execute("SELECT code FROM crm.project WHERE id = %s", (pid,)).fetchone()[0]
        for pid in (first, second)
    ]
    assert codes == ["BOIS-GENTIL-01", "BOIS-GENTIL-02"]


# --- the handoff --------------------------------------------------------------


def test_promoting_keeps_the_file_rather_than_copying_the_name(conn) -> None:
    """The gap the surveyed tools are criticised for: a system that stops at
    "closed won" makes the first day of the engagement a copy-paste job."""
    db.save_site(
        conn, 1,
        {"status": "found", "url": "https://bois-gentil.ch", "method": "web-search"},
    )
    conn.commit()
    client_id = db.promote_to_client(conn, 1)
    row = conn.execute(
        """SELECT co.purpose, w.url FROM crm.client c
             JOIN core.company co ON co.id = c.company_id
             LEFT JOIN intel.site w ON w.company_id = co.id
            WHERE c.id = %s""",
        (client_id,),
    ).fetchone()
    assert row["purpose"] and row["url"] == "https://bois-gentil.ch"


def test_promoting_twice_is_the_same_client(conn) -> None:
    """The button is on a page anyone may reload."""
    assert db.promote_to_client(conn, 1) == db.promote_to_client(conn, 1)


# --- one record, three readings -----------------------------------------------


def test_the_diary_and_the_timeline_are_the_same_rows(conn) -> None:
    """The architectural decision. Built as two systems they drift, and the
    drift is always found during the audit."""
    client_id = db.promote_to_client(conn, 1)
    db.record_event(
        conn, client_id, kind="meeting", state="planned",
        occurred_at="2026-08-20T14:00:00", subject="Réunion de cadrage",
    )
    conn.commit()

    in_file = [e["subject"] for e in db.timeline(conn, client_id)]
    in_diary = [e["subject"] for e in db.diary(conn, since="2026-08-01", until="2026-08-31")]
    assert in_file == in_diary == ["Réunion de cadrage"]


def test_the_diary_holds_the_appointments_and_the_file_holds_the_rest(conn) -> None:
    """One table, two readings, and they are not the same set.

    A calendar answers "where do I have to be"; a timeline answers "what has
    happened with this client". Every update to a prospect landing in the month
    grid buries the one meeting of that week under entries nobody attends.
    """
    client_id = db.promote_to_client(conn, 1)
    for kind, subject in (
        ("meeting", "Réunion de cadrage"),
        ("email", "Devis envoyé"),
        ("document", "Organigramme reçu"),
        ("note", "Ils réfléchissent"),
    ):
        db.record_event(
            conn, client_id, kind=kind, occurred_at="2026-08-20T14:00:00",
            subject=subject,
        )
    conn.commit()

    in_file = {e["subject"] for e in db.timeline(conn, client_id)}
    in_diary = [e["subject"] for e in db.diary(conn, since="2026-08-01", until="2026-08-31")]
    assert len(in_file) == 4
    assert in_diary == ["Réunion de cadrage"]


def test_anything_planned_is_in_the_diary_whatever_its_kind(conn) -> None:
    """Something committed to on a stated date is what a diary is for, and an
    offer promised for Friday is as much an appointment as a meeting is."""
    client_id = db.promote_to_client(conn, 1)
    db.record_event(
        conn, client_id, kind="email", state="planned",
        occurred_at="2026-08-21T09:00:00", subject="Envoyer l'offre",
    )
    conn.commit()
    assert [e["subject"] for e in db.diary(conn, since="2026-08-01", until="2026-08-31")] == [
        "Envoyer l'offre"
    ]


def test_a_project_carries_its_own_dates_into_the_calendar(conn) -> None:
    """Derived from the project, not logged once when the record was created.

    A "project starts" row written on the day someone typed it says what was
    true that morning and then goes stale. Moving the deadline has to move the
    calendar, and that only holds if the calendar reads the project.
    """
    client_id = db.promote_to_client(conn, 1)
    project_id = db.add_project(
        conn, client_id, "Diagnostic",
        started_at="2026-09-01", due_at="2026-09-30",
    )
    conn.commit()
    assert [
        e["subject"] for e in db.diary(conn, since="2026-09-01", until="2026-09-30")
    ] == ["Starts: Diagnostic", "Due: Diagnostic"]

    db.update_project(conn, project_id, due_at="2026-10-15")
    conn.commit()
    september = [e["subject"] for e in db.diary(conn, since="2026-09-01", until="2026-09-30")]
    october = [e["subject"] for e in db.diary(conn, since="2026-10-01", until="2026-10-31")]
    assert september == ["Starts: Diagnostic"]
    assert october == ["Due: Diagnostic"]


def test_a_planned_meeting_becomes_one_that_happened(conn) -> None:
    client_id = db.promote_to_client(conn, 1)
    event_id = db.record_event(
        conn, client_id, kind="meeting", state="planned",
        occurred_at="2026-08-20T14:00:00",
    )
    db.set_event_state(conn, event_id, "done")
    conn.commit()
    assert conn.execute(
        "SELECT state FROM crm.interaction WHERE id = %s", (event_id,)
    ).fetchone()[0] == "done"


def test_a_cancelled_meeting_keeps_its_row(conn) -> None:
    """An audit trail with a delete button is a diary."""
    client_id = db.promote_to_client(conn, 1)
    event_id = db.record_event(
        conn, client_id, kind="meeting", state="planned",
        occurred_at="2026-08-20T14:00:00", subject="Réunion annulée",
    )
    db.set_event_state(conn, event_id, "cancelled")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS n FROM crm.interaction").fetchone()[0] == 1
    # Gone from the diary, still in the record. Both are correct.
    assert db.diary(conn, since="2026-08-01", until="2026-08-31") == []
    assert len(db.timeline(conn, client_id)) == 1


def test_an_unknown_state_is_refused(conn) -> None:
    client_id = db.promote_to_client(conn, 1)
    event_id = db.record_event(
        conn, client_id, kind="note", occurred_at="2026-08-20T14:00:00"
    )
    with pytest.raises(ValueError, match="unknown interaction state"):
        db.set_event_state(conn, event_id, "deleted")


# --- correspondence -----------------------------------------------------------


def test_the_same_email_is_never_recorded_twice(conn) -> None:
    """Every surveyed tool loses the Message-ID and duplicates correspondence
    on re-import. It costs one column not to."""
    client_id = db.promote_to_client(conn, 1)
    first = db.record_event(
        conn, client_id, kind="email", occurred_at="2026-08-11T09:20:00",
        direction="in", subject="Suite à notre échange",
        external_id="<CAF-abc@mail.example.ch>",
    )
    second = db.record_event(
        conn, client_id, kind="email", occurred_at="2026-08-11T09:20:00",
        direction="in", subject="Suite à notre échange",
        external_id="<CAF-abc@mail.example.ch>",
    )
    conn.commit()
    assert first == second
    assert conn.execute("SELECT COUNT(*) AS n FROM crm.interaction").fetchone()[0] == 1


def test_two_notes_without_a_message_id_are_two_notes(conn) -> None:
    """The dedup key is optional, and absent must not mean identical."""
    client_id = db.promote_to_client(conn, 1)
    db.record_event(conn, client_id, kind="note", occurred_at="2026-08-11T09:00:00")
    db.record_event(conn, client_id, kind="note", occurred_at="2026-08-11T09:00:00")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS n FROM crm.interaction").fetchone()[0] == 2


# --- through the console ------------------------------------------------------


def test_a_company_becomes_a_client_from_its_page(client, conn) -> None:
    reply = client.post("/company/1/client", follow_redirects=False)
    assert reply.status_code == 303 and reply.headers["location"] == "/client/1"
    assert "BOIS-GENTIL" in client.get("/client/1").text


def test_opening_a_project_is_itself_part_of_the_record(client, conn) -> None:
    """The timeline should answer "what happened" without anyone having to
    remember to say it."""
    client.post("/company/1/client")
    client.post("/client/1/projects", data={"name": "Diagnostic", "billing": "fixed"})
    kinds = [e["kind"] for e in db.timeline(conn, 1)]
    assert "milestone" in kinds


def test_a_change_of_project_status_is_recorded(client, conn) -> None:
    client.post("/company/1/client")
    client.post("/client/1/projects", data={"name": "Diagnostic"})
    client.post("/project/1", data={"status": "active"})
    subjects = [e["subject"] or "" for e in db.timeline(conn, 1)]
    assert any("proposed to active" in s for s in subjects)


def test_closing_a_project_stamps_the_date(client, conn) -> None:
    """A delivered project should say when, not only that."""
    client.post("/company/1/client")
    client.post("/client/1/projects", data={"name": "Diagnostic"})
    client.post("/project/1", data={"status": "delivered"})
    assert conn.execute("SELECT closed_at FROM crm.project WHERE id = 1").fetchone()[0]


def test_a_typed_time_sorts_against_a_recorded_one(client, conn) -> None:
    """The browser sends 2026-08-11T09:20 and db.now() writes a full ISO
    stamp. Storing one with a space and the other with a T looks harmless and
    sorts wrong, because a space is below T in ASCII and the timeline is
    ordered by this column."""
    client.post("/company/1/client")
    client.post(
        "/client/1/events",
        data={"kind": "note", "occurred_at": "2026-08-11T09:20", "subject": "later"},
    )
    client.post(
        "/client/1/events",
        data={"kind": "note", "occurred_at": "2026-08-11T09:00", "subject": "earlier"},
    )
    assert [e["subject"] for e in db.timeline(conn, 1)] == ["later", "earlier"]


def test_the_calendar_shows_what_is_planned(client, conn) -> None:
    client.post("/company/1/client")
    client.post(
        "/client/1/events",
        data={"kind": "meeting", "state": "planned",
              "occurred_at": "2026-08-20T14:00", "subject": "Réunion de cadrage"},
    )
    assert "Réunion de cadrage" in client.get("/calendar?month=2026-08").text


def test_the_calendar_leaves_the_running_record_alone(client, conn) -> None:
    """Opening a project is part of the record and is not an appointment.

    The project's own start date is, and it appears without anyone logging it.
    """
    client.post("/company/1/client")
    client.post(
        "/client/1/projects",
        data={"name": "Diagnostic", "started_at": "2026-09-01"},
    )
    client.post(
        "/client/1/events",
        data={"kind": "email", "occurred_at": "2026-09-02T09:00",
              "subject": "Devis envoyé"},
    )
    page = client.get("/calendar?month=2026-09").text
    assert "Starts: Diagnostic" in page
    assert "Project opened" not in page and "Devis envoyé" not in page
    # Both are still on the client's own timeline, which is where they belong.
    assert {"Project opened: Diagnostic", "Devis envoyé"} <= {
        e["subject"] for e in db.timeline(conn, 1)
    }


def test_a_bad_uid_says_what_one_looks_like(client) -> None:
    reply = client.post("/clients/import", data={"uid": "12345"}, follow_redirects=True)
    assert "CHE-103.895.771" in reply.text


def test_the_client_section_cannot_send_either(client, conn) -> None:
    """The guarantee the whole console rests on, restated for the half of it
    that now holds correspondence."""
    routes = {getattr(r, "path", "") for r in client.app.routes}
    assert not [p for p in routes if "send" in p or "mail" in p or "smtp" in p]
