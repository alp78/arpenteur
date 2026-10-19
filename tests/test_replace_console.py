"""The replacement search on the company page: the button, the run, the decisions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.config import Territory
from arpenteur.console.app import create_app
from arpenteur.kernel.db import Connection
from arpenteur.modules.scout import store

pytestmark = pytest.mark.pg


@pytest.fixture
def settings(db_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        database_url=db_path,
        territory=Territory(cantons=("GE",), legal_forms=("0107",), name="geneve"),
        segments={"regie-core": "Manages property."}, sectors={},
    )


@pytest.fixture
def conn(settings) -> Connection:
    connection = db.open_db(settings.database_url)
    for uid, name, address, status in (("CHE1", "A&J Immobilier Sàrl", "contact@aj.ch", "bounced"),
                                       ("CHE2", "Régie Calme SA", "info@calme.ch", "deliverable")):
        cid = store.upsert_company(connection, {"uid": uid, "name": name, "canton": "GE",
                                                "status": "ACTIVE", "method": "zefix",
                                                "hydrated": True})
        store.save_contact(connection, company_id=cid, kind="email", value=address,
                           method="published", status=status)
    connection.commit()
    return connection


@pytest.fixture
def web(settings, conn) -> TestClient:
    client = TestClient(create_app(settings))
    # The route reloads the settings from the project folder before a run;
    # these settings are a stand-in with no folder behind them.
    client.app.state.console.reload = lambda: settings
    return client


def _id(conn: Connection, uid: str) -> int:
    return int(conn.execute("SELECT id FROM core.company WHERE uid = %s", (uid,)).fetchone()["id"])


def test_a_bounced_address_says_so_and_offers_the_search(web, conn) -> None:
    page = web.get(f"/company/{_id(conn, 'CHE1')}").text
    assert ">bounced<" in page and "Find a replacement" in page
    calm = web.get(f"/company/{_id(conn, 'CHE2')}").text
    assert "Find a replacement" not in calm


def test_the_button_starts_the_search_for_that_firm_as_a_run(web, conn) -> None:
    started = []
    runner = web.app.state.console.runner
    runner.start = lambda name, form, **kw: started.append((name, form, kw)) or 7
    reply = web.post(f"/company/{_id(conn, 'CHE1')}/replacement", follow_redirects=False)
    assert reply.status_code == 303 and reply.headers["location"] == "/runs/7"
    assert started == [("replace", {"company": str(_id(conn, "CHE1"))}, {"internal": True})]


def test_an_unknown_firm_is_refused_before_any_run(web) -> None:
    reply = web.post("/company/999999/replacement", follow_redirects=False)
    assert "error=" in reply.headers["location"]


def _propose(conn: Connection) -> tuple[int, int]:
    cid = _id(conn, "CHE1")
    store.propose_address(conn, cid, address="alice.rocchi@aj.ch", kind="person",
                          found_by="page", source_url="https://aj.ch/contact",
                          verdict="deliverable")
    store.propose_address(conn, cid, address="info@aj.ch", kind="desk", found_by="inferred",
                          source_url=None, verdict="deliverable",
                          note="constructed at a domain that takes mail")
    store.record_address_search(conn, cid, replaces=["contact@aj.ch"],
                                domains=[{"domain": "aj.ch", "verdict": "deliverable",
                                          "why": "MX smtp.google.com"}], tried=[])
    conn.commit()
    ids = {p["address"]: p["id"] for p in store.address_proposals(conn, cid)}
    return ids["alice.rocchi@aj.ch"], ids["info@aj.ch"]


def test_the_panel_lists_each_proposal_with_both_decisions(web, conn) -> None:
    _propose(conn)
    page = web.get(f"/company/{_id(conn, 'CHE1')}").text
    assert "id='replacement'" in page and "aj.ch</b>: MX smtp.google.com" in page
    assert "alice.rocchi@aj.ch" in page and "a named person" in page
    assert "a guess" in page and page.count("Use this address") == 2


def test_accepting_stores_the_address_and_refusing_keeps_the_no(web, conn) -> None:
    alice, info = _propose(conn)
    accepted = web.post(f"/proposal/{info}/accept", follow_redirects=False)
    assert "note=" in accepted.headers["location"]
    held = {r["value"]: r for r in store.contacts_for(conn, _id(conn, "CHE1"))}
    assert held["info@aj.ch"]["method"] == "inferred"
    refused = web.post(f"/proposal/{alice}/refuse", follow_redirects=False)
    assert "note=" in refused.headers["location"]
    assert "alice.rocchi@aj.ch" not in {r["value"] for r in store.contacts_for(conn, _id(conn, "CHE1"))}
    again = web.post(f"/proposal/{alice}/accept", follow_redirects=False)
    assert "error=" in again.headers["location"]
    assert "accepted on" in web.get(f"/company/{_id(conn, 'CHE1')}").text


def test_a_decision_with_no_name_is_refused(web, conn) -> None:
    _alice, info = _propose(conn)
    assert "error=" in web.post(f"/proposal/{info}/maybe",
                                follow_redirects=False).headers["location"]
