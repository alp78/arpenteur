"""The board: the working set, its columns, and its swimlanes.

The survey list and the board answer different questions and the tests here are
mostly about keeping them different. A list sorted by score cannot show that a
company contacted in June has not moved since; a column of eight cards headed
"contacted" with nothing in "replied" says it at a glance.

Three decisions are load-bearing and each has a test:

  a working set     only companies somebody put on the board are on it. If a
                    good score were enough, the counts would be a property of
                    the scorer rather than of the work.
  never a reset     bringing a shortlist in twice must not walk a company at
                    "meeting" back to "new".
  two ways to move  dragging, and a plain form underneath it. A board that can
                    only be dragged cannot be operated from a keyboard or a
                    phone.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.config import Territory
from arpenteur.console import board as view
from arpenteur.console.app import create_app
from arpenteur.kernel.db import Connection
from arpenteur.modules.scout import store


@pytest.fixture
def settings(db_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        database_url=db_path,
        territory=Territory(cantons=("VD",), legal_forms=("0110",), name="vaud"),
        segments={"ems": "A nursing home.", "clinic": "A clinic."},
    )


@pytest.fixture
def conn(settings) -> Connection:
    connection = db.open_db(settings.database_url)
    for uid, name, canton, score, band in (
        ("CHE1", "Fondation Bois-Gentil", "VD", 88, "high"),
        ("CHE2", "EMS des Cèdres", "VD", 71, "high"),
        ("CHE3", "Clinique du Léman", "GE", 44, "medium"),
        ("CHE4", "Boulangerie Rime", "VD", 8, "low"),
    ):
        company_id = store.upsert_company(
            connection,
            {"uid": uid, "name": name, "canton": canton, "status": "ACTIVE",
             "method": "zefix", "hydrated": True},
        )
        store.save_fit(connection, company_id, {"score": score, "band": band})
    connection.commit()
    return connection


@pytest.fixture
def client(settings, conn) -> TestClient:
    return TestClient(create_app(settings))


# --- the working set ----------------------------------------------------------


def test_only_what_somebody_put_there_is_on_the_board(conn) -> None:
    """A high score is not a decision to work on something. Were it enough,
    the column counts would describe the scorer rather than the work."""
    assert store.board(conn) == []
    store.set_prospect(conn, 1, state="contacted")
    conn.commit()
    assert [row["name"] for row in store.board(conn)] == ["Fondation Bois-Gentil"]


def test_a_card_carries_what_it_needs_without_a_second_query(conn) -> None:
    """Forty cards each fetching their own segments is forty round trips to
    draw one page, and the usual way a kanban view becomes the slowest screen
    in a CRM."""
    store.save_segments(conn, 1, [{"segment": "ems", "confidence": 0.9}], model="test")
    store.save_contact(
        conn, company_id=1, kind="email", value="info@example.ch",
        method="published",
    )
    store.set_prospect(conn, 1, state="new", owner="alex")
    conn.commit()
    row = store.board(conn)[0]
    assert row["score"] == 88 and row["band"] == "high"
    assert row["segments"] == ["ems"] and row["reachable"] is True
    assert row["owner"] == "alex"


def test_the_board_filters_the_way_the_survey_does(conn) -> None:
    for company_id in (1, 2, 3):
        store.set_prospect(conn, company_id, state="new")
    conn.commit()
    assert len(store.board(conn, canton="VD")) == 2
    assert [r["name"] for r in store.board(conn, canton="GE")] == ["Clinique du Léman"]
    assert [r["name"] for r in store.board(conn, q="Bois")] == ["Fondation Bois-Gentil"]


def test_taking_a_company_off_the_board_keeps_what_is_known(conn) -> None:
    """A prospect row is a working state, not a finding."""
    store.set_prospect(conn, 1, state="parked")
    conn.commit()
    store.remove_from_board(conn, 1)
    conn.commit()
    assert store.board(conn) == []
    assert store.fit_for(conn, 1)["score"] == 88


# --- bringing a shortlist in --------------------------------------------------


def test_bringing_a_shortlist_in_takes_the_best_of_a_band(conn) -> None:
    assert store.bring_in(conn, band="high", limit=10) == 2
    conn.commit()
    assert {r["name"] for r in store.board(conn)} == {
        "Fondation Bois-Gentil", "EMS des Cèdres"
    }
    assert {r["state"] for r in store.board(conn)} == {"new"}


def test_bringing_the_same_shortlist_in_twice_resets_nothing(conn) -> None:
    """The mistake that would make the board untrustworthy: a company at
    "meeting" walked back to "new" by a routine bulk action."""
    store.bring_in(conn, band="high", limit=10)
    store.set_prospect(conn, 1, state="meeting")
    conn.commit()
    assert store.bring_in(conn, band="high", limit=10) == 0
    conn.commit()
    assert store.prospect_for(conn, 1)["state"] == "meeting"


def test_a_shortlist_can_be_narrowed_before_it_is_brought_in(conn) -> None:
    assert store.bring_in(conn, band="high", canton="VD", limit=1) == 1
    conn.commit()
    assert len(store.board(conn)) == 1


# --- swimlanes ----------------------------------------------------------------


def _row(**fields) -> dict:
    base = {"next_at": None, "band": None, "canton": None, "owner": None}
    return {**base, **fields}


def test_the_due_lane_says_what_has_slipped() -> None:
    """The lane the option exists for. Overdue first, because a board is read
    top down and the thing that has slipped should not be under the thing that
    has not."""
    today = date(2026, 8, 13)
    lanes = [
        view.lane_of(_row(next_at=(today - timedelta(days=3)).isoformat()), "due", today),
        view.lane_of(_row(next_at=today.isoformat()), "due", today),
        view.lane_of(_row(next_at=(today + timedelta(days=4)).isoformat()), "due", today),
        view.lane_of(_row(next_at=(today + timedelta(days=40)).isoformat()), "due", today),
        view.lane_of(_row(), "due", today),
    ]
    assert lanes == ["overdue", "today", "this week", "later", "no next action"]
    assert view._lane_order("due", set(lanes)) == list(view.DUE_LANES)


def test_a_lane_that_is_not_set_still_has_a_name() -> None:
    """"nobody" is a lane. A blank heading over four cards is not."""
    today = date(2026, 8, 13)
    assert view.lane_of(_row(), "owner", today) == "nobody"
    assert view.lane_of(_row(), "band", today) == "not scored"


# --- through the console ------------------------------------------------------


def test_the_board_draws_a_column_for_every_state(client, conn) -> None:
    store.set_prospect(conn, 1, state="contacted")
    conn.commit()
    page = client.get("/board").text
    for state in store.PROSPECT_STATES:
        assert f'data-state="{state}"' in page
    assert "Fondation Bois-Gentil" in page


def test_dropping_a_card_saves_where_it_landed(client, conn) -> None:
    store.set_prospect(conn, 1, state="new")
    conn.commit()
    reply = client.post("/board/move", data={"company_id": 1, "state": "replied"})
    assert reply.status_code == 200
    assert store.prospect_for(conn, 1)["state"] == "replied"


def test_the_fold_under_the_card_works_without_javascript(client, conn) -> None:
    """It carries where it came from and gets a redirect, where the drag gets
    an answer. Same route, because it is the same change."""
    store.set_prospect(conn, 1, state="new")
    conn.commit()
    reply = client.post(
        "/board/move",
        data={"company_id": 1, "state": "meeting", "back": "lane=due&canton=VD"},
        follow_redirects=False,
    )
    assert reply.status_code == 303
    assert reply.headers["location"] == "/board?lane=due&canton=VD"
    assert store.prospect_for(conn, 1)["state"] == "meeting"


def test_a_state_the_board_does_not_offer_is_refused(client, conn) -> None:
    """The move route is reachable by anything that can POST, and a typo would
    otherwise create a column nobody can see."""
    store.set_prospect(conn, 1, state="new")
    conn.commit()
    assert client.post(
        "/board/move", data={"company_id": 1, "state": "invoiced"}
    ).status_code == 400
    assert store.prospect_for(conn, 1)["state"] == "new"


def test_bringing_a_shortlist_in_from_the_page_says_how_many(client) -> None:
    reply = client.post(
        "/board/bring-in", data={"band": "high", "limit": "10"}, follow_redirects=True
    )
    assert "2 companies put in new" in reply.text


def test_a_won_card_offers_the_handoff(client, conn) -> None:
    """The board's own answer to the handoff gap: won is where a client record
    is opened, keeping the file rather than copying the name."""
    store.set_prospect(conn, 1, state="won")
    conn.commit()
    assert "make a client" in client.get("/board").text

    client.post("/company/1/client")
    page = client.get("/board").text
    assert "make a client" not in page and "BOIS-GENTIL" in page


def test_the_board_cannot_send_either(client) -> None:
    routes = {getattr(r, "path", "") for r in client.app.routes}
    # "mail" is not in this list: the console reads a mailbox now, and /mail is
    # the review queue. test_console.py holds the guarantee itself, as a scan
    # of the whole package for a send mechanism.
    assert not [p for p in routes if "send" in p or "smtp" in p]
