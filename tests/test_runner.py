"""Starting a stage from the console, and watching it work.

The console used to refuse to start anything. That reasoning was about the
money rather than the button, so the tests here are mostly about whether the
guards that made the command line safe survived the move to a form: one run
at a time, a declared size before anything is paid for, and a dry run offered
first.

The other half is the log. A stage that runs for eight hours is useless to
watch if the view only says "running", and a progress view that loses
everything on a page reload is a toy.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.kernel.db import Connection, Row
from arpenteur.config import Territory
from arpenteur.console import runner as runner_module
from arpenteur.console.app import create_app
from arpenteur.console.runner import Runner, Stage, coerce, check

SEGMENTS = {"sme-owner": "A small owner-run business."}


@pytest.fixture
def settings(db_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        database_url=db_path,
        territory=Territory(cantons=("GE",), legal_forms=("0107",), name="geneve"),
        segments=SEGMENTS,
    )


@pytest.fixture
def conn(settings) -> Connection:
    connection = db.open_db(settings.database_url)
    db.upsert_company(
        connection,
        {"uid": "CHE1", "name": "Fiduciaire Sàrl", "canton": "GE", "status": "ACTIVE",
         "hydrated": True, "method": "zefix"},
    )
    connection.commit()
    return connection


@pytest.fixture
def client(settings, conn) -> TestClient:
    return TestClient(create_app(settings))


def wait_for(predicate, timeout: float = 5.0) -> bool:
    """Threads, so the assertions have to wait for one rather than assume."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def fake_stage(name: str, call, **kw) -> Stage:
    """Keyword-built on purpose: Stage carries the words the page shows, so it
    gains fields as the explanations improve and positional construction would
    silently shift `call` into a description."""
    return Stage(
        name=name, title=name, what="a test stage", call=call,
        button=f"run {name}", **kw,
    )


# --- the guards ---------------------------------------------------------------


def test_a_paid_stage_will_not_start_without_a_declared_size(client) -> None:
    """The objection was never to the button. It was to spending real money
    from a page that looks like a filter."""
    reply = client.post("/run/enrich", data={}, follow_redirects=False)
    assert reply.status_code == 303
    assert "limit" in reply.headers["location"]
    assert "Nothing has been run yet" in client.get("/runs").text


def test_the_size_is_checked_on_the_server_not_in_the_markup(settings) -> None:
    """A required attribute and a disabled button are conveniences for the
    person, not constraints on the request."""
    with pytest.raises(ValueError, match="limit"):
        check(runner_module.STAGES["enrich"], {"dry_run": False})


def test_a_dry_run_needs_no_size(settings) -> None:
    check(runner_module.STAGES["enrich"], {"dry_run": True})


def test_naming_the_companies_is_a_declared_size(settings) -> None:
    check(runner_module.STAGES["enrich"], {"dry_run": False, "uids": ["CHE1"]})


def test_a_free_stage_needs_no_size(settings) -> None:
    check(runner_module.STAGES["profile"], {"dry_run": False})


def test_only_one_run_at_a_time(settings, conn) -> None:
    """Two enrich runs would spend twice and interleave their writes."""
    release = threading.Event()

    def slow(_settings, _conn, *, log, **_kw):
        log("working")
        release.wait(5)
        return {}

    runner = Runner(settings)
    runner_module.STAGES["_slow"] = fake_stage("_slow", slow)
    try:
        first = runner.start("_slow", {})
        assert wait_for(lambda: runner.running() == first)
        with pytest.raises(RuntimeError, match="still going"):
            runner.start("_slow", {})
        release.set()
        assert wait_for(lambda: runner.running() is None)
        # And the lock is released, not merely held by a dead thread.
        assert runner.start("_slow", {}) != first
        release.set()
    finally:
        release.set()
        runner_module.STAGES.pop("_slow", None)


def test_an_unknown_stage_is_refused(settings) -> None:
    with pytest.raises(ValueError, match="unknown stage"):
        Runner(settings).start("rm-rf", {})


# --- form values reaching the stage -------------------------------------------


def test_a_blank_field_leaves_the_stage_default_alone(settings) -> None:
    """Blank means "not given", not "given as empty". Passing "" for canton
    would filter on a canton called empty string and select nothing."""
    stage = runner_module.STAGES["enrich"]
    assert "canton" not in coerce(stage, {"canton": "  ", "segment": ""})


def test_an_unchecked_box_is_false_not_missing(settings) -> None:
    """A browser omits unchecked boxes entirely, so an absent dry_run must
    become False rather than leaving the stage's own default of False to be
    reasoned about twice."""
    assert coerce(runner_module.STAGES["profile"], {}) == {
        "reclassify": False, "dry_run": False
    }


def test_uids_arrive_as_a_list(settings) -> None:
    got = coerce(runner_module.STAGES["enrich"], {"uids": "CHE1, CHE2 ,"})
    assert got["uids"] == ["CHE1", "CHE2"]


def test_a_limit_that_is_not_a_number_says_so(settings) -> None:
    with pytest.raises(ValueError, match="whole number"):
        coerce(runner_module.STAGES["enrich"], {"limit": "lots"})


# --- the log ------------------------------------------------------------------


def test_the_log_records_what_the_stage_said(settings, conn) -> None:
    def talkative(_settings, _conn, *, log, **_kw):
        for i in range(3):
            log(f"company {i}")
        return {"done": 3}

    runner = Runner(settings)
    runner_module.STAGES["_talk"] = fake_stage("_talk", talkative)
    try:
        run_id = runner.start("_talk", {})
        assert wait_for(lambda: runner.running() is None)
        lines = [r["text"] for r in db.lines_after(conn, run_id, 0)]
        assert "company 0" in lines and "company 2" in lines
        assert lines[-1] == "done: done=3"
        row = conn.execute("SELECT * FROM intel.run WHERE id = %s", (run_id,)).fetchone()
        assert row["finished_at"] and row["error"] is None
        assert row["started_by"] == "console"
    finally:
        runner_module.STAGES.pop("_talk", None)


def test_a_stage_returning_a_bare_count_still_reports_it(settings, conn) -> None:
    """hydrate returns an int rather than a stats dict, and the runner used to
    record `{}` for it: a finished run whose result said nothing at all. Its
    own progress lines are pitched for sixty thousand companies, so a run of
    five is otherwise completely silent about what it did."""
    runner = Runner(settings)
    runner_module.STAGES["_count"] = fake_stage(
        "_count", lambda _s, _c, *, log, **_kw: 5, result_key="hydrated"
    )
    try:
        run_id = runner.start("_count", {})
        assert wait_for(lambda: runner.running() is None)
        row = conn.execute("SELECT stats_json FROM intel.run WHERE id = %s", (run_id,)).fetchone()
        # jsonb comes back parsed rather than as text.
        assert row["stats_json"]["hydrated"] == 5
        assert db.lines_after(conn, run_id, 0)[-1]["text"] == "done: hydrated=5"
    finally:
        runner_module.STAGES.pop("_count", None)


def test_a_stage_that_fails_leaves_the_lines_it_got_to(settings, conn) -> None:
    """A run that dies at company 4000 should leave four thousand lines
    explaining how it got there, not an empty log and a traceback."""
    def doomed(_settings, _conn, *, log, **_kw):
        log("company 0")
        raise RuntimeError("the registry said no")

    runner = Runner(settings)
    runner_module.STAGES["_doomed"] = fake_stage("_doomed", doomed)
    try:
        run_id = runner.start("_doomed", {})
        assert wait_for(lambda: runner.running() is None)
        lines = [r["text"] for r in db.lines_after(conn, run_id, 0)]
        assert "company 0" in lines
        assert any("the registry said no" in line for line in lines)
        row = conn.execute("SELECT * FROM intel.run WHERE id = %s", (run_id,)).fetchone()
        assert row["error"] == "the registry said no" and row["finished_at"]
    finally:
        runner_module.STAGES.pop("_doomed", None)


def test_a_watcher_is_only_sent_what_it_has_not_seen(client, conn) -> None:
    """A run emitting sixty thousand lines must not re-send them every poll."""
    run_id = db.start_run(conn, "test", {})
    for text in ("one", "two", "three"):
        db.append_line(conn, run_id, text)
    conn.commit()

    body = client.get(f"/runs/{run_id}/lines?after=2").text
    assert "three" in body
    assert "one" not in body and "two" not in body


def test_a_finished_run_stops_the_polling(client, conn) -> None:
    """No timer to cancel and no connection left hanging if a run died."""
    run_id = db.start_run(conn, "test", {})
    db.append_line(conn, run_id, "one")
    conn.commit()
    assert "hx-trigger" in client.get(f"/runs/{run_id}/lines").text

    db.finish_run(conn, run_id, {"ok": True})
    assert "hx-trigger" not in client.get(f"/runs/{run_id}/lines").text


def test_the_last_lines_survive_the_run_ending_mid_poll(client, conn) -> None:
    """Reading finished_at before the lines would drop whatever was written
    between the two queries, which is exactly the end of the log."""
    run_id = db.start_run(conn, "test", {})
    db.append_line(conn, run_id, "the last thing it said")
    db.finish_run(conn, run_id, {})
    conn.commit()
    body = client.get(f"/runs/{run_id}/lines").text
    assert "the last thing it said" in body
    assert "hx-trigger" not in body


def test_the_log_survives_a_reload(client, conn) -> None:
    """The difference between a progress view and a toy."""
    run_id = db.start_run(conn, "test", {})
    db.append_line(conn, run_id, "company 0")
    conn.commit()
    assert "company 0" in client.get(f"/runs/{run_id}").text


def test_a_missing_run_is_a_404(client) -> None:
    assert client.get("/runs/999").status_code == 404


# --- a console that was stopped mid-run ---------------------------------------


def test_a_run_orphaned_by_a_restart_is_settled(settings, conn) -> None:
    """A run is a thread inside the console, so stopping it kills the run and
    leaves a row saying "running" with nothing running. The progress view
    would poll that forever."""
    orphan = db.start_run(conn, "profile", {}, started_by="console")
    conn.commit()

    create_app(settings)  # a fresh console over the same database
    row = conn.execute("SELECT * FROM intel.run WHERE id = %s", (orphan,)).fetchone()
    assert row["finished_at"] and "interrupted" in row["error"]


def test_a_command_line_run_is_left_alone(settings, conn) -> None:
    """It may belong to a process still working in another terminal, and
    marking that interrupted would be inventing a fact about someone else."""
    live = db.start_run(conn, "hydrate", {}, started_by="cli")
    conn.commit()

    create_app(settings)
    row = conn.execute("SELECT * FROM intel.run WHERE id = %s", (live,)).fetchone()
    assert row["finished_at"] is None and row["error"] is None


def test_the_dry_run_that_still_costs_says_so(client) -> None:
    """profile's dry run calls the model for every batch and skips only the
    write. An operator ticking "spend nothing" over sixty thousand companies
    would be billed for the whole classification."""
    body = client.get("/run").text
    assert "still calls the model" in body


# --- the form itself ----------------------------------------------------------


def test_every_stage_is_offered_with_what_it_costs(client) -> None:
    """"Costs money per company" is the one thing an operator must not have
    to remember, so it is on the card rather than in the documentation."""
    body = client.get("/pipeline").text
    for name in runner_module.ORDER:
        assert f"/run/{name}" in body
    assert "centimes" in body and "THE EXPENSIVE ONE" in body


def test_every_stage_says_what_it_needs_and_what_it_produces(client) -> None:
    """An empty result should read as "nothing to do yet" rather than as a
    broken stage, which needs the prerequisite stated on the card."""
    body = client.get("/pipeline").text
    assert body.count("Needs first") == len(runner_module.ORDER)
    assert body.count("Produces") == len(runner_module.ORDER)


def test_every_button_says_what_pressing_it_will_do(client) -> None:
    body = client.get("/pipeline").text
    assert "Works through the companies that have no detail yet" in body
    assert "Will not start without a size or a dry run" in body


def test_the_old_separate_form_leads_to_the_operations_page(client) -> None:
    """It was a stack of unlabelled boxes on a page of its own. The controls
    belong next to the counts that say which one to press."""
    reply = client.get("/run", follow_redirects=False)
    assert reply.status_code == 307 and reply.headers["location"] == "/pipeline"


def test_sync_counts_days_of_gazette_not_companies(client, conn) -> None:
    """Its backlog is days not yet read. Showing the count of companies marked
    as changed said the gazette was behind when what was behind was the
    re-reading, which is the next stage's job."""
    body = client.get("/pipeline").text
    assert "days behind" in body and "days read" in body


def test_a_stage_shows_how_much_is_waiting_for_it(client, conn) -> None:
    """The number that turns a form into an instrument. A bare button says
    nothing about whether pressing it would do anything at all."""
    db.upsert_company(
        conn,
        {"uid": "CHE2", "name": "Sans détail Sàrl", "canton": "GE", "method": "zefix"},
    )
    conn.commit()
    body = client.get("/pipeline").text
    assert "waiting</span>" in body


def test_choices_are_offered_rather_than_typed(client, conn) -> None:
    """Anything with a knowable set of values is a list to pick from. A canton
    typed by hand is a filter that silently matches nothing."""
    body = client.get("/pipeline").text
    assert "<option value='GE'>GE</option>" in body
    assert "All cantons" in body and "sme-owner" in body
    # And the sizes, which used to be a bare number box.
    assert "100 companies" in body and "No limit, run the whole queue" in body


def test_a_field_that_must_be_typed_says_where_to_get_it(client) -> None:
    body = client.get("/pipeline").text
    assert "CHE164075515" in body, "an example of the format"
    assert "or from zefix.ch" in body, "where to find one"


def test_a_refusal_comes_back_on_the_form(client) -> None:
    reply = client.post("/run/enrich", data={}, follow_redirects=True)
    assert "limit" in reply.text


def test_a_company_can_be_actioned_from_its_own_page(client, conn) -> None:
    """The other half of running a stage: having opened a company and decided
    it is interesting, acting on it without going back to build a filter that
    selects exactly it."""
    body = client.get("/company/1").text
    assert "/run/enrich" in body and "/run/match" in body
    assert "value='CHE1'" in body, "the action must name this company"


def test_a_named_company_needs_no_size(client, conn) -> None:
    """Naming a company is declaring the size of the run, which is why the
    paid stages accept it without a limit."""
    reply = client.post("/run/enrich", data={"uids": "CHE1"}, follow_redirects=False)
    assert reply.status_code == 303
    assert "/runs/" in reply.headers["location"], reply.headers["location"]


def test_the_console_still_cannot_send(client, conn) -> None:
    """The guarantee the runner must not have weakened. Starting stages is a
    different thing from transmitting to a company, and no stage that sends
    exists to be offered."""
    routes = {getattr(r, "path", "") for r in client.app.routes}
    assert not [p for p in routes if "send" in p or "mail" in p]
    assert "draft" not in runner_module.STAGES
    assert conn.execute("SELECT COUNT(*) AS n FROM intel.draft WHERE sent_at IS NOT NULL").fetchone()[0] == 0
