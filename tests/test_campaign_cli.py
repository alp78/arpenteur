"""The audience and campaign commands, against the test database.

Smoke tests of the surface: each command parses, reaches its stage, and says
what it did. The rules themselves are tested where they live.
"""

from __future__ import annotations

import pytest

from arpenteur import cli, db
from arpenteur.kernel.db import Connection
from arpenteur.modules.clients import campaigns as K

pytestmark = pytest.mark.pg

FIRMS = (
    ("CHE300000011", "Zermatt Tourismus", "VS", "info@zermatt.ch"),
    ("CHE300000012", "Champéry Tourisme", "VS", "info@champery.ch"),
    ("CHE300000013", "Adelboden Tourismus", "BE", "info@adelboden.ch"),
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


def run(*argv: str) -> int:
    return cli.main(list(argv))


def test_a_list_is_made_built_and_shown_by_canton(conn, capsys) -> None:
    assert run("audience", "new", "L1", "Tourisme", "--segment", "tourism-office=1") == 0
    assert run("audience", "build", "L1") == 0
    assert len(K.members(conn, K.audience_by_code(conn, "L1")["id"], verdict="kept")) == 3
    capsys.readouterr()
    assert run("audience", "show", "L1") == 0
    out = capsys.readouterr().out
    assert out.index("\nBE") < out.index("\nVS")


def test_a_pin_needs_a_reason(conn, capsys) -> None:
    run("audience", "new", "L1", "Tourisme", "--segment", "tourism-office=1")
    run("audience", "build", "L1")
    assert run("audience", "drop", "L1", "CHE300000011") == 1
    assert "--why" in capsys.readouterr().err
    assert run("audience", "drop", "L1", "CHE-300.000.011", "--why", "a client") == 0


def test_the_strategies_are_listed_with_their_explanations(conn, capsys) -> None:
    assert run("campaign", "strategies") == 0
    out = capsys.readouterr().out
    for name in ("canton-blocks", "canton-per-wave", "score-by-language", "score-overall"):
        assert name in out


def test_a_plan_dry_run_writes_no_wave(conn) -> None:
    run("audience", "new", "L1", "Tourisme", "--segment", "tourism-office=1")
    run("audience", "build", "L1")
    assert run("campaign", "new", "C1", "Essai", "--audience", "L1", "--templates",
               "templates/drymos", "--strategy", "canton-per-wave") == 0
    assert run("campaign", "plan", "C1", "--dry-run") == 0
    assert K.waves(conn, K.campaign_by_code(conn, "C1")["id"]) == []


def test_send_without_yes_names_the_confirmation_and_posts_nothing(conn, capsys) -> None:
    run("audience", "new", "L1", "Tourisme", "--segment", "tourism-office=1")
    run("audience", "build", "L1")
    run("campaign", "new", "C1", "Essai", "--audience", "L1", "--templates",
        "templates/drymos", "--strategy", "score-overall")
    run("campaign", "plan", "C1")
    capsys.readouterr()
    assert run("campaign", "send", "C1", "1") == 0
    assert "--confirm C1/1" in capsys.readouterr().out
    assert conn.execute("SELECT count(*) AS n FROM crm.outbound").fetchone()["n"] == 0


def test_templates_outside_the_templates_folder_are_refused(conn, capsys) -> None:
    run("audience", "new", "L1", "Tourisme", "--segment", "tourism-office=1")
    assert run("campaign", "new", "C1", "Essai", "--audience", "L1", "--templates",
               "../etc", "--strategy", "score-overall") == 1
    assert "templates/" in capsys.readouterr().err


def test_pause_and_resume_take_no_wave_number(conn) -> None:
    run("audience", "new", "L1", "Tourisme", "--segment", "tourism-office=1")
    run("audience", "build", "L1")
    run("campaign", "new", "C1", "Essai", "--audience", "L1", "--templates",
        "templates/drymos", "--strategy", "score-overall")
    assert run("campaign", "pause", "C1", "--why", "checking") == 0
    assert K.campaign_by_code(conn, "C1")["paused_at"] is not None
    assert run("campaign", "resume", "C1", "--why", "checked") == 0
    assert K.campaign_by_code(conn, "C1")["paused_at"] is None
