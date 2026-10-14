from __future__ import annotations

import argparse
import contextlib
import datetime as dt

import psycopg
import pytest

from arpenteur import cli, db
from arpenteur import config as arpenteur_config
from arpenteur.modules.clients import store as clients_store
from arpenteur.commands import common
from arpenteur.commands import scout as scout_commands
from arpenteur.stages import campaign as campaign_stage


def test_fixture_source_uses_its_own_database(tmp_path, monkeypatch, build_settings) -> None:
    config = build_settings(tmp_path)
    monkeypatch.setattr(common, "load_settings", lambda: config)
    args = cli.build_parser().parse_args(["source", "zefix", "--fixture"])

    assert scout_commands.cmd_source(args) == 0
    # A separate database on the same server, not the live one.
    assert "_fixture" in cli._database_url(config, True)
    assert cli._database_url(config, False) == config.database_url


def test_fixture_flags_are_available_to_follow_up_commands() -> None:
    parser = cli.build_parser()

    assert parser.parse_args(["init-db", "--fixture"]).fixture is True
    assert parser.parse_args(["hydrate", "--fixture"]).fixture is True
    assert parser.parse_args(["profile", "--fixture"]).fixture is True
    assert parser.parse_args(["stats", "--fixture"]).fixture is True


# --- a run always finishes ----------------------------------------------------
#
# A stage whose statement fails leaves the connection's transaction aborted, and
# nothing can be written on it until it is rolled back, the row that says the
# run failed included. Fifteen commands wrote that row without rolling back, so
# the write raised in its turn and the run stayed "running" for ever. Ctrl-C is
# not an `Exception`, so none of them closed the row for it either.

ARGS = argparse.Namespace(limit=3, dry_run=False, func=print)


def _runs(url: str) -> list[dict]:
    """The run rows as another connection sees them: only what was committed."""
    conn = db.open_db(url)
    try:
        return conn.execute(
            "SELECT stage, finished_at, error, stats_json, args_json"
            "  FROM intel.run ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def test_a_run_that_ends_well_records_its_stats(db_path) -> None:
    conn = db.open_db(db_path)

    with cli.run_scope(conn, "fake", ARGS) as run:
        run.stats = {"seen": 2}

    (row,) = _runs(db_path)
    assert row["stage"] == "fake"
    assert row["finished_at"] is not None and row["error"] is None
    assert row["stats_json"] == {"seen": 2}
    assert row["args_json"] == {"limit": 3, "dry_run": False}, "the handler is no argument"
    assert conn.closed, "the scope owns the connection it is given"


def test_a_stage_that_fails_a_statement_still_finishes_its_run(db_path) -> None:
    conn = db.open_db(db_path)

    with pytest.raises(psycopg.errors.DivisionByZero):
        with cli.run_scope(conn, "fake", ARGS, close=False):
            conn.execute("SELECT 1/0")

    (row,) = _runs(db_path)
    assert row["finished_at"] is not None
    assert "division by zero" in row["error"]
    # Rolled back rather than abandoned: the same connection takes a statement.
    assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1
    conn.close()


def test_ctrl_c_finishes_the_run_as_interrupted(db_path) -> None:
    conn = db.open_db(db_path)

    with pytest.raises(KeyboardInterrupt):
        with cli.run_scope(conn, "fake", ARGS, close=False):
            with contextlib.suppress(psycopg.Error):
                conn.execute("SELECT 1/0")
            raise KeyboardInterrupt

    (row,) = _runs(db_path)
    assert row["finished_at"] is not None
    assert row["error"] == "interrupted"
    assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1
    conn.close()


def test_a_failure_without_a_message_still_reads_as_a_failure(db_path) -> None:
    """`str(AssertionError())` is empty and an empty error reads as none, so
    the stats listing would show a failed run as done."""
    conn = db.open_db(db_path)

    with pytest.raises(AssertionError):
        with cli.run_scope(conn, "fake", ARGS):
            raise AssertionError

    assert _runs(db_path)[0]["error"] == "AssertionError"


def test_the_scope_closes_its_connection_when_the_stage_fails(db_path) -> None:
    conn = db.open_db(db_path)

    with pytest.raises(RuntimeError, match="boom"):
        with cli.run_scope(conn, "fake", ARGS):
            raise RuntimeError("boom")

    assert conn.closed


def test_a_connection_lost_mid_run_does_not_hide_why_the_run_failed(db_path) -> None:
    """The server stopping takes the bookkeeping down with the stage. What the
    operator needs is the stage's error, not a second one about the rollback."""
    conn = db.open_db(db_path)

    with pytest.raises(RuntimeError, match="boom"):
        with cli.run_scope(conn, "fake", ARGS, close=False):
            conn.close()
            raise RuntimeError("boom")


# Every command that starts a run: its words, and where it reaches the stage.
STAGE_COMMANDS = [
    (["source"], "source", "run_zefix"),
    (["hydrate"], "source", "hydrate_pending"),
    (["sync"], "sync", "run"),
    (["profile"], "profile", "run"),
    (["people"], "people", "run"),
    (["resolve"], "resolve", "run"),
    (["enrich"], "enrich", "run"),
    (["match"], "match", "run"),
    (["reach"], "reach", "run"),
    (["mail"], "mail", "run"),
    (["boundaries"], "boundaries", "run"),
    (["geocode"], "geocode", "run"),
    (["import", "CHE100000001"], "source", "import_company"),
    (["advise", "CHE100000001"], "advise", "run"),
    (["prep", "CHE100000001"], "prep", "run"),
    (["verify"], "verify", "run"),
    (["draft"], "draft", "run"),
    (["bounces"], "bounce_stage", "run"),
    (["send"], "send_stage", "run"),
    (["answers"], "answers_stage", "run"),
]
STAGE_IDS = [words[0] for words, _module, _function in STAGE_COMMANDS]


@pytest.fixture
def survey(db_path: str) -> str:
    """An empty database but for one client, which `advise` and `prep` look up."""
    conn = db.open_db(db_path)
    company_id = db.upsert_company(conn, {
        "uid": "CHE100000001", "name": "Regie Naef SA", "canton": "VD", "method": "zefix",
    })
    db.promote_to_client(conn, company_id)
    conn.commit()
    conn.close()
    return db_path


def _failing_stage(how: str):
    """A stage whose statement failed halfway: the transaction is left aborted,
    which is the state the commands used to write their last row in.

    `error` raises what the stores raise for a refusal, a `ValueError`; `crash`
    lets the driver's own error out; `interrupt` is Ctrl-C.
    """

    def stage(*args: object, **_kwargs: object) -> None:
        conn = next(a for a in args if isinstance(a, psycopg.Connection))
        try:
            conn.execute("SELECT 1/0")
        except psycopg.Error as exc:
            if how == "interrupt":
                raise KeyboardInterrupt from exc
            if how == "error":
                raise ValueError(str(exc)) from exc
            raise

    return stage


def _stage(name: str):
    """The stage module a command runs, by the name the table above uses."""
    import importlib

    name = {"bounce_stage": "bounces"}.get(name, name.removesuffix("_stage"))
    return importlib.import_module(f"arpenteur.stages.{name}")


def _spy_on_connections(monkeypatch) -> list[psycopg.Connection]:
    opened: list[psycopg.Connection] = []
    real = db.open_db

    def spy(url: str | None = None, enabled=None) -> psycopg.Connection:
        conn = real(url, enabled)
        opened.append(conn)
        return conn

    monkeypatch.setattr(db, "open_db", spy)
    return opened


@pytest.mark.parametrize(("argv", "module", "function"), STAGE_COMMANDS, ids=STAGE_IDS)
def test_a_command_whose_stage_fails_leaves_its_run_finished(
    argv, module, function, survey, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(_stage(module), function, _failing_stage("error"))
    opened = _spy_on_connections(monkeypatch)

    assert cli.main(argv) == 1

    assert "division by zero" in capsys.readouterr().err
    assert all(conn.closed for conn in opened), "the command left a connection open"
    (row,) = _runs(survey)
    assert row["finished_at"] is not None
    assert "division by zero" in row["error"]


@pytest.mark.parametrize(("argv", "module", "function"), STAGE_COMMANDS, ids=STAGE_IDS)
def test_ctrl_c_leaves_the_run_of_every_command_finished(
    argv, module, function, survey, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(_stage(module), function, _failing_stage("interrupt"))
    opened = _spy_on_connections(monkeypatch)

    assert cli.main(argv) == 130

    assert "interrupted" in capsys.readouterr().err
    assert all(conn.closed for conn in opened), "the command left a connection open"
    (row,) = _runs(survey)
    assert row["finished_at"] is not None
    assert row["error"] == "interrupted"


def test_a_refused_date_leaves_no_run_behind(db_path, capsys) -> None:
    """`bounces` opened its run and then read `--since`, so a date that did not
    parse was refused with the row left "running"."""
    assert cli.main(["bounces", "--since", "last tuesday"]) == 1

    assert "--since wants a date" in capsys.readouterr().err
    assert _runs(db_path) == []


def test_an_unreadable_body_file_leaves_no_run_behind(db_path, tmp_path, capsys) -> None:
    """The same for `send`, which read its message after opening the run."""
    absent = tmp_path / "absent.txt"

    assert cli.main(["send", "--to", "info@naef.test", "--body-file", str(absent)]) == 1

    assert "absent.txt" in capsys.readouterr().err
    assert _runs(db_path) == []


# A wave's test and send keep the connection the command opened, so only the
# run is asserted here.
WAVE_ACTIONS = [
    (["campaign", "test", "C1", "1", "--yes"], "test_wave", "campaign-test"),
    (["campaign", "send", "C1", "1", "--yes", "--confirm", "C1/1"], "send_wave",
     "campaign-send"),
]


@pytest.mark.parametrize("how", ["error", "crash", "interrupt"])
@pytest.mark.parametrize(
    ("argv", "function", "stage"), WAVE_ACTIONS, ids=[w[2] for w in WAVE_ACTIONS]
)
def test_a_wave_test_or_send_that_fails_leaves_its_run_finished(
    how, argv, function, stage, db_path, monkeypatch
) -> None:
    monkeypatch.setattr(campaign_stage, "resolve", lambda _conn, _ref: {"id": 1, "code": "C1"})
    monkeypatch.setattr(campaign_stage, function, _failing_stage(how))

    if how == "crash":
        # Not a `ValueError`, so the command's own handler lets it through. The
        # send used to finish its row for a `ValueError` and for nothing else.
        with pytest.raises(psycopg.errors.DivisionByZero):
            cli.main(argv)
    else:
        assert cli.main(argv) == (1 if how == "error" else 130)

    (row,) = _runs(db_path)
    assert row["stage"] == stage
    assert row["finished_at"] is not None
    assert ("interrupted" if how == "interrupt" else "division by zero") in row["error"]


# --- one sentence instead of a traceback --------------------------------------

NOTHING_LISTENS = "postgresql://arpenteur:arpenteur@127.0.0.1:1/arpenteur"


def _said_once(capsys) -> str:
    """What went to stderr, which must be exactly one line and no traceback."""
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert err.endswith("\n") and err.count("\n") == 1, err
    return err


def test_a_stopped_database_is_one_sentence_that_says_how_to_start_it(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv("DATABASE_URL", NOTHING_LISTENS)

    assert cli.main(["stats"]) == 1

    err = _said_once(capsys)
    assert "127.0.0.1:1" in err
    assert "Start it with `docker compose up -d`" in err
    assert "arpenteur:arpenteur" not in err, "the password is not repeated"


def test_a_database_that_answered_with_an_error_is_not_called_stopped(
    monkeypatch
) -> None:
    """A deadlock or a cancelled query is an `OperationalError` too, and telling
    the operator to start a container that is running would send them away from
    the cause."""

    def deadlock():
        raise psycopg.errors.DeadlockDetected("deadlock detected")

    monkeypatch.setattr(common, "load_settings", deadlock)

    with pytest.raises(psycopg.errors.DeadlockDetected):
        cli.main(["stats"])


def _settings_file(tmp_path, monkeypatch, text: str | None) -> None:
    """Point the command at a project root holding this arpenteur.toml, or none."""
    if text is not None:
        (tmp_path / "arpenteur.toml").write_text(text, encoding="utf-8")
    # The settings overlay is read from the database, and none is needed here.
    monkeypatch.setenv("DATABASE_URL", NOTHING_LISTENS)
    monkeypatch.setattr(common, "load_settings", lambda: arpenteur_config.load_settings(tmp_path))


def test_a_mistyped_effort_is_one_sentence(tmp_path, monkeypatch, capsys) -> None:
    _settings_file(tmp_path, monkeypatch, '[llm]\nadvise_effort = "turbo"\n')

    assert cli.main(["stats"]) == 1

    err = _said_once(capsys)
    assert "Unknown effort 'turbo'" in err and "xhigh" in err


def test_a_mistyped_auth_mode_is_one_sentence(tmp_path, monkeypatch, capsys) -> None:
    _settings_file(tmp_path, monkeypatch, '[llm]\nauth_mode = "magic"\n')

    assert cli.main(["stats"]) == 1

    assert "Unknown llm.auth_mode 'magic'" in _said_once(capsys)


def test_a_missing_settings_file_says_where_it_was_expected(
    tmp_path, monkeypatch, capsys
) -> None:
    _settings_file(tmp_path, monkeypatch, None)

    assert cli.main(["stats"]) == 1

    err = _said_once(capsys)
    assert str(tmp_path / "arpenteur.toml") in err and "expected" in err


def test_a_settings_file_that_is_not_toml_is_named(tmp_path, monkeypatch, capsys) -> None:
    """A parse error is a `ValueError`, and its own message does not say which
    file it was reading."""
    _settings_file(tmp_path, monkeypatch, "[survey\n")

    assert cli.main(["stats"]) == 1

    assert "arpenteur.toml" in _said_once(capsys)


def test_any_other_missing_file_is_named_too(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", NOTHING_LISTENS)
    absent = tmp_path / "absent.txt"

    assert cli.main(["source", "--name-file", str(absent)]) == 1

    err = _said_once(capsys)
    assert str(absent) in err and "arpenteur.toml" not in err


# --- the programme commands read dates the way the row factory hands them -----
#
# The row factory returns every timestamp as an ISO string. Three commands
# called `.date()` on those strings and raised as soon as a member had written.


@pytest.fixture
def replied(db_path: str) -> str:
    """A member who wrote back after being contacted; the date of that message."""
    conn = db.open_db(db_path)
    company_id = db.upsert_company(conn, {
        "uid": "CHE100000001", "name": "Regie Naef SA", "canton": "VD", "method": "zefix",
    })
    programme_id = clients_store.add_programme(conn, "flatcheck", "Etat des lieux app")
    member_id, _ = clients_store.add_member(conn, programme_id, company_id)
    # `replies` offers a member who wrote after the move that put it at the
    # stage, and a move stamps the time, so the message is dated after it.
    clients_store.move_member(conn, member_id, "contacted")
    wrote = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
    clients_store.save_email(conn, {
        "message_id": "<reply@naef.test>", "folder": "INBOX", "direction": "in",
        "sent_at": wrote.isoformat(timespec="seconds"), "subject": "Re: votre application",
        "from_email": "info@naef.test", "to_emails": ["me@example.test"],
        "company_id": company_id,
    })
    conn.commit()
    # Read back the way the commands read it, so the date is whatever the
    # session's time zone makes of the instant.
    stored = conn.execute("SELECT sent_at FROM crm.email_message").fetchone()["sent_at"]
    conn.close()
    return stored[:10]


def test_programme_replies_lists_who_wrote_back_and_when(replied, capsys) -> None:
    assert cli.main(["programme", "replies", "FLATCHECK"]) == 0

    out = capsys.readouterr().out
    assert "Regie Naef SA" in out and replied in out


def test_programme_ledger_shows_when_each_member_last_wrote(replied, capsys) -> None:
    assert cli.main(["programme", "ledger", "FLATCHECK"]) == 0

    out = capsys.readouterr().out
    assert "Regie Naef SA" in out and replied in out


def test_programme_history_dates_every_message(replied, capsys) -> None:
    assert cli.main(["programme", "history", "FLATCHECK", "CHE100000001"]) == 0

    out = capsys.readouterr().out
    assert "Re: votre application" in out and replied in out


# --- a limit of zero ----------------------------------------------------------
#
# The stages read `limit=0` as "no limit" when they slice and as "a limit was
# given" when they guard the cost, so `--limit 0` ran everything and skipped
# the check that stops a run from doing so. The parser no longer lets it in.


@pytest.mark.parametrize("text", ["0", "-3", "1.5", "many", ""])
def test_a_limit_must_be_a_whole_number_of_one_or_more(text) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        cli.positive_int(text)


def test_a_limit_of_one_or_more_is_taken_as_a_number() -> None:
    assert cli.positive_int("1") == 1
    assert cli.positive_int("250") == 250


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    (action,) = (a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return action.choices


def test_every_limit_flag_refuses_zero() -> None:
    """Checked over the whole parser, so a command added later with a plain
    `type=int` is the thing this fails on."""
    found = []
    for name, sub in _subparsers(cli.build_parser()).items():
        for action in sub._actions:
            if "--limit" in action.option_strings:
                found.append(name)
                assert action.type is cli.positive_int, f"{name} --limit takes any integer"
    assert len(found) >= 10, found


@pytest.mark.parametrize("command", ["source", "enrich", "match", "reach", "draft"])
def test_a_limit_of_zero_is_refused_before_anything_runs(
    command, db_path, monkeypatch, capsys
) -> None:
    def must_not_run(*_args: object, **_kwargs: object) -> None:
        pytest.fail(f"{command} ran with --limit 0")

    for module, function in {
        "source": [(_stage("source"), "run_zefix"), (_stage("source"), "run_by_name")],
        "enrich": [(_stage("enrich"), "run")],
        "match": [(_stage("match"), "run")],
        "reach": [(_stage("reach"), "run")],
        "draft": [(_stage("draft"), "run")],
    }[command]:
        monkeypatch.setattr(module, function, must_not_run)

    with pytest.raises(SystemExit) as caught:
        cli.main([command, "--limit", "0"])

    assert caught.value.code == 2
    assert "0 is not a positive number" in capsys.readouterr().err


# --- the settings held in the database ----------------------------------------


def test_a_model_id_outside_the_family_is_refused_at_load(tmp_path, monkeypatch, capsys) -> None:
    _settings_file(tmp_path, monkeypatch, '[llm]\nmatch_model = "gpt-9"\n')

    assert cli.main(["stats"]) == 1

    err = _said_once(capsys)
    assert "llm.match_model 'gpt-9' is not a Claude model id" in err


def test_the_unwired_provider_is_refused_at_load(tmp_path, monkeypatch, capsys) -> None:
    _settings_file(tmp_path, monkeypatch, '[llm]\nprovider = "infomaniak"\n')

    assert cli.main(["stats"]) == 1

    assert "Unknown llm.provider 'infomaniak'" in _said_once(capsys)


ISSUER_TOML = """
[survey]
cantons = ["GE"]
legal_forms = ["0107"]

[zefix]
contact = "arpenteur (contact: me@example.ch)"

[issuer]
name = "Nadia Example"
town = "Vevey"
iban = "CH93 0076 2011 6238 5295 7"
payment_days = 30

[enrol]
programme = "pilot"
segments = ["sme-owner"]

[[rates.service]]
code = "day"
name = "Day"
unit = "day"
low = 800
high = 800
"""


def _own_database(tmp_path, monkeypatch, db_path: str) -> None:
    (tmp_path / "arpenteur.toml").write_text(ISSUER_TOML, encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", db_path)
    monkeypatch.setattr(common, "load_settings", lambda: arpenteur_config.load_settings(tmp_path))


def test_import_toml_moves_each_section_once(tmp_path, monkeypatch, capsys, db_path) -> None:
    """The file's tables reach the database; a second run changes nothing, and
    the page's own change survives it."""
    from arpenteur import db

    _own_database(tmp_path, monkeypatch, db_path)
    assert cli.main(["settings", "import-toml"]) == 0
    out = capsys.readouterr().out
    assert "issuer: imported 4 key(s)" in out and "zefix: imported 1 key(s)" in out
    assert "enrol: imported 2 key(s)" in out and "rates: imported 1 key(s)" in out
    assert "smtp: not in the file" in out
    loaded = arpenteur_config.load_settings(tmp_path)
    assert loaded.issuer.name == "Nadia Example" and loaded.enrol.programme == "PILOT"

    conn = db.open_db(db_path)
    db.save_setting(conn, "issuer", {"name": "Changed on the page"})
    conn.commit()
    before = {r["key"]: r["updated_at"] for r in db.setting_history(conn)}
    conn.close()
    assert cli.main(["settings", "import-toml"]) == 0
    assert "issuer: kept" in capsys.readouterr().out
    conn = db.open_db(db_path)
    after = {r["key"]: r["updated_at"] for r in db.setting_history(conn)}
    conn.close()
    assert after == before
    assert arpenteur_config.load_settings(tmp_path).issuer.name == "Changed on the page"

    assert cli.main(["settings", "import-toml", "--force"]) == 0
    assert arpenteur_config.load_settings(tmp_path).issuer.name == "Nadia Example"


def test_settings_show_never_prints_the_whole_iban(tmp_path, monkeypatch, capsys, db_path) -> None:
    _own_database(tmp_path, monkeypatch, db_path)
    assert cli.main(["settings", "import-toml"]) == 0
    capsys.readouterr()
    assert cli.main(["settings", "show"]) == 0
    out = capsys.readouterr().out
    assert "[issuer]" in out and "name = Nadia Example" in out
    assert "CH93" not in out and "5295" not in out.replace("2957", "")
    assert out.count("2957") == 1
