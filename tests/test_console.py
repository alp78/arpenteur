"""The console, over a real database and a real HTTP client.

Three properties are worth more than the rest.

There is no way to send anything. Not a disabled button, not a guarded route:
no route at all. The defence against Swiss law on unsolicited advertising mail
is that the software has no mechanism, and a test is how that stays true when
somebody later adds a "helpful" endpoint.

A correction made here is the same row a re-run has to respect. The console
writes through the same helpers the stages use, so there is one meaning of
"manual" rather than two.

And nothing user-supplied reaches the page unescaped. Company names come from
a public register, notes are typed by hand, and neither is trustworthy.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.kernel.db import Connection
from arpenteur.config import Territory
from arpenteur.console import create_app

SEGMENTS = {
    "sme-owner": "A small owner-run business.",
    "data-driven": "Work that produces or depends on data.",
}

SECTORS = {
    "legal-accounting": "Fiduciary, accounting and audit work.",
    "hospitality": "Restaurants, cafés and hotels.",
}


@pytest.fixture
def settings(db_path: str) -> SimpleNamespace:
    """Enough of `Settings` for every page in the header to render.

    It used to hold three attributes, which was enough for the pages the tests
    happened to open. Walking every navigation link is what showed that up: a
    page nobody had a test for is a page nobody knew was reachable.
    """
    return SimpleNamespace(
        database_url=db_path,
        territory=Territory(cantons=("GE",), legal_forms=("0107",), name="geneve"),
        segments=SEGMENTS,
        sectors=SECTORS,
        mail=SimpleNamespace(configured=False),
    )


@pytest.fixture
def conn(settings) -> Connection:
    connection = db.open_db(settings.database_url)
    db.upsert_company(
        connection,
        {"uid": "CHE1", "name": "Fiduciaire Sàrl", "canton": "GE",
         "legal_seat": "Genève", "legal_form_label": "Sàrl", "status": "ACTIVE",
         "purpose": "Tenue de comptabilité.", "hydrated": True, "method": "zefix"},
    )
    db.save_segments(
        connection, 1, [{"segment": "sme-owner", "confidence": 0.7}], "claude-haiku-4-5"
    )
    db.save_site(
        connection, 1,
        {"status": "found", "url": "https://f.ch", "summary": "Fiduciaire.",
         "signals": ["site ancien"], "restricted": ["poursuite en cours"],
         "method": "web-search"},
    )
    db.save_fit(
        connection, 1,
        {"score": 72, "band": "high", "reasoning": "Bon profil.",
         "advice": "Bonjour, j'ai vu votre site...", "against": ["déjà accompagnée"],
         "matched": ["site ancien"], "model": "m", "brief_hash": "abc"},
    )
    connection.commit()
    return connection


@pytest.fixture
def client(settings, conn) -> TestClient:
    return TestClient(create_app(settings))


# --- nothing sends ------------------------------------------------------------


def test_no_route_transmits_anything(settings, conn) -> None:
    """Not a disabled button. No mechanism at all.

    LCD art. 3 al. 1 let. o draws the line at mass advertising email without
    consent, and this tool stays on the research side by having no way across.

    The word "mail" used to be in the list below and is deliberately not any
    more: the console reads a mailbox now, and `/mail` is the queue where
    imported correspondence waits for somebody to accept it. Reading is not
    transmitting, and a test that cannot tell them apart would have had to be
    deleted the first time it was inconvenient. What replaces it is stronger
    than a word in a path: the package as a whole holds no send mechanism.
    """
    app = create_app(settings)
    paths = " ".join(getattr(r, "path", "") for r in app.routes).lower()
    for word in ("send", "smtp", "transmit", "dispatch"):
        assert word not in paths, f"a route mentioning {word!r} appeared"

    root = Path(__file__).resolve().parent.parent / "arpenteur"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "smtplib" not in text, f"{path.name} imports an SMTP client"
        assert "SMTP(" not in text, f"{path.name} opens an SMTP session"
        # The IMAP commands that write to a mailbox. Arpenteur reads one, and
        # a marked-read or moved message is a change to somebody else's record
        # of their own correspondence.
        for command in ('"APPEND"', '"STORE"', '"EXPUNGE"'):
            assert command not in text, f"{path.name} writes to the mailbox"


def test_the_advice_is_labelled_as_a_draft(client) -> None:
    """Shown so a human rewrites it, not so a human forwards it."""
    body = client.get("/company/1").text
    assert "Bonjour, j&#x27;ai vu votre site" in body or "Bonjour" in body
    assert "sends" in body or "send path" in body


# --- restricted material never reaches the page -------------------------------


def test_the_company_page_never_shows_restricted_findings(client) -> None:
    """Collected to inform a judgement, never repeated. A page a person reads
    aloud on a call is exactly where it must not appear."""
    assert "poursuite" not in client.get("/company/1").text


# --- corrections are the same rows the stages respect -------------------------


def test_a_label_set_here_survives_re_classification(client, conn) -> None:
    """The console and the pipeline must share one meaning of "manual"."""
    client.post("/company/1/segments", data={"segment": ["data-driven"]})

    db.save_segments(
        conn, 1, [{"segment": "data-driven", "confidence": 0.1}], "claude-haiku-4-5"
    )
    conn.commit()
    row = conn.execute(
        "SELECT source, confidence FROM intel.segment WHERE segment = 'data-driven'"
    ).fetchone()
    assert row["source"] == "manual" and row["confidence"] == 1.0


def test_unchecking_a_label_removes_it(client, conn) -> None:
    """The false positive case, which is most of why the console exists."""
    client.post("/company/1/segments", data={"segment": []})
    assert conn.execute("SELECT COUNT(*) AS n FROM intel.segment").fetchone()[0] == 0


def test_a_sector_set_here_survives_re_classification(client, conn) -> None:
    client.post("/company/1/sector", data={"sector": "legal-accounting"})

    db.save_sector(conn, 1, "hospitality", confidence=0.9, model="claude-haiku-4-5")
    conn.commit()
    row = conn.execute("SELECT sector, source FROM intel.sector").fetchone()
    assert (row["sector"], row["source"]) == ("legal-accounting", "manual")


def test_a_sector_outside_the_vocabulary_is_refused(client, conn) -> None:
    """Only an edited form can send one, and storing it would put a name in
    the filter that no definition anywhere explains."""
    client.post("/company/1/sector", data={"sector": "restauration-de-luxe"})
    assert conn.execute("SELECT COUNT(*) AS n FROM intel.sector").fetchone()[0] == 0


def test_clearing_the_sector_here_removes_it(client, conn) -> None:
    client.post("/company/1/sector", data={"sector": "legal-accounting"})
    client.post("/company/1/sector", data={"sector": ""})
    assert conn.execute("SELECT COUNT(*) AS n FROM intel.sector").fetchone()[0] == 0


def test_the_survey_narrows_by_sector(client, conn) -> None:
    db.save_sector(conn, 1, "legal-accounting", model="m")
    db.upsert_company(
        conn, {"uid": "CHE4", "name": "Café du Marché Sàrl", "canton": "GE",
               "method": "zefix"}
    )
    company_id = conn.execute(
        "SELECT id FROM core.company WHERE uid = 'CHE4'"
    ).fetchone()["id"]
    db.save_sector(conn, company_id, "hospitality", model="m")
    conn.commit()

    only_food = client.get("/companies", params={"sector": "hospitality"}).text
    assert "Café du Marché" in only_food
    assert "Fiduciaire" not in only_food


def test_a_company_with_no_sector_is_findable_as_such(client, conn) -> None:
    """The backlog the vocabulary created has to be visible, or the only way to
    see it is to subtract two counts and hope."""
    waiting = client.get("/companies", params={"stage": "no-sector"}).text
    assert "Fiduciaire" in waiting

    db.save_sector(conn, 1, "legal-accounting", model="m")
    conn.commit()
    assert "Fiduciaire" not in client.get(
        "/companies", params={"stage": "no-sector"}
    ).text


def test_pipeline_state_and_notes_are_written(client, conn) -> None:
    client.post("/company/1/prospect", data={"state": "meeting", "owner": "alex"})
    client.post("/company/1/notes", data={"body": "Called, spoke to the owner.",
                                          "kind": "call"})
    assert conn.execute("SELECT state FROM intel.prospect").fetchone()[0] == "meeting"
    assert len(db.notes_for(conn, 1)) == 1


def test_an_empty_note_is_not_stored(client, conn) -> None:
    client.post("/company/1/notes", data={"body": "   "})
    assert db.notes_for(conn, 1) == []


def test_suppressing_a_contact_toggles_and_persists(client, conn) -> None:
    """A person said do not write here. Honoured before any draft is made."""
    db.save_contact(conn, company_id=1, kind="email", value="a@f.ch", method="site")
    conn.commit()
    contact_id = conn.execute("SELECT id AS id FROM core.contact").fetchone()[0]

    client.post(f"/contact/{contact_id}/suppress")
    assert conn.execute("SELECT suppressed FROM core.contact").fetchone()[0] == 1
    client.post(f"/contact/{contact_id}/suppress")
    assert conn.execute("SELECT suppressed FROM core.contact").fetchone()[0] == 0


def test_an_address_says_which_page_published_it(client, conn) -> None:
    """Deciding months later whether to keep suppressing an address means
    looking at the page that published it."""
    db.save_contact(
        conn, company_id=1, kind="email", value="a@f.ch", method="published",
        source_url="https://f.ch/mentions-legales",
    )
    conn.commit()
    body = client.get("/company/1").text
    assert "https://f.ch/mentions-legales" in body


def test_suppressing_redraws_the_same_row_the_page_drew(client, conn) -> None:
    """The route swaps the row in place, so the two must not drift apart."""
    db.save_contact(
        conn, company_id=1, kind="email", value="a@f.ch", method="published",
        source_url="https://f.ch/contact",
    )
    conn.commit()
    contact_id = conn.execute("SELECT id AS id FROM core.contact").fetchone()[0]
    swapped = client.post(f"/contact/{contact_id}/suppress").text
    assert "https://f.ch/contact" in swapped
    assert swapped.count("<td>") + swapped.count("<td class") == 4


# --- escaping -----------------------------------------------------------------


def test_a_company_named_like_a_script_tag_is_escaped(client, conn) -> None:
    """`<script>alert(1)</script> Sàrl` is a perfectly legal registration."""
    db.upsert_company(
        conn,
        {"uid": "CHE2", "name": "<script>alert(1)</script> Sàrl", "canton": "GE",
         "status": "ACTIVE", "method": "zefix"},
    )
    conn.commit()
    body = client.get("/").text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_a_note_cannot_inject_markup(client, conn) -> None:
    client.post("/company/1/notes", data={"body": "<img src=x onerror=alert(1)>"})
    assert "<img src=x" not in client.get("/company/1").text


# --- the ordinary pages -------------------------------------------------------


def test_the_list_filters_without_reloading_the_page(client) -> None:
    """The HTMX target returns the table alone, not a whole document."""
    fragment = client.get("/companies", params={"canton": "GE"}).text
    assert "Fiduciaire" in fragment
    assert "<html" not in fragment.lower()


def test_filters_actually_narrow(client, conn) -> None:
    db.upsert_company(
        conn, {"uid": "CHE3", "name": "Vaudoise SA", "canton": "VD", "method": "zefix"}
    )
    conn.commit()
    assert "Vaudoise" not in client.get("/companies", params={"canton": "GE"}).text
    assert "Vaudoise" in client.get("/companies", params={"canton": "VD"}).text


def test_a_company_page_shows_the_register_attribution(client) -> None:
    """A condition of Zefix access, not a courtesy."""
    body = client.get("/company/1").text
    assert "Zefix" in body
    assert "sans garantie" in body


def test_a_struck_off_company_says_so(client, conn) -> None:
    db.upsert_company(
        conn, {"uid": "CHE4", "name": "Mourante Sàrl", "canton": "GE",
               "status": "BEING_CANCELLED", "method": "zefix"},
    )
    conn.commit()
    body = client.get("/company/2").text
    assert "winding up" in body


def test_an_unknown_company_is_a_404_not_a_crash(client) -> None:
    assert client.get("/company/999").status_code == 404


def test_the_pipeline_and_runs_pages_render(client) -> None:
    assert client.get("/pipeline").status_code == 200
    assert client.get("/runs").status_code == 200
    assert "runs" in client.get("/runs/rows").text


def test_the_header_says_which_section_you_are_on(client) -> None:
    for url, label in (("/", "Survey"), ("/board", "Board"), ("/runs", "History"),
                       ("/quotes", "Quotes"), ("/reports", "Reports")):
        header = client.get(url).text.split("<header>")[1].split("</header>")[0]
        current = re.findall(r'<a href="[^"]+" aria-current="page">([^<]+)</a>', header)
        assert current == [label], (url, current)


def test_a_filtered_list_is_given_an_address_that_renders_whole(client) -> None:
    """The filter fetches a fragment; the bar must show the page.

    Pushing the fragment's own URL would put an address in the bar that answers
    with a bare table when anybody opens it.
    """
    reply = client.get("/companies?canton=GE", headers={"HX-Request": "true"})
    assert reply.headers["HX-Push-Url"] == "/?canton=GE"
    whole = client.get(reply.headers["HX-Push-Url"])
    assert whole.status_code == 200
    assert "<header>" in whole.text


def test_the_palette_finds_a_company_by_name(client) -> None:
    hits = client.get("/find?q=Fiduciaire").text
    assert 'href="/company/1"' in hits
    assert "Fiduciaire" in hits


def test_the_palette_says_where_you_can_go_before_anything_is_typed(client) -> None:
    """An empty palette that says nothing teaches nobody what it is for."""
    hits = client.get("/find?q=").text
    assert "Go to" in hits
    assert 'href="/board"' in hits


def test_the_palette_admits_what_it_does_not_search(client) -> None:
    hits = client.get("/find?q=zzzznothing").text
    assert "Nothing matches" in hits
    assert "not" in hits and "notes" in hits


def test_a_company_named_like_a_script_tag_is_escaped_in_the_palette(
    client, conn
) -> None:
    db.upsert_company(
        conn, {"uid": "CHE-EVIL", "name": '<script>alert("x")</script> SA',
               "canton": "GE", "method": "zefix"})
    conn.commit()
    hits = client.get("/find?q=script").text
    assert "<script>alert" not in hits


def test_the_ledgers_render_and_filter(client) -> None:
    """Quotes and invoices had no index at all: they were reachable only from
    the client they belonged to."""
    assert client.get("/quotes").status_code == 200
    assert client.get("/invoices").status_code == 200
    fragment = client.get("/quotes?status=sent", headers={"HX-Request": "true"})
    assert fragment.status_code == 200
    assert "<header>" not in fragment.text
    assert 'id="ledger"' in fragment.text


def test_the_console_script_is_served_by_the_application_not_a_cdn(client) -> None:
    """Thirty lines that belong to this console, from this console.

    The URL carries the script's own digest, so a change produces a different
    URL rather than a cached copy of the previous one that nobody can flush.
    """
    page = client.get("/company/1").text
    url = re.search(r'/static/console\.[0-9a-f]+\.js', page)
    assert url, "the page does not reference the console script"
    reply = client.get(url.group(0))
    assert reply.status_code == 200
    assert "javascript" in reply.headers["content-type"]
    assert "immutable" in reply.headers["cache-control"]
    assert "data-sortable" in reply.text
