"""The reach stage: reading a company's own site for a published address.

The parsing is tested in test_web.py. What is tested here is the conduct:
what gets fetched, what does not, what a second run does, and whether the
row that lands can answer where the address came from.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from arpenteur import db
from arpenteur.kernel.db import Connection
from arpenteur.config import Settings
from arpenteur.stages import reach

HOME = """
<a href="/contact">Contact</a>
<a href="/services">Services</a>
<p>Une etude d'avocats a Geneve.</p>
"""

CONTACT = """<p>Ecrivez-nous : <a href="mailto:info@etude.ch">info@etude.ch</a></p>"""


class FakeClient:
    """Stands in for PoliteClient. Records every URL it was asked for, which
    is how the politeness claims below are actually checked."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.asked: list[str] = []

    def get(self, url: str, **_kw: object) -> SimpleNamespace:
        self.asked.append(url)
        if url not in self.pages:
            return SimpleNamespace(status=404, body="", url=url)
        return SimpleNamespace(status=200, body=self.pages[url], url=url)

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


PAGES = {
    "https://etude.ch/": HOME,
    "https://etude.ch/contact": CONTACT,
    "https://etude.ch/services": "<p>Nos services</p>",
}


@pytest.fixture
def settings(tmp_path: Path) -> SimpleNamespace:
    fake = SimpleNamespace(
        root=tmp_path,
        cache_dir=tmp_path / "cache",
        statuses=("ACTIVE",),
        zefix=SimpleNamespace(contact="arpenteur (mailto:test@example.ch)"),
    )
    fake.status_clause = lambda column="c.status": Settings.status_clause(fake, column)
    return fake


@pytest.fixture
def conn(db_path: Path) -> Connection:
    connection = db.open_db(db_path)
    db.upsert_company(
        connection,
        {"uid": "CHE1", "name": "Etude Sàrl", "canton": "GE", "status": "ACTIVE",
         "hydrated": True, "method": "zefix"},
    )
    db.save_site(
        connection, 1,
        {"status": "found", "url": "https://etude.ch/", "method": "web-search"},
    )
    connection.commit()
    return connection


def reach_with(monkeypatch, client: FakeClient, settings, conn, **kwargs):
    monkeypatch.setattr(reach, "_client", lambda _s: client)
    return reach.run(settings, conn, log=lambda _m: None, **kwargs)


# --- what it finds ------------------------------------------------------------


def test_a_published_address_is_stored_with_the_page_it_was_on(
    monkeypatch, settings, conn
) -> None:
    """`source_url` must be the page carrying the address, not the home page,
    or a suppression decision months later cannot be checked against what was
    actually published."""
    reach_with(monkeypatch, FakeClient(PAGES), settings, conn)
    row = conn.execute("SELECT * FROM core.contact").fetchone()
    assert row["value"] == "info@etude.ch"
    assert row["source_url"] == "https://etude.ch/contact"
    assert row["method"] == "published"


def test_a_site_publishing_nothing_is_a_finding_not_a_failure(
    monkeypatch, settings, conn
) -> None:
    """A web form and no address is a settled answer, and the company belongs
    to the pattern tier rather than to a re-run."""
    stats = reach_with(
        monkeypatch, FakeClient({"https://etude.ch/": "<p>Formulaire</p>"}),
        settings, conn,
    )
    assert stats["without"] == 1 and stats["for_pattern"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM core.contact").fetchone()[0] == 0
    assert conn.execute("SELECT reached_at FROM intel.site").fetchone()[0] is not None


# --- conduct ------------------------------------------------------------------


def test_robots_txt_is_asked_before_the_site_is_read(monkeypatch, settings, conn) -> None:
    client = FakeClient(PAGES)
    reach_with(monkeypatch, client, settings, conn)
    assert client.asked[0] == "https://etude.ch/robots.txt"


def test_a_site_that_asks_us_not_to_is_not_read(monkeypatch, settings, conn) -> None:
    """The first time this tool opens a stranger's website itself. A tool that
    reads company sites without checking is a scraper whatever its rate limit
    says."""
    client = FakeClient(
        {"https://etude.ch/robots.txt": "User-agent: *\nDisallow: /", **PAGES}
    )
    stats = reach_with(monkeypatch, client, settings, conn)
    assert stats["blocked"] == 1
    assert client.asked == ["https://etude.ch/robots.txt"]
    assert conn.execute("SELECT COUNT(*) AS n FROM core.contact").fetchone()[0] == 0


def test_a_disallowed_subpage_is_skipped_but_the_home_page_is_not(
    monkeypatch, settings, conn
) -> None:
    client = FakeClient(
        {"https://etude.ch/robots.txt": "User-agent: *\nDisallow: /contact", **PAGES}
    )
    reach_with(monkeypatch, client, settings, conn)
    assert "https://etude.ch/contact" not in client.asked
    assert "https://etude.ch/" in client.asked


def test_no_robots_file_means_allowed(monkeypatch, settings, conn) -> None:
    """Absence of a rule is not a prohibition."""
    stats = reach_with(monkeypatch, FakeClient(PAGES), settings, conn)
    assert stats["with_address"] == 1


def test_only_pages_that_look_like_contact_pages_are_fetched(
    monkeypatch, settings, conn
) -> None:
    """The page budget is the difference between this and a crawler."""
    client = FakeClient(PAGES)
    reach_with(monkeypatch, client, settings, conn)
    assert "https://etude.ch/services" not in client.asked


# --- sites whose navigation is JavaScript --------------------------------------

# What the first live run met: a page builder serves the navigation as
# JavaScript, so the markup that arrives carries no <a href> at all, while the
# address sits on a page the sitemap names.
BUILT_HOME = "<html><body><div id='app'>Une etude d'avocats.</div></body></html>"

BUILT = {
    "https://etude.ch/": BUILT_HOME,
    "https://etude.ch/robots.txt": "User-agent: *\nAllow: /\nSitemap: https://etude.ch/sitemap.xml",
    "https://etude.ch/sitemap.xml": (
        "<sitemapindex><sitemap><loc>https://etude.ch/blog-sitemap.xml</loc></sitemap>"
        "<sitemap><loc>https://etude.ch/pages-sitemap.xml</loc></sitemap></sitemapindex>"
    ),
    "https://etude.ch/pages-sitemap.xml": (
        "<urlset><url><loc>https://etude.ch/tarifs</loc></url>"
        "<url><loc>https://etude.ch/mentions-l%C3%A9gales</loc></url></urlset>"
    ),
    "https://etude.ch/mentions-l%C3%A9gales": CONTACT,
}


def test_a_site_with_no_links_is_found_through_its_sitemap(
    monkeypatch, settings, conn
) -> None:
    stats = reach_with(monkeypatch, FakeClient(BUILT), settings, conn)
    assert stats["with_address"] == 1
    row = conn.execute("SELECT * FROM core.contact").fetchone()
    assert row["value"] == "info@etude.ch"
    assert row["source_url"] == "https://etude.ch/mentions-l%C3%A9gales"


def test_the_sitemap_named_in_robots_is_the_one_asked_for(
    monkeypatch, settings, conn
) -> None:
    client = FakeClient(BUILT)
    reach_with(monkeypatch, client, settings, conn)
    assert "https://etude.ch/sitemap.xml" in client.asked
    assert "https://etude.ch/pages-sitemap.xml" in client.asked


def test_the_blog_feed_is_not_read_looking_for_an_address(
    monkeypatch, settings, conn
) -> None:
    """An index lists several sitemaps and the pages live in one of them.
    Reading the blog feed first spends the budget on the wrong file."""
    client = FakeClient(BUILT)
    reach_with(monkeypatch, client, settings, conn)
    assert "https://etude.ch/tarifs" not in client.asked


def test_a_sitemap_is_not_fetched_when_the_page_has_links(
    monkeypatch, settings, conn
) -> None:
    """It is a fallback, not a routine extra request."""
    client = FakeClient(PAGES)
    stats = reach_with(monkeypatch, client, settings, conn)
    assert stats["sitemaps"] == 0
    assert not [url for url in client.asked if "sitemap" in url]


# --- running it twice ---------------------------------------------------------


def test_a_second_run_reads_nothing(monkeypatch, settings, conn) -> None:
    """The marker records the looking, not the finding. Without it a site
    publishing only a form is re-fetched forever."""
    reach_with(monkeypatch, FakeClient(PAGES), settings, conn)
    client = FakeClient(PAGES)
    stats = reach_with(monkeypatch, client, settings, conn)
    assert stats["considered"] == 0 and client.asked == []


def test_refresh_reads_it_again_and_reports_the_address_as_known(
    monkeypatch, settings, conn
) -> None:
    """The count that an upsert's rowcount got wrong: an address already held
    is found again, and must not be reported as newly found."""
    first = reach_with(monkeypatch, FakeClient(PAGES), settings, conn)
    assert (first["addresses"], first["new"]) == (1, 1)

    second = reach_with(monkeypatch, FakeClient(PAGES), settings, conn, refresh=True)
    assert (second["addresses"], second["new"]) == (1, 0)
    assert conn.execute("SELECT COUNT(*) AS n FROM core.contact").fetchone()[0] == 1


def test_re_enriching_onto_a_new_url_makes_the_site_worth_reading_again(
    monkeypatch, settings, conn
) -> None:
    """Having looked survives a re-reading of the same site and does not
    survive a different one."""
    reach_with(monkeypatch, FakeClient(PAGES), settings, conn)

    db.save_site(
        conn, 1,
        {"status": "found", "url": "https://etude.ch/", "method": "web-search"},
    )
    conn.commit()
    assert reach_with(monkeypatch, FakeClient(PAGES), settings, conn)["considered"] == 0

    db.save_site(
        conn, 1,
        {"status": "found", "url": "https://nouveau.ch/", "method": "web-search"},
    )
    conn.commit()
    assert reach_with(monkeypatch, FakeClient(PAGES), settings, conn)["considered"] == 1


def test_a_suppressed_address_stays_suppressed_when_found_again(
    monkeypatch, settings, conn
) -> None:
    """A human's decision outranks the site continuing to publish it."""
    reach_with(monkeypatch, FakeClient(PAGES), settings, conn)
    conn.execute("UPDATE core.contact SET suppressed = TRUE")
    conn.commit()
    reach_with(monkeypatch, FakeClient(PAGES), settings, conn, refresh=True)
    assert conn.execute("SELECT suppressed FROM core.contact").fetchone()[0] == 1


# --- selection and resilience -------------------------------------------------


def test_a_company_with_no_site_found_is_left_for_the_later_tiers(
    monkeypatch, settings, conn
) -> None:
    """"No address found" and "no site to look at" are different answers, and
    only the second is a job for pattern or provider."""
    db.upsert_company(
        conn,
        {"uid": "CHE2", "name": "Invisible Sàrl", "canton": "GE", "status": "ACTIVE",
         "hydrated": True, "method": "zefix"},
    )
    db.save_site(conn, 2, {"status": "not_found", "method": "web-search"})
    conn.commit()
    stats = reach_with(monkeypatch, FakeClient(PAGES), settings, conn)
    assert stats["considered"] == 1 and stats["for_pattern"] == 1


def test_a_company_in_liquidation_is_not_read(monkeypatch, settings, conn) -> None:
    db.upsert_company(
        conn,
        {"uid": "CHE9", "name": "Mourante Sàrl", "canton": "GE",
         "status": "BEING_CANCELLED", "hydrated": True, "method": "zefix"},
    )
    db.save_site(
        conn, 2, {"status": "found", "url": "https://m.ch/", "method": "web-search"}
    )
    conn.commit()
    assert reach_with(monkeypatch, FakeClient(PAGES), settings, conn)["considered"] == 1


def test_a_site_that_does_not_answer_does_not_end_the_run(
    monkeypatch, settings, conn
) -> None:
    db.upsert_company(
        conn,
        {"uid": "CHE2", "name": "Autre Sàrl", "canton": "GE", "status": "ACTIVE",
         "hydrated": True, "method": "zefix"},
    )
    db.save_site(
        conn, 2, {"status": "found", "url": "https://absent.ch/", "method": "web-search"}
    )
    conn.commit()
    stats = reach_with(monkeypatch, FakeClient(PAGES), settings, conn)
    assert stats["unreadable"] == 1 and stats["with_address"] == 1


def test_dry_run_fetches_nothing(monkeypatch, settings, conn) -> None:
    client = FakeClient(PAGES)
    stats = reach_with(monkeypatch, client, settings, conn, dry_run=True)
    assert stats["considered"] == 1 and client.asked == []
    assert conn.execute("SELECT reached_at FROM intel.site").fetchone()[0] is None


# --- the cascade: what a negative is allowed to claim --------------------------
#
# `none` is the settled negative. It tells every later run to stop looking and
# it lets an export print a dash, so the cost of it being wrong is not a wrong
# label but a company that leaves the search and cannot come back. These pin
# the two ways it was being handed out unearned.


class FakeProvider:
    """A search tier that answers, or refuses, on command."""

    def __init__(self, data: dict | None = None, raises: Exception | None = None):
        self.data = data
        self.raises = raises
        self.calls = 0

    def research(self, **_kw: object) -> SimpleNamespace:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return SimpleNamespace(
            data=self.data, searches=1, input_tokens=0, output_tokens=0
        )


ANON = {"uid": "CHE9", "name": "Introuvable SA", "canton": "GE",
        "legal_seat": "Genève"}


@pytest.fixture
def no_mx(monkeypatch):
    """No domain accepts mail, so tiers 3 and 4 fall through to the end."""
    monkeypatch.setattr(reach.mailcheck, "accepts_mail", lambda *_a, **_k: False)


def test_a_cascade_that_never_searched_does_not_report_a_settled_negative(
    no_mx,
) -> None:
    """The machine running this has no API credit, so the search tier is off.
    Calling that outcome `none` would claim the company publishes nothing when
    the tier that finds most companies never ran."""
    hunt = reach.hunt_one(
        FakeClient({}), ANON, agent="test", allow_search=False,
    )
    assert hunt.outcome == "partial"
    assert "search" in hunt.note
    assert any("search" in t["what"] for t in hunt.tried)


def test_a_search_that_ran_and_found_nothing_is_settled(no_mx) -> None:
    """The other side of it: the tier ran, so the negative is earned and the
    company should not be paid for twice."""
    provider = FakeProvider({"website": None, "emails": [], "confidence": 0.0,
                             "reasoning": "no such company"})
    hunt = reach.hunt_one(
        FakeClient({}), ANON, agent="test", provider=provider,
    )
    assert provider.calls == 1
    assert hunt.outcome == "none" and hunt.note == "every tier ran"


def test_a_search_tier_that_fails_leaves_the_company_owed_a_look(no_mx) -> None:
    """A credit error and an empty result are not the same fact. Treating them
    alike is how an outage becomes a permanent negative in the record."""
    provider = FakeProvider(raises=RuntimeError("credit balance is too low"))
    hunt = reach.hunt_one(
        FakeClient({}), ANON, agent="test", provider=provider,
    )
    assert hunt.outcome == "partial"
    assert any("credit balance" in t["why"] for t in hunt.tried)


def test_an_unfinished_company_is_selected_again_and_a_settled_one_is_not(
    monkeypatch, settings, conn
) -> None:
    """The selection is where a premature negative does its damage: it skipped
    on the existence of a row, so one source having nothing retired a company
    from the cascade before its own site was ever read."""
    from arpenteur.modules.scout import store as scout_store

    db.upsert_company(
        conn,
        {"uid": "CHE3", "name": "Ouverte SA", "canton": "GE", "status": "ACTIVE",
         "hydrated": True, "method": "zefix"},
    )
    conn.commit()
    scout_store.save_address_hunt(conn, 1, reach.Hunt("none", note="every tier ran"))
    scout_store.save_address_hunt(
        conn, 2, reach.Hunt("partial", note="ran without the search tier")
    )
    conn.commit()

    monkeypatch.setattr(reach, "_client", lambda _s: FakeClient({}))
    stats = reach.hunt_all(
        settings, conn, allow_search=False, dry_run=True, log=lambda _m: None
    )
    assert stats["considered"] == 1
