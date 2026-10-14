"""The setup checks: what a first run needs, named before it is attempted.

One list feeds the landing page and `arpenteur doctor`, so the two cannot
disagree. The quick checks reach nothing; the live ones sign in to the
mailbox and open a session with the mail server, and send nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arpenteur import cli, db, health, llm
from arpenteur import config as arpenteur_config
from arpenteur.config import load_settings
from arpenteur.console import create_app
from arpenteur.commands import common
from fastapi.testclient import TestClient

BARE = """
[survey]
cantons = []
legal_forms = []

[zefix]
base_url = "https://example.test/ZefixPublicREST"
contact = "tests"
"""

READY = """
[survey]
cantons = ["VD"]
legal_forms = ["0107"]

[zefix]
base_url = "https://example.test/ZefixPublicREST"
contact = "tests"

[issuer]
name = "Nadia Example"
iban = "CH93 0076 2011 6238 5295 7"

[enrol]
programme = "pilot"
brief = "brief.md"
include_in_draft = true
"""


@pytest.fixture
def signed_out(monkeypatch):
    monkeypatch.setattr(llm, "_claude_cli", lambda: None)
    monkeypatch.setattr(llm, "_has_local_profile", lambda: False)
    for name in ("ZEFIX_USER", "ZEFIX_PASSWORD", "ANTHROPIC_API_KEY", "MAIL_IMAP_SERVER",
                 "MAIL_USERNAME", "MAIL_PASSWORD", "SMTP_SERVER", "SMTP_SENDER",
                 "SMTP_PASSWORD", "INFOMANIAK_USERNAME", "INFOMANIAK_PASSWORD",
                 "INFOMANIAK_IMAP_SERVER", "INFOMANIAK_SMTP_SERVER"):
        monkeypatch.delenv(name, raising=False)


def _root(tmp_path: Path, text: str) -> Path:
    (tmp_path / "arpenteur.toml").write_text(text, encoding="utf-8")
    return tmp_path


def _by_name(found: list[health.Check]) -> dict[str, health.Check]:
    return {check.name: check for check in found}


def test_an_empty_setup_names_every_missing_piece(tmp_path, db_path, signed_out) -> None:
    settings = load_settings(_root(tmp_path, BARE))
    conn = db.open_db(db_path)
    try:
        found = _by_name(health.checks(settings, conn))
    finally:
        conn.close()
    assert found["Database"].ok and found["Modules"].ok
    assert found["Territory"].state == "missing" and "No canton" in found["Territory"].detail
    assert found["Register"].state == "missing" and "ZEFIX_USER" in found["Register"].detail
    assert found["Model access"].state == "missing"
    assert found["Scoring brief"].state == "missing"
    assert found["Mailbox"].state == "warn" and found["Sending"].state == "warn"
    assert found["Identity"].state == "missing"
    assert found["Signature"].state == "warn"
    assert found["Message brief"].state == "warn"
    assert "Invitations" not in found, "no invitation is expected without a programme"
    for check in found.values():
        assert check.detail.endswith("."), check
        assert check.href.startswith("/"), check


def test_a_ready_setup_passes_the_quick_checks(tmp_path, db_path, monkeypatch, signed_out) -> None:
    root = _root(tmp_path, READY)
    (root / "campaign.md").write_text("# Brief\n\nWho we serve.\n", encoding="utf-8")
    (root / "brief.md").write_text("# Message\n\nWhat we say.\n", encoding="utf-8")
    (root / "signature.html").write_text("<p>Nadia</p>", encoding="utf-8")
    folder = root / "templates" / "pilot"
    folder.mkdir(parents=True)
    for language in ("fr", "de", "it"):
        (folder / f"invite.{language}.md").write_text(
            "---\nComment participer : {ios_link}\n---\n", encoding="utf-8")
    monkeypatch.setenv("ZEFIX_USER", "u")
    monkeypatch.setenv("ZEFIX_PASSWORD", "p")
    monkeypatch.setenv("MAIL_IMAP_SERVER", "imap.example.test")
    monkeypatch.setenv("MAIL_USERNAME", "nadia@example.test")
    monkeypatch.setenv("MAIL_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_SERVER", "smtp.example.test")
    monkeypatch.setattr(llm, "_claude_cli", lambda: "/usr/bin/claude")
    monkeypatch.setattr(llm, "_has_local_profile", lambda: True)
    settings = load_settings(root)
    conn = db.open_db(db_path)
    try:
        found = _by_name(health.checks(settings, conn))
    finally:
        conn.close()
    missing = {name for name, check in found.items() if not check.ok}
    assert missing == set(), found
    assert "not yet tried" in found["Mailbox"].detail
    assert "secret" not in health.report(list(found.values()))


def test_the_live_checks_sign_in_and_send_nothing(tmp_path, db_path, monkeypatch, signed_out) -> None:
    from arpenteur.sources import mail as mail_source
    from arpenteur.sources import smtp as smtp_source

    root = _root(tmp_path, READY)
    monkeypatch.setenv("MAIL_IMAP_SERVER", "imap.example.test")
    monkeypatch.setenv("MAIL_USERNAME", "nadia@example.test")
    monkeypatch.setenv("MAIL_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_SERVER", "smtp.example.test")
    opened: list[str] = []

    class FakeMailbox:
        def __init__(self, host, port=993, *, user, password, timeout=30.0):
            opened.append(f"imap {host} {user}")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    class FakeSender:
        def __init__(self, host, port=587, user="", password="", security="starttls",
                     timeout=30):
            opened.append(f"smtp {host} {security}")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def alive(self):
            return True

        def post(self, *_a, **_k):
            raise AssertionError("the doctor never sends")

    monkeypatch.setattr(mail_source, "Mailbox", FakeMailbox)
    monkeypatch.setattr(smtp_source, "Sender", FakeSender)
    settings = load_settings(root)
    conn = db.open_db(db_path)
    try:
        found = _by_name(health.checks(settings, conn, live=True))
    finally:
        conn.close()
    assert found["Mailbox"].ok and "Signed in" in found["Mailbox"].detail
    assert found["Sending"].ok and "nothing was sent" in found["Sending"].detail
    assert opened == ["imap imap.example.test nadia@example.test", "smtp smtp.example.test starttls"]

    class RefusingMailbox(FakeMailbox):
        def __enter__(self):
            raise mail_source.MailError("imap.example.test refused the login")

    monkeypatch.setattr(mail_source, "Mailbox", RefusingMailbox)
    conn = db.open_db(db_path)
    try:
        refused = _by_name(health.checks(settings, conn, live=True))
    finally:
        conn.close()
    assert refused["Mailbox"].state == "missing" and "refused" in refused["Mailbox"].detail


def test_the_doctor_prints_the_list_and_exits_one_when_something_is_missing(
    tmp_path, db_path, monkeypatch, capsys, signed_out
) -> None:
    root = _root(tmp_path, BARE)
    monkeypatch.setenv("DATABASE_URL", db_path)
    monkeypatch.setattr(common, "load_settings", lambda: arpenteur_config.load_settings(root))
    assert cli.main(["doctor", "--quick"]) == 1
    out = capsys.readouterr().out
    assert "!!  Territory: No canton is set" in out
    assert "ok  Database:" in out
    assert "thing(s) to set before the first run" in out
    assert "[/settings#territory]" in out


def test_home_and_the_doctor_page_draw_the_same_checks(tmp_path, db_path, monkeypatch, signed_out) -> None:
    root = _root(tmp_path, BARE)
    monkeypatch.setenv("DATABASE_URL", db_path)
    client = TestClient(create_app(load_settings(root)))
    home = client.get("/").text
    assert "Register" in home and "Model access" in home and "Scoring brief" in home
    assert 'href="/doctor"' in home
    page = client.get("/doctor")
    assert page.status_code == 200
    assert "<h1>Doctor</h1>" in page.text and "No canton is set" in page.text
    assert "nothing was sent" in page.text


def test_the_identity_check_follows_the_sales_module(tmp_path, db_path, signed_out) -> None:
    from dataclasses import replace

    settings = replace(load_settings(_root(tmp_path, BARE)), modules=("scout", "geo", "clients"))
    conn = db.open_db(db_path)
    try:
        names = {check.name for check in health.checks(settings, conn)}
    finally:
        conn.close()
    assert "Identity" not in names


def test_a_campaign_whose_templates_are_broken_is_named(tmp_path, db_path, signed_out) -> None:
    from arpenteur.modules.clients import campaigns as campaign_store
    from arpenteur.sources import audience as audience_rules
    from arpenteur.stages import audience as audience_stage

    root = _root(tmp_path, READY)
    settings = load_settings(root)
    conn = db.open_db(db_path)
    try:
        audience_id = audience_stage.create(
            conn, code="list-1", name="Firms",
            rule=audience_rules.Rule(segments={"sme-owner": 1.0}))
        campaign_store.add_campaign(
            conn, code="pilot-a", name="Pilot", audience_id=audience_id,
            templates="templates/pilot-a", strategy="score-overall",
            pilot_size=0, wave_size=5, gap_days=0)
        conn.commit()
        found = _by_name(health.checks(settings, conn))
        assert found["Templates of PILOT-A"].state == "missing"
        folder = root / "templates" / "pilot-a"
        folder.mkdir(parents=True)
        (folder / "fr.md").write_text("---\nObjet : Bonjour\n\nTexte.\n---\n", encoding="utf-8")
        found = _by_name(health.checks(settings, conn))
        assert found["Templates of PILOT-A"].ok
    finally:
        conn.close()
