"""Programmes: one endeavour across many companies, each at its own stage.

What is worth testing here is not that a row inserts. It is the promises this
schema makes that the two shapes of work already in the tool could not:

that a company's stage belongs to one programme, so the same agency can be
testing here and untouched elsewhere; that adding a company twice gives the
member already there rather than a twin in two columns; that a stage list the
operator controls cannot be edited into stranding the members standing in it;
and that a bulk import only ever adds, so re-running it after another sweep
cannot walk somebody at `testing` back to the start.

The last one is a rule about what this tool never does. Inbound mail is counted
on a card because "who replied" is the question the board exists to answer, and
counting it must not move the card: nothing in this schema files itself, and a
stage that advanced because a message arrived would be the first thing that
did.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.config import load_settings
from arpenteur.console.app import create_app
from arpenteur.kernel.db import Connection
from arpenteur.modules.clients import store as clients_store
from arpenteur.modules.scout import store as scout_store
from arpenteur.console.routes import every_route

pytestmark = pytest.mark.pg

TOML = """
[survey]
cantons = ["VD"]
legal_forms = ["0106"]

[segments]
sme-owner = "A small owner-run business."

[sectors]
real-estate = "Owning, letting, managing, brokering or developing property."
consulting = "Advice and services to businesses."
"""

AGENCIES = (
    ("CHE100000001", "Regie Naef SA", "VD"),
    ("CHE100000002", "Regie Braun SA", "VD"),
    ("CHE100000003", "Regie Rosset SA", "GE"),
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "arpenteur.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "campaign.md").write_text("# Campaign\n\nx\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def conn(root: Path, db_path: str) -> Connection:
    connection = db.open_db(db_path)
    for uid, name, canton in AGENCIES:
        connection.execute(
            "INSERT INTO core.company (uid, name, canton, legal_form_uid,"
            " legal_form_label, legal_seat, status, purpose, source_url, method,"
            " fetched_at, first_seen, hydrated_at)"
            " VALUES (%s, %s, %s, '0106', 'SA', 'Lausanne', 'ACTIVE',"
            " 'gerance immobiliere', 'https://example.test', 'zefix',"
            " '2026-09-01', '2026-09-01', '2026-09-01')",
            (uid, name, canton),
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
    new_id = clients_store.add_programme(
        conn, "flatcheck", "Etat des lieux app", what="Handover application"
    )
    conn.commit()
    return new_id


@pytest.fixture
def web(root: Path, conn) -> TestClient:
    return TestClient(create_app(load_settings(root)))


def _sector(conn: Connection, company_id: int, sector: str) -> None:
    conn.execute(
        "INSERT INTO intel.sector (company_id, sector, source, classified_at)"
        " VALUES (%s, %s, 'manual', now())",
        (company_id, sector),
    )
    conn.commit()


# --- starting one -------------------------------------------------------------


def test_a_programme_starts_with_editable_default_stages(conn, programme_id) -> None:
    row = clients_store.programme(conn, programme_id)
    assert row["code"] == "FLATCHECK", "the code is upper-cased, it gets quoted"
    assert list(row["stages"]) == list(clients_store.DEFAULT_STAGES)
    assert row["stage_notes"]["testing"], "each column carries its own gloss"


def test_two_programmes_cannot_share_a_code(conn, programme_id) -> None:
    with pytest.raises(ValueError, match="already goes by"):
        clients_store.add_programme(conn, "FLATCHECK", "Something else")


# --- members ------------------------------------------------------------------


def test_a_member_is_a_company_not_a_client(conn, programme_id, companies) -> None:
    """The point of the whole design.

    A refusal is a member that never becomes a client, so keying on the client
    would mean the outcome worth studying is the one the schema cannot hold.
    """
    member_id, existed = clients_store.add_member(
        conn, programme_id, companies["CHE100000001"]
    )
    conn.commit()
    assert not existed
    assert conn.execute("SELECT COUNT(*) AS n FROM crm.client").fetchone()["n"] == 0
    assert clients_store.member(conn, member_id)["company_name"] == "Regie Naef SA"


def test_adding_a_company_twice_is_the_same_member(
    conn, programme_id, companies
) -> None:
    """Two rows would put one agency in two columns of one board."""
    first, _ = clients_store.add_member(conn, programme_id, companies["CHE100000001"])
    again, existed = clients_store.add_member(
        conn, programme_id, companies["CHE100000001"]
    )
    conn.commit()
    assert existed
    assert again == first
    assert len(clients_store.members_for(conn, programme_id)) == 1


def test_a_company_carries_a_separate_stage_in_each_programme(
    conn, programme_id, companies
) -> None:
    """What `intel.prospect` could not do, and the reason this table exists."""
    other = clients_store.add_programme(conn, "PILOT", "A different endeavour")
    here, _ = clients_store.add_member(conn, programme_id, companies["CHE100000001"])
    there, _ = clients_store.add_member(conn, other, companies["CHE100000001"])
    clients_store.move_member(conn, here, "testing")
    conn.commit()

    assert clients_store.member(conn, here)["stage"] == "testing"
    assert clients_store.member(conn, there)["stage"] == "selected"


# --- moving -------------------------------------------------------------------


def test_a_move_is_recorded_so_a_card_can_say_how_long_it_has_sat(
    conn, programme_id, companies
) -> None:
    """No history table here: `audit.state_change` holds no foreign key and
    takes a transition from any module, which is what makes the board's
    existing `time_in_state` query date these cards with no new code."""
    member_id, _ = clients_store.add_member(
        conn, programme_id, companies["CHE100000001"]
    )
    clients_store.move_member(conn, member_id, "replied", by="tests")
    conn.commit()

    history = scout_store.state_history(conn, "programme_member", member_id)
    assert [(r["from_state"], r["to_state"]) for r in history] == [
        ("selected", "replied"), (None, "selected"),
    ]
    assert scout_store.time_in_state(conn, "programme_member", [member_id])


def test_a_stage_outside_the_programme_is_refused_and_says_which_exist(
    conn, programme_id, companies
) -> None:
    """The valid set is a column on another row rather than a constant, so a
    bare refusal would send the reader looking for a list that is not in the
    code."""
    member_id, _ = clients_store.add_member(
        conn, programme_id, companies["CHE100000001"]
    )
    conn.commit()
    with pytest.raises(ValueError, match="onboarding"):
        clients_store.move_member(conn, member_id, "shipped")


# --- the stage list -----------------------------------------------------------


def test_removing_a_stage_that_members_occupy_is_refused_with_the_count(
    conn, programme_id, companies
) -> None:
    """Absorbing this edit would mean deleting those members or moving them
    somewhere nobody chose. Both are decisions to take while looking at the
    cards, so the refusal names the stage and counts who is in it."""
    first, _ = clients_store.add_member(conn, programme_id, companies["CHE100000001"])
    second, _ = clients_store.add_member(conn, programme_id, companies["CHE100000002"])
    clients_store.move_member(conn, first, "testing")
    clients_store.move_member(conn, second, "testing")
    conn.commit()

    with pytest.raises(ValueError, match=r"testing \(2\)"):
        clients_store.set_stages(conn, programme_id, ["selected", "contacted"])
    assert clients_store.member(conn, first)["stage"] == "testing"


def test_stages_keep_their_order_and_lose_their_repeats(conn, programme_id) -> None:
    kept = clients_store.set_stages(
        conn, programme_id, ["sent", " trying ", "sent", "", "bought"]
    )
    conn.commit()
    assert kept == ["sent", "trying", "bought"]
    assert list(clients_store.programme(conn, programme_id)["stages"]) == kept


def test_a_programme_needs_at_least_one_stage(conn, programme_id) -> None:
    with pytest.raises(ValueError, match="at least one stage"):
        clients_store.set_stages(conn, programme_id, ["", "  "])


# --- bringing a shortlist in --------------------------------------------------


def test_bring_in_only_ever_adds(conn, programme_id, companies) -> None:
    """Re-running after another sweep must not walk an agency at `testing` back
    to the start. The same rule the prospect board's own bring-in keeps."""
    for uid in ("CHE100000001", "CHE100000002", "CHE100000003"):
        _sector(conn, companies[uid], "real-estate")

    assert clients_store.bring_in_members(conn, programme_id, sector="real-estate") == 3
    conn.commit()
    moved = clients_store.members_for(conn, programme_id)[0]
    clients_store.move_member(conn, int(moved["id"]), "testing")
    conn.commit()

    assert clients_store.bring_in_members(conn, programme_id, sector="real-estate") == 0
    conn.commit()
    assert clients_store.member(conn, int(moved["id"]))["stage"] == "testing"
    assert len(clients_store.members_for(conn, programme_id)) == 3


def test_bring_in_filters_by_canton(conn, programme_id, companies) -> None:
    for uid in ("CHE100000001", "CHE100000002", "CHE100000003"):
        _sector(conn, companies[uid], "real-estate")
    added = clients_store.bring_in_members(
        conn, programme_id, sector="real-estate", canton="GE"
    )
    conn.commit()
    assert added == 1
    assert clients_store.members_for(conn, programme_id)[0]["canton"] == "GE"


# --- what they said -----------------------------------------------------------


def test_feedback_belongs_to_the_programme_and_the_company_together(
    conn, programme_id, companies
) -> None:
    """Neither existing log can express this scoping. `crm.interaction` needs a
    client and these are not clients; `intel.note` is keyed on the company, so
    two programmes' feedback about one agency would sit in one undifferentiated
    list."""
    other = clients_store.add_programme(conn, "PILOT", "A different endeavour")
    here, _ = clients_store.add_member(conn, programme_id, companies["CHE100000001"])
    there, _ = clients_store.add_member(conn, other, companies["CHE100000001"])
    clients_store.add_feedback(conn, here, "photo upload fails on iOS 18", kind="bug")
    clients_store.add_feedback(conn, there, "unrelated pilot remark")
    conn.commit()

    said = clients_store.feedback_on(conn, programme_id)
    assert [row["body"] for row in said] == ["photo upload fails on iOS 18"]
    assert said[0]["company_name"] == "Regie Naef SA"


def test_feedback_needs_something_in_it(conn, programme_id, companies) -> None:
    member_id, _ = clients_store.add_member(
        conn, programme_id, companies["CHE100000001"]
    )
    with pytest.raises(ValueError, match="something in it"):
        clients_store.add_feedback(conn, member_id, "   ")


# --- mail, counted and never acted on -----------------------------------------


def test_inbound_mail_is_counted_and_moves_nothing(
    conn, programme_id, companies
) -> None:
    """The rule this whole schema is built against: nothing files itself.

    A message arriving is the prompt to move a card, and a human moves it.
    """
    member_id, _ = clients_store.add_member(
        conn, programme_id, companies["CHE100000001"]
    )
    for n in range(3):
        clients_store.save_email(conn, {
            "message_id": f"<m{n}@example.test>", "folder": "INBOX",
            "direction": "in", "sent_at": f"2026-09-1{n}",
            "subject": "Re: votre application", "from_email": "x@naef.test",
            "to_emails": ["me@example.test"], "company_id": companies["CHE100000001"],
        })
    conn.commit()

    member = clients_store.members_for(conn, programme_id)[0]
    assert member["inbound"] == 3
    assert member["last_inbound"], "the card says when they last wrote"
    assert member["stage"] == "selected", "counting a reply never moves the card"
    assert clients_store.member(conn, member_id)["stage"] == "selected"


# --- removing -----------------------------------------------------------------


def test_deleting_a_programme_keeps_the_companies_and_says_what_went(
    conn, programme_id, companies
) -> None:
    """A programme has no client to write a tombstone to: its members are
    companies and most never became one. `audit.state_change` takes it, because
    it holds no foreign key and outlives what it describes."""
    clients_store.add_member(conn, programme_id, companies["CHE100000001"])
    clients_store.add_member(conn, programme_id, companies["CHE100000002"])
    conn.commit()

    assert clients_store.delete_programme(
        conn, programme_id, reason="never ran"
    ) == "FLATCHECK"
    conn.commit()

    assert conn.execute("SELECT COUNT(*) AS n FROM core.company").fetchone()["n"] == 3
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM crm.programme_member"
    ).fetchone()["n"] == 0
    tomb = scout_store.state_history(conn, "programme", programme_id)[0]
    assert tomb["to_state"] == "deleted"
    assert "2 member(s) released" in tomb["reason"]


def test_removing_a_member_leaves_its_moves_in_the_audit_trail(
    conn, programme_id, companies
) -> None:
    member_id, _ = clients_store.add_member(
        conn, programme_id, companies["CHE100000001"]
    )
    clients_store.move_member(conn, member_id, "refused", reason="uses its own tool")
    clients_store.remove_member(conn, member_id)
    conn.commit()

    history = scout_store.state_history(conn, "programme_member", member_id)
    assert history[0]["to_state"] == "refused"
    assert history[0]["reason"] == "uses its own tool"


# --- the console --------------------------------------------------------------


def test_the_board_draws_the_programmes_own_stages(web, conn, programme_id) -> None:
    clients_store.set_stages(conn, programme_id, ["sent", "trying", "bought"])
    conn.commit()
    html = web.get(f"/programme/{programme_id}").text
    assert 'data-state="sent"' in html
    assert 'data-state="trying"' in html
    assert 'data-state="onboarding"' not in html, "the defaults are gone"


def test_the_drag_posts_to_this_programme(web, conn, programme_id) -> None:
    """Both boards share one drag handler; each says where its moves go."""
    html = web.get(f"/programme/{programme_id}").text
    assert f"fetch('/programme/{programme_id}/move'" in html
    assert "member_id: card.dataset.id" in html


def test_a_card_moves_from_the_fold_without_javascript(
    web, conn, programme_id, companies
) -> None:
    member_id, _ = clients_store.add_member(
        conn, programme_id, companies["CHE100000001"]
    )
    conn.commit()
    reply = web.post(f"/member/{member_id}/move", data={"stage": "replied"},
                     follow_redirects=False)
    assert reply.status_code == 303
    assert clients_store.member(conn, member_id)["stage"] == "replied"


def test_the_page_shows_a_refusal_rather_than_swallowing_it(
    web, conn, programme_id, companies
) -> None:
    member_id, _ = clients_store.add_member(
        conn, programme_id, companies["CHE100000001"]
    )
    clients_store.move_member(conn, member_id, "testing")
    conn.commit()
    reply = web.post(f"/programme/{programme_id}/stages",
                     data={"stages": "selected\ncontacted"}, follow_redirects=False)
    assert reply.status_code == 303
    assert "testing" in reply.headers["location"]
    assert list(clients_store.programme(conn, programme_id)["stages"])[0] == "selected"
    assert clients_store.member(conn, member_id)["stage"] == "testing"


def test_the_console_cannot_send_anything_from_a_programme(web) -> None:
    """The same guarantee the rest of the console gives, checked here because a
    rollout to fifty agencies is exactly where a send button would appear."""
    routes = [
        r.path for r in every_route(web.app)
        if "programme" in getattr(r, "path", "") or "member" in getattr(r, "path", "")
    ]
    assert routes
    assert not [r for r in routes if "send" in r or "mail" in r]
