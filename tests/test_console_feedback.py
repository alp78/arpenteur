"""What a page says first, what it asks before it acts, and what an empty one offers.

Four promises the console makes to somebody who has never seen it.

The shell draws what a redirect asked it to say, once, in red for a refusal and
green for a success. A page that drew its own banner as well would show the
sentence twice, and a route that sent a refusal in `?note=` would show it green.

An action that cannot be taken back asks first, and the question names the
thing: the quote, the folder, the person's note, never "are you sure".

A page that cannot be left by any link, or that quotes a command line, sends the
reader to a place they cannot go. Links go to pages, and commands are named by
the card that runs them on Operations.

An empty page says what would put something on it.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.config import load_settings
from arpenteur.console import create_app, draft_page, map_page, mail_page, people_page
from arpenteur.console import runner, ui
from arpenteur.kernel.db import Connection
from arpenteur.modules.clients import store as clients_store
from arpenteur.modules.geo import store as geo_store
from arpenteur.modules.sales import store as sales_store
from arpenteur.sources import audience as audience_rules
from arpenteur.stages import audience as audience_stage
from arpenteur.stages import campaign as campaign_stage

pytestmark = pytest.mark.pg

TOML = """
[survey]
cantons = ["GE"]
legal_forms = ["0107"]

[segments]
sme-owner = "A small owner-run business."

[sectors]
hospitality = "Restaurants, cafés and hotels."

[zefix]
base_url = "https://example.test/ZefixPublicREST"
rate_limit = 2.0
max_retries = 0
contact = "tests"

[llm]
provider = "anthropic"
classify_model = "test"
draft_model = "test"
batch_size = 20
"""

TEMPLATE = """---

Betreff: Drymos: Einladung

Guten Tag

Ein Atlas der Schweizer Natur.

---
"""

MARKER = "Ouvrez [[ MANQUE : lien TestFlight ]]"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "arpenteur.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "campaign.md").write_text("# Brief\n\nOriginal.\n", encoding="utf-8")
    folder = tmp_path / "templates" / "drymos"
    folder.mkdir(parents=True)
    (folder / "de.md").write_text(TEMPLATE, encoding="utf-8")
    return tmp_path


@pytest.fixture
def conn(db_path: str) -> Connection:
    return db.open_db(db_path)


@pytest.fixture
def web(root: Path, conn: Connection) -> TestClient:
    return TestClient(create_app(load_settings(root)))


@pytest.fixture
def world(conn: Connection) -> dict[str, int]:
    """One client with everything on it that can be deleted, and two companies."""
    for uid, name in (("CHE100000011", "Fondation Clair-Logis"),
                      ("CHE100000022", "Atelier Bois SA")):
        db.upsert_company(conn, {
            "uid": uid, "name": name, "canton": "GE", "legal_seat": "Genève",
            "legal_form_label": "Fondation", "status": "ACTIVE",
            "purpose": "Tenue de comptabilité.", "hydrated": True, "method": "zefix",
        })
    db.save_fit(conn, 1, {
        "score": 72, "band": "high", "reasoning": "Bon profil.",
        "advice": "Bonjour, j'ai vu votre site.", "against": [], "matched": [],
        "model": "m", "brief_hash": "abc",
    })
    client_id = db.promote_to_client(conn, 1)
    project_id = db.add_project(conn, client_id, "Refonte du socle")
    db.record_event(conn, client_id, kind="meeting", state="planned",
                    occurred_at="2026-10-20T09:00:00", subject="Kickoff")
    note_id, _ = clients_store.save_note(
        conn, client_id, title="Access", body="# Access\n\nBadge at the desk.",
        digest="d1",
    )
    folder_id, _ = clients_store.add_folder(conn, client_id, "Consultation")
    attachment_id, _ = clients_store.add_attachment(conn, client_id, {
        "filename": "accounts.pdf", "bytes": 2048, "sha256": "a" * 64,
        "kind": "accounts", "folder_id": folder_id, "text": "Balance sheet.",
    })
    address = geo_store.save_address(conn, {
        "street": "Chemin du Petit-Flon", "house_number": "49",
        "postal_code": "1018", "city": "Lausanne",
        "query": "Chemin du Petit-Flon 49 1018 Lausanne", "method": "manual",
    })
    site_id = geo_store.save_establishment(
        conn, 1, address_id=address, name="EMS Petit-Flon", method="manual")
    clients_store.save_email_link(conn, 1, kind="domain", value="clairlogis.ch")
    programme_id = clients_store.add_programme(conn, "FLATCHECK", "Flat check")
    member_id, _ = clients_store.add_member(conn, programme_id, 2)

    open_quote = sales_store.create_quote(conn, client_id)
    open_version = sales_store.add_version(conn, open_quote)
    sales_store.add_line(conn, open_version, {
        "label": "Diagnostic", "quantity": 1, "unit_price": 3200})
    sales_store.recompute(conn, open_version, vat_rate=None)
    sent_quote = sales_store.create_quote(conn, client_id)
    sent_version = sales_store.add_version(conn, sent_quote)
    sales_store.add_line(conn, sent_version, {
        "label": "Travel", "quantity": 1, "unit_price": 120})
    sales_store.recompute(conn, sent_version, vat_rate=None)
    sales_store.mark_sent(conn, sent_version, to="director@clairlogis.ch")
    invoice_id = sales_store.create_invoice(conn, client_id, reference="CLAIR-WORK")
    sales_store.add_invoice_line(conn, invoice_id, {"label": "Day", "unit_price": 800})
    sales_store.recompute_invoice(conn, invoice_id, vat_rate=None)

    draft_id = int(conn.execute(
        "INSERT INTO intel.draft (company_id, address, language, subject, body,"
        " model, brief_digest) VALUES (2, 'info@bois.ch', 'fr', 'Limen', %s, 'm', 'd1')"
        " RETURNING id", ("Madame, Monsieur,\n\nUn texte.",)).fetchone()["id"])
    flawed_id = int(conn.execute(
        "INSERT INTO intel.draft (company_id, address, language, subject, body,"
        " model, brief_digest) VALUES (1, 'info@clair.ch', 'fr', 'Limen', %s, 'm', 'd1')"
        " RETURNING id", (MARKER,)).fetchone()["id"])
    party_id = int(conn.execute(
        "INSERT INTO core.party (full_name, sort_name, origin, method)"
        " VALUES ('Anne Muller', 'anne muller', 'Lausanne', 'name-and-origin')"
        " RETURNING id").fetchone()["id"])
    for company_id in (1, 2):
        conn.execute(
            "INSERT INTO core.party_role (party_id, company_id, role)"
            " VALUES (%s, %s, 'administrateur')", (party_id, company_id))
    conn.commit()
    event_id = int(conn.execute(
        "SELECT id FROM crm.interaction WHERE kind = 'meeting'").fetchone()["id"])
    link_id = int(conn.execute("SELECT id FROM crm.email_link").fetchone()["id"])
    return {
        "client": client_id, "project": project_id, "event": event_id,
        "note": note_id, "folder": folder_id, "attachment": attachment_id,
        "site": site_id, "link": link_id, "programme": programme_id,
        "member": member_id, "open_quote": open_quote, "sent_quote": sent_quote,
        "open_version": open_version, "invoice": invoice_id, "draft": draft_id,
        "flawed": flawed_id, "party": party_id,
    }


@pytest.fixture
def campaigns(web: TestClient, conn: Connection, root: Path, world) -> dict[str, int]:
    """A list, a campaign and its first wave, over two firms that can be reached."""
    settings = load_settings(root)
    for company_id, mailbox in ((1, "info@clair.ch"), (2, "info@bois.ch")):
        db.save_segments(conn, company_id,
                         [{"segment": "sme-owner", "confidence": 1.0}], "m")
        conn.execute(
            "INSERT INTO core.contact (company_id, kind, value, method, status,"
            " status_at, source_url, fetched_at) VALUES (%s, 'email', %s, 'published',"
            " 'deliverable', now(), 'https://example.test', now())",
            (company_id, mailbox))
    conn.commit()
    audience_id = audience_stage.create(
        conn, code="list-1", name="Firms",
        rule=audience_rules.Rule(segments={"sme-owner": 1.0}))
    audience_stage.build(settings, conn, audience=audience_id, log=lambda _: None)
    campaign_id = campaign_stage.create(
        settings, conn, code="drymos-a", name="Essai", audience="LIST-1",
        templates="templates/drymos", strategy="score-overall",
        pilot_size=0, wave_size=5, gap_days=0)
    campaign_stage.plan(settings, conn, campaign=campaign_id, log=lambda _: None)
    return {"audience": audience_id, "campaign": campaign_id}


# --- the shell says it once ---------------------------------------------------

# Every page that used to draw `?note=` itself, and the ones that drew both.
FLASH_PAGES = (
    "/board", "/programmes", "/drafts", "/reports", "/settings", "/signature",
    "/clients", "/calendar", "/mail", "/people", "/map", "/companies",
    "/audiences", "/campaigns", "/quotes", "/invoices",
    "/client/{client}", "/client/{client}/notes", "/client/{client}/dossier",
    "/client/{client}/prep", "/project/{project}", "/note/{note}",
    "/programme/{programme}", "/programme/{programme}/ledger",
    "/member/{member}/history", "/quote/{open_quote}", "/invoice/{invoice}",
    "/draft/{draft}", "/company/1",
)


@pytest.mark.parametrize("path", FLASH_PAGES)
def test_a_note_is_drawn_once_and_green_on_every_page(web, world, path) -> None:
    body = web.get(path.format(**world), params={"note": "Zebra saved."}).text
    assert body.count("Zebra saved.") == 1, "drawn by the shell and by the page"
    assert '<div class="banner banner-good" role="status">Zebra saved.</div>' in body
    assert 'class="warn">Zebra' not in body


@pytest.mark.parametrize("path", FLASH_PAGES)
def test_an_error_is_drawn_once_and_red_on_every_page(web, world, path) -> None:
    body = web.get(path.format(**world), params={"error": "Zebra refused."}).text
    assert body.count("Zebra refused.") == 1
    assert '<div class="banner banner-bad" role="status">Zebra refused.</div>' in body


def _where(reply) -> str:
    return unquote(reply.headers["location"])


def test_a_programme_refused_comes_back_red_not_green(web, world) -> None:
    reply = web.post("/programmes/new", data={"code": "", "name": ""},
                     follow_redirects=False)
    assert reply.status_code == 303 and "/programmes?error=" in reply.headers["location"]
    page = web.get(reply.headers["location"]).text
    assert 'class="banner banner-bad"' in page
    assert 'class="banner banner-good"' not in page


def test_a_programme_started_says_so_in_green(web, world) -> None:
    reply = web.post("/programmes/new", data={"code": "pilot", "name": "Pilot"},
                     follow_redirects=False)
    assert "?note=" in reply.headers["location"]
    assert "Programme PILOT is started." in web.get(reply.headers["location"]).text


def test_a_refused_import_is_shown_on_the_clients_page(web, world) -> None:
    reply = web.post("/clients/import", data={"uid": "not a uid"},
                     follow_redirects=False)
    assert reply.headers["location"].startswith("/clients?error=")
    page = web.get(reply.headers["location"]).text
    assert "is not a Swiss UID" in page and "banner-bad" in page


def test_an_empty_client_note_returns_to_the_notes_page_with_an_error(web, world) -> None:
    reply = web.post(f"/client/{world['client']}/notes", data={"body": "  "},
                     follow_redirects=False)
    assert reply.headers["location"].startswith(f"/client/{world['client']}/notes?error=")
    assert "Nothing to save" in web.get(reply.headers["location"]).text


def test_a_saved_client_note_names_it_in_green(web, world) -> None:
    reply = web.post(f"/client/{world['client']}/notes",
                     data={"body": "# Parking\n\nUnder the school."},
                     follow_redirects=False)
    assert "?note=" in reply.headers["location"]
    assert "Note saved: Parking." in _where(reply)


def test_an_empty_note_edit_returns_to_the_note_with_an_error(web, world) -> None:
    reply = web.post(f"/note/{world['note']}", data={"body": ""}, follow_redirects=False)
    assert reply.headers["location"].startswith(f"/note/{world['note']}?error=")


def test_a_folder_move_refused_returns_to_the_dossier_with_an_error(web, conn, world) -> None:
    other = db.promote_to_client(conn, 2)
    foreign, _ = clients_store.add_folder(conn, other, "Not theirs")
    conn.commit()
    reply = web.post(f"/attachment/{world['attachment']}/folder",
                     data={"folder_id": str(foreign)}, follow_redirects=False)
    assert reply.headers["location"].startswith(
        f"/client/{world['client']}/dossier?error=")
    assert "belongs to another client" in web.get(reply.headers["location"]).text


def test_a_folder_without_a_name_is_an_error_and_a_made_one_is_a_note(web, world) -> None:
    refused = web.post(f"/dossier/{world['client']}/folder", data={"name": " "},
                       follow_redirects=False)
    assert "?error=" in refused.headers["location"]
    made = web.post(f"/dossier/{world['client']}/folder", data={"name": "Audit"},
                    follow_redirects=False)
    assert "?note=" in made.headers["location"]


def test_a_budget_that_is_not_an_amount_is_an_error(web, world) -> None:
    reply = web.post(f"/client/{world['client']}/projects",
                     data={"name": "Audit", "budget": "abc"}, follow_redirects=False)
    assert "?error=" in reply.headers["location"]
    saved = web.post(f"/project/{world['project']}", data={"name": "Refonte"},
                     follow_redirects=False)
    assert "?note=" in saved.headers["location"]


def test_an_approval_refused_for_a_marker_is_an_error(web, world) -> None:
    reply = web.post(f"/draft/{world['flawed']}/review", follow_redirects=False)
    assert reply.headers["location"].startswith(f"/draft/{world['flawed']}?error=")
    approved = web.post(f"/draft/{world['draft']}/review", follow_redirects=False)
    assert approved.headers["location"].startswith("/drafts?note=")


def test_a_deletion_says_what_went_in_green(web, world) -> None:
    for path in (f"/note/{world['note']}/delete", f"/event/{world['event']}/delete",
                 f"/attachment/{world['attachment']}/delete",
                 f"/folder/{world['folder']}/delete",
                 f"/establishment/{world['site']}/delete",
                 f"/project/{world['project']}/delete"):
        reply = web.post(path, data={"reason": "wrong client"}, follow_redirects=False)
        assert reply.status_code == 303 and "?note=" in reply.headers["location"], path


def test_the_reports_refresh_and_the_signature_save_say_so(web, world) -> None:
    assert "?note=" in web.post("/reports/refresh", follow_redirects=False).headers["location"]
    saved = web.post("/signature", data={"html": "<p>Nadia</p>"}, follow_redirects=False)
    assert saved.headers["location"] == "/signature?note=Signature%20saved."


def test_a_mail_action_says_what_it_did(web, conn, world) -> None:
    gone = web.post(f"/mail/link/{world['link']}/delete", follow_redirects=False)
    assert "?note=" in gone.headers["location"]


# --- settings is a page of the console ---------------------------------------


def test_the_settings_page_has_the_menu_the_banners_and_the_help_link(web) -> None:
    body = web.get("/settings", params={"note": "Territory saved: GE."}).text
    assert '<nav aria-label="Sections">' in body
    assert '<a class="brand" href="/">' in body
    assert 'class="help" href="/help/' in body
    assert "Territory saved: GE." in body
    assert 'aria-current="page">Settings</a>' in body


def test_the_settings_page_keeps_its_changed_on_notes(web, conn) -> None:
    assert "shipped default" in web.get("/settings").text
    db.save_setting(conn, "cantons", ["VD"])
    conn.commit()
    assert "changed 20" in web.get("/settings").text


def test_every_settings_save_says_what_was_saved(web, world) -> None:
    saved = {
        "/settings/territory": {"cantons": ["VD", "FR"], "legal_forms": ["0107"]},
        "/settings/segments": {"seg_name": ["owner"], "seg_text": ["Owner run."]},
        "/settings/sectors": {"sec_name": ["hotels"], "sec_text": ["Hotels."]},
        "/settings/signal": {"signal_brief": "Old website."},
        "/settings/rates": {"rate_card": "Day rate CHF 1400."},
        "/settings/campaign": {"campaign": "# Brief\n\nNew."},
    }
    for path, data in saved.items():
        reply = web.post(path, data=data, follow_redirects=False)
        assert reply.headers["location"].startswith("/settings?note="), path
    territory = web.post("/settings/territory", data={"cantons": ["VD", "FR"],
                                                      "legal_forms": ["0107"]},
                         follow_redirects=False)
    assert "Territory saved: VD, FR" in _where(territory)


def test_every_settings_refusal_is_an_error(web, world) -> None:
    for path, data in (
        ("/settings/territory", {}),
        ("/settings/segments", {"seg_name": [""], "seg_text": [""]}),
        ("/settings/campaign", {"campaign": "  "}),
    ):
        reply = web.post(path, data=data, follow_redirects=False)
        assert reply.headers["location"].startswith("/settings?error="), path


# --- two pages that had no door ------------------------------------------------


def test_the_signature_is_reachable_from_settings_and_from_the_campaign(
    web, campaigns
) -> None:
    for path in ("/settings", f"/campaign/{campaigns['campaign']}",
                 f"/campaign/{campaigns['campaign']}/wave/1"):
        assert 'href="/signature"' in web.get(path).text, path
    assert web.get("/signature").status_code == 200


def test_each_company_in_the_overlap_panel_links_to_its_route(web, conn, world) -> None:
    body = web.get("/company/1").text
    assert 'href="/company/1/route/2"' in body
    assert web.get("/company/1/route/2").status_code == 200
    rows = [{
        "company_id": 7, "name": "Other SA", "canton": "VD", "people": ["A B"],
        "current": True, "score": 50, "band": "medium", "source": "match",
        "state": "new",
    }]
    panel = people_page.overlap_panel(rows, 3)
    assert 'href="/company/3/route/7"' in panel


# --- the question before an action ---------------------------------------------

CONFIRMED = (
    # (page, the form's action, a fragment of the question it asks)
    ("/quote/{open_quote}", "/quote-version/{open_version}/sent", "Mark version 1"),
    ("/quote/{open_quote}", "/quote-line/", "Remove the line Diagnostic from this quote"),
    ("/quote/{sent_quote}", "/quote/{sent_quote}/status", "as declined"),
    ("/quote/{sent_quote}", "/quote/{sent_quote}/status", "as withdrawn"),
    ("/invoice/{invoice}", "/invoice-line/", "Remove the line Day from this invoice"),
    ("/draft/{draft}", "/draft/{draft}/refuse", "Refuse the draft to Atelier Bois SA"),
    ("/client/{client}", "/establishment/{site}/delete", "Remove the site EMS Petit-Flon"),
    ("/client/{client}", "/event/{event}/delete", "Delete the entry Kickoff"),
    ("/project/{project}", "/project/{project}/delete", "Delete the project Refonte du socle"),
    ("/client/{client}/notes", "/note/{note}/delete", "Delete the note Access"),
    ("/client/{client}/dossier", "/folder/{folder}/delete", "Delete the folder Consultation"),
    ("/client/{client}/dossier", "/attachment/{attachment}/delete",
     "Delete the document accounts.pdf"),
    ("/company/1", "/mail/link/{link}/delete", "Forget clairlogis.ch"),
)


def _form_tag(body: str, action: str) -> str:
    start = body.index(f'action="{action}') if f'action="{action}' in body else -1
    assert start >= 0, f"no form posting to {action}"
    return body[body.rindex("<form", 0, start):body.index(">", start)]


@pytest.mark.parametrize("page_path,action,question", CONFIRMED)
def test_an_irreversible_action_asks_a_question_that_names_the_thing(
    web, world, page_path, action, question
) -> None:
    body = web.get(page_path.format(**world)).text
    tags = [m for m in re.findall(r"<form[^>]*>", body)
            if f'action="{action.format(**world)}' in m]
    assert tags, f"no form posting to {action}"
    asked = " ".join(unquote(t) for t in tags)
    assert "data-confirm=" in asked and question in asked.replace("&#x27;", "'")


def test_what_can_be_taken_back_asks_nothing(web, conn, world) -> None:
    conn.execute(
        "INSERT INTO crm.email_message (message_id, folder, from_email, to_emails,"
        " subject, state, direction, fetched_at) VALUES ('<m1>', 'INBOX', 'a@b.ch',"
        " '{}', 'Hello', 'pending', 'in', now())")
    conn.commit()
    body = web.get("/mail").text
    assert 'action="/mail/' in body and "/ignore" in body
    for tag in re.findall(r"<form[^>]*>", body):
        assert "data-confirm" not in tag, tag
    sent = web.get(f"/quote/{world['sent_quote']}").text
    accepted = _form_tag(sent, f"/quote/{world['sent_quote']}/status")
    assert "data-confirm" not in accepted, "accepting a quote is the way forward"


# --- crumbs ---------------------------------------------------------------------


def _crumbs(body: str) -> list[tuple[str, str]]:
    nav = re.search(r'<nav class="crumbs".*?</nav>', body, re.S)
    assert nav, "no breadcrumb on the page"
    return re.findall(r'<a href="([^"]+)">([^<]+)</a>', nav.group(0))


def test_the_campaign_pages_link_back_to_lists_and_campaigns(web, campaigns) -> None:
    audience = _crumbs(web.get(f"/audience/{campaigns['audience']}").text)
    assert audience == [("/audiences", "Lists")]
    campaign = _crumbs(web.get(f"/campaign/{campaigns['campaign']}").text)
    assert campaign == [("/campaigns", "Campaigns")]
    wave = _crumbs(web.get(f"/campaign/{campaigns['campaign']}/wave/1").text)
    assert wave == [("/campaigns", "Campaigns"),
                    (f"/campaign/{campaigns['campaign']}", "DRYMOS-A")]
    for href, _label in audience + campaign + wave:
        assert web.get(href).status_code == 200


def test_quote_and_invoice_crumbs_go_through_sales(web, world) -> None:
    quote = _crumbs(web.get(f"/quote/{world['open_quote']}").text)
    assert quote[0] == ("/quotes", "Quotes") and ("/clients", "Clients") not in quote
    invoice = _crumbs(web.get(f"/invoice/{world['invoice']}").text)
    assert invoice[0] == ("/invoices", "Invoices") and ("/clients", "Clients") not in invoice


def test_the_company_page_leads_back_to_the_list(web, world) -> None:
    body = web.get("/company/1").text
    assert _crumbs(body)[0] == ("/companies", "Companies")
    header = body.split("<header>")[1].split("</header>")[0]
    assert 'href="/companies" aria-current="page"' in header


# --- controls with a name -----------------------------------------------------


class _Controls(HTMLParser):
    """Collects the controls that nothing names."""

    NAMED_BY_THEMSELVES = {"hidden", "submit", "button", "reset", "image"}

    def __init__(self) -> None:
        super().__init__()
        self.labels: set[str] = set()
        self.controls: list[dict[str, str]] = []
        self.inside_label = 0
        self.labelled_by_wrapping: list[bool] = []

    def handle_starttag(self, tag, attrs) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "label":
            self.inside_label += 1
            if a.get("for"):
                self.labels.add(a["for"])
        if tag in {"input", "select", "textarea"}:
            if tag == "input" and a.get("type", "text") in self.NAMED_BY_THEMSELVES:
                return
            self.controls.append({**a, "tag": tag, "wrapped": str(bool(self.inside_label))})

    def handle_endtag(self, tag) -> None:
        if tag == "label" and self.inside_label:
            self.inside_label -= 1

    def unnamed(self) -> list[str]:
        out = []
        for control in self.controls:
            if control.get("aria-label") or control["wrapped"] == "True":
                continue
            if control.get("id") and control["id"] in self.labels:
                continue
            out.append(f'{control["tag"]} name={control.get("name", "?")}')
        return out


def unnamed_controls(html: str) -> list[str]:
    body = html.split('<main id="main">', 1)[-1]
    parser = _Controls()
    parser.feed(body)
    return parser.unnamed()


LABELLED_PAGES = (
    "/company/1", "/drafts", "/draft/{draft}", "/quote/{open_quote}",
    "/invoice/{invoice}", "/invoice/{invoice}/print", "/programme/{programme}",
    "/programme/{programme}/ledger", "/audiences", "/campaigns",
    "/audience/{audience}", "/campaign/{campaign}", "/campaign/{campaign}/wave/1",
    "/client/{client}", "/client/{client}/dossier", "/client/{client}/notes",
    "/note/{note}", "/settings", "/signature", "/board", "/mail", "/people",
    "/map", "/clients", "/calendar",
)


@pytest.mark.parametrize("path", LABELLED_PAGES)
def test_every_control_on_the_page_has_a_label(web, world, campaigns, conn, path) -> None:
    conn.execute(
        "UPDATE core.company SET hydrated_at = now() WHERE id = 1")
    db.set_prospect(conn, 2, state="contacted")
    conn.commit()
    body = web.get(path.format(**world, **campaigns)).text
    assert unnamed_controls(body) == []


def test_a_member_who_answered_gets_a_named_enrolment_form(web, conn, world) -> None:
    conn.execute(
        "INSERT INTO crm.email_message (message_id, folder, from_email, to_emails,"
        " subject, state, direction, company_id, fetched_at)"
        " VALUES ('<m2>', 'INBOX', 'info@bois.ch', '{}', 'Re', 'filed', 'in', 2, now())")
    conn.commit()
    body = web.get(f"/programme/{world['programme']}/ledger").text
    assert "enrol-form" in body and unnamed_controls(body) == []


def test_a_link_styled_as_a_button_holds_no_button(web, world) -> None:
    body = web.get("/company/1").text
    assert "<a class='btn primary' href='/client/" in body
    assert not re.search(r"<a [^>]*>\s*<button", body)


def test_required_fields_are_marked_to_the_eye_and_to_the_form(web, world) -> None:
    clients = web.get("/clients").text
    assert "required" in clients and 'class="req"' in clients
    assert re.search(r"Swiss UID<span class=\"req\"", clients)
    programmes = web.get("/programmes").text
    assert re.search(r'name="code"[^>]*required', programmes)
    assert re.search(r'Code<span class="req"', programmes)
    campaigns = web.get("/campaigns").text
    assert "required" in campaigns


def test_ui_text_field_can_demand_a_value() -> None:
    out = ui.text_field("code", "Code", required=True)
    assert "required" in out and ui.REQUIRED_MARK in out
    assert "required" not in ui.text_field("code", "Code")
    assert 'step="0.05"' in ui.text_field("budget", "Budget", kind="number", step="0.05")


def test_a_budget_steps_by_centimes_not_hundreds(web, world) -> None:
    for path in (f"/client/{world['client']}", f"/project/{world['project']}"):
        body = web.get(path).text
        assert "step='100'" not in body and 'step="100"' not in body, path
        assert "step='0.05'" in body or 'step="0.05"' in body, path


# --- a redirect target stays on this host -----------------------------------------


@pytest.mark.parametrize("target,expected", [
    ("/campaigns", "/campaigns"),
    ("/campaign/3/wave/1", "/campaign/3/wave/1"),
    ("//evil.example", "/campaigns"),
    ("//evil.example/campaigns", "/campaigns"),
    ("/\\evil.example", "/campaigns"),
    ("https://evil.example", "/campaigns"),
    ("campaigns", "/campaigns"),
    ("", "/campaigns"),
])
def test_a_redirect_target_must_be_a_path_on_this_console(target, expected) -> None:
    assert ui.local_path(target, "/campaigns") == expected


def test_the_settle_route_refuses_to_leave(web, campaigns) -> None:
    reply = web.post("/outbound/1/settle", data={"posted": "no", "back_to": "//evil.example"},
                     follow_redirects=False)
    assert reply.headers["location"] == "/campaigns"


# --- empty pages say what would fill them -------------------------------------------


def test_companies_tells_an_empty_register_from_an_empty_filter(web, conn) -> None:
    body = web.get("/companies").text
    assert "No company is held yet." in body
    assert 'href="/pipeline"' in body.split("No company is held yet.")[1][:400]
    assert "Nothing matches" not in body
    db.upsert_company(conn, {"uid": "CHE1", "name": "Alpha", "canton": "GE",
                             "status": "ACTIVE", "method": "zefix"})
    conn.commit()
    filtered = web.get("/companies", params={"q": "zzz"}).text
    assert "Nothing matches these filters." in filtered
    assert "No company is held yet." not in filtered
    fragment = web.get("/companies/rows", params={"canton": "VS"}).text
    assert "Nothing matches these filters." in fragment


def test_every_empty_page_names_its_next_step(web, conn) -> None:
    expectations = {
        "/reports": ("Nothing to report yet.", "/pipeline"),
        "/audiences": ("No list yet.", "Start one below"),
        "/campaigns": ("No campaign yet.", "make a list"),
        "/calendar": ("Nothing is scheduled in this month.", "/clients"),
        "/clients": ("No clients yet.", "/companies"),
        "/mail": ("Nothing is waiting.", "/pipeline"),
        "/people": ("No one is resolved yet.", "/pipeline"),
        "/board": ("Nothing is on the board yet.", "#bring-in"),
        "/programmes": ("No programmes yet.", "#start"),
        "/drafts": ("No drafts are waiting for review.", "/pipeline"),
    }
    for path, (sentence, step) in expectations.items():
        body = web.get(path).text
        assert sentence in body, path
        assert step in body, path


def test_the_campaign_form_says_a_list_is_made_first_and_shows_no_path(web, conn) -> None:
    body = web.get("/campaigns").text
    assert "<select name='audience' id='f-audience' disabled>" in body
    assert '<a href=\'/audiences\'>make a list</a> first' in body
    form = body.split("Start a campaign")[1]
    assert re.search(r"<button[^>]*disabled", form)
    assert "/home/" not in body and "C:\\" not in body


def test_an_empty_drafts_filter_does_not_blame_the_stage(web, conn) -> None:
    body = web.get("/drafts", params={"language": "de"}).text
    assert "No draft in that language is waiting for review." in body


# --- copy ---------------------------------------------------------------------------


def test_drafts_say_a_draft_is_never_sent_by_itself(web, world) -> None:
    body = web.get("/drafts").text
    assert "A draft is never sent by itself" in body
    assert "sending happens from a wave of a" in body
    assert 'href="/campaigns"' in body
    assert "nothing in this program sends" not in body


COMMANDS = re.compile(
    r"arpenteur (draft|mail|send|match|resolve|enrich|verify|bounces|programme|note|"
    r"profile|reach|geocode|boundaries|source|sync|hydrate|people|doctor)\b"
)


@pytest.mark.parametrize("path", LABELLED_PAGES + (
    "/reports", "/drafts?language=de", "/member/{member}/history", "/company/2",
))
def test_no_page_quotes_a_command_line(web, world, campaigns, path) -> None:
    body = web.get(path.format(**world, **campaigns)).text
    assert COMMANDS.search(body.split('<main id="main">', 1)[-1]) is None, path


def test_a_command_is_named_by_the_card_that_runs_it() -> None:
    link = ui.operations("mail")
    assert runner.STAGES["mail"].title in link and 'href="/pipeline"' in link
    assert ui.stage_title("geocode") == runner.STAGES["geocode"].title
    assert ui.operations("no-such-stage") == '<a href="/pipeline">Operations</a>'
    mail = mail_page.mail_page([], [], {}, state="pending")
    assert runner.STAGES["mail"].title in mail
    placed = map_page.map_page(count=0, placed=0, with_address=5, cantons=[],
                               segments=[], states=[])
    assert runner.STAGES["geocode"].title in placed
    assert runner.STAGES["boundaries"].title in placed


def test_counts_are_grouped_the_swiss_way() -> None:
    page = draft_page.queue_page([], {"awaiting": 12345}, stale=0)
    assert "12'345" in page and "12,345" not in page
    assert ui.num(1234567) == "1'234'567"


def test_the_signature_page_is_in_english_and_names_no_path(web, root) -> None:
    body = web.get("/signature").text
    for french in ("gras", "italique", "souligné", "ajouter un lien", "Adresse du lien"):
        assert french not in body
    assert "Bold" in body and "Add a link" in body and "Link address" in body
    assert str(root) not in body and "signature.html" not in body
    assert "arpenteur send" not in body


def test_mail_does_not_send_the_reader_to_the_board(web, conn) -> None:
    conn.execute(
        "INSERT INTO crm.email_message (message_id, folder, from_email, to_emails,"
        " subject, state, direction, fetched_at) VALUES ('<m3>', 'INBOX', 'a@b.ch',"
        " '{}', 'Hello', 'pending', 'in', now())")
    conn.commit()
    body = web.get("/mail").text
    assert "Promote a company" not in body
    assert "becomes a client from its own page" in body


def test_the_names_of_things_agree(web, world, campaigns) -> None:
    settings = web.get("/settings").text
    assert "Scoring brief" in settings and "Campaign brief" not in settings
    lists = web.get("/audiences").text
    assert "Segments and weights" in lists and "Bands and weights" not in lists
    assert "Segments " in web.get(f"/audience/{campaigns['audience']}").text
    programme = web.get(f"/programme/{world['programme']}").text
    assert "ledger of every firm" in programme and "status of every firm" not in programme
    ledger = web.get(f"/programme/{world['programme']}/ledger").text
    assert "FLATCHECK ledger" in ledger and "&larr; programme" in ledger
    company = web.get("/company/1").text
    assert "draft opening" in company and "suggested opening" not in company
    assert "Scored against the scoring brief" in web.get("/companies").text
    assert "Survey" not in web.get("/companies").text.split("<header>")[1].split("</header>")[0]


def test_the_company_page_does_not_claim_nothing_sends(web, world) -> None:
    body = web.get("/company/1").text
    assert "Nothing on this page sends it" in body
    assert "No route here sends" not in body
