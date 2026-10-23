"""The console: read the survey, correct it, and keep notes on it.

Server-rendered HTML with HTMX. Every route reads or writes through the same
`db` helpers the stages use, so a correction made here is the same row a
re-run has to respect, and there is no second implementation of what a manual
label means.

**It sends in one place, and only there.** Two routes transmit: the test and
the send of a campaign wave, listed in `TRANSMITTING` below. Both start a
registered stage through the runner, which posts through `stages/send.py` with
every guard that stage applies. A wave is sent only after it was rendered,
test-sent to the operator, approved by a person who says they read the tests,
and confirmed by typing its code, and only when the previous wave's bounces
have been read and stayed under the campaign's ceiling. It is capped by
`per_run` and the daily quota, paused between messages, and refused for any
address that asked to be left alone, bounced, no longer resolves, or already
received a message, and for a firm or domain written to inside the cooldown.

Swiss law (UWG art. 3 al. 1 let. o) makes mass advertising by email without
consent unfair. These controls do not settle consent, which stays the
operator's decision; they keep every message reviewed, identified, refusable
and slow. Until 13.10.2026 this console had no route that sent at all, and
the reason it now has two is that the operator asked for a campaign manager.

**It runs stages, under the guards that made the command line safe.** One at a
time, the paid stages refusing to start without a declared size, and a dry run
offered first. The objection was never to the button but to spending real
money from a page that looks like a filter, so the guards are enforced on the
server in `runner.py` rather than by a disabled field. Every stage's own cost
refusal is untouched and still applies.
"""

from __future__ import annotations

from contextvars import ContextVar
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qs, quote

import logging

from fastapi import FastAPI, Form, Request
from fastapi import Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import (
    HTMLResponse, PlainTextResponse, RedirectResponse,
)

from .. import db, health
from ..kernel import modules as module_registry
from ..kernel.db import Connection
from ..modules.clients import store as clients_store
from ..modules.sales import money as money_lib
from ..modules.sales import store as sales_store
from ..modules.scout import store as scout_store
from .. import config as config_lib
from ..config import SECTION_KEYS, Settings, env_state, load_settings, project_root
from ..modules.scout import scoring
from ..stages import (
    match,
)
from . import assets, ui
from . import score_page as score_view
from . import home_page as home_view
from . import help_page as help_view
from . import render, routes
from . import settings_page as settings_view_page
from .context import Console
from .runner import ORDER, STAGES, Runner, close_orphans, options_for, status_of

# The only routes that can hand anything to an SMTP server. A test asserts that
# no other route can, so adding a third means adding it here, in sight.
TRANSMITTING = frozenset({
    "/campaign/{campaign_id}/wave/{number}/test",
    "/campaign/{campaign_id}/wave/{number}/send",
})


# What a redirect asked the next page to say, read from its query string by
# the middleware below and drawn by the shell. A context variable rather than
# an argument: it reaches `at()` from every route without each of the hundred
# and forty routes having to take the request and pass it on.
_FLASH: ContextVar[tuple[str, str]] = ContextVar("arpenteur_flash", default=("", ""))


class _Flash:
    """Reads `?note=` and `?error=` once per request, for the shell to draw."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        args = parse_qs(scope.get("query_string", b"").decode("utf-8", "replace"))
        token = _FLASH.set((args.get("note", [""])[0], args.get("error", [""])[0]))
        try:
            await self.app(scope, receive, send)
        finally:
            _FLASH.reset(token)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Built by a factory so tests can point it at a temporary database."""
    settings = settings or load_settings()
    app = FastAPI(title="arpenteur", docs_url=None, redoc_url=None)
    app.add_middleware(_Flash)
    app.state.settings = settings
    # One runner per app, holding the one-at-a-time lock. On the app rather
    # than at module scope so two consoles in one test process do not share a
    # lock and refuse each other's runs.
    runner = Runner(settings)
    app.state.runner = runner

    # A run is a thread inside this process, so a previous console that was
    # stopped mid-run left a row claiming to be running. Settled at startup,
    # before any page can show it or poll it.
    # The modules this deployment actually has, resolved once. The header is
    # built from it, so turning `sales` off in arpenteur.toml removes Quotes
    # and Invoices from every page rather than leaving links to a schema that
    # is no longer there; and only their schemas are migrated.
    registry = module_registry.load(
        getattr(settings, "modules", None) or module_registry.DEFAULT
    )
    app.state.registry = registry
    enabled = set(registry.by_name)

    def connect() -> Connection:
        # One connection per request: a long-lived shared connection would
        # serialise the console behind whatever a stage is writing.
        return db.open_db(settings.database_url, tuple(registry.by_name))

    _startup = connect()
    try:
        close_orphans(_startup)
    finally:
        _startup.close()

    def at(here: str = "") -> render.Chrome:
        """The header, as seen from one section of the console, and what the
        page was asked to say first."""
        note, error = _FLASH.get()
        return render.Chrome.of(
            registry, settings.territory.describe(), here,
        ).with_flash(note, error)

    def shell(body: str, here: str = "", status: int = 200, title: str = "") -> HTMLResponse:
        # The score breakdown's own rules travel with the shell rather than
        # with the panel: a <style> block inside the body would have to survive
        # an f-string, and the doubled braces that takes reach the browser and
        # kill the whole stylesheet. See `render.page`.
        return HTMLResponse(
            render.page(title, body, at(here), css=score_view.STYLES),
            status_code=status,
        )

    def _back(url: str, *, note: str = "", error: str = "") -> RedirectResponse:
        """Redirect to a page that will say what happened.

        A refusal that redirects to the unchanged page is indistinguishable
        from a button that does nothing; the page reads `error` or `note`
        from the query string and shows it as a banner.
        """
        if error:
            url += ("&" if "?" in url else "?") + "error=" + quote(error)
        elif note:
            url += ("&" if "?" in url else "?") + "note=" + quote(note)
        return RedirectResponse(url, status_code=303)

    # What a module's route file closes over. `settings` on it is the live
    # copy: `_reload` below rebinds it, and every route reads it at request
    # time, so a section saved on the Settings page reaches the next run.
    ctx = Console(
        settings=settings, runner=runner, registry=registry,
        connect=connect, at=at, shell=shell, back=_back,
    )
    app.state.console = ctx

    @app.exception_handler(StarletteHTTPException)
    async def _not_here(request: Request, exc: StarletteHTTPException) -> Response:
        """A path nothing answers to, inside the shell like every other page.

        FastAPI's own reply is two bare words of JSON. The routes that find
        no record already answer inside the shell; this covers the address
        that matches no route at all, which is what a mistyped or outdated
        link produces.
        """
        if exc.status_code == 404:
            return shell(render.not_found(), status=404)
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _crashed(request: Request, exc: Exception) -> HTMLResponse:
        """A fault, inside the shell rather than FastAPI's bare two words.

        The traceback goes to the log, where it can be read in full; the page
        names the fault and says where to look, so a 500 is never mistaken
        for a button that does nothing.
        """
        logging.getLogger("arpenteur.console").exception(
            "%s %s failed", request.method, request.url.path
        )
        body = (
            "<h1>Something went wrong</h1>"
            f"<p class='banner banner-bad'>{ui.esc(type(exc).__name__)}: {ui.esc(str(exc))}</p>"
            "<p>The request stopped here. The console's log holds the full "
            "trace, and <a href='/runs'>History</a> shows what the stages did.</p>"
        )
        return HTMLResponse(render.page("Error", body, at(request.url.path)),
                            status_code=500)

    # --- home -----------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def home() -> HTMLResponse:
        """Where things stand: the setup checks, the counts, the last runs."""
        conn = connect()
        try:
            tiles: list[tuple[str, int, str]] = []

            def tile(one: str, many: str, value: int, href: str) -> None:
                tiles.append((one if value == 1 else many, value, href))

            survey = scout_store.home_counts(conn)
            tile("company", "companies", survey["companies"], "/companies")
            tile("on the board", "on the board", survey["on_board"], "/board")
            body = home_view.home_page(
                checks=health.checks(settings, conn), tiles=tiles,
                runs=db.runs(conn, limit=3), territory=settings.territory.describe(),
                more=registry.fill("home.tiles", conn),
            )
            return HTMLResponse(home_view.shell(body, at("/")))
        finally:
            conn.close()

    @app.get("/doctor", response_class=HTMLResponse)
    def doctor() -> HTMLResponse:
        """The full check, live: it signs in and opens a session, sending nothing."""
        conn = connect()
        try:
            found = health.checks(_reload(), conn, live=True)
        finally:
            conn.close()
        return HTMLResponse(home_view.shell(home_view.doctor_page(found), at("/")))

    # --- help -----------------------------------------------------------------

    docs_root = project_root() / "docs"

    @app.get("/help", response_class=HTMLResponse)
    @app.get("/help/{path:path}", response_class=HTMLResponse)
    def help_doc(path: str = "index") -> HTMLResponse:
        """A documentation page, rendered inside the shell."""
        body = help_view.render_doc(docs_root, path)
        if body is None:
            return HTMLResponse(
                help_view.shell(help_view.missing(path), at("/help")), status_code=404
            )
        return HTMLResponse(help_view.shell(body, at("/help")))

    # --- the map --------------------------------------------------------------
    #
    # --- watching the pipeline ------------------------------------------------

    @app.get("/runs", response_class=HTMLResponse)
    def runs(before: int | None = None) -> HTMLResponse:
        """The history, newest first, a page at a time.

        `before` is the id of the last run on the page before this one, so the
        next page is the runs under it.
        """
        conn = connect()
        try:
            rows = db.runs(conn, limit=render.RUNS_PAGE, before=before)
            return shell(render.runs_page(rows, before=before), "/runs")
        finally:
            conn.close()

    @app.get("/runs/rows", response_class=HTMLResponse)
    def runs_rows(before: int | None = None) -> HTMLResponse:
        """Polled every few seconds while a run on the page is unfinished.

        Polling rather than SSE. The stages are separate processes writing to
        the database, so there is no in-process event to stream; a three second
        poll of one indexed table is honest about that and cannot leave a
        hanging connection when a run dies. The table stops asking once every
        run it shows has ended.
        """
        conn = connect()
        try:
            rows = db.runs(conn, limit=render.RUNS_PAGE, before=before)
            return HTMLResponse(render.runs_table(rows, before=before))
        finally:
            conn.close()

    def _stop_of(run_id: int) -> str:
        """What the run page may say about stopping this run right now."""
        if runner.can_cancel(run_id):
            return "offer"
        return "asked" if runner.cancelling(run_id) else ""

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def one_run(run_id: int) -> HTMLResponse:
        """One run, read line by line as it happens."""
        conn = connect()
        try:
            run = db.run(conn, run_id)
            if run is None:
                return shell(render.not_found(), status=404)
            rows = db.lines_after(conn, run_id, 0)
            after = rows[-1]["seq"] if rows else 0
            stale = not run["finished_at"] and not runner.is_live(run_id)
            return shell(
                render.run_page(run, rows, after, stale=stale, stop=_stop_of(run_id)),
                "/runs",
            )
        finally:
            conn.close()

    @app.post("/runs/{run_id}/cancel")
    def cancel_run(run_id: int) -> Response:
        """Ask a live run to stop at its next log line.

        Only a run this console is running can be stopped: a terminal's run is
        another process, and the settle button below is how a person says one
        of those is over.
        """
        conn = connect()
        try:
            run = db.run(conn, run_id)
        finally:
            conn.close()
        if run is None:
            return shell(render.not_found("run"), status=404)
        if runner.cancel(run_id):
            return _back(
                f"/runs/{run_id}",
                note="Cancel requested. The stage stops at its next log line.",
            )
        if run["finished_at"]:
            return _back(f"/runs/{run_id}", error="That run has already ended.")
        return _back(
            f"/runs/{run_id}",
            error="This console is not running that stage, or the stage cannot "
                  "be stopped part way.",
        )

    @app.post("/runs/{run_id}/settle")
    def settle_run(run_id: int) -> RedirectResponse:
        """Mark a run that nothing is running any more as interrupted.

        The console settles its own orphans at startup; a run started from a
        terminal is left alone, because it may still be going in another
        window. When it is not, this is how a person says so.
        """
        conn = connect()
        try:
            run = db.run(conn, run_id)
            if run is None:
                return shell(render.not_found("run"), status=404)
            if not run["finished_at"] and not runner.is_live(run_id):
                scout_store.finish_run(
                    conn, run_id,
                    error="interrupted: marked by hand, nothing was running it",
                )
            return RedirectResponse(f"/runs/{run_id}", status_code=303)
        finally:
            conn.close()

    @app.get("/runs/{run_id}/lines", response_class=HTMLResponse)
    def run_lines(run_id: int, after: int = 0) -> HTMLResponse:
        """The lines a watcher has not seen, and a tail asking for the next.

        Only what is new: the tail carries the sequence it has reached, so a
        run emitting sixty thousand lines does not re-send them every poll.
        """
        conn = connect()
        try:
            # The run is read before the lines, never after: a stage writes its
            # last line and then finishes, so a run seen as finished has all its
            # lines to be read next. The other order can see the lines, miss
            # the last ones written, then see the run finished and stop the
            # poll: a log missing its own ending.
            run = db.run(conn, run_id)
            rows = db.lines_after(conn, run_id, after)
            reached = rows[-1]["seq"] if rows else after
            done = run is not None and run["finished_at"] is not None
            # The header rides along out of band, so it follows the run from
            # running to its end instead of staying as it was drawn.
            state = (
                render.run_state(
                    run, oob=True, stop=_stop_of(run_id),
                    stale=not done and not runner.is_live(run_id),
                )
                if run is not None else ""
            )
            return HTMLResponse(render.run_log(
                rows, run_id, done, reached,
                error=run["error"] if run is not None else None, state=state,
            ))
        finally:
            conn.close()

    # --- starting one ---------------------------------------------------------

    @app.get("/pipeline", response_class=HTMLResponse)
    def pipeline() -> HTMLResponse:
        """The operations page: every stage, where it stands, and how to run it.

        One page rather than a form page and a counts page. The number that
        makes a button meaningful is how much work is queued for it, and that
        was on the other page: "3 done, 41'238 waiting" says what to press
        next, where a bare button says nothing at all.

        It asks the runner for every lane, so a wave being sent is named here
        as well as a sweep. A refusal or a note comes in the query string and
        is drawn by the shell, as on every page.
        """
        conn = connect()
        try:
            # Only the stages whose module is installed. A geocode button on a
            # console with no `geo` schema is one that can only fail.
            stages = [STAGES[name] for name in ORDER if STAGES[name].module in enabled]
            live = runner.live()
            titles = {}
            for run_id in live.values():
                name = runner.stage_of(run_id)
                if name is not None:
                    titles[run_id] = STAGES[name].title if name in STAGES else name
            return shell(
                render.pipeline_page(
                    stages,
                    {stage.name: status_of(conn, stage) for stage in stages},
                    options_for(conn, settings),
                    live,
                    titles,
                ),
                "/pipeline",
            )
        finally:
            conn.close()

    @app.post("/run/{name}")
    async def start_run(name: str, request: Request) -> RedirectResponse:
        # Repeated keys are kept as lists. `dict()` over the form keeps only
        # the last value for a key, and a multi-select posts one value per
        # selection, so picking FR, GE and VD swept VD alone. `runner.coerce`
        # already tests for a list; it was never given one.
        posted = await request.form()
        form: dict[str, Any] = {}
        for key, value in posted.multi_items():
            if key not in form:
                form[key] = value
                continue
            held = form[key]
            form[key] = [*held, value] if isinstance(held, list) else [held, value]
        try:
            run_id = runner.start(name, form)
        except (ValueError, RuntimeError) as exc:
            # Back to the operations page with the reason, rather than an error
            # page: the answer is always to change something and try again.
            return _back("/pipeline", error=str(exc))
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    # --- settings -------------------------------------------------------------
    #
    # The territory, the segments, the signal brief and the campaign brief
    # decide what every stage does. Keeping them in files made the most
    # consequential decisions in the tool the ones you had to leave the tool to
    # make, and hid them: a survey containing no foundations because 0110 was
    # absent from a list is not a discoverable state.

    def _reload() -> Settings:
        """Settings re-read after a change, and made the console's own.

        Every route closes over the `settings` cell and the runner holds the
        same object, so rebinding both here is what makes a territory narrowed
        on this page the territory the next run sweeps, without a restart.
        """
        nonlocal settings
        settings = ctx.reload()
        app.state.settings = settings
        return settings

    @app.get("/settings", response_class=HTMLResponse)
    def settings_view() -> HTMLResponse:
        conn = connect()
        try:
            live = _reload()
            counts = scout_store.counts_by_legal_form(conn)
            brief_path = live.root / "campaign.md"
            text = brief_path.read_text(encoding="utf-8") if brief_path.is_file() else ""
            programmes = (
                [str(row["code"]) for row in clients_store.programmes(conn)]
                if "clients" in enabled else []
            )
            return HTMLResponse(
                settings_view_page.shell(
                    settings_view_page.settings_page(
                        live=live,
                        catalogue=db.catalogue(conn),
                        counts=counts,
                        campaign=text,
                        campaign_digest=match.brief_digest(text.strip()) if text.strip() else "—",
                        scored=scout_store.scored_count(conn),
                        history=db.setting_history(conn),
                        programmes=programmes,
                        env_set=env_state(),
                        enabled=sorted(enabled),
                    ),
                    at("/settings"),
                )
            )
        finally:
            conn.close()

    # --- the configuration tables, one form each ---------------------------------
    #
    # Each form posts its whole section and the route saves that section as
    # one overlay key, which `load_settings` merges over the file's table. A
    # field the environment decides is not on the form, so what the database
    # held for it stays as it was.

    def _section(conn: Connection, name: str) -> dict[str, Any]:
        held = db.settings_overlay(conn).get(name)
        return dict(held) if isinstance(held, dict) else {}

    def _save_section(conn: Connection, name: str, value: dict[str, Any], *,
                      note: str) -> RedirectResponse:
        db.save_setting(conn, name, value)
        conn.commit()
        _reload()
        return _back("/settings", note=note)

    def _whole(form: Any, key: str, label: str, *, low: int, high: int) -> int:
        """A whole number from a form, inside its bounds, or a refusal."""
        try:
            number = int(str(form.get(key, "")).strip())
        except ValueError:
            raise ValueError(f"{label} must be a whole number.") from None
        if not low <= number <= high:
            raise ValueError(f"{label} must be between {low} and {high}.")
        return number

    @app.post("/settings/issuer")
    async def save_issuer(request: Request) -> RedirectResponse:
        form = await request.form()
        got: dict[str, Any] = {
            key: str(form.get(key, "")).strip() for key in SECTION_KEYS["issuer"]
        }
        if not got["name"]:
            return _back("/settings", error="Give the name that bills: it is printed "
                                            "on every invoice.")
        if got["iban"] and not money_lib.iban_is_valid(got["iban"]):
            return _back("/settings", error=f"{got['iban']} is not a valid IBAN. "
                                            "A Swiss one is CH and nineteen more characters.")
        try:
            got["payment_days"] = _whole(form, "payment_days", "Days to pay", low=0, high=365)
        except ValueError as exc:
            return _back("/settings", error=str(exc))
        got["country"] = (got["country"] or "CH").upper()[:2]
        conn = connect()
        try:
            return _save_section(conn, "issuer", got, note="Identity saved.")
        finally:
            conn.close()

    @app.post("/settings/delivery")
    async def save_smtp(request: Request) -> RedirectResponse:
        form = await request.form()
        conn = connect()
        try:
            got = _section(conn, "smtp")
            for key in ("sender", "sender_name", "reply_to", "security", "sent_folder",
                        "test_to", "signature_image"):
                if key in form:
                    got[key] = str(form.get(key, "")).strip()
            try:
                for key, label, low, high in (
                    ("per_run", "Messages per run", 1, 1000),
                    ("daily_quota", "Messages per day", 0, 100000),
                    ("pause_seconds", "Seconds between messages", 0, 120),
                ):
                    if key in form:
                        got[key] = _whole(form, key, label, low=low, high=high)
            except ValueError as exc:
                return _back("/settings", error=str(exc))
            if got.get("security") not in (None, "", "starttls", "ssl"):
                return _back("/settings", error="Security is starttls or ssl.")
            if got.get("signature_image", "").startswith(("/", "\\")) or ".." in got.get("signature_image", ""):
                return _back("/settings", error="The signature picture is a file under "
                                                "the project folder, named from there.")
            return _save_section(conn, "smtp", got, note="Sending saved.")
        finally:
            conn.close()

    @app.post("/settings/mail")
    def save_mail(own_domains: str = Form("")) -> RedirectResponse:
        domains = sorted({
            line.strip().lower().lstrip("@")
            for line in own_domains.replace(",", "\n").splitlines() if line.strip()
        })
        conn = connect()
        try:
            return _save_section(conn, "mail", {"own_domains": domains},
                                 note=f"Mailbox saved: {ui.num(len(domains))} own domain(s).")
        finally:
            conn.close()

    @app.post("/settings/zefix")
    def save_zefix(contact: str = Form("")) -> RedirectResponse:
        if not contact.strip():
            return _back("/settings", error="The contact cannot be empty: it is how "
                                            "the register's operators reach whoever "
                                            "is asking.")
        conn = connect()
        try:
            return _save_section(conn, "zefix", {"contact": contact.strip()},
                                 note="Contact saved.")
        finally:
            conn.close()

    @app.post("/settings/llm")
    async def save_llm(request: Request) -> RedirectResponse:
        form = await request.form()
        try:
            got: dict[str, Any] = {
                "auth_mode": config_lib._auth_mode(form.get("auth_mode", "subscription")),
                "advise_effort": config_lib._effort(form.get("advise_effort", "")),
                "prep_effort": config_lib._effort(form.get("prep_effort", "")),
            }
            for key in SECTION_KEYS["llm"]:
                if key.endswith("_model"):
                    got[key] = config_lib._model_id(key, form.get(key, ""))
        except ValueError as exc:
            return _back("/settings", error=str(exc))
        conn = connect()
        try:
            return _save_section(conn, "llm", got, note="Model access saved.")
        finally:
            conn.close()

    @app.post("/settings/enrol")
    async def save_enrol(request: Request) -> RedirectResponse:
        form = await request.form()
        brief = str(form.get("brief", "")).strip()
        if brief:
            if brief.startswith(("/", "\\")) or ".." in brief:
                return _back("/settings", error="The message brief is a file under the "
                                                "project folder, named from there.")
            if not (settings.root / brief).is_file():
                return _back("/settings", error=f"No file named {brief} under the project "
                                                "folder. Write it first, then name it here.")
        try:
            testers = _whole(form, "android_testers", "Testers Google asks for", low=1, high=1000)
            days = _whole(form, "android_days", "Days of testing", low=1, high=365)
        except ValueError as exc:
            return _back("/settings", error=str(exc))
        got = {
            "programme": str(form.get("programme", "")).strip().upper(),
            "segments": [str(v).strip() for v in form.getlist("segments") if str(v).strip()],
            "brief": brief,
            "include_in_draft": bool(form.get("include_in_draft")),
            "ios_link": str(form.get("ios_link", "")).strip(),
            "android_window_start": str(form.get("android_window_start", "")).strip(),
            "android_window_end": str(form.get("android_window_end", "")).strip(),
            "android_testers": testers,
            "android_days": days,
        }
        conn = connect()
        try:
            return _save_section(conn, "enrol", got, note="Enrolment saved.")
        finally:
            conn.close()

    @app.post("/settings/scoring")
    async def save_scoring(request: Request) -> RedirectResponse:
        form = await request.form()
        name = str(form.get("name", "")).strip()
        if not name:
            return _back("/settings", error="Name the weighting: a score cites the set "
                                            "it was made with.")
        raw = {c.key: str(form.get(c.key, "")).strip() for c in scoring.COMPONENTS}
        try:
            normalised = scoring.normalise({k: Decimal(v) for k, v in raw.items()})
        except (scoring.ScoringError, InvalidOperation, ValueError) as exc:
            return _back("/settings", error=f"These weights cannot be used: {exc}")
        conn = connect()
        try:
            db.save_setting(conn, "scoring", {
                "name": name, "note": str(form.get("note", "")).strip(), "weights": raw,
            })
            conn.commit()
            live = _reload()
            set_id, active = scout_store.active_weights(conn)
            same = set_id is not None and scoring.normalise(
                {k: Decimal(str(v)) for k, v in active.items()}
            ) == normalised
            if same:
                return _back("/settings", note="Weights saved; they are the set in "
                                                "force, so no new version was opened.")
            opened = scout_store.save_weight_set(
                conn, live.weight_set_name, dict(live.weights), note=live.weight_set_note,
            )
            conn.commit()
            return _back("/settings", note=f"Weights saved; weight set {opened} is in force.")
        finally:
            conn.close()

    @app.post("/settings/services")
    async def save_services(request: Request) -> RedirectResponse:
        form = await request.form()
        columns = {
            key: [str(v) for v in form.getlist(key)]
            for key in ("code", "name", "unit", "low", "high", "sort_order", "what",
                        "prerequisite", "basis")
        }
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for i, code in enumerate(columns["code"]):
            code = code.strip().lower()
            if not code:
                continue
            if code in seen:
                return _back("/settings", error=f"The code {code} is used twice.")
            seen.add(code)

            def cell(key: str) -> str:
                values = columns[key]
                return values[i].strip() if i < len(values) else ""

            unit = cell("unit") or "fixed"
            if unit not in sales_store.UNITS:
                return _back("/settings", error=f"{code}: the unit is one of "
                                                f"{', '.join(sales_store.UNITS)}.")
            try:
                low = money_lib.money(cell("low")) if cell("low") else None
                high = money_lib.money(cell("high")) if cell("high") else None
                order = int(cell("sort_order") or 100)
            except ValueError:
                return _back("/settings", error=f"{code}: the prices and the order "
                                                "must be numbers.")
            if low is not None and high is not None and low > high:
                return _back("/settings", error=f"{code}: the lower price is above "
                                                "the higher one.")
            rows.append({
                "code": code, "name": cell("name") or code, "unit": unit,
                "low": None if low is None else str(low),
                "high": None if high is None else str(high),
                "what": cell("what"), "prerequisite": cell("prerequisite"),
                "basis": cell("basis"), "sort_order": order,
            })
        rows.sort(key=lambda r: (r["sort_order"], r["code"]))
        note_text = str(form.get("card_note", "")).strip()
        conn = connect()
        try:
            before = tuple(settings.catalogue)
            db.save_setting(conn, "rates", {"service": rows, "card_note": {"text": note_text}})
            conn.commit()
            live = _reload()
            if "sales" not in enabled or not live.catalogue:
                return _back("/settings", note="Catalogue saved.")
            if live.catalogue == before and sales_store.current_card(conn) is not None:
                return _back("/settings", note="Catalogue saved; the lines are unchanged, "
                                                "so the rate card in force stays.")
            sales_store.load_catalogue(conn, list(live.catalogue), note=live.card_note)
            conn.commit()
            return _back("/settings", note=f"Catalogue saved; a rate card with "
                                            f"{ui.num(len(live.catalogue))} line(s) is in force.")
        finally:
            conn.close()

    @app.post("/settings/territory")
    async def save_territory(request: Request) -> RedirectResponse:
        form = await request.form()
        cantons = [str(v).strip().upper() for v in form.getlist("cantons") if str(v).strip()]
        forms = [str(v).strip() for v in form.getlist("legal_forms") if str(v).strip()]
        if not cantons or not forms:
            # An empty canton list means "no restriction" everywhere else in
            # this codebase, so saving one from a form where nothing was ticked
            # would silently widen the survey to Switzerland.
            return _back(
                "/settings",
                error="Pick at least one canton and one legal form. An empty "
                      "list means no restriction, which would widen the sweep "
                      "rather than narrow it.",
            )
        conn = connect()
        try:
            db.save_setting(conn, "cantons", cantons)
            db.save_setting(conn, "legal_forms", forms)
            conn.commit()
            _reload()
            return _back(
                "/settings",
                note=f"Territory saved: {', '.join(cantons)}; "
                     f"{ui.num(len(forms))} legal form(s).",
            )
        finally:
            conn.close()

    @app.post("/settings/catalogue")
    def refresh_catalogue() -> RedirectResponse:
        """The register's own list of legal forms, so a code is picked rather
        than remembered."""
        conn = connect()
        try:
            from ..sources.zefix import ZefixClient
            from ..stages.source import _client

            with _client(settings) as http:
                raw = ZefixClient(http, settings.zefix.base_url).legal_forms()
            forms = []
            for entry in raw:
                name, short = entry.get("name"), entry.get("shortName")
                label = name.get("fr") if isinstance(name, dict) else name
                tag = short.get("fr") if isinstance(short, dict) else short
                if entry.get("uid"):
                    forms.append({
                        "uid": str(entry["uid"]),
                        "label": label or tag or str(entry["uid"]),
                        "short": tag or "",
                    })
            db.save_catalogue(conn, sorted(forms, key=lambda f: f["uid"]))
            _reload()
            return _back(
                "/settings",
                note=f"Legal form catalogue fetched: {ui.num(len(forms))} forms.",
            )
        except Exception as exc:
            return _back("/settings", error=f"The catalogue could not be fetched: {exc}")
        finally:
            conn.close()

    @app.post("/settings/segments")
    async def save_segments_setting(request: Request) -> RedirectResponse:
        form = await request.form()
        names = form.getlist("seg_name")
        texts = form.getlist("seg_text")
        dropped = {str(v) for v in form.getlist("seg_drop")}
        segments = {
            str(name).strip(): str(text).strip()
            for name, text in zip(names, texts)
            if str(name).strip() and str(text).strip() and str(name).strip() not in dropped
        }
        if not segments:
            return _back(
                "/settings",
                error="Companies cannot be sliced with no segments. Keep at "
                      "least one.",
            )
        conn = connect()
        try:
            db.save_setting(conn, "segments", segments)
            conn.commit()
            _reload()
            return _back(
                "/settings",
                note=f"Segments saved: {ui.num(len(segments))} defined.",
            )
        finally:
            conn.close()

    @app.post("/settings/sectors")
    async def save_sectors_setting(request: Request) -> RedirectResponse:
        """The trade vocabulary, saved the way the segments one is.

        An empty list is allowed here where it is refused for segments, and
        the difference is what each absence means. A survey with no segments
        cannot be sliced at all. A survey with no sectors is the survey this
        tool was before the vocabulary existed: the classify stage asks for
        the segments alone and nothing breaks. Sectors already stored stay
        stored; they simply stop being offered.
        """
        form = await request.form()
        names = form.getlist("sec_name")
        texts = form.getlist("sec_text")
        dropped = {str(v) for v in form.getlist("sec_drop")}
        sectors = {
            str(name).strip(): str(text).strip()
            for name, text in zip(names, texts)
            if str(name).strip() and str(text).strip() and str(name).strip() not in dropped
        }
        conn = connect()
        try:
            db.save_setting(conn, "sectors", sectors)
            conn.commit()
            _reload()
            return _back(
                "/settings",
                note=f"Sectors saved: {ui.num(len(sectors))} defined.",
            )
        finally:
            conn.close()

    @app.post("/settings/signal")
    def save_signal(signal_brief: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            db.save_setting(conn, "signal_brief", " ".join(signal_brief.split()))
            conn.commit()
            _reload()
            return _back("/settings", note="What the research looks for is saved.")
        finally:
            conn.close()

    @app.post("/settings/rates")
    def save_rates(rate_card: str = Form("")) -> RedirectResponse:
        """The rate card, kept in the database rather than in campaign.md.

        It is not part of the campaign: the same prices apply across campaigns,
        and it is the one setting here that is nobody's business but the
        operator's, so it does not want to be in a versioned file that gets
        shown to people.
        """
        conn = connect()
        try:
            db.save_setting(conn, "rate_card", rate_card.strip())
            conn.commit()
            _reload()
            return _back("/settings", note="The rate card is saved.")
        finally:
            conn.close()

    @app.post("/settings/campaign")
    def save_campaign(campaign: str = Form("")) -> RedirectResponse:
        """Written back to campaign.md rather than into the database.

        It stays a versioned file on purpose: when a run produces surprising
        scores the first question is what changed in the brief, and a diff
        answers that where a database column does not.
        """
        if not campaign.strip():
            return _back(
                "/settings",
                error="An empty brief would produce confident scores built on "
                      "nothing, which is worse than no scores.",
            )
        try:
            (settings.root / "campaign.md").write_text(campaign, encoding="utf-8")
        except OSError as fault:
            return _back(
                "/settings",
                error="The scoring brief could not be written: "
                      f"{fault.strerror or 'the disk refused it'}.",
            )
        _reload()
        return _back("/settings", note="The scoring brief is saved.")

    @app.get("/find", response_class=HTMLResponse)
    def find(request: Request) -> HTMLResponse:
        """Everything with a name, from one box.

        Eighty-seven routes hang off eleven links, so most of this console is
        reachable only by knowing which page a thing lives under. That is fine
        while you built it and useless to anybody else.

        Only the modules that are installed are searched, and each result says
        what kind of thing it is: a code like Q-2026-004 and a client code look
        alike, and a list that does not distinguish them is a list you have to
        click to read.
        """
        q = request.query_params.get("q", "").strip()
        if len(q) < 2:
            return HTMLResponse(_palette_places(q))
        conn = connect()
        try:
            # (heading, what one of them is, results). The singular is stated
            # rather than derived from the heading: "Companies"[:-1] is
            # "Companie", and a label nobody proof-read is how a tool starts
            # looking unfinished.
            groups: list[tuple[str, str, list[tuple[str, str, str]]]] = []

            companies, _ = scout_store.search_companies(
                conn, q=q, limit=6, with_total=False
            )
            groups.append(("Companies", "company", [
                (f"/company/{r['id']}", r["name"], r["canton"] or "")
                for r in companies
            ]))

            if registry.has("clients"):
                groups.append(("Clients", "client", [
                    (f"/client/{r['id']}", r["name"] or r["code"], r["code"])
                    for r in clients_store.search_clients(conn, q, limit=6)
                ]))
            if registry.has("sales"):
                groups.append(("Quotes", "quote", [
                    (f"/quote/{r['id']}", r["code"], r["company_name"])
                    for r in sales_store.all_quotes(conn, q=q, limit=5)
                ]))
                groups.append(("Invoices", "invoice", [
                    (f"/invoice/{r['id']}", r["number"], r["company_name"])
                    for r in sales_store.all_invoices(conn, q=q, limit=5)
                ]))

            groups.append(("Pages", "page", [
                (item.href, item.label, "")
                for item in at().nav if q.lower() in item.label.lower()
            ]))
            return HTMLResponse(_palette_hits(groups, q))
        finally:
            conn.close()

    def _palette_places(q: str) -> str:
        """Before anything is typed: where you can go, not nothing at all.

        An empty palette that says nothing teaches nobody what it is for.
        """
        hits = "".join(
            f'<a class="hit" href="{ui.esc(item.href)}">'
            f'<span class="kind">page</span><span>{ui.esc(item.label)}</span></a>'
            for item in at().nav
        )
        return f"<h4>Go to</h4>{hits}"

    def _palette_hits(
        groups: list[tuple[str, str, list[tuple[str, str, str]]]], q: str
    ) -> str:
        out = []
        for title, kind, items in groups:
            if not items:
                continue
            out.append(f"<h4>{ui.esc(title)}</h4>")
            for href, label, why in items:
                out.append(
                    f'<a class="hit" href="{ui.esc(href)}">'
                    f'<span class="kind">{ui.esc(kind)}</span>'
                    f"<span>{ui.esc(label)}</span>"
                    + (f'<span class="why">{ui.esc(why)}</span>' if why else "")
                    + "</a>"
                )
        if not out:
            return (
                f'<p class="empty" style="margin:1rem">Nothing matches '
                f"{ui.esc(q)!s}. This searches names, codes and numbers, not "
                "the text of notes or documents.</p>"
            )
        return "".join(out)

    @app.get("/static/console.{digest}.js")
    def console_js(digest: str) -> PlainTextResponse:
        """The one script, under a URL that changes when the script does.

        Served from the application rather than a CDN, because it is thirty
        lines that belong to this console and a third party able to change what
        runs on a page holding a client's file is a bad trade for one fewer
        route. Cached for a year: the digest is in the path, so a change
        produces a different URL rather than a stale copy nobody can flush.
        """
        return PlainTextResponse(
            assets.CONSOLE_JS,
            media_type="text/javascript",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @app.get(f"/static/htmx-{render.HTMX_VERSION}.min.js")
    def htmx_js() -> Response:
        """The one library, from the package rather than a CDN."""
        return Response(
            assets.static("htmx-" + render.HTMX_VERSION + ".min.js"),
            media_type="text/javascript",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    # Each enabled module's routes, from its own file under `routes/`.
    routes.mount(app, ctx)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
