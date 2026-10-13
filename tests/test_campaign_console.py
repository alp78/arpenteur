"""The list and campaign pages, over the test database and a real HTTP client."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.config import MailConfig, SmtpConfig
from arpenteur.console.app import create_app
from arpenteur.kernel.db import Connection
from arpenteur.modules.clients import campaigns as K
from arpenteur.sources import audience as A
from arpenteur.stages import audience as audience_stage
from arpenteur.stages import campaign as C

pytestmark = pytest.mark.pg

TEMPLATE = """---

Betreff: Drymos: Einladung

Guten Tag

Ein Atlas der Schweizer Natur.

---
"""

FIRMS = (
    ("CHE400000001", "Zermatt Tourismus", "VS", "info@zermatt.ch"),
    ("CHE400000002", "Saastal Tourismus AG", "VS", "info@saas-fee.ch"),
    ("CHE400000003", "Adelboden Tourismus", "BE", "info@adelboden.ch"),
)


@pytest.fixture
def settings(build_settings, tmp_path: Path):
    folder = tmp_path / "templates" / "drymos"
    folder.mkdir(parents=True)
    (folder / "de.md").write_text(TEMPLATE, encoding="utf-8")
    (tmp_path / "signature.html").write_text("<div>Nadia Voirol</div>", encoding="utf-8")
    return build_settings(
        tmp_path,
        mail=MailConfig(host="imap.test", user="me@arpenteur-demo.ch", password="x"),
        smtp=SmtpConfig(host="smtp.test", user="me@arpenteur-demo.ch", password="x",
                        sender="me@arpenteur-demo.ch", per_run=25, pause_seconds=0,
                        signature_image=""),
    )


@pytest.fixture
def conn(db_path: str) -> Connection:
    connection = db.open_db(db_path)
    for uid, name, canton, mailbox in FIRMS:
        cid = connection.execute(
            "INSERT INTO core.company (uid, name, canton, legal_form_uid, legal_form_label,"
            " status, first_seen) VALUES (%s, %s, %s, '0109', 'Association', 'ACTIVE',"
            " now()) RETURNING id", (uid, name, canton)).fetchone()["id"]
        connection.execute(
            "INSERT INTO intel.segment (company_id, segment, confidence, source,"
            " classified_at) VALUES (%s, 'tourism-office', 1, 'manual', now())", (cid,))
        connection.execute(
            "INSERT INTO core.contact (company_id, kind, value, method, status, status_at,"
            " source_url, fetched_at) VALUES (%s, 'email', %s, 'published', 'deliverable',"
            " now(), 'https://example.test', now())", (cid, mailbox))
    connection.commit()
    return connection


@pytest.fixture
def ids(settings, conn) -> dict[str, int]:
    rule = A.Rule(segments={"tourism-office": 1.0})
    audience_id = audience_stage.create(conn, code="list-1", name="Tourisme", rule=rule)
    audience_stage.build(settings, conn, audience=audience_id, log=lambda _: None)
    campaign_id = C.create(settings, conn, code="drymos-a", name="Essai", audience="LIST-1",
                           templates="templates/drymos", strategy="score-overall",
                           pilot_size=0, wave_size=5, gap_days=0)
    C.plan(settings, conn, campaign=campaign_id, log=lambda _: None)
    return {"audience": audience_id, "campaign": campaign_id}


@pytest.fixture
def web(settings, conn) -> TestClient:
    return TestClient(create_app(settings))


def test_the_list_page_groups_by_canton_then_score(web, ids) -> None:
    body = web.get(f"/audience/{ids['audience']}").text
    assert body.index(">BE<") < body.index(">VS<")
    assert "Zermatt Tourismus" in body and "kept" in body


def test_a_pin_from_the_page_survives_a_rebuild(web, conn, ids, settings) -> None:
    member = next(r for r in K.members(conn, ids["audience"])
                  if r["company_name"] == "Adelboden Tourismus")
    reply = web.post(f"/audience-row/{member['id']}/pin",
                     data={"verdict": "dropped", "why": "already a client"},
                     follow_redirects=False)
    assert reply.status_code == 303
    web.post(f"/audience/{ids['audience']}/build", follow_redirects=False)
    row = next(r for r in K.members(conn, ids["audience"])
               if r["company_name"] == "Adelboden Tourismus")
    assert (row["verdict"], row["reason_code"]) == ("dropped", "operator")


def test_a_pin_without_a_reason_is_refused(web, conn, ids) -> None:
    member = K.members(conn, ids["audience"])[0]
    reply = web.post(f"/audience-row/{member['id']}/pin", data={"verdict": "dropped"},
                     follow_redirects=False)
    assert "error=" in reply.headers["location"]


def test_the_wave_page_withholds_send_until_tested_and_approved(web, ids) -> None:
    body = web.get(f"/campaign/{ids['campaign']}/wave/1").text
    assert "Send the wave" in body
    send_button = body[body.index("Send the wave") - 200:body.index("Send the wave")]
    assert "disabled" in send_button


def test_the_send_form_names_the_count_and_asks_for_the_code(web, ids) -> None:
    body = web.get(f"/campaign/{ids['campaign']}/wave/1").text
    assert "Type DRYMOS-A/1 to send" in body
    assert "3 message(s)" in body


def test_the_preview_is_the_html_a_recipient_gets(web, conn, ids, settings) -> None:
    C.render_wave(settings, conn, campaign=ids["campaign"], wave=1, log=lambda _: None)
    body = web.get(f"/campaign/{ids['campaign']}/wave/1").text
    assert "<iframe class='cm-preview' sandbox srcdoc=" in body
    assert "Ein Atlas der Schweizer Natur" in body
    assert "Wenn Sie keine weiteren Nachrichten" in body, "the opt-out is in the preview"


def test_a_send_posted_without_approval_never_starts_a_job(web, ids) -> None:
    reply = web.post(f"/campaign/{ids['campaign']}/wave/1/send",
                     data={"confirm": "DRYMOS-A/1"}, follow_redirects=False)
    assert "error=" in reply.headers["location"]
    assert "/runs/" not in reply.headers["location"]


def test_the_generic_run_route_cannot_start_a_send(web, ids) -> None:
    reply = web.post("/run/campaign-send", data={"campaign": str(ids["campaign"]),
                                                 "wave": "1", "confirm": "DRYMOS-A/1"},
                     follow_redirects=False)
    assert "/runs/" not in reply.headers["location"]


def test_looking_at_a_wave_never_pauses_the_campaign(web, conn, ids) -> None:
    web.get(f"/campaign/{ids['campaign']}/wave/1")
    assert K.campaign(conn, ids["campaign"])["paused_at"] is None


def test_the_lists_and_campaigns_pages_answer(web, ids) -> None:
    assert web.get("/audiences").status_code == 200
    assert web.get("/campaigns").status_code == 200
    assert web.get(f"/campaign/{ids['campaign']}").status_code == 200
    export = web.get(f"/audience/{ids['audience']}/export.csv")
    assert export.status_code == 200
    lines = export.text.strip().splitlines()
    assert lines[0].startswith("uid,name,canton,verdict")
    assert len(lines) == 1 + len(FIRMS)


def test_a_campaigns_drafts_stay_off_the_review_queue(web, conn, ids, settings) -> None:
    C.render_wave(settings, conn, campaign=ids["campaign"], wave=1, log=lambda _: None)
    body = web.get("/drafts").text
    assert "Zermatt Tourismus" not in body
