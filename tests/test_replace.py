"""Looking again for a firm whose address bounced, and deciding what it found.

The two firms here are the shapes the first Limen campaign met. One bounced at
`contact@` on a domain that takes mail and publishes only a named agent; the
other bounced on a domain whose mail server no longer exists, and the owner's
free mailbox was printed in a directory. Nothing leaves the machine: the site
is a fake client, the search a fake model, the DNS a fixed table.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from arpenteur import db
from arpenteur.kernel.db import Connection
from arpenteur.modules.scout import store as scout_store
from arpenteur.stages import replace

pytestmark = pytest.mark.pg


class FakeClient:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages, self.asked = pages, []

    def get(self, url: str, **_kw: object) -> SimpleNamespace:
        self.asked.append(url)
        if url not in self.pages:
            return SimpleNamespace(status=404, body="", url=url)
        return SimpleNamespace(status=200, body=self.pages[url], url=url)

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class FakeProvider:
    def __init__(self, addresses: list[dict[str, str]]) -> None:
        self.addresses, self.asked = addresses, []

    def research(self, **kw: object) -> SimpleNamespace:
        self.asked.append(kw)
        return SimpleNamespace(data={"website": None, "addresses": self.addresses,
                                     "reasoning": ""}, searches=1,
                               input_tokens=0, output_tokens=0)


AJ_PAGES = {
    "https://aj.ch/": '<a href="/contact">Contact</a> '
                      '<a href="mailto:alice.rocchi@aj.ch">Alice</a> '
                      '<a href="mailto:communication@agence-web.fr">site</a>',
    "https://aj.ch/contact": "<p>Ecrivez à alice.rocchi@aj.ch</p>",
}
FJ_PAGES = {"https://www.fj.ch/robots.txt": "User-agent: *\nDisallow: /"}
LISTING = "https://www.local.ch/fr/d/gingins/1276/agence-immobiliere/jolivet"
VERDICTS = {"fj.ch": ("dead", "its mail server fj-ch.mail.protection.outlook.com does not exist")}


@pytest.fixture
def settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(root=tmp_path, cache_dir=tmp_path / "cache",
                           zefix=SimpleNamespace(contact="arpenteur (test)"),
                           llm=SimpleNamespace(enrich_model="claude-test"))


@pytest.fixture
def conn(db_path: Path, monkeypatch) -> Connection:
    monkeypatch.setattr(replace.mailcheck, "mail_verdict",
                        lambda domain, **_k: VERDICTS.get(domain, ("deliverable", "MX mx")))
    connection = db.open_db(db_path)
    for uid, name, canton, site, bounced in (
        ("CHE1", "A&J Immobilier Sàrl", "GE", "https://aj.ch/", "contact@aj.ch"),
        ("CHE2", "Agence Jolivet Sàrl", "VD", "https://www.fj.ch/", "contact@fj.ch"),
    ):
        db.upsert_company(connection, {"uid": uid, "name": name, "canton": canton,
                                       "status": "ACTIVE", "hydrated": True,
                                       "method": "zefix"})
        cid = connection.execute("SELECT id FROM core.company WHERE uid = %s",
                                 (uid,)).fetchone()["id"]
        db.save_site(connection, cid, {"status": "found", "url": site, "method": "directory"})
        scout_store.save_contact(connection, company_id=cid, kind="email", value=bounced,
                                 method="published", status="bounced")
    connection.commit()
    return connection


def _id(conn: Connection, uid: str) -> int:
    return int(conn.execute("SELECT id FROM core.company WHERE uid = %s",
                            (uid,)).fetchone()["id"])


def look(settings, conn, uid: str, *, pages=None, found=()) -> tuple[dict, FakeProvider]:
    provider = FakeProvider(list(found))
    stats = replace.search(settings, conn, company=uid, log=lambda _m: None,
                           client=FakeClient(pages or {}), provider=provider)
    return stats, provider


def proposals(conn: Connection, uid: str) -> dict[str, dict]:
    return {r["address"]: dict(r) for r in scout_store.address_proposals(conn, _id(conn, uid))}


# --- what a search proposes -----------------------------------------------------

def test_a_live_domain_gives_its_published_agent_and_the_desk_guess(settings, conn) -> None:
    look(settings, conn, "CHE1", pages=AJ_PAGES)
    found = proposals(conn, "CHE1")
    assert set(found) == {"alice.rocchi@aj.ch", "info@aj.ch"}
    assert (found["alice.rocchi@aj.ch"]["found_by"], found["alice.rocchi@aj.ch"]["kind"]) \
        == ("page", "person")
    assert found["alice.rocchi@aj.ch"]["source_url"] == "https://aj.ch/"
    assert found["info@aj.ch"]["found_by"] == "inferred"
    assert "unverified" in found["info@aj.ch"]["note"]


def test_another_firms_address_on_the_site_is_never_proposed(settings, conn) -> None:
    look(settings, conn, "CHE1", pages=AJ_PAGES)
    assert "communication@agence-web.fr" not in proposals(conn, "CHE1")
    tried = scout_store.address_search(conn, _id(conn, "CHE1"))["tried"]
    assert any("other domains" in t["what"] for t in tried)


def test_a_dead_domain_is_said_and_a_listed_free_mailbox_is_proposed(settings, conn) -> None:
    _stats, provider = look(settings, conn, "CHE2", pages=FJ_PAGES, found=[
        {"address": "francoise.jolivet@gmail.com", "page": LISTING},
        {"address": "contact@fj.ch", "page": LISTING},
        {"address": "info@fj.ch", "page": LISTING},
    ])
    found = proposals(conn, "CHE2")
    assert set(found) == {"francoise.jolivet@gmail.com"}, "nothing at a dead domain"
    gmail = found["francoise.jolivet@gmail.com"]
    assert gmail["found_by"] == "listing" and gmail["source_url"] == LISTING
    assert "free mailbox" in gmail["note"]
    search = scout_store.address_search(conn, _id(conn, "CHE2"))
    assert search["domains"] == [{"domain": "fj.ch", "verdict": "dead",
                                  "why": VERDICTS["fj.ch"][1]}]
    assert any("robots.txt" in t["why"] for t in search["tried"])
    assert "contact@fj.ch" in provider.asked[0]["user"], "the search is told what bounced"


def test_nothing_is_proposed_twice_and_a_refusal_stays(settings, conn) -> None:
    look(settings, conn, "CHE1", pages=AJ_PAGES)
    refused = proposals(conn, "CHE1")["info@aj.ch"]["id"]
    scout_store.decide_address(conn, refused, accept=False, by="test")
    conn.commit()
    stats, _ = look(settings, conn, "CHE1", pages=AJ_PAGES)
    assert stats["proposed"] == 0
    assert proposals(conn, "CHE1")["info@aj.ch"]["decision"] == "refused"


# --- deciding -------------------------------------------------------------------

def test_an_accepted_address_becomes_the_firms_with_where_it_was_found(settings, conn) -> None:
    look(settings, conn, "CHE2", pages=FJ_PAGES,
         found=[{"address": "francoise.jolivet@gmail.com", "page": LISTING}])
    proposal = proposals(conn, "CHE2")["francoise.jolivet@gmail.com"]
    decided = scout_store.decide_address(conn, proposal["id"], accept=True, by="operator")
    conn.commit()
    contact = conn.execute("SELECT * FROM core.contact WHERE id = %s",
                           (decided["contact_id"],)).fetchone()
    assert (contact["value"], contact["method"], contact["source_url"], contact["status"]) == \
        ("francoise.jolivet@gmail.com", "listing", LISTING, "deliverable")
    assert scout_store.accepted_address(conn, _id(conn, "CHE2"))["address"] == \
        "francoise.jolivet@gmail.com"
    with pytest.raises(ValueError, match="already accepted"):
        scout_store.decide_address(conn, proposal["id"], accept=False, by="operator")


def test_an_accepted_address_that_bounces_in_turn_is_no_longer_the_answer(
    settings, conn
) -> None:
    look(settings, conn, "CHE1", pages=AJ_PAGES)
    info = proposals(conn, "CHE1")["info@aj.ch"]["id"]
    scout_store.decide_address(conn, info, accept=True, by="operator")
    conn.execute("UPDATE core.contact SET status = 'bounced' WHERE value = 'info@aj.ch'")
    conn.commit()
    assert scout_store.accepted_address(conn, _id(conn, "CHE1")) is None


def test_the_command_line_shows_accepts_and_refuses(settings, conn, db_path, monkeypatch,
                                                    capsys) -> None:
    from arpenteur import cli
    from arpenteur.commands import common

    look(settings, conn, "CHE1", pages=AJ_PAGES)
    monkeypatch.setattr(common, "load_settings",
                        lambda: SimpleNamespace(database_url=db_path, modules=()))
    assert cli.main(["replace", "CHE1", "--show"]) == 0
    out = capsys.readouterr().out
    assert "info@aj.ch" in out and "undecided" in out
    assert cli.main(["replace", "CHE1", "--accept", "info@aj.ch"]) == 0
    assert "info@aj.ch: accepted" in capsys.readouterr().out
    assert cli.main(["replace", "CHE1", "--refuse", "nobody@aj.ch"]) == 1
    assert "not proposed" in capsys.readouterr().err
