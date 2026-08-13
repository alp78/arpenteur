"""Resolving officer rows into people, and the graph that follows.

`core.person` is what a notice said, where it said it. `core.party` is the
human those rows are about, and everything here is about the judgement between
the two, because that judgement is the whole honesty of the relationship graph.

The rule errs deliberately in one direction. A person wrongly split is a
missing edge: a gap in a suggestion, and recoverable. A person wrongly merged
is a false statement about a named individual in a tool that goes on to propose
writing to them, and no later correction reaches the letter that was sent. So
the rule splits when it is unsure, and every party records what it was decided
on and how much to believe it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.config import Territory
from arpenteur.console.app import create_app
from arpenteur.kernel.db import Connection
from arpenteur.modules.scout import store


@pytest.fixture
def settings(db_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        database_url=db_path,
        territory=Territory(cantons=("GE",), legal_forms=("0106",), name="geneve"),
        segments={},
    )


@pytest.fixture
def conn(settings) -> Connection:
    connection = db.open_db(settings.database_url)
    for n, name in enumerate(
        ("Alpha Sàrl", "Beta Sàrl", "Gamma Sàrl", "Delta Sàrl", "Epsilon Sàrl"), 1
    ):
        store.upsert_company(
            connection,
            {"uid": f"CHE{n:012d}", "name": name, "canton": "GE",
             "status": "ACTIVE", "method": "zefix", "hydrated": True},
        )
    connection.commit()
    return connection


@pytest.fixture
def client(settings, conn) -> TestClient:
    return TestClient(create_app(settings))


def officer(conn, company_id: int, name: str, **fields) -> None:
    store.save_people(
        conn, company_id,
        [{"full_name": name, "role": fields.get("role", "gérant"),
          "origin": fields.get("origin"), "domicile": fields.get("domicile"),
          "signature": fields.get("signature"), "last_seen_date": "2026-01-01",
          "source_url": "https://example.test/notice", "method": "shab"}],
    )


# --- the comparison form ------------------------------------------------------


def test_a_name_is_compared_unaccented_and_lowercased() -> None:
    assert store.sort_name("BOCCARD Christian") == store.sort_name("Boccard Christian")
    assert store.sort_name("Gorecki Frédéric") == "gorecki frederic"


def test_two_spellings_that_are_not_obviously_one_person_stay_apart() -> None:
    """Folding further would start merging people to make the graph look
    better, which is the failure this whole file is written against."""
    assert store.sort_name("Müller Hans") != store.sort_name("Mueller Hans")


# --- the rule -----------------------------------------------------------------


def test_one_name_and_one_origin_across_boards_is_one_person(conn) -> None:
    for company_id in (1, 2, 3):
        officer(conn, company_id, "Boccard Christian", origin="France",
                domicile="Gaillard")
    conn.commit()
    stats = store.resolve_parties(conn)
    conn.commit()
    assert stats["parties"] == 1 and stats["roles"] == 3 and stats["merged"] == 1
    person = store.parties(conn)[0]
    assert person["boards"] == 3
    assert person["method"] == "name-and-origin" and person["confidence"] == 0.9


def test_one_name_and_two_origins_is_two_people(conn) -> None:
    """The case a graph is most flattered by getting wrong. An origin is a
    legal attribute of a Swiss person printed in the notice, and two different
    ones under one name are two people until somebody says otherwise."""
    officer(conn, 1, "Favre Anne", origin="Gibloux")
    officer(conn, 2, "Favre Anne", origin="Rossens")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    assert [p["boards"] for p in store.parties(conn)] == [1, 1]
    assert {p["origin"] for p in store.parties(conn)} == {"Gibloux", "Rossens"}


def test_a_silent_row_joins_the_one_origin_that_is_known(conn) -> None:
    """The register introduces someone once and afterwards names them without
    repeating their origin. Refusing to join those would split most people
    from themselves."""
    officer(conn, 1, "Boccard Christian", origin="France")
    officer(conn, 2, "Boccard Christian")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    person = store.parties(conn)[0]
    assert person["boards"] == 2
    assert person["method"] == "sole-origin" and person["confidence"] == 0.7


def test_a_silent_row_joins_nobody_when_two_origins_are_known(conn) -> None:
    """It cannot say which of the two it belongs to, and guessing would attach
    a real board seat to the wrong human."""
    officer(conn, 1, "Favre Anne", origin="Gibloux")
    officer(conn, 2, "Favre Anne", origin="Rossens")
    officer(conn, 3, "Favre Anne")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    assert sorted(p["boards"] for p in store.parties(conn)) == [1, 1, 1]


def test_a_name_with_no_origin_anywhere_says_so(conn) -> None:
    officer(conn, 1, "Dupont Jean")
    officer(conn, 2, "Dupont Jean")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    person = store.parties(conn)[0]
    assert person["method"] == "name-only" and person["confidence"] == 0.5


def test_a_person_named_once_claims_nothing(conn) -> None:
    """Nothing was merged, so there is no identity judgement to doubt."""
    officer(conn, 1, "Dupont Jean")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    assert store.parties(conn)[0]["method"] == "single"


def test_resolving_twice_does_not_double_anything(conn) -> None:
    """It rebuilds, because the rule will change and a table that accumulated
    its own history would make every improvement a migration."""
    for company_id in (1, 2):
        officer(conn, company_id, "Boccard Christian", origin="France")
    conn.commit()
    first = store.resolve_parties(conn)
    conn.commit()
    second = store.resolve_parties(conn)
    conn.commit()
    assert first == second
    assert store.party_counts(conn)["parties"] == 1


def test_the_display_name_is_the_commonest_spelling(conn) -> None:
    officer(conn, 1, "de la Fontaine Marie", origin="Genève")
    officer(conn, 2, "de la Fontaine Marie", origin="Genève")
    officer(conn, 3, "De La Fontaine Marie", origin="Genève", role="associé")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    assert store.parties(conn)[0]["full_name"] == "de la Fontaine Marie"


# --- the graph ----------------------------------------------------------------


def test_board_overlap_names_who_the_link_is(conn) -> None:
    """The question the party table exists for. Asked of `core.person` it
    needs name matching inside the query, which is how two different people
    with one surname become a business relationship."""
    officer(conn, 1, "Boccard Christian", origin="France")
    officer(conn, 2, "Boccard Christian", origin="France")
    officer(conn, 2, "Gonzalez Javier", origin="Genève", role="associé")
    officer(conn, 3, "Gonzalez Javier", origin="Genève", role="associé")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    overlap = store.board_overlap(conn, 2)
    assert {row["name"] for row in overlap} == {"Alpha Sàrl", "Gamma Sàrl"}
    assert overlap[0]["people"] and overlap[0]["shared"] == 1


def test_the_strongest_link_comes_first(conn) -> None:
    """Two shared officers is a firmer route than one, and the list is read
    from the top."""
    for name in ("Boccard Christian", "Gonzalez Javier"):
        officer(conn, 1, name, origin="Genève", role=name)
        officer(conn, 2, name, origin="Genève", role=name)
    officer(conn, 3, "Boccard Christian", origin="Genève", role="Boccard Christian")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    overlap = store.board_overlap(conn, 1)
    assert [row["name"] for row in overlap] == ["Beta Sàrl", "Gamma Sàrl"]
    assert [row["shared"] for row in overlap] == [2, 1]


def test_a_route_is_the_shortest_chain_of_shared_officers(conn) -> None:
    officer(conn, 1, "Aaa Aaa", origin="Genève")
    officer(conn, 2, "Aaa Aaa", origin="Genève")
    officer(conn, 2, "Bbb Bbb", origin="Genève", role="associé")
    officer(conn, 3, "Bbb Bbb", origin="Genève", role="associé")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    routes = store.path_between(conn, 1, 3)
    assert routes and routes[0]["depth"] == 2
    assert routes[0]["path"] == [1, 2, 3]
    assert routes[0]["people"] == ["Aaa Aaa", "Bbb Bbb"]


def test_a_route_that_does_not_exist_is_an_answer(conn) -> None:
    officer(conn, 1, "Aaa Aaa", origin="Genève")
    officer(conn, 4, "Zzz Zzz", origin="Genève")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    assert store.path_between(conn, 1, 4) == []


def test_one_person_holding_two_roles_is_not_two_routes(conn) -> None:
    """The join multiplies by role, and the same chain of companies would come
    back twice with nothing to tell the copies apart."""
    officer(conn, 1, "Aaa Aaa", origin="Genève", role="gérant")
    officer(conn, 1, "Aaa Aaa", origin="Genève", role="associé")
    officer(conn, 2, "Aaa Aaa", origin="Genève", role="gérant")
    officer(conn, 2, "Aaa Aaa", origin="Genève", role="associé")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    routes = store.path_between(conn, 1, 2)
    assert [r["path"] for r in routes] == [[1, 2]]


def test_an_edge_can_be_taken_back_to_the_notice_it_came_from(conn) -> None:
    """A relationship graph nobody can audit is a rumour with an index."""
    officer(conn, 1, "Boccard Christian", origin="France")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    role = store.roles_for_party(conn, store.parties(conn)[0]["id"])[0]
    assert role["source_url"] == "https://example.test/notice"
    assert role["person_id"] is not None


def test_a_party_survives_the_officer_row_being_withdrawn(conn) -> None:
    """A reparse that takes back a person row should leave the edge visibly
    unsourced rather than make it vanish without trace."""
    officer(conn, 1, "Boccard Christian", origin="France")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    store.prune_people(conn, 1, [])
    conn.commit()
    role = store.roles_for_party(conn, store.parties(conn)[0]["id"])[0]
    assert role["person_id"] is None


# --- through the console ------------------------------------------------------


def test_the_people_page_sorts_by_reach(client, conn) -> None:
    officer(conn, 1, "Busy Person", origin="Genève")
    officer(conn, 2, "Busy Person", origin="Genève")
    officer(conn, 3, "Quiet Person", origin="Genève")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    body = client.get("/people").text
    assert body.index("Busy Person") < body.index("Quiet Person")


def test_a_weak_resolution_says_so_where_it_is_read(client, conn) -> None:
    """A table of people with no gloss on the method column gets trusted
    uniformly, and these are not uniform claims."""
    officer(conn, 1, "Dupont Jean")
    officer(conn, 2, "Dupont Jean")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    party_id = store.parties(conn)[0]["id"]
    assert "name only" in client.get("/people").text
    assert "two people with one name" in client.get(f"/party/{party_id}").text


def test_a_company_page_says_what_else_its_officers_sit_on(client, conn) -> None:
    officer(conn, 1, "Boccard Christian", origin="France")
    officer(conn, 2, "Boccard Christian", origin="France")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    body = client.get("/company/1").text
    assert "shares an officer with" in body and "Beta Sàrl" in body
    assert "1 other board" in body


def test_the_same_name_held_apart_is_offered_to_a_person(client, conn) -> None:
    """Sometimes it is two people and sometimes it is one: a commune merger or
    a country renaming moves an origin without moving the person."""
    officer(conn, 1, "Jashari Zaim", origin="Macédoine")
    officer(conn, 2, "Jashari Zaim", origin="Macédoine du Nord")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    party_id = store.parties(conn)[0]["id"]
    assert "possibly the same person" in client.get(f"/party/{party_id}").text


def test_a_route_page_names_every_hop(client, conn) -> None:
    officer(conn, 1, "Aaa Aaa", origin="Genève")
    officer(conn, 2, "Aaa Aaa", origin="Genève")
    officer(conn, 2, "Bbb Bbb", origin="Genève", role="associé")
    officer(conn, 3, "Bbb Bbb", origin="Genève", role="associé")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    body = client.get("/company/1/route/3").text
    assert "Beta Sàrl" in body and "Aaa Aaa" in body and "2 hops" in body


def test_no_route_is_reported_as_an_answer(client, conn) -> None:
    officer(conn, 1, "Aaa Aaa", origin="Genève")
    officer(conn, 4, "Zzz Zzz", origin="Genève")
    conn.commit()
    store.resolve_parties(conn)
    conn.commit()
    assert "No route" in client.get("/company/1/route/4").text
