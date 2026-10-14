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
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.kernel.db import Connection
from arpenteur.config import Territory
from arpenteur.modules.scout import store as scout_store
from arpenteur.sources import mailcheck
from arpenteur.stages import draft
from arpenteur.console import runner as runner_module
from arpenteur.console.app import create_app
from arpenteur.console.runner import Runner, Stage, coerce, check

SEGMENTS = {"sme-owner": "A small owner-run business."}
SECTORS = {"hospitality": "Restaurants, cafés and hotels."}


@pytest.fixture
def settings(db_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        database_url=db_path,
        territory=Territory(cantons=("GE",), legal_forms=("0107",), name="geneve"),
        segments=SEGMENTS,
        sectors=SECTORS,
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
    body = client.get("/pipeline").text
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


def test_only_the_wave_routes_and_stages_can_send(client, conn) -> None:
    """The guarantee the runner must not have weakened. Starting stages is a
    different thing from transmitting to a company: only the two wave stages
    transmit, and only from the two wave routes."""
    from arpenteur.console.app import TRANSMITTING

    routes = {getattr(r, "path", "") for r in client.app.routes}
    assert not [p for p in routes
                if ("send" in p or "smtp" in p) and p not in TRANSMITTING]
    listed = {name for name, stage in runner_module.STAGES.items() if stage.listed}
    assert not listed & runner_module.TRANSMITTING_STAGES
    # `draft` is offered now, and it still posts nothing: its proposals are
    # written with sent_at null, and it is neither a transmitting stage nor on
    # the send lane.
    assert "draft" not in runner_module.TRANSMITTING_STAGES
    assert runner_module.STAGES["draft"].lane != "send"
    assert conn.execute("SELECT COUNT(*) AS n FROM intel.draft WHERE sent_at IS NOT NULL").fetchone()[0] == 0


# --- lanes, prechecks and the stages that transmit ----------------------------

import threading as _threading  # noqa: E402


def test_only_the_two_wave_stages_can_post() -> None:
    """Every stage that can reach an SMTP server is named, unlisted and checked first."""
    assert runner_module.TRANSMITTING_STAGES == {"campaign-test", "campaign-send"}
    for name in runner_module.TRANSMITTING_STAGES:
        stage = runner_module.STAGES[name]
        assert not stage.listed and stage.lane == "send" and stage.precheck is not None


def test_an_unlisted_stage_is_refused_by_the_generic_route(settings) -> None:
    with pytest.raises(ValueError, match="its own page"):
        Runner(settings).start("campaign-send", {"campaign": "X", "wave": "1",
                                                 "confirm": "X/1"})


def test_a_precheck_refusal_returns_before_any_thread(settings, conn) -> None:
    def refuse(_settings, _form):
        raise ValueError("not approved")

    runner = Runner(settings)
    runner_module.STAGES["_guarded"] = fake_stage(
        "_guarded", lambda *_a, **_k: {}, precheck=refuse, listed=False, lane="send")
    try:
        before = conn.execute("SELECT count(*) AS n FROM intel.run").fetchone()["n"]
        with pytest.raises(ValueError, match="not approved"):
            runner.start("_guarded", {}, internal=True)
        assert runner.running("send") is None
        assert conn.execute("SELECT count(*) AS n FROM intel.run").fetchone()["n"] == before
    finally:
        runner_module.STAGES.pop("_guarded", None)


def test_a_send_and_a_pipeline_run_hold_separate_locks(settings, conn) -> None:
    release = _threading.Event()

    def slow(_settings, _conn, *, log, **_kw):
        release.wait(5)
        return {}

    runner = Runner(settings)
    runner_module.STAGES["_pipe"] = fake_stage("_pipe", slow)
    runner_module.STAGES["_mail"] = fake_stage("_mail", slow, lane="send", listed=False)
    try:
        runner.start("_pipe", {})
        runner.start("_mail", {}, internal=True)
        assert runner.running() is not None and runner.running("send") is not None
    finally:
        release.set()
        assert wait_for(lambda: runner.running() is None and runner.running("send") is None)
        runner_module.STAGES.pop("_pipe", None)
        runner_module.STAGES.pop("_mail", None)


def test_two_sends_cannot_overlap(settings, conn) -> None:
    release = _threading.Event()

    def slow(_settings, _conn, *, log, **_kw):
        release.wait(5)
        return {}

    runner = Runner(settings)
    runner_module.STAGES["_mail"] = fake_stage("_mail", slow, lane="send", listed=False)
    try:
        runner.start("_mail", {}, internal=True)
        with pytest.raises(RuntimeError, match="still going"):
            runner.start("_mail", {}, internal=True)
    finally:
        release.set()
        assert wait_for(lambda: runner.running("send") is None)
        runner_module.STAGES.pop("_mail", None)


def test_a_failed_statement_is_rolled_back_before_logging(settings, conn) -> None:
    """Without the rollback the log line fails too, and the reason is lost."""
    def broken(_settings, stage_conn, *, log, **_kw):
        stage_conn.execute("SELECT * FROM no_such_table")
        return {}

    runner = Runner(settings)
    runner_module.STAGES["_broken"] = fake_stage("_broken", broken)
    try:
        run_id = runner.start("_broken", {})
        assert wait_for(lambda: runner.running() is None)
        lines = [r["line"] for r in conn.execute(
            "SELECT text AS line FROM intel.run_line WHERE run_id = %s ORDER BY seq",
            (run_id,))]
        assert any(line.startswith("! ") and "no_such_table" in line for line in lines)
    finally:
        runner_module.STAGES.pop("_broken", None)


# --- stopping a run -----------------------------------------------------------


def blocked(release: threading.Event):
    """A stage that works until it is let go, saying so once."""
    def work(_settings, _conn, *, log, **_kw):
        log("working")
        release.wait(5)
        return {}
    return work


def looping(ticks: list[int], release: threading.Event):
    """A stage that logs on every turn of a loop, like a sweep does."""
    def work(_settings, _conn, *, log, **_kw):
        while not release.is_set():
            ticks.append(1)
            log("tick")
            time.sleep(0.01)
        return {"ticks": len(ticks)}
    return work


def test_a_running_stage_stops_at_its_next_log_line(settings, conn) -> None:
    release = threading.Event()
    ticks: list[int] = []
    runner = Runner(settings)
    runner_module.STAGES["_loop"] = fake_stage("_loop", looping(ticks, release))
    try:
        run_id = runner.start("_loop", {})
        assert wait_for(lambda: len(ticks) >= 2)
        assert runner.cancel(run_id) is True
        assert wait_for(lambda: runner.running() is None)
        reached = len(ticks)
        time.sleep(0.1)
        assert len(ticks) == reached, "the stage kept working after it was stopped"
        row = db.run(conn, run_id)
        assert row["finished_at"] and row["error"] == runner_module.CANCELLED
        lines = [r["text"] for r in db.lines_after(conn, run_id, 0)]
        assert lines[-1].startswith("cancelled")
        assert not any(line.startswith("! ") for line in lines), "a stop is not a failure"
        assert run_id not in runner.cancelled, "the flag outlived the run"
    finally:
        release.set()
        runner_module.STAGES.pop("_loop", None)


def test_cancelling_a_run_nothing_is_running_does_nothing(settings, conn) -> None:
    runner = Runner(settings)
    assert runner.cancel(999) is False
    assert runner.cancelled == set()

    runner_module.STAGES["_quick"] = fake_stage("_quick", lambda *_a, **_k: {})
    try:
        run_id = runner.start("_quick", {})
        assert wait_for(lambda: runner.running() is None)
        assert runner.cancel(run_id) is False
        assert runner.cancelled == set()
        assert db.run(conn, run_id)["error"] is None
    finally:
        runner_module.STAGES.pop("_quick", None)


def test_what_a_stopped_stage_wrote_before_the_stop_stays(settings, conn) -> None:
    release = threading.Event()
    wrote = threading.Event()

    def writes_then_waits(_settings, stage_conn, *, log, **_kw):
        db.upsert_company(
            stage_conn,
            {"uid": "CHE9", "name": "Écrit Sàrl", "canton": "GE", "method": "zefix"},
        )
        log("wrote one")
        wrote.set()
        release.wait(5)
        log("about to write another")
        return {}

    runner = Runner(settings)
    runner_module.STAGES["_writer"] = fake_stage("_writer", writes_then_waits)
    try:
        run_id = runner.start("_writer", {})
        assert wrote.wait(5)
        runner.cancel(run_id)
        release.set()
        assert wait_for(lambda: runner.running() is None)
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM core.company WHERE uid = 'CHE9'"
        ).fetchone()["n"] == 1
    finally:
        release.set()
        runner_module.STAGES.pop("_writer", None)


def test_a_stage_that_swallows_exceptions_is_still_stopped(settings, conn) -> None:
    """The per-company `except Exception` in enrich and match would turn a
    stop into one failed company and carry on with the rest of the list."""
    release = threading.Event()
    ticks: list[int] = []

    def swallows(_settings, _conn, *, log, **_kw):
        while not release.is_set():
            try:
                ticks.append(1)
                log("tick")
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.01)
        return {}

    runner = Runner(settings)
    runner_module.STAGES["_swallow"] = fake_stage("_swallow", swallows)
    try:
        run_id = runner.start("_swallow", {})
        assert wait_for(lambda: len(ticks) >= 2)
        runner.cancel(run_id)
        assert wait_for(lambda: runner.running() is None)
        assert db.run(conn, run_id)["error"] == runner_module.CANCELLED
    finally:
        release.set()
        runner_module.STAGES.pop("_swallow", None)


def test_a_wave_cannot_be_stopped_between_a_post_and_its_record(settings, conn) -> None:
    """The send loop posts a message and logs it in the next breath; a stop
    raised by that log would leave a posted message not marked as sent."""
    for name in runner_module.TRANSMITTING_STAGES:
        assert runner_module.STAGES[name].cancellable is False

    release = threading.Event()
    runner = Runner(settings)
    runner_module.STAGES["_post"] = fake_stage(
        "_post", blocked(release), lane="send", listed=False, cancellable=False)
    try:
        run_id = runner.start("_post", {}, internal=True)
        assert runner.cancel(run_id) is False
        assert runner.cancelled == set()
        assert runner.can_cancel(run_id) is False
    finally:
        release.set()
        assert wait_for(lambda: runner.running("send") is None)
        runner_module.STAGES.pop("_post", None)


def test_a_single_model_call_offers_no_stop(settings) -> None:
    """Advice and preparation are one call that cannot be interrupted, so a
    button promising to stop them would promise nothing."""
    assert runner_module.STAGES["advise"].cancellable is False
    assert runner_module.STAGES["prep"].cancellable is False


def test_the_route_stops_a_live_run_and_the_page_says_cancelled(client, conn) -> None:
    release = threading.Event()
    ticks: list[int] = []
    runner_module.STAGES["_loop"] = fake_stage("_loop", looping(ticks, release))
    runner = client.app.state.runner
    try:
        run_id = runner.start("_loop", {})
        assert wait_for(lambda: len(ticks) >= 1)
        reply = client.post(f"/runs/{run_id}/cancel", follow_redirects=False)
        assert reply.status_code == 303
        assert reply.headers["location"].startswith(f"/runs/{run_id}?note=")
        assert wait_for(lambda: runner.running() is None)
        page = client.get(f"/runs/{run_id}").text
        assert "state-cancelled" in page and ">cancelled<" in page
        assert f"/runs/{run_id}/cancel" not in page, "nothing left to stop"
        assert "state-cancelled" in client.get("/runs").text
    finally:
        release.set()
        runner_module.STAGES.pop("_loop", None)


def test_the_run_page_offers_the_stop_while_live_and_names_the_stage(client, conn) -> None:
    release = threading.Event()
    runner_module.STAGES["_hold"] = fake_stage("_hold", blocked(release))
    runner = client.app.state.runner
    try:
        run_id = runner.start("_hold", {})
        assert wait_for(lambda: runner.is_live(run_id))
        page = client.get(f"/runs/{run_id}").text
        assert f'action="/runs/{run_id}/cancel"' in page
        assert 'data-confirm="Stop the _hold run? What it has already written stays."' in page
        assert "Are you sure" not in page
    finally:
        release.set()
        assert wait_for(lambda: runner.running() is None)
        runner_module.STAGES.pop("_hold", None)


def test_a_stop_that_is_asked_for_but_not_yet_reached_says_so(client, conn) -> None:
    """A stage waiting on a slow company reaches its next log line in minutes,
    and a page that still offered the button would invite a second press."""
    gate = threading.Event()

    def stalls(_settings, _conn, *, log, **_kw):
        log("start")
        gate.wait(5)
        log("after")
        return {}

    runner_module.STAGES["_stall"] = fake_stage("_stall", stalls)
    runner = client.app.state.runner
    try:
        run_id = runner.start("_stall", {})
        assert wait_for(lambda: bool(db.lines_after(conn, run_id, 0)))
        client.post(f"/runs/{run_id}/cancel", follow_redirects=False)
        page = client.get(f"/runs/{run_id}").text
        assert "Cancel requested" in page
        assert f'action="/runs/{run_id}/cancel"' not in page
        gate.set()
        assert wait_for(lambda: runner.running() is None)
        assert db.run(conn, run_id)["error"] == runner_module.CANCELLED
    finally:
        gate.set()
        runner_module.STAGES.pop("_stall", None)


def test_stopping_a_finished_run_is_refused_with_a_reason(client, conn) -> None:
    run_id = db.start_run(conn, "profile", {}, started_by="console")
    db.finish_run(conn, run_id, {"ok": True})
    reply = client.post(f"/runs/{run_id}/cancel", follow_redirects=False)
    assert reply.status_code == 303
    assert reply.headers["location"].startswith(f"/runs/{run_id}?error=")
    assert db.run(conn, run_id)["error"] is None
    assert client.post("/runs/999/cancel").status_code == 404


def test_a_cancelled_run_is_its_own_state_in_the_history(client, conn) -> None:
    run_id = db.start_run(conn, "profile", {}, started_by="console")
    db.finish_run(conn, run_id, error=runner_module.CANCELLED)
    failed = db.start_run(conn, "profile", {}, started_by="console")
    db.finish_run(conn, failed, error="the model said no")
    body = client.get("/runs").text
    assert "<td class='state-cancelled'>cancelled</td>" in body
    assert "<td class='state-error'>error</td>" in body


# --- the run page header is live ----------------------------------------------


def test_the_run_page_header_is_an_element_the_poll_can_replace(client, conn) -> None:
    run_id = db.start_run(conn, "profile", {"limit": "50"}, started_by="console")
    conn.commit()
    page = client.get(f"/runs/{run_id}").text
    assert page.count("id='run-state'") == 1
    assert "hx-swap-oob" not in page.split("id='run-state'")[1].split(">")[0]


def test_the_poll_reply_carries_the_current_state_out_of_band(client, conn) -> None:
    run_id = db.start_run(conn, "profile", {}, started_by="cli")
    db.append_line(conn, run_id, "one")
    conn.commit()
    running = client.get(f"/runs/{run_id}/lines").text
    assert "id='run-state'" in running and "hx-swap-oob='true'" in running
    assert "state-running" in running

    db.finish_run(conn, run_id, error=runner_module.CANCELLED)
    done = client.get(f"/runs/{run_id}/lines").text
    assert "hx-swap-oob='true'" in done and "state-cancelled" in done
    assert "state-running" not in done


@pytest.mark.parametrize(
    "error, finished, said",
    [
        ("the registry said no", True, "The run failed."),
        ("cancelled: stopped from the run page", True, "The run was cancelled."),
        (None, True, "The run has finished."),
        (None, False, "Running."),
    ],
)
def test_the_status_sentence_is_honest(client, conn, error, finished, said) -> None:
    """It is the one thing a screen reader announces, so it must not say the
    run finished when it failed."""
    run_id = db.start_run(conn, "profile", {}, started_by="cli")
    if finished:
        db.finish_run(conn, run_id, error=error)
    conn.commit()
    body = client.get(f"/runs/{run_id}/lines").text
    assert f'aria-live="polite">{said}</span>' in body


def test_the_run_page_shows_its_arguments_as_fields_not_json(client, conn) -> None:
    run_id = db.start_run(
        conn, "enrich",
        {"limit": "50", "canton": "VD", "dry_run": "1", "uids": "", "refresh": ""},
        started_by="console",
    )
    conn.commit()
    page = client.get(f"/runs/{run_id}").text
    assert "limit 50, canton VD, dry run" in page
    assert '{"' not in page and "&quot;limit&quot;" not in page


def test_the_run_page_leads_back_to_the_history(client, conn) -> None:
    run_id = db.start_run(conn, "profile", {}, started_by="console")
    conn.commit()
    assert "href='/runs'" in client.get(f"/runs/{run_id}").text


def test_the_run_page_leads_back_to_the_client_it_was_about(client, conn) -> None:
    advice = db.start_run(conn, "advise", {"client_id": 7, "dry_run": ""}, started_by="console")
    prep = db.start_run(conn, "prep", {"client_id": "7"}, started_by="console")
    other = db.start_run(conn, "profile", {"client_id": 7}, started_by="console")
    conn.commit()
    assert "href='/client/7/dossier'" in client.get(f"/runs/{advice}").text
    assert "href='/client/7/prep'" in client.get(f"/runs/{prep}").text
    assert "href='/client/7" not in client.get(f"/runs/{other}").text


def test_an_exception_with_no_message_is_still_a_failure(settings, conn) -> None:
    """An empty error reads as none, and the history then shows the run as done."""
    def bare(*_a, **_k):
        raise RuntimeError()

    runner = Runner(settings)
    runner_module.STAGES["_bare"] = fake_stage("_bare", bare)
    try:
        run_id = runner.start("_bare", {})
        assert wait_for(lambda: runner.running() is None)
        assert db.run(conn, run_id)["error"] == "RuntimeError"
    finally:
        runner_module.STAGES.pop("_bare", None)


# --- the operations page checks every lane ------------------------------------


def test_live_reports_the_run_each_lane_holds(settings, conn) -> None:
    release = threading.Event()
    runner = Runner(settings)
    runner_module.STAGES["_pipe"] = fake_stage("_pipe", blocked(release))
    runner_module.STAGES["_mail"] = fake_stage("_mail", blocked(release), lane="send", listed=False)
    try:
        assert runner.live() == {}
        pipe = runner.start("_pipe", {})
        mail = runner.start("_mail", {}, internal=True)
        assert runner.live() == {"pipeline": pipe, "send": mail}
        # `running` still answers for one lane, which is what the stages' own
        # callers ask.
        assert runner.running() == pipe and runner.running("send") == mail
    finally:
        release.set()
        assert wait_for(lambda: runner.live() == {})
        runner_module.STAGES.pop("_pipe", None)
        runner_module.STAGES.pop("_mail", None)


def test_operations_names_a_send_that_is_running(client, conn) -> None:
    """`running()` reports the pipeline lane only, so the banner used to stay
    silent while a wave was going out."""
    release = threading.Event()
    runner_module.STAGES["_mail"] = fake_stage("_mail", blocked(release), lane="send", listed=False)
    runner = client.app.state.runner
    try:
        run_id = runner.start("_mail", {}, internal=True)
        body = client.get("/pipeline").text
        assert f"href='/runs/{run_id}'" in body
        # The pipeline lane is free, so its cards stay pressable.
        assert "<button disabled>" not in body
        assert "One stage at a time" not in body
    finally:
        release.set()
        assert wait_for(lambda: runner.live() == {})
        runner_module.STAGES.pop("_mail", None)


def test_operations_names_every_running_run_and_disables_only_its_lane(client, conn) -> None:
    release = threading.Event()
    runner_module.STAGES["_pipe"] = fake_stage("_pipe", blocked(release))
    runner_module.STAGES["_mail"] = fake_stage("_mail", blocked(release), lane="send", listed=False)
    runner = client.app.state.runner
    try:
        pipe = runner.start("_pipe", {})
        mail = runner.start("_mail", {}, internal=True)
        body = client.get("/pipeline").text
        assert f"href='/runs/{pipe}'" in body and f"href='/runs/{mail}'" in body
        assert "<button disabled>" in body
    finally:
        release.set()
        assert wait_for(lambda: runner.live() == {})
        runner_module.STAGES.pop("_pipe", None)
        runner_module.STAGES.pop("_mail", None)


def test_operations_does_not_draw_a_banner_from_note(client) -> None:
    """The shell draws `?note=` on every page; the page drawing it too showed
    every refusal twice."""
    body = client.get("/pipeline?note=Seen%20once").text
    assert body.count("Seen once") == 1


def test_a_refused_start_comes_back_as_an_error_not_a_note(client) -> None:
    reply = client.post("/run/enrich", data={}, follow_redirects=False)
    location = reply.headers["location"]
    assert location.startswith("/pipeline?error=") and "note=" not in location
    page = client.post("/run/enrich", data={}, follow_redirects=True).text
    assert 'class="banner banner-bad"' in page


# --- the stages that were only on the command line ----------------------------


def test_every_stage_the_page_could_offer_is_offered() -> None:
    """`bounces` gates a campaign's next wave and was defined without being
    offered; the registry should not hold a listed stage the order omits."""
    listed = {
        name for name, stage in runner_module.STAGES.items()
        if stage.listed and not name.startswith("_")
    }
    assert listed == set(runner_module.ORDER)
    assert all(name in runner_module.STAGES for name in runner_module.ORDER)


def test_the_new_stages_sit_where_they_belong_in_the_flow() -> None:
    order = list(runner_module.ORDER)
    assert order.index("verify") > order.index("reach")
    assert order.index("draft") > order.index("match")
    assert order.index("draft") > order.index("verify")
    assert order.index("bounces") > order.index("mail")


def test_the_new_cards_are_on_the_operations_page(client) -> None:
    body = client.get("/pipeline").text
    for name in ("verify", "draft", "bounces"):
        assert f"action='/run/{name}'" in body


def test_no_card_quotes_a_command_line() -> None:
    """The person reading a card came to the browser so as not to type."""
    for name in runner_module.ORDER:
        stage = runner_module.STAGES[name]
        text = " ".join([stage.what, stage.needs, stage.produces, stage.cost,
                         stage.button_does,
                         *(f.help + f.where for f in stage.fields)])
        assert "arpenteur " not in text and " --" not in text and "`" not in text, name


def test_the_draft_card_says_what_it_needs_in_the_consoles_words() -> None:
    stage = runner_module.STAGES["draft"]
    assert stage.needs_limit is True
    assert stage.listed and stage.lane == "pipeline" and stage.module == "scout"
    for words in ("address", "message brief", "model"):
        assert words in stage.needs, words
    assert "Drafts" in stage.produces
    assert "none is sent" in stage.produces.lower() or "not sent" in stage.produces.lower()
    assert "money" in stage.cost.lower() or "franc" in stage.cost.lower()
    names = [f.name for f in stage.fields]
    assert names == ["segment", "canton", "limit", "confirmed", "dry_run"]


def test_the_batch_box_says_what_it_does_and_no_more() -> None:
    """`confirmed` silences the cap's reminder; it does not raise the cap."""
    box = next(f for f in runner_module.STAGES["draft"].fields if f.name == "confirmed")
    assert box.kind == "bool" and not box.default
    assert "LCD" in box.help and "does not raise" in box.help


def test_a_draft_run_hands_the_stage_what_the_form_chose(settings, conn, monkeypatch) -> None:
    seen: dict = {}

    def fake_run(_settings, _conn, **kw):
        seen.update(kw)
        return {"drafted": 0}

    monkeypatch.setattr(draft, "run", fake_run)
    runner = Runner(settings)
    run_id = runner.start("draft", {
        "segment": "sme-owner", "canton": "GE", "limit": "10", "confirmed": "1",
    })
    assert wait_for(lambda: runner.running() is None)
    assert db.run(conn, run_id)["error"] is None
    assert seen["segments"] == ["sme-owner"] and seen["cantons"] == ["GE"]
    assert seen["limit"] == 10 and seen["confirmed"] is True and seen["dry_run"] is False
    assert seen["desk_parts"] == mailcheck.desk_parts() and "info" in seen["desk_parts"]
    assert callable(seen["log"])


def test_a_draft_run_without_a_size_is_refused_unless_it_is_dry() -> None:
    stage = runner_module.STAGES["draft"]
    with pytest.raises(ValueError, match="limit"):
        check(stage, {"dry_run": False})
    check(stage, {"dry_run": True})


def test_the_desk_vocabulary_lives_with_the_address_checks() -> None:
    parts = mailcheck.desk_parts()
    assert parts == tuple(sorted(set(parts)))
    assert {"info", "verwaltung", "segreteria", "administration"} <= set(parts)
    from arpenteur import cli

    assert not hasattr(cli, "_desk_parts")


def test_the_address_check_runs_from_the_console(client, conn) -> None:
    company_id = conn.execute("SELECT id FROM core.company").fetchone()["id"]
    db.save_contact(conn, company_id=company_id, kind="email",
                    value="info@verifie.example", source_url="https://verifie.example",
                    method="published")
    conn.commit()
    reply = client.post("/run/verify", data={"dry_run": "1"}, follow_redirects=False)
    assert reply.status_code == 303 and reply.headers["location"].startswith("/runs/")
    runner = client.app.state.runner
    assert wait_for(lambda: runner.running() is None)
    run_id = int(reply.headers["location"].rsplit("/", 1)[1])
    row = db.run(conn, run_id)
    assert row["error"] is None and row["stats_json"]["domains"] == 1
    assert any("verifie.example" in r["text"] for r in db.lines_after(conn, run_id, 0))


def test_the_verify_card_counts_domains_it_has_and_has_not_checked(client, conn) -> None:
    company_id = conn.execute("SELECT id FROM core.company").fetchone()["id"]
    for address in ("a@un.example", "b@un.example", "c@deux.example"):
        db.save_contact(conn, company_id=company_id, kind="email", value=address,
                        source_url="https://x.example", method="published")
    conn.commit()
    stage = runner_module.STAGES["verify"]
    assert runner_module.status_of(conn, stage) == {"done": 0, "waiting": 2}
    scout_store.save_domain_verdict(conn, "un.example", status="deliverable", note="MX x")
    conn.commit()
    assert runner_module.status_of(conn, stage) == {"done": 1, "waiting": 1}


def test_a_card_with_no_queue_does_not_claim_nothing_is_waiting(client) -> None:
    """`bounces` and `draft` have no honest queue to count, and the card used
    to say 'Nothing is waiting' for any stage whose count was zero."""
    body = client.get("/pipeline").text
    card = body.split("action='/run/bounces'")[1].split("</form>")[0]
    assert "Nothing is waiting" not in card
    assert "0</b><span>done" not in card


def test_a_dry_run_of_the_bounce_read_does_not_open_the_wave_gate(conn) -> None:
    """A campaign's next wave waits for a bounce read after the last send. A
    dry run reads the mailbox and records nothing, so counting it as that read
    would let the default-ticked box clear the gate."""
    for args in ({"dry_run": "1"}, {"dry_run": True}, {"dry_run": "on"}):
        run_id = db.start_run(conn, "bounces", args, started_by="console")
        db.finish_run(conn, run_id, {})
    assert scout_store.last_run(conn, "bounces") is None
    real = db.start_run(conn, "bounces", {"dry_run": ""}, started_by="console")
    db.finish_run(conn, real, {})
    cli_real = db.start_run(conn, "bounces", {"dry_run": False}, started_by="cli")
    db.finish_run(conn, cli_real, {})
    assert scout_store.last_run(conn, "bounces")["id"] == cli_real


# --- history ------------------------------------------------------------------


def _finished_runs(conn, count: int) -> list[int]:
    ids = []
    for _ in range(count):
        run_id = db.start_run(conn, "profile", {}, started_by="console")
        db.finish_run(conn, run_id, {})
        ids.append(run_id)
    return ids


def test_runs_page_back_through_the_history(conn) -> None:
    ids = _finished_runs(conn, 45)
    newest = [r["id"] for r in db.runs(conn, limit=40)]
    assert newest == sorted(ids, reverse=True)[:40]
    older = [r["id"] for r in db.runs(conn, limit=40, before=newest[-1])]
    assert older == sorted(ids, reverse=True)[40:]
    assert db.runs(conn, limit=40, before=min(ids)) == []


def test_a_full_page_offers_the_older_runs(client, conn) -> None:
    ids = _finished_runs(conn, 45)
    last_shown = sorted(ids, reverse=True)[39]
    body = client.get("/runs").text
    assert f"href='/runs?before={last_shown}'" in body and "Older runs" in body
    assert f"href='/runs/{min(ids)}'" not in body

    older = client.get(f"/runs?before={last_shown}").text
    assert f"href='/runs/{min(ids)}'" in older
    assert "Older runs" not in older, "a short page has nothing older"
    assert "href='/runs'" in older and "Newest runs" in older


def test_a_short_history_offers_no_older_link(client, conn) -> None:
    _finished_runs(conn, 3)
    assert "Older runs" not in client.get("/runs").text


def test_the_poll_keeps_the_page_it_was_drawn_for(client, conn) -> None:
    ids = _finished_runs(conn, 45)
    last_shown = sorted(ids, reverse=True)[39]
    rows = client.get(f"/runs/rows?before={last_shown}").text
    assert f"href='/runs/{min(ids)}'" in rows and f"href='/runs/{max(ids)}'" not in rows


def test_the_history_polls_only_while_a_run_is_unfinished(client, conn) -> None:
    _finished_runs(conn, 2)
    for url in ("/runs", "/runs/rows"):
        body = client.get(url).text
        assert "every 3s" not in body and 'hx-get="/runs/rows' not in body, url
    db.start_run(conn, "profile", {}, started_by="console")
    conn.commit()
    for url in ("/runs", "/runs/rows"):
        body = client.get(url).text
        assert 'hx-trigger="every 3s"' in body and 'hx-get="/runs/rows"' in body, url


def test_the_history_page_is_called_history(client) -> None:
    assert "<h1>History</h1>" in client.get("/runs").text


# --- what the card says -------------------------------------------------------


def test_the_scoring_stage_says_scoring_brief(client) -> None:
    """The campaign is email sent in waves; the brief `match` scores against is
    the scoring brief."""
    stage = runner_module.STAGES["match"]
    text = " ".join([stage.title, stage.what, stage.needs, stage.button_does,
                     *(f.help for f in stage.fields)])
    assert "scoring brief" in text and "campaign brief" not in text
    assert "campaign.md" not in text


def test_segments_are_defined_on_the_settings_page() -> None:
    assert "Settings" in runner_module.SEGMENT.help
    assert "arpenteur.toml" not in runner_module.SEGMENT.help


# --- advice and preparation, which are one client's model call ----------------


def test_advice_and_preparation_are_startable_only_from_their_own_pages(client) -> None:
    for name in ("advise", "prep"):
        stage = runner_module.STAGES[name]
        assert not stage.listed and stage.lane == "dossier" and stage.module == "clients"
        assert [f.name for f in stage.fields] == ["client_id", "dry_run"]
        reply = client.post(f"/run/{name}", data={"client_id": "1"}, follow_redirects=False)
        assert reply.headers["location"].startswith("/pipeline?error=")
        assert name not in runner_module.ORDER


def test_advice_for_a_client_nobody_holds_is_refused_before_a_run(settings, conn) -> None:
    runner = Runner(settings)
    before = conn.execute("SELECT count(*) AS n FROM intel.run").fetchone()["n"]
    with pytest.raises(ValueError, match="No client"):
        runner.start("advise", {"client_id": 999}, internal=True)
    with pytest.raises(ValueError, match="Name the client"):
        runner.start("prep", {}, internal=True)
    assert conn.execute("SELECT count(*) AS n FROM intel.run").fetchone()["n"] == before
