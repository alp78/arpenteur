"""A campaign's waves, end to end, with no message leaving the machine.

Render, test, approve, send, and the gate between waves, each driven through
the stage with a stand-in SMTP session. The promises are the ones a wave makes
to the operator: what was tested is what goes, nothing goes unapproved or
untyped, and a bad pilot stops the rest.
"""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

import pytest

from arpenteur import db
from arpenteur.config import MailConfig, SmtpConfig
from arpenteur.kernel.db import Connection
from arpenteur.modules.clients import campaigns as K
from arpenteur.modules.clients import store as clients_store
from arpenteur.sources import audience as A
from arpenteur.sources import smtp
from arpenteur.stages import audience as audience_stage
from arpenteur.stages import campaign as C

pytestmark = pytest.mark.pg

DE = """notes

---

Betreff: Drymos: Einladung zum Test

Guten Tag

Ein Atlas der Schweizer Natur.

Freundliche Grüsse

---
"""
FR = DE.replace("Betreff", "Objet").replace("Guten Tag", "Madame, Monsieur,")

FIRMS = (
    ("CHE200000001", "Zermatt Tourismus", "VS", "info@zermatt.ch"),
    ("CHE200000002", "Saastal Tourismus AG", "VS", "info@saas-fee.ch"),
    ("CHE200000003", "Champéry Tourisme", "VS", "info@champery.ch"),
    ("CHE200000004", "Lötschental Tourismus", "VS", "info@loetschental.ch"),
)


class FakeSender:
    def __init__(self) -> None:
        self.posted: list[EmailMessage] = []

    def __call__(self) -> "FakeSender":
        return self

    def __enter__(self) -> "FakeSender":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def alive(self) -> bool:
        return True

    def reopen(self) -> None:
        return None

    def post(self, message: EmailMessage) -> smtp.Posted:
        self.posted.append(message)
        return smtp.Posted(message_id=str(message["Message-ID"]),
                           to=str(message["To"]), subject=str(message["Subject"]),
                           at=__import__("datetime").datetime.now())


@pytest.fixture
def settings(build_settings, tmp_path: Path):
    folder = tmp_path / "templates" / "drymos"
    folder.mkdir(parents=True)
    (folder / "de.md").write_text(DE, encoding="utf-8")
    (folder / "fr.md").write_text(FR, encoding="utf-8")
    (tmp_path / "signature.html").write_text("<div>Nadia Voirol</div>", encoding="utf-8")
    return build_settings(
        tmp_path,
        mail=MailConfig(host="imap.test", user="me@arpenteur-demo.ch", password="x"),
        smtp=SmtpConfig(host="smtp.test", user="me@arpenteur-demo.ch", password="x",
                        sender="me@arpenteur-demo.ch", sender_name="Me", per_run=25,
                        pause_seconds=0, signature_image=""),
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
    clients_store.add_programme(connection, "drymos", "Drymos")
    connection.commit()
    return connection


@pytest.fixture
def campaign(settings, conn) -> str:
    rule = A.Rule(segments={"tourism-office": 1.0}, programme="DRYMOS")
    audience_stage.create(conn, code="list-1", name="Tourisme", rule=rule)
    audience_stage.build(settings, conn, audience="LIST-1", log=lambda _: None)
    C.create(settings, conn, code="drymos-a", name="Essai", audience="LIST-1",
             templates="templates/drymos", strategy="score-overall", programme="DRYMOS",
             pilot_size=1, wave_size=3, gap_days=0, settle_hours=0)
    C.plan(settings, conn, campaign="DRYMOS-A", log=lambda _: None)
    return "DRYMOS-A"


def ready(settings, conn, campaign: str, wave: int = 1) -> FakeSender:
    """Render, test and approve one wave; return the session that took the tests."""
    C.render_wave(settings, conn, campaign=campaign, wave=wave, log=lambda _: None)
    tests = FakeSender()
    C.test_wave(settings, conn, campaign=campaign, wave=wave, log=lambda _: None,
                open_sender=tests, sleep=lambda _s: None)
    C.approve(settings, conn, campaign=campaign, wave=wave, by="test", read=True)
    return tests


# --- render ---------------------------------------------------------------------

def test_an_edit_to_a_template_the_wave_did_not_use_changes_nothing(
    settings, conn, campaign
) -> None:
    """The fingerprint covers what the wave rendered from, and only that."""
    ready(settings, conn, campaign)
    languages = {r["language"] for r in conn.execute(
        "SELECT d.language FROM intel.draft d JOIN crm.campaign_message cm"
        " ON cm.draft_id = d.id JOIN crm.campaign_wave w ON w.id = cm.wave_id"
        " WHERE w.number = 1")}
    unused = ({"de", "fr"} - languages).pop()
    (Path(settings.root) / "templates" / "drymos" / f"{unused}.md").write_text(
        (DE if unused == "de" else FR) + "\n", encoding="utf-8")
    stats = C.send_wave(settings, conn, campaign=campaign, wave=1, confirm="DRYMOS-A/1",
                        open_sender=FakeSender(), sleep=lambda _s: None, log=lambda _: None)
    assert stats["sent"] == 1


def test_rendering_refuses_a_missing_template_naming_the_file(settings, conn, campaign) -> None:
    (Path(settings.root) / "templates" / "drymos" / "de.md").unlink()
    with pytest.raises(ValueError, match="templates/drymos/de.md"):
        for wave in (1, 2):
            C.render_wave(settings, conn, campaign=campaign, wave=wave, log=lambda _: None)


def test_rendering_writes_the_template_text_and_appends_nothing(settings, conn, campaign) -> None:
    """The message is the template's text: an opt-out sentence, when the
    campaign needs one, is written in the template, where the operator sees it."""
    C.render_wave(settings, conn, campaign=campaign, wave=2, log=lambda _: None)
    rows = conn.execute("SELECT model, language, body FROM intel.draft").fetchall()
    assert rows and all(r["model"].startswith("template:templates/drymos/") for r in rows)
    german = [r for r in rows if r["language"] == "de"]
    assert german and all(r["body"].rstrip().endswith("Freundliche Grüsse") for r in german)
    assert not [r for r in rows if "STOP" in r["body"]]


def test_a_bilingual_canton_gets_both_versions_its_own_language_first(
    settings, conn, campaign
) -> None:
    """In Valais an agency may work in French or in German. The message carries
    both, so a wrong guess costs the reader a scroll rather than the message."""
    planned = C.plan(settings, conn, campaign=campaign, bilingual=["vs"], log=lambda _: None)
    for wave in range(1, planned["waves"] + 1):
        C.render_wave(settings, conn, campaign=campaign, wave=wave, log=lambda _: None)
    rows = conn.execute("SELECT language, subject, body FROM intel.draft").fetchall()
    assert len(rows) == len(FIRMS)
    for r in rows:
        own, other, label = (("Guten Tag", "Madame, Monsieur,", "Version française")
                             if r["language"] == "de" else
                             ("Madame, Monsieur,", "Guten Tag", "Deutsche Version"))
        assert r["body"].index(own) < r["body"].index(label) < r["body"].index(other)
        assert r["subject"].count(" / ") == 1
    variants = {r["variant"] for r in conn.execute(
        "SELECT variant FROM crm.campaign_message WHERE draft_id IS NOT NULL")}
    assert variants == {"fr+de", "de+fr"}


def test_a_bilingual_canton_is_one_wave_whatever_each_firm_speaks(
    settings, conn, campaign
) -> None:
    split = C.plan(settings, conn, campaign=campaign, strategy="canton-per-wave",
                   pilot_size=0, wave_size=10, log=lambda _: None)
    assert len(split["sizes"]) == 2
    joined = C.plan(settings, conn, campaign=campaign, bilingual=["VS"], log=lambda _: None)
    assert joined["sizes"] == [len(FIRMS)]


def test_a_canton_with_one_official_language_cannot_be_bilingual(
    settings, conn, campaign
) -> None:
    with pytest.raises(ValueError, match="GE"):
        C.plan(settings, conn, campaign=campaign, bilingual=["GE"], log=lambda _: None)


def test_a_bilingual_message_needs_the_other_language_too(settings, conn, campaign) -> None:
    conn.execute("UPDATE crm.audience_member SET language = 'fr'")
    conn.commit()
    (Path(settings.root) / "templates" / "drymos" / "de.md").unlink()
    C.render_wave(settings, conn, campaign=campaign, wave=1, log=lambda _: None)
    C.plan(settings, conn, campaign=campaign, bilingual=["VS"], log=lambda _: None)
    with pytest.raises(ValueError, match="templates/drymos/de.md"):
        C.render_wave(settings, conn, campaign=campaign, wave=1, log=lambda _: None)


def test_an_edit_to_the_second_version_blocks_the_send(settings, conn, campaign) -> None:
    C.plan(settings, conn, campaign=campaign, bilingual=["VS"], log=lambda _: None)
    ready(settings, conn, campaign)
    folder = Path(settings.root) / "templates" / "drymos"
    for name, text in (("de.md", DE), ("fr.md", FR)):
        (folder / name).write_text(text.replace("Ein Atlas", "Ein neuer Atlas"),
                                   encoding="utf-8")
        with pytest.raises(ValueError, match="changed after it was rendered"):
            C.send_wave(settings, conn, campaign=campaign, wave=1, confirm="DRYMOS-A/1",
                        open_sender=FakeSender(), sleep=lambda _s: None,
                        log=lambda _: None)
        (folder / name).write_text(text, encoding="utf-8")


def test_rendering_refuses_without_a_signature(settings, conn, campaign) -> None:
    (Path(settings.root) / "signature.html").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="signature"):
        C.render_wave(settings, conn, campaign=campaign, wave=1, log=lambda _: None)


# --- test and approve -------------------------------------------------------------

def test_a_test_reaches_only_the_operator_once_per_variant(settings, conn, campaign) -> None:
    tests = ready(settings, conn, campaign, wave=2)
    assert tests.posted and {str(m["To"]) for m in tests.posted} == {"me@arpenteur-demo.ch"}
    assert all(str(m["Subject"]).startswith("[TEST] ") for m in tests.posted)
    variants = {r["variant"] for r in conn.execute(
        "SELECT variant FROM crm.campaign_message WHERE draft_id IS NOT NULL")}
    assert len(tests.posted) <= len(variants) + 1
    assert conn.execute("SELECT count(*) AS n FROM intel.draft WHERE sent_at IS NOT NULL"
                        ).fetchone()["n"] == 0


def test_every_message_equals_its_variants_test_but_for_the_recipient(
    settings, conn, campaign
) -> None:
    tests = ready(settings, conn, campaign, wave=2)
    tested = {str(m["Subject"])[len(C.TEST_PREFIX):]: m.get_body(("plain",)).get_content()
              for m in tests.posted}
    for row in conn.execute("SELECT subject, body FROM intel.draft").fetchall():
        assert row["body"].rstrip() in tested[row["subject"]]


def test_approval_needs_a_test_after_the_last_render(settings, conn, campaign) -> None:
    C.render_wave(settings, conn, campaign=campaign, wave=1, log=lambda _: None)
    with pytest.raises(ValueError, match="tested"):
        C.approve(settings, conn, campaign=campaign, wave=1, by="t", read=True)


def test_approval_needs_the_operator_to_say_they_read_the_tests(settings, conn, campaign) -> None:
    C.render_wave(settings, conn, campaign=campaign, wave=1, log=lambda _: None)
    with pytest.raises(ValueError, match="reading every test"):
        C.approve(settings, conn, campaign=campaign, wave=1, by="t", read=False)


def test_a_rerender_clears_the_test_and_the_approval(settings, conn, campaign) -> None:
    ready(settings, conn, campaign)
    C.render_wave(settings, conn, campaign=campaign, wave=1, log=lambda _: None)
    w = K.wave(conn, K.campaign_by_code(conn, campaign)["id"], 1)
    assert w["tested_at"] is None and w["approved_at"] is None


# --- send -------------------------------------------------------------------------

def test_send_refuses_a_wrong_typed_confirmation(settings, conn, campaign) -> None:
    ready(settings, conn, campaign)
    with pytest.raises(ValueError, match="type DRYMOS-A/1"):
        C.send_wave(settings, conn, campaign=campaign, wave=1, confirm="yes",
                    open_sender=FakeSender(), sleep=lambda _s: None, log=lambda _: None)


def test_send_refuses_without_approval(settings, conn, campaign) -> None:
    C.render_wave(settings, conn, campaign=campaign, wave=1, log=lambda _: None)
    with pytest.raises(ValueError, match="not approved"):
        C.send_wave(settings, conn, campaign=campaign, wave=1, confirm="DRYMOS-A/1",
                    open_sender=FakeSender(), sleep=lambda _s: None, log=lambda _: None)


def test_send_refuses_a_template_changed_after_the_test(settings, conn, campaign) -> None:
    ready(settings, conn, campaign)
    folder = Path(settings.root) / "templates" / "drymos"
    for name, text in (("de.md", DE), ("fr.md", FR)):
        (folder / name).write_text(text.replace("Ein Atlas", "Ein neuer Atlas"),
                                   encoding="utf-8")
    with pytest.raises(ValueError, match="changed after it was rendered"):
        C.send_wave(settings, conn, campaign=campaign, wave=1, confirm="DRYMOS-A/1",
                    open_sender=FakeSender(), sleep=lambda _s: None, log=lambda _: None)


def test_send_posts_the_wave_freezes_the_list_and_moves_the_cards(
    settings, conn, campaign
) -> None:
    ready(settings, conn, campaign)
    out = FakeSender()
    stats = C.send_wave(settings, conn, campaign=campaign, wave=1, confirm="drymos-a/1",
                        open_sender=out, sleep=lambda _s: None, log=lambda _: None)
    assert stats["sent"] == 1 and stats["wave_done"]
    assert not str(out.posted[0]["Subject"]).startswith("[TEST]")
    assert K.audience_by_code(conn, "LIST-1")["frozen_at"] is not None
    stages = [r["stage"] for r in conn.execute("SELECT stage FROM crm.programme_member")]
    assert stages == ["contacted"]
    with pytest.raises(ValueError, match="frozen"):
        audience_stage.build(settings, conn, audience="LIST-1", log=lambda _: None)


def test_the_next_wave_waits_until_bounces_have_been_read(settings, conn, campaign) -> None:
    ready(settings, conn, campaign, wave=1)
    C.send_wave(settings, conn, campaign=campaign, wave=1, confirm="DRYMOS-A/1",
                open_sender=FakeSender(), sleep=lambda _s: None, log=lambda _: None)
    ready(settings, conn, campaign, wave=2)
    with pytest.raises(ValueError, match="bounces to arrive"):
        C.send_wave(settings, conn, campaign=campaign, wave=2, confirm="DRYMOS-A/2",
                    open_sender=FakeSender(), sleep=lambda _s: None, log=lambda _: None)


def test_a_pilot_above_the_ceiling_pauses_the_campaign(settings, conn, campaign) -> None:
    ready(settings, conn, campaign, wave=1)
    out = FakeSender()
    C.send_wave(settings, conn, campaign=campaign, wave=1, confirm="DRYMOS-A/1",
                open_sender=out, sleep=lambda _s: None, log=lambda _: None)
    conn.execute("UPDATE core.contact SET status = 'bounced', status_at = now()"
                 " WHERE lower(value) = %s", (str(out.posted[0]["To"]).lower(),))
    conn.execute("INSERT INTO intel.run (stage, started_at, finished_at)"
                 " VALUES ('bounces', now(), now())")
    conn.commit()
    ready(settings, conn, campaign, wave=2)
    with pytest.raises(ValueError, match="above"):
        C.send_wave(settings, conn, campaign=campaign, wave=2, confirm="DRYMOS-A/2",
                    open_sender=FakeSender(), sleep=lambda _s: None, log=lambda _: None)
    assert K.campaign_by_code(conn, campaign)["paused_at"] is not None


def _bounce_the_pilot(settings, conn, campaign) -> None:
    """Send wave 1, bounce its one message, read the mailbox, render wave 2."""
    ready(settings, conn, campaign, wave=1)
    out = FakeSender()
    C.send_wave(settings, conn, campaign=campaign, wave=1, confirm="DRYMOS-A/1",
                open_sender=out, sleep=lambda _s: None, log=lambda _: None)
    conn.execute("UPDATE core.contact SET status = 'bounced', status_at = now()"
                 " WHERE lower(value) = %s", (str(out.posted[0]["To"]).lower(),))
    conn.execute("INSERT INTO intel.run (stage, started_at, finished_at)"
                 " VALUES ('bounces', now(), now())")
    conn.commit()
    ready(settings, conn, campaign, wave=2)


def _send_two(settings, conn, campaign) -> dict:
    return C.send_wave(settings, conn, campaign=campaign, wave=2, confirm="DRYMOS-A/2",
                       open_sender=FakeSender(), sleep=lambda _s: None, log=lambda _: None)


def test_a_resume_with_a_reason_lets_the_next_wave_past_the_ceiling(
    settings, conn, campaign
) -> None:
    """The pause asks a person to read the bounces. Once they have resumed the
    campaign with a reason, the wave that paused it holds it no longer."""
    _bounce_the_pilot(settings, conn, campaign)
    with pytest.raises(ValueError, match="above"):
        _send_two(settings, conn, campaign)
    campaign_id = int(K.campaign_by_code(conn, campaign)["id"])
    K.resume(conn, campaign_id, reason="a dead domain, not a refusal of ours", by="test")
    conn.commit()
    assert _send_two(settings, conn, campaign)["sent"] >= 1


def test_a_resume_before_the_gate_paused_is_no_answer_to_it(settings, conn, campaign) -> None:
    _bounce_the_pilot(settings, conn, campaign)
    campaign_id = int(K.campaign_by_code(conn, campaign)["id"])
    K.pause(conn, campaign_id, reason="lunch", by="test")
    K.resume(conn, campaign_id, reason="back", by="test")
    conn.commit()
    with pytest.raises(ValueError, match="above"):
        _send_two(settings, conn, campaign)
    assert K.campaign_by_code(conn, campaign)["paused_at"] is not None


def test_planning_refuses_a_wave_larger_than_one_run(settings, conn, campaign) -> None:
    with pytest.raises(ValueError, match="per_run"):
        C.plan(settings, conn, campaign=campaign, wave_size=40, log=lambda _: None)


def test_a_plan_can_be_read_before_it_is_kept(settings, conn, campaign) -> None:
    before = [w["id"] for w in K.waves(conn, K.campaign_by_code(conn, campaign)["id"])]
    C.plan(settings, conn, campaign=campaign, strategy="canton-per-wave", dry_run=True,
           log=lambda _: None)
    after = [w["id"] for w in K.waves(conn, K.campaign_by_code(conn, campaign)["id"])]
    assert before == after
    assert K.campaign_by_code(conn, campaign)["strategy"] == "score-overall"


# --- a bounced message sent again to an accepted replacement --------------------

def _bounced_and_replaced(settings, conn, campaign, *, accept: bool = True):
    """Send the pilot, bounce it, and propose a replacement for its firm."""
    from arpenteur.modules.scout import store as scout_store

    ready(settings, conn, campaign, wave=1)
    out = FakeSender()
    C.send_wave(settings, conn, campaign=campaign, wave=1, confirm="DRYMOS-A/1",
                open_sender=out, sleep=lambda _s: None, log=lambda _: None)
    sent = out.posted[0]
    to = str(sent["To"]).lower()
    company_id = int(conn.execute("SELECT company_id FROM core.contact WHERE lower(value) = %s",
                                  (to,)).fetchone()["company_id"])
    conn.execute("UPDATE core.contact SET status = 'bounced' WHERE lower(value) = %s", (to,))
    scout_store.propose_address(conn, company_id, address="bureau@nouveau.ch", kind="desk",
                                found_by="listing", source_url="https://www.local.ch/x",
                                verdict="deliverable")
    if accept:
        proposal = scout_store.address_proposals(conn, company_id)[0]
        scout_store.decide_address(conn, proposal["id"], accept=True, by="test")
    conn.commit()
    return company_id, sent


def _again(settings, conn, campaign, company_id, *, confirm="DRYMOS-A/1"):
    out = FakeSender()
    stats = C.send_wave(settings, conn, campaign=campaign, wave=1, confirm=confirm,
                        replacement=company_id, open_sender=out, sleep=lambda _s: None,
                        log=lambda _: None)
    return stats, out


def test_a_bounced_message_goes_again_word_for_word_to_the_accepted_address(
    settings, conn, campaign
) -> None:
    company_id, first = _bounced_and_replaced(settings, conn, campaign)
    stats, out = _again(settings, conn, campaign, company_id)
    assert stats["sent"] == 1 and len(out.posted) == 1
    again = out.posted[0]
    assert str(again["To"]) == "bureau@nouveau.ch"
    assert str(again["Subject"]) == str(first["Subject"])
    assert again.get_body(("plain",)).get_content() == first.get_body(("plain",)).get_content()
    row = conn.execute("SELECT replacement_draft_id FROM crm.campaign_message"
                       " WHERE replacement_draft_id IS NOT NULL").fetchone()
    assert row is not None
    with pytest.raises(ValueError, match="already sent again"):
        _again(settings, conn, campaign, company_id)


def test_a_replacement_needs_the_wave_code_and_an_accepted_address(
    settings, conn, campaign
) -> None:
    company_id, _first = _bounced_and_replaced(settings, conn, campaign, accept=False)
    with pytest.raises(ValueError, match="no replacement address is accepted"):
        _again(settings, conn, campaign, company_id)
    with pytest.raises(ValueError, match="type DRYMOS-A/1"):
        _again(settings, conn, campaign, company_id, confirm="yes")


def test_only_a_message_that_bounced_goes_again(settings, conn, campaign) -> None:
    company_id, first = _bounced_and_replaced(settings, conn, campaign)
    conn.execute("UPDATE core.contact SET status = 'deliverable' WHERE lower(value) = %s",
                 (str(first["To"]).lower(),))
    conn.commit()
    with pytest.raises(ValueError, match="did not bounce"):
        _again(settings, conn, campaign, company_id)


def test_a_paused_campaign_may_still_send_a_replacement(settings, conn, campaign) -> None:
    company_id, _first = _bounced_and_replaced(settings, conn, campaign)
    K.pause(conn, int(K.campaign_by_code(conn, campaign)["id"]), reason="bounces", by="gate")
    conn.commit()
    assert _again(settings, conn, campaign, company_id)[0]["sent"] == 1
