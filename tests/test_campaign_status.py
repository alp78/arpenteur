"""Tracking a campaign: what went out, what came back, where each firm stands.

The promises under test are the ones four separate tables could not keep
between them:

that marking a draft sent moves the programme card and never walks it back, so
the tracker and the drafts cannot disagree about whether a firm was written to;
that the ledger holds every status in one row, including the one that matters
most, a firm counted as written to whose address returned a permanent failure;
that an answer is recorded against the member and the message is cited rather
than copied; that agreement to the dated window stays unset until somebody sets
it; and that reading a reply records nothing by itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arpenteur import db
from arpenteur.config import load_settings
from arpenteur.kernel.db import Connection
from arpenteur.modules.clients import store as clients_store
from arpenteur.sources import enrol
from arpenteur.stages import answers as answers_stage

pytestmark = pytest.mark.pg

TOML = """
[survey]
cantons = ["VD"]
legal_forms = ["0106"]

[segments]
regie-core = "Manages property for third parties."

[sectors]
real-estate = "Owning, letting, managing or brokering property."

[enrol]
programme = "LIMENTEST"
segments = ["regie-core"]
"""

AGENCIES = (
    ("CHE200000001", "Regie Alpha SA", "VD", "info@alpha.test"),
    ("CHE200000002", "Regie Beta SA", "VD", "contact@beta.test"),
    ("CHE200000003", "Regie Gamma SA", "GE", "info@gamma.test"),
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "arpenteur.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "campaign.md").write_text("# Campaign\n\nx\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def conn(root: Path, db_path: str) -> Connection:
    connection = db.open_db(db_path)
    for uid, name, canton, address in AGENCIES:
        row = connection.execute(
            "INSERT INTO core.company (uid, name, canton, legal_form_uid,"
            " legal_form_label, legal_seat, status, purpose, source_url,"
            " method, fetched_at, first_seen, hydrated_at)"
            " VALUES (%s, %s, %s, '0106', 'SA', 'Lausanne', 'ACTIVE',"
            " 'gerance immobiliere pour le compte de tiers',"
            " 'https://example.test', 'zefix', '2026-09-01', '2026-09-01',"
            " '2026-09-01') RETURNING id",
            (uid, name, canton),
        ).fetchone()
        connection.execute(
            "INSERT INTO core.contact (company_id, kind, value, source_url,"
            " method, fetched_at)"
            " VALUES (%s, 'email', %s, 'https://example.test', 'website',"
            " now())",
            (int(row["id"]), address),
        )
    connection.commit()
    return connection


@pytest.fixture
def companies(conn: Connection) -> dict[str, int]:
    return {
        row["uid"]: int(row["id"])
        for row in conn.execute("SELECT id, uid FROM core.company")
    }


@pytest.fixture
def programme_id(conn: Connection) -> int:
    new_id = clients_store.add_programme(conn, "LIMENTEST", "Limen outreach")
    conn.commit()
    return new_id


def _sent_draft(conn: Connection, company_id: int, address: str) -> int:
    row = conn.execute(
        "INSERT INTO intel.draft (company_id, language, subject, body, model,"
        " address, brief_digest, sent_at, reviewed_at)"
        " VALUES (%s, 'fr', 'Limen', 'corps', 'test', %s, 'abc', now(), now())"
        " RETURNING id",
        (company_id, address),
    ).fetchone()
    conn.commit()
    return int(row["id"])


def _inbound(
    conn: Connection, company_id: int, *, subject: str, body: str,
    sender: str, message_id: str = "", minutes: int = 60,
) -> int:
    row = conn.execute(
        "INSERT INTO crm.email_message (message_id, folder, uid, direction,"
        " sent_at, subject, from_email, to_emails, body, body_kind, state,"
        " company_id, match_method, decided_at)"
        " VALUES (%s, 'INBOX', 1, 'in', now() + make_interval(mins => %s), %s,"
        " %s, ARRAY['alexis@peringer.ch'], %s, 'text', 'accepted', %s,"
        " 'address', now()) RETURNING id",
        (message_id or f"<{sender}-{subject}>", minutes, subject, sender,
         body, company_id),
    ).fetchone()
    conn.commit()
    return int(row["id"])


# --- the record of what went out ---------------------------------------------


def test_marking_a_message_sent_puts_the_firm_on_the_programme(
    conn, programme_id, companies
) -> None:
    """A send list selects from the survey, not from the membership.

    So the first contact is often also the first the programme hears of the
    firm, and `note_contacted` has to insert rather than fail.
    """
    company_id = companies["CHE200000001"]
    member_id, moved = clients_store.note_contacted(
        conn, programme_id, company_id, address="info@alpha.test"
    )
    conn.commit()
    assert moved
    row = clients_store.member(conn, member_id)
    assert row["stage"] == "contacted"
    assert row["contact"] == "info@alpha.test"


def test_a_second_message_never_walks_a_card_back(
    conn, programme_id, companies
) -> None:
    company_id = companies["CHE200000001"]
    member_id, _ = clients_store.note_contacted(
        conn, programme_id, company_id, address="info@alpha.test"
    )
    clients_store.move_member(conn, member_id, "testing", by="test")
    conn.commit()

    _again, moved = clients_store.note_contacted(
        conn, programme_id, company_id, address="autre@alpha.test"
    )
    conn.commit()
    assert not moved
    assert clients_store.member(conn, member_id)["stage"] == "testing", (
        "marking a second draft sent says nothing about the stage, and the "
        "only record of how far the firm got is the one that would be lost"
    )


def test_contacting_writes_the_move_to_the_audit_trail(
    conn, programme_id, companies
) -> None:
    member_id, _ = clients_store.note_contacted(
        conn, programme_id, companies["CHE200000001"], address="info@alpha.test"
    )
    conn.commit()
    rows = conn.execute(
        "SELECT to_state, reason, by FROM audit.state_change"
        " WHERE entity = 'programme_member' AND entity_id = %s"
        # By id, not by `at`: both rows land in one transaction and share a
        # timestamp to the microsecond, so ordering on it is arbitrary.
        " ORDER BY id",
        (member_id,),
    ).fetchall()
    assert [row["to_state"] for row in rows] == ["selected", "contacted"]
    assert "info@alpha.test" in rows[-1]["reason"]


def test_an_unknown_stage_is_refused_rather_than_invented(
    conn, programme_id, companies
) -> None:
    with pytest.raises(ValueError, match="stage"):
        clients_store.note_contacted(
            conn, programme_id, companies["CHE200000001"], stage="posted"
        )


# --- the ledger ---------------------------------------------------------------


def test_the_ledger_holds_every_status_in_one_row(
    conn, programme_id, companies
) -> None:
    company_id = companies["CHE200000001"]
    _sent_draft(conn, company_id, "info@alpha.test")
    clients_store.note_contacted(
        conn, programme_id, company_id, address="info@alpha.test"
    )
    _inbound(conn, company_id, subject="Re: Limen", body="Android",
             sender="info@alpha.test")
    conn.commit()

    rows = clients_store.campaign_ledger(conn, programme_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["sent"] == 1
    assert row["inbound"] == 1
    assert row["last_inbound"] is not None
    assert row["stage"] == "contacted"


def test_a_firm_written_to_whose_address_failed_is_visible_as_such(
    conn, programme_id, companies
) -> None:
    """The row this whole page exists for.

    On a board a firm that ignored three messages and a firm those messages
    never reached look identical. Here they do not.
    """
    company_id = companies["CHE200000002"]
    _sent_draft(conn, company_id, "contact@beta.test")
    clients_store.note_contacted(
        conn, programme_id, company_id, address="contact@beta.test"
    )
    conn.execute(
        "UPDATE core.contact SET status = 'bounced', status_at = now()"
        " WHERE company_id = %s AND kind = 'email'",
        (company_id,),
    )
    conn.commit()

    row = clients_store.campaign_ledger(conn, programme_id)[0]
    assert row["sent"] == 1 and row["inbound"] == 0
    assert row["failed"] == 1, "a message counted as sent that never arrived"

    assert len(clients_store.campaign_ledger(conn, programme_id, flag="failed")) == 1
    assert len(clients_store.campaign_ledger(conn, programme_id, flag="silent")) == 1


def test_a_suppressed_address_reads_as_refused(
    conn, programme_id, companies
) -> None:
    company_id = companies["CHE200000003"]
    clients_store.note_contacted(conn, programme_id, company_id)
    conn.execute(
        "UPDATE core.contact SET suppressed = true WHERE company_id = %s",
        (company_id,),
    )
    conn.commit()
    row = clients_store.campaign_ledger(conn, programme_id, flag="refused")[0]
    assert row["refused"] == 1


def test_every_ledger_flag_runs_against_the_schema(
    conn, programme_id, companies
) -> None:
    """The flags read the subquery's own aliases, not the inner ones.

    The first version wrote one of them against the inner table and Postgres
    refused it, which no unit test would have caught because the failure is in
    the SQL rather than in the logic.
    """
    clients_store.note_contacted(conn, programme_id, companies["CHE200000001"])
    conn.commit()
    for flag in clients_store.LEDGER_FLAGS:
        clients_store.campaign_ledger(conn, programme_id, flag=flag)


def test_an_unknown_flag_widens_rather_than_narrows(
    conn, programme_id, companies
) -> None:
    clients_store.note_contacted(conn, programme_id, companies["CHE200000001"])
    conn.commit()
    rows = clients_store.campaign_ledger(conn, programme_id, flag="nonsense")
    assert len(rows) == 1, "an unknown flag is ignored, never read as SQL"


def test_the_tally_counts_a_silent_firm_apart_from_an_unwritten_one(
    conn, programme_id, companies
) -> None:
    written = companies["CHE200000001"]
    _sent_draft(conn, written, "info@alpha.test")
    clients_store.note_contacted(conn, programme_id, written,
                                 address="info@alpha.test")
    clients_store.add_member(conn, programme_id, companies["CHE200000002"])
    conn.commit()

    tally = clients_store.ledger_tally(conn, programme_id)
    assert tally["members"] == 2
    assert tally["silent"] == 1, "the firm nobody wrote to is not silent"
    assert tally["answered"] == 0


# --- the answer ---------------------------------------------------------------


def test_an_answer_is_recorded_on_the_member_and_the_message_cited(
    conn, programme_id, companies
) -> None:
    company_id = companies["CHE200000001"]
    member_id, _ = clients_store.note_contacted(
        conn, programme_id, company_id, address="info@alpha.test"
    )
    message_id = _inbound(
        conn, company_id, subject="Re: Limen",
        body="Android, compte jd@gmail.com", sender="jd@gmail.com",
    )
    clients_store.record_answer(
        conn, member_id, platform="android", account="jd@gmail.com",
        message_id=message_id,
    )
    conn.commit()

    row = clients_store.member(conn, member_id)
    assert row["platform"] == "android"
    assert row["tester_account"] == "jd@gmail.com"
    assert row["answer_message_id"] == message_id
    assert row["answered_at"] is not None
    assert row["stage"] == "replied"


def test_the_answer_text_is_never_copied_onto_the_member(
    conn, programme_id, companies
) -> None:
    """Two copies of one text means one of them drifts."""
    company_id = companies["CHE200000001"]
    member_id, _ = clients_store.note_contacted(conn, programme_id, company_id)
    message_id = _inbound(
        conn, company_id, subject="Re: Limen", body="un texte distinctif",
        sender="jd@gmail.com",
    )
    clients_store.record_answer(conn, member_id, platform="ios",
                               message_id=message_id)
    conn.commit()
    row = clients_store.member(conn, member_id)
    assert "distinctif" not in str(dict(row)), (
        "the body stays in crm.email_message and is reached through the id"
    )


def test_agreement_to_the_dates_stays_unset_until_somebody_sets_it(
    conn, programme_id, companies
) -> None:
    member_id, _ = clients_store.note_contacted(
        conn, programme_id, companies["CHE200000001"]
    )
    clients_store.record_answer(conn, member_id, platform="android",
                                account="jd@gmail.com")
    conn.commit()
    assert clients_store.member(conn, member_id)["window_ok"] is None, (
        "a reply names the platform in a word and accepts the dates in a "
        "sentence"
    )

    clients_store.record_answer(conn, member_id, window_ok=False)
    conn.commit()
    assert clients_store.member(conn, member_id)["window_ok"] is False


def test_a_platform_outside_the_two_store_tracks_is_refused(
    conn, programme_id, companies
) -> None:
    member_id, _ = clients_store.note_contacted(
        conn, programme_id, companies["CHE200000001"]
    )
    with pytest.raises(ValueError, match="platform"):
        clients_store.record_answer(conn, member_id, platform="windows")


def test_the_testers_account_never_reaches_the_survey_bank(
    conn, programme_id, companies
) -> None:
    """The rule the migration header states, as a test.

    `core.contact` holds addresses taken from public sources. A volunteered
    Google account is different in kind, and the send list selects from that
    table alone.
    """
    member_id, _ = clients_store.note_contacted(
        conn, programme_id, companies["CHE200000001"]
    )
    clients_store.record_answer(conn, member_id, platform="android",
                                account="jd1978@gmail.com")
    conn.commit()
    held = conn.execute(
        "SELECT COUNT(*) AS n FROM core.contact WHERE lower(value) = %s",
        ("jd1978@gmail.com",),
    ).fetchone()
    assert held["n"] == 0


def test_deleting_the_programme_deletes_the_testers_account(
    conn, programme_id, companies
) -> None:
    member_id, _ = clients_store.note_contacted(
        conn, programme_id, companies["CHE200000001"]
    )
    clients_store.record_answer(conn, member_id, platform="android",
                                account="jd1978@gmail.com")
    conn.commit()
    clients_store.delete_programme(conn, programme_id)
    conn.commit()
    left = conn.execute(
        "SELECT COUNT(*) AS n FROM crm.programme_member"
        " WHERE tester_account = %s",
        ("jd1978@gmail.com",),
    ).fetchone()
    assert left["n"] == 0


# --- reading the replies ------------------------------------------------------


def test_reading_the_replies_records_nothing_against_a_member(
    root, conn, programme_id, companies
) -> None:
    company_id = companies["CHE200000001"]
    member_id, _ = clients_store.note_contacted(
        conn, programme_id, company_id, address="info@alpha.test"
    )
    _inbound(conn, company_id, subject="Re: Limen",
             body="Volontiers, Android, compte jd@gmail.com",
             sender="jd@gmail.com")
    conn.commit()

    stats = answers_stage.run(load_settings(root), conn, log=lambda _m: None)
    assert stats["messages"] == 1
    assert stats[enrol.ANSWER] == 1
    assert stats["enrollable"] == 1
    row = clients_store.member(conn, member_id)
    assert row["platform"] is None, "the stage proposes, a person accepts"
    assert row["stage"] == "contacted"


def test_a_reply_older_than_the_move_is_not_read_as_an_answer(
    conn, programme_id, companies
) -> None:
    """What separates a reply from an old thread."""
    company_id = companies["CHE200000001"]
    _inbound(conn, company_id, subject="Facture 2024", body="Android",
             sender="info@alpha.test", minutes=-60 * 24 * 365)
    clients_store.note_contacted(conn, programme_id, company_id)
    conn.commit()
    assert clients_store.unread_answers(conn, programme_id) == []


def test_a_refusal_in_a_reply_suppresses_the_address(
    root, conn, programme_id, companies
) -> None:
    """The one thing this stage writes, and it is an instruction.

    `bounces` honours a refusal from the mailbox. One reaching here means that
    envelope screen missed it, so the promise the message makes is kept twice
    rather than once.
    """
    company_id = companies["CHE200000002"]
    clients_store.note_contacted(conn, programme_id, company_id,
                                 address="contact@beta.test")
    _inbound(conn, company_id, subject="Re: Limen",
             body="Merci de nous retirer de vos listes. STOP",
             sender="contact@beta.test")
    conn.commit()

    stats = answers_stage.run(load_settings(root), conn, log=lambda _m: None)
    assert stats[enrol.REFUSAL] == 1
    assert stats["suppressed"] == 1
    held = conn.execute(
        "SELECT suppressed FROM core.contact WHERE company_id = %s"
        " AND kind = 'email'",
        (company_id,),
    ).fetchone()
    assert held["suppressed"] is True


def test_a_dry_run_suppresses_nothing(
    root, conn, programme_id, companies
) -> None:
    company_id = companies["CHE200000002"]
    clients_store.note_contacted(conn, programme_id, company_id)
    _inbound(conn, company_id, subject="Re: Limen", body="STOP",
             sender="contact@beta.test")
    conn.commit()
    answers_stage.run(load_settings(root), conn, dry_run=True,
                      log=lambda _m: None)
    held = conn.execute(
        "SELECT suppressed FROM core.contact WHERE company_id = %s",
        (company_id,),
    ).fetchone()
    assert held["suppressed"] is False


def test_an_unaccepted_message_is_reported_rather_than_read(
    root, conn, programme_id, companies
) -> None:
    """The mail stage fetches a body only for a message somebody filed."""
    company_id = companies["CHE200000001"]
    clients_store.note_contacted(conn, programme_id, company_id)
    message_id = _inbound(conn, company_id, subject="Re: Limen", body="",
                          sender="info@alpha.test")
    conn.execute(
        "UPDATE crm.email_message SET state = 'pending', decided_at = NULL"
        " WHERE id = %s",
        (message_id,),
    )
    conn.commit()
    stats = answers_stage.run(load_settings(root), conn, log=lambda _m: None)
    assert stats["unaccepted"] == 1


def test_the_programme_comes_from_settings_when_none_is_named(
    root, conn, programme_id, companies
) -> None:
    clients_store.note_contacted(conn, programme_id, companies["CHE200000001"])
    _inbound(conn, companies["CHE200000001"], subject="Re: Limen",
             body="iPhone", sender="info@alpha.test")
    conn.commit()
    stats = answers_stage.run(load_settings(root), conn, log=lambda _m: None)
    assert stats["messages"] == 1, "[enrol] programme named LIMENTEST"


def test_a_missing_programme_is_said_rather_than_raised(
    root, conn, programme_id
) -> None:
    said: list[str] = []
    stats = answers_stage.run(
        load_settings(root), conn, programme="NOSUCH", log=said.append
    )
    assert stats["messages"] == 0
    assert any("NOSUCH" in line for line in said)


# --- one firm's history -------------------------------------------------------


def test_history_holds_both_directions_in_order(
    conn, programme_id, companies
) -> None:
    """A message the operator sent arrives from the Sent folder.

    An outbound row exists because the mail stage read it back out of the
    mailbox, which is also how a message `arpenteur send` posted gets
    attributed: the send files a copy in Sent and the next import finds it.
    """
    company_id = companies["CHE200000001"]
    conn.execute(
        "INSERT INTO crm.email_message (message_id, folder, uid, direction,"
        " sent_at, subject, from_email, to_emails, state, company_id,"
        " match_method) VALUES ('<out-1>', 'Sent', 2, 'out',"
        " now() - interval '1 day', 'Limen', 'alexis@peringer.ch',"
        " ARRAY['info@alpha.test'], 'accepted', %s, 'address')",
        (company_id,),
    )
    _inbound(conn, company_id, subject="Re: Limen", body="Android",
             sender="info@alpha.test")
    conn.commit()

    rows = clients_store.history_for(conn, company_id)
    assert [row["direction"] for row in rows] == ["out", "in"]


# --- the console --------------------------------------------------------------


@pytest.fixture
def web(root: Path, conn):
    from fastapi.testclient import TestClient

    from arpenteur.console.app import create_app

    return TestClient(create_app(load_settings(root)))


def test_marking_a_draft_sent_moves_the_card(
    web, conn, programme_id, companies
) -> None:
    """The link that was missing.

    `sent_at` and the programme card record the same fact, and writing only
    the first left the campaign's own tracker empty while messages went out.
    """
    company_id = companies["CHE200000001"]
    row = conn.execute(
        "INSERT INTO intel.draft (company_id, language, subject, body, model,"
        " address, brief_digest, reviewed_at)"
        " VALUES (%s, 'fr', 'Limen', 'corps', 'test', 'info@alpha.test',"
        " 'abc', now()) RETURNING id",
        (company_id,),
    ).fetchone()
    conn.commit()

    answer = web.post(f"/draft/{int(row['id'])}/sent", follow_redirects=False)
    assert answer.status_code == 303
    assert "contacted" in answer.headers["location"]

    found = conn.execute(
        "SELECT stage, contact FROM crm.programme_member"
        " WHERE programme_id = %s AND company_id = %s",
        (programme_id, company_id),
    ).fetchone()
    assert found["stage"] == "contacted"
    assert found["contact"] == "info@alpha.test"


def test_the_ledger_page_shows_a_firm_and_its_counts(
    web, conn, programme_id, companies
) -> None:
    company_id = companies["CHE200000001"]
    _sent_draft(conn, company_id, "info@alpha.test")
    clients_store.note_contacted(conn, programme_id, company_id,
                                 address="info@alpha.test")
    conn.commit()
    page = web.get(f"/programme/{programme_id}/ledger")
    assert page.status_code == 200
    assert "Regie Alpha SA" in page.text
    assert "written to, silent" in page.text


def test_the_ledger_page_offers_the_reading_as_a_proposal(
    web, conn, programme_id, companies
) -> None:
    company_id = companies["CHE200000001"]
    member_id, _ = clients_store.note_contacted(
        conn, programme_id, company_id, address="info@alpha.test"
    )
    _inbound(conn, company_id, subject="Re: Limen",
             body="Volontiers sur Android, compte jd1978@gmail.com",
             sender="jd1978@gmail.com")
    conn.commit()
    page = web.get(f"/programme/{programme_id}/ledger")
    assert f'action="/member/{member_id}/answer"' in page.text
    assert 'value="jd1978@gmail.com"' in page.text, "pre-filled, not recorded"
    assert 'value="android" selected' in page.text
    assert clients_store.member(conn, member_id)["platform"] is None


def test_accepting_the_reading_records_it(
    web, conn, programme_id, companies
) -> None:
    member_id, _ = clients_store.note_contacted(
        conn, programme_id, companies["CHE200000001"]
    )
    conn.commit()
    answer = web.post(
        f"/member/{member_id}/answer",
        data={"platform": "android", "account": "jd1978@gmail.com",
              "window_ok": "1"},
        follow_redirects=False,
    )
    assert answer.status_code == 303
    row = clients_store.member(conn, member_id)
    assert row["platform"] == "android"
    assert row["tester_account"] == "jd1978@gmail.com"
    assert row["window_ok"] is True
    assert row["stage"] == "replied"


def test_an_unticked_dates_box_leaves_the_dates_unsaid(
    web, conn, programme_id, companies
) -> None:
    """Absent from the form means nobody said, never that they declined."""
    member_id, _ = clients_store.note_contacted(
        conn, programme_id, companies["CHE200000001"]
    )
    conn.commit()
    web.post(f"/member/{member_id}/answer", data={"platform": "ios"},
             follow_redirects=False)
    assert clients_store.member(conn, member_id)["window_ok"] is None


def test_a_platform_the_stores_do_not_have_is_refused_by_the_route(
    web, conn, programme_id, companies
) -> None:
    member_id, _ = clients_store.note_contacted(
        conn, programme_id, companies["CHE200000001"]
    )
    conn.commit()
    answer = web.post(f"/member/{member_id}/answer",
                      data={"platform": "windows"}, follow_redirects=False)
    assert answer.status_code == 303
    assert "error=" in answer.headers["location"]
    assert clients_store.member(conn, member_id)["platform"] is None


def test_the_history_page_names_both_directions(
    web, conn, programme_id, companies
) -> None:
    company_id = companies["CHE200000001"]
    member_id, _ = clients_store.note_contacted(conn, programme_id, company_id)
    conn.execute(
        "INSERT INTO crm.email_message (message_id, folder, uid, direction,"
        " sent_at, subject, from_email, to_emails, state, company_id,"
        " match_method) VALUES ('<out-9>', 'Sent', 9, 'out',"
        " now() - interval '2 days', 'Limen, protocole', 'alexis@peringer.ch',"
        " ARRAY['info@alpha.test'], 'accepted', %s, 'address')",
        (company_id,),
    )
    _inbound(conn, company_id, subject="Re: Limen, protocole",
             body="Android", sender="info@alpha.test")
    conn.commit()
    page = web.get(f"/member/{member_id}/history")
    assert page.status_code == 200
    assert "Limen, protocole" in page.text
    assert "info@alpha.test" in page.text


def test_the_board_links_to_the_status_of_every_firm(
    web, conn, programme_id
) -> None:
    page = web.get(f"/programme/{programme_id}")
    assert f'/programme/{programme_id}/ledger' in page.text
