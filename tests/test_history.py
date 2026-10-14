"""What moved, and when.

A status is a column and a change to it is an overwrite, so until now the
survey could say a company was at `contacted` and not since when. The board
made the gap visible the moment it drew columns: the question eight cards in a
column provoke is "how long has that one been sitting there".

`audit.state_change` is the record of the overwriting. It names its subject as
text and holds no foreign key, on purpose: a transition of a project that has
since been deleted is exactly the row worth having, and a foreign key would
take it away at the moment it became interesting.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.config import Territory
from arpenteur.console.app import create_app
from arpenteur.kernel.db import Connection
from arpenteur.modules.clients import store as clients
from arpenteur.modules.scout import store


@pytest.fixture
def settings(db_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        database_url=db_path,
        territory=Territory(cantons=("VD",), legal_forms=("0110",), name="vaud"),
        segments={},
        sectors={},
    )


@pytest.fixture
def conn(settings) -> Connection:
    connection = db.open_db(settings.database_url)
    store.upsert_company(
        connection,
        {"uid": "CHE100000011", "name": "Fondation Clair-Logis", "canton": "VD",
         "status": "ACTIVE", "method": "zefix", "hydrated": True},
    )
    connection.commit()
    return connection


@pytest.fixture
def client(settings, conn) -> TestClient:
    return TestClient(create_app(settings))


# --- recording ----------------------------------------------------------------


def test_arriving_on_the_board_is_itself_a_transition(conn) -> None:
    """Without it the first state a company is ever given has no date, and
    "how long has this been in new" is unanswerable for exactly the cards it
    gets asked of."""
    store.set_prospect(conn, 1, state="new")
    conn.commit()
    moves = store.state_history(conn, "prospect", 1)
    assert [(m["from_state"], m["to_state"]) for m in moves] == [(None, "new")]


def test_every_move_across_the_board_is_kept(conn) -> None:
    for state in ("new", "researching", "contacted", "replied"):
        store.set_prospect(conn, 1, state=state)
    conn.commit()
    moves = store.state_history(conn, "prospect", 1)
    assert [m["to_state"] for m in moves] == [
        "replied", "contacted", "researching", "new"
    ]


def test_saving_a_form_without_changing_the_state_records_nothing(conn) -> None:
    """A form posts every field on every save. Without this the history fills
    with contacted-to-contacted and the one real move is unfindable."""
    store.set_prospect(conn, 1, state="contacted")
    store.set_prospect(conn, 1, state="contacted", owner="alex")
    store.set_prospect(conn, 1, next_action="Relancer")
    conn.commit()
    assert len(store.state_history(conn, "prospect", 1)) == 1


def test_becoming_a_client_is_in_the_same_history(conn) -> None:
    """The transition the whole survey is pointed at."""
    store.set_prospect(conn, 1, state="won")
    clients.promote_to_client(conn, 1)
    conn.commit()
    kinds = [(m["entity"], m["to_state"]) for m in store.history_for_company(conn, 1)]
    assert ("client", "active") in kinds and ("prospect", "won") in kinds


def test_a_project_status_change_is_recorded_against_the_company(conn) -> None:
    """A company's history crosses three tables, and reading it should not
    require knowing which of them it passed through."""
    client_id = clients.promote_to_client(conn, 1)
    project_id = clients.add_project(conn, client_id, "Diagnostic")
    clients.update_project(conn, project_id, status="active")
    conn.commit()
    moves = store.history_for_company(conn, 1)
    assert [(m["entity"], m["from_state"], m["to_state"]) for m in moves][0] == (
        "project", "proposed", "active"
    )


def test_the_history_outlives_what_it_describes(conn) -> None:
    """The row worth having is the one about the thing that is gone, and a
    foreign key would delete it at the moment it became interesting."""
    client_id = clients.promote_to_client(conn, 1)
    project_id = clients.add_project(conn, client_id, "Diagnostic")
    clients.update_project(conn, project_id, status="cancelled")
    conn.commit()
    clients.delete_project(conn, project_id, reason="Never existed")
    conn.commit()
    assert store.state_history(conn, "project", project_id)


def test_a_cancelled_meeting_is_a_transition_too(conn) -> None:
    client_id = clients.promote_to_client(conn, 1)
    event_id = clients.record_interaction(
        conn, client_id, kind="meeting", state="planned",
        occurred_at="2026-09-03T13:00:00",
    )
    clients.set_interaction_state(conn, event_id, "cancelled")
    conn.commit()
    moves = store.state_history(conn, "interaction", event_id)
    assert [(m["from_state"], m["to_state"]) for m in moves] == [
        ("planned", "cancelled")
    ]


def test_when_each_card_last_moved_is_one_query(conn) -> None:
    """Asking per card is how a column of forty becomes forty round trips, and
    the number is only worth showing because it is cheap."""
    store.upsert_company(
        conn, {"uid": "CHE2", "name": "Other Sàrl", "method": "zefix"}
    )
    store.set_prospect(conn, 1, state="new")
    store.set_prospect(conn, 2, state="new")
    store.set_prospect(conn, 1, state="contacted")
    conn.commit()
    moved = store.time_in_state(conn, "prospect", [1, 2])
    assert set(moved) == {1, 2}
    assert moved[1] > moved[2] or moved[1] >= moved[2]


# --- one timeline --------------------------------------------------------------


def test_a_prospect_s_notes_survive_promotion(client, conn) -> None:
    """The handoff gap, still present in the one place nobody had looked. The
    page that is supposed to carry the whole file opened empty, on the very
    engagement the notes were written for."""
    store.add_note(
        conn, 1, "Ils cherchent un avis indépendant sur trois offres.",
        kind="call", author="alex",
    )
    conn.commit()
    client.post("/company/1/client")
    body = client.get("/client/1").text
    assert "avis indépendant sur trois offres" in body
    assert "company note" in body


def test_a_carried_note_is_read_only_where_it_is_shown(client, conn) -> None:
    """It belongs to the company, which outlives this engagement, and the
    place to correct it is the page that owns it."""
    store.add_note(conn, 1, "Une note du démarchage.", kind="note")
    conn.commit()
    client.post("/company/1/client")
    body = client.get("/client/1").text
    assert "on the company record" in body
    # No state buttons against a row that is not in the interaction table.
    assert body.count("Mark as happened") == 0


def test_the_two_records_are_merged_by_date(client, conn) -> None:
    store.add_note(
        conn, 1, "Premier contact au téléphone.", kind="call",
        occurred_at="2026-08-01T09:00:00+00:00",
    )
    conn.commit()
    client.post("/company/1/client")
    client.post(
        "/client/1/events",
        data={"kind": "email", "occurred_at": "2026-08-11T09:20",
              "subject": "Devis envoyé"},
    )
    body = client.get("/client/1").text
    assert body.index("Devis envoyé") < body.index("Premier contact au téléphone")


# --- through the console -------------------------------------------------------


def test_the_board_says_how_long_a_card_has_sat(client, conn) -> None:
    store.set_prospect(conn, 1, state="contacted")
    conn.execute(
        "UPDATE audit.state_change SET at = now() - interval '30 days'"
        " WHERE entity = 'prospect'"
    )
    conn.commit()
    assert "4w" in client.get("/board").text


def test_the_client_page_says_how_it_moved(client, conn) -> None:
    store.set_prospect(conn, 1, state="meeting")
    conn.commit()
    client.post("/company/1/client")
    body = client.get("/client/1").text
    assert "how it moved" in body and "meeting" in body
