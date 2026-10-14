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
address that asked to be left alone, bounced, no longer resolves, or was
written to inside the cooldown.

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

import re
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qs, quote

import logging

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi import Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import (
    HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse,
)

from .. import attachments as att
from .. import attribution, db, dossier, health
from .. import markdown as md
from ..kernel import modules as module_registry
from ..kernel.db import Connection
from ..modules.clients import store as clients_store
from ..modules.clients import campaigns as campaign_store
from ..modules.geo import store as geo_store
from ..modules.analytics import store as analytics_store
from ..modules.sales import money as money_lib
from ..modules.sales import store as sales_store
from ..modules.scout import store as scout_store
from .. import config as config_lib
from ..config import SECTION_KEYS, Settings, env_state, load_settings, project_root
from ..modules.scout import scoring
from ..sources import geoadmin
from ..sources import letter as letter_source
from ..sources import mail as mail_source
from ..sources import smtp as smtp_source
from ..stages import (
    answers as answers_stage,
    audience as audience_stage,
    campaign as campaign_stage,
    send as send_stage,
    draft as draft_stage,
    geocode,
    mail as mail_stage,
    match,
    source,
)
from . import assets, ui
from . import board as board_view
from . import clients as clients_view
from . import mail_page as mail_view
from . import money_page as money_view
from . import score_page as score_view
from . import reports_page as reports_view
from . import map_page as map_view_page
from . import notes_page as notes_view
from . import people_page as people_view_page
from . import draft_page as draft_view
from . import ledger_page as ledger_view
from . import campaign_page as campaign_view
from . import signature_page as signature_view
from . import programme_page as programme_view
from . import dossier_page as dossier_view
from . import prep_page as prep_view
from . import home_page as home_view
from . import help_page as help_view
from . import render
from . import settings_page as settings_view_page
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
    _startup = db.open_db(settings.database_url)
    try:
        close_orphans(_startup)
    finally:
        _startup.close()

    def connect() -> Connection:
        # One connection per request. SQLite in WAL mode handles concurrent
        # readers, and a long-lived shared connection would serialise the
        # console behind whatever a stage is writing.
        return db.open_db(settings.database_url)

    # The modules this deployment actually has, resolved once. The header is
    # built from it, so turning `sales` off in arpenteur.toml removes Quotes
    # and Invoices from every page rather than leaving links to a schema that
    # is no longer there.
    registry = module_registry.load(
        getattr(settings, "modules", None) or module_registry.DEFAULT
    )
    app.state.registry = registry
    enabled = set(registry.by_name)

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
            if "clients" in enabled:
                crm = clients_store.home_counts(conn)
                tile("client", "clients", crm["clients"], "/clients")
                tile("campaign under way", "campaigns under way",
                     crm["campaigns_running"], "/campaigns")
                tile("message waiting", "messages waiting", crm["mail_waiting"], "/mail")
            if "sales" in enabled:
                money = sales_store.home_counts(conn)
                tile("open quote", "open quotes", money["open_quotes"], "/quotes")
                tile("unpaid invoice", "unpaid invoices", money["unpaid_invoices"], "/invoices")
            body = home_view.home_page(
                checks=health.checks(settings, conn), tiles=tiles,
                runs=db.runs(conn, limit=3), territory=settings.territory.describe(),
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

    # --- the company list -----------------------------------------------------

    def _filtered(args: Any) -> bool:
        """Whether any filter is set, which decides what an empty list says."""
        return any(
            str(args.get(key, "")).strip()
            for key in ("q", "canton", "segment", "sector", "band", "state", "stage")
        )

    @app.get("/companies", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        conn = connect()
        try:
            args = request.query_params
            rows, total = scout_store.search_companies(
                conn, q=args.get("q", ""), canton=args.get("canton", ""),
                segment=args.get("segment", ""), sector=args.get("sector", ""),
                band=args.get("band", ""),
                state=args.get("state", ""), stage=args.get("stage", ""),
            )
            cantons = scout_store.cantons_present(conn)
            body = (
                "<h1>Companies</h1>"
                + render.filter_form(
                    q=args.get("q", ""), canton=args.get("canton", ""),
                    segment=args.get("segment", ""), band_name=args.get("band", ""),
                    state=args.get("state", ""), stage=args.get("stage", ""),
                    cantons=cantons,
                    segments=sorted(settings.segments), states=list(db.PROSPECT_STATES),
                    sector=args.get("sector", ""),
                    sectors=sorted(settings.sectors),
                )
                + f'<div id="list">{render.company_table(rows, total, filtered=_filtered(args))}</div>'
            )
            return shell(body, "/companies")
        finally:
            conn.close()

    @app.get("/companies/rows", response_class=HTMLResponse)
    def companies(request: Request) -> HTMLResponse:
        """The filter's HTMX target: the table alone, no page furniture.

        It also names the address the browser should be showing. The filter
        fetches this route, but the page that renders the same result whole is
        `/companies`, so pushing this URL would put something in the bar that
        answers with a bare fragment when anybody opens it. `HX-Push-Url` is
        how the server says where the reader actually is, which is what makes
        a filtered list something you can send to somebody.
        """
        conn = connect()
        try:
            args = request.query_params
            rows, total = scout_store.search_companies(
                conn, q=args.get("q", ""), canton=args.get("canton", ""),
                segment=args.get("segment", ""), sector=args.get("sector", ""),
                band=args.get("band", ""),
                state=args.get("state", ""), stage=args.get("stage", ""),
            )
            query = str(request.url.query or "")
            return HTMLResponse(
                f'<div id="list">{render.company_table(rows, total, filtered=_filtered(args))}</div>',
                headers={"HX-Push-Url": f"/companies?{query}" if query else "/companies"},
            )
        finally:
            conn.close()

    # --- one company ----------------------------------------------------------

    @app.get("/company/{company_id}", response_class=HTMLResponse)
    def company(company_id: int) -> HTMLResponse:
        conn = connect()
        try:
            row = scout_store.company(conn, company_id)
            if row is None:
                return shell(render.not_found(), status=404)
            site = scout_store.site_for(conn, company_id)
            methods = ["zefix"]
            if site is not None:
                methods.append("web-search")
            people = scout_store.people_for(conn, company_id)
            if people:
                methods.append("shab")
            body = render.company_page(
                row,
                scout_store.segments_for(conn, company_id),
                people,
                site,
                scout_store.fit_for(conn, company_id),
                scout_store.contacts_for(conn, company_id),
                db.notes_for(conn, company_id),
                scout_store.prospect_for(conn, company_id),
                scout_store.publications_for(conn, company_id),
                sorted(settings.segments),
                list(db.PROSPECT_STATES),
                # Zefix grants access on condition that origin is stated and
                # any modification declared; the methods behind this page
                # decide which notices apply.
                attribution.notice(methods),
                runner.running(),
                clients_store.client_for_company(conn, company_id),
                parties=scout_store.parties_for_company(conn, company_id),
                overlap=people_view_page.overlap_panel(
                    scout_store.board_overlap(conn, company_id), company_id
                ),
                mail_links=mail_view.links_panel(
                    clients_store.links_for(conn, company_id)
                ),
                score_parts=(
                    score_view.parts_panel(
                        scout_store.score_with_parts(conn, company_id),
                        scout_store.prospect_for(conn, company_id),
                    )
                    + score_view.disagreement_panel(
                        scout_store.disagreements(conn, company_id)
                    )
                ),
                sector=scout_store.sector_for(conn, company_id),
                sector_vocabulary=sorted(settings.sectors),
            )
            return shell(body, "/companies")
        finally:
            conn.close()

    # --- corrections ----------------------------------------------------------

    @app.post("/company/{company_id}/segments", response_class=HTMLResponse)
    async def set_segments(company_id: int, request: Request) -> HTMLResponse:
        """Labels as a person sees them, marked so no re-run undoes it."""
        form = await request.form()
        chosen = set(form.getlist("segment"))
        conn = connect()
        try:
            for name in settings.segments:
                db.set_segment_manually(
                    conn, company_id, name, present=name in chosen, author="console"
                )
            conn.commit()
            return HTMLResponse(
                render._segments_card(
                    company_id,
                    scout_store.segments_for(conn, company_id),
                    sorted(settings.segments),
                )
            )
        finally:
            conn.close()

    @app.post("/company/{company_id}/sector", response_class=HTMLResponse)
    async def set_sector(company_id: int, request: Request) -> HTMLResponse:
        """The trade as a person sees it, marked so no re-run undoes it.

        A name outside the vocabulary is refused rather than stored. The only
        way to send one is to have edited the form, and accepting it would put
        a sector in the filter dropdown that no definition anywhere explains.
        """
        form = await request.form()
        chosen = str(form.get("sector") or "").strip()
        if chosen and chosen not in settings.sectors:
            chosen = ""
        conn = connect()
        try:
            db.set_sector_manually(conn, company_id, chosen, author="console")
            conn.commit()
            return HTMLResponse(
                render._sector_card(
                    company_id,
                    scout_store.sector_for(conn, company_id),
                    sorted(settings.sectors),
                )
            )
        finally:
            conn.close()

    @app.post("/company/{company_id}/prospect", response_class=HTMLResponse)
    def set_prospect(
        company_id: int,
        state: str = Form(""),
        owner: str = Form(""),
        next_action: str = Form(""),
        next_at: str = Form(""),
    ) -> HTMLResponse:
        conn = connect()
        try:
            db.set_prospect(
                conn, company_id,
                state=state or None, owner=owner or None,
                next_action=next_action or None, next_at=next_at or None,
            )
            conn.commit()
            return HTMLResponse(
                render._prospect_card(
                    company_id,
                    scout_store.prospect_for(conn, company_id),
                    list(db.PROSPECT_STATES),
                )
            )
        finally:
            conn.close()

    @app.post("/company/{company_id}/notes", response_class=HTMLResponse)
    def add_company_note(
        company_id: int,
        body: str = Form(""),
        kind: str = Form("note"),
        author: str = Form(""),
        occurred_at: str = Form(""),
    ) -> HTMLResponse:
        conn = connect()
        try:
            if body.strip():
                db.add_note(
                    conn, company_id, body.strip(), kind=kind or "note",
                    author=author or None,
                    occurred_at=f"{occurred_at}T00:00:00+00:00" if occurred_at else None,
                )
                conn.commit()
            return HTMLResponse(
                render._notes_card(company_id, db.notes_for(conn, company_id))
            )
        finally:
            conn.close()

    @app.post("/contact/{contact_id}/suppress", response_class=HTMLResponse)
    def suppress(contact_id: int) -> HTMLResponse:
        """Toggle "do not write here".

        Honoured by the draft stage and never cleared by a later run that
        finds the address published again, which is the whole point: a person
        said no, and re-discovering the address is not consent.
        """
        conn = connect()
        try:
            row = scout_store.toggle_suppressed(conn, contact_id)
            if row is None:
                return HTMLResponse(
                    '<tr><td colspan="5" class="dim">This address no longer exists.'
                    "</td></tr>", status_code=404,
                )
            conn.commit()
            return HTMLResponse(render.contact_row(row))
        finally:
            conn.close()

    # --- the board ------------------------------------------------------------
    #
    # The survey list answers "what is out there". The board answers "what is
    # moving", which the list cannot: a company contacted in June looks exactly
    # like one contacted yesterday when both are rows sorted by score.
    #
    # It is a scout page reading `intel.prospect`, and it stays one. The client
    # chip on a won card is looked up here and passed in, because the console is
    # allowed to know about both modules and `modules/scout` is not.

    @app.get("/board", response_class=HTMLResponse)
    def board(request: Request) -> HTMLResponse:
        args = request.query_params
        conn = connect()
        try:
            rows = scout_store.board(
                conn, canton=args.get("canton", ""), segment=args.get("segment", ""),
                sector=args.get("sector", ""),
                band=args.get("band", ""), owner=args.get("owner", ""),
                q=args.get("q", ""),
            )
            today = date.fromisoformat(
                conn.execute("SELECT current_date AS d").fetchone()["d"]
            )
            return HTMLResponse(
                board_view.shell(
                    board_view.board_page(
                        rows,
                        states=list(db.PROSPECT_STATES),
                        clients=clients_store.clients_by_company(
                            conn, [int(r["id"]) for r in rows]
                        ),
                        today=today,
                        # When each card last moved, so a column can say how
                        # long something has been sitting in it. One query for
                        # the whole board.
                        moved=scout_store.time_in_state(
                            conn, "prospect", [int(r["id"]) for r in rows]
                        ),
                        lane=args.get("lane", ""),
                        q=args.get("q", ""), canton=args.get("canton", ""),
                        segment=args.get("segment", ""), band=args.get("band", ""),
                        owner=args.get("owner", ""),
                        cantons=scout_store.cantons_present(conn),
                        segments=sorted(settings.segments),
                        sector=args.get("sector", ""),
                        sectors=sorted(settings.sectors),
                        owners=scout_store.prospect_owners(conn),
                        back=str(request.url.query or ""),
                    ),
                    at("/board"),
                )
            )
        finally:
            conn.close()

    @app.post("/board/move")
    def move_card(
        company_id: int = Form(...), state: str = Form(...), back: str = Form("")
    ) -> Any:
        """Where a card lands. Called by the drag, and by the fold under it.

        The drag wants an answer, not a page: it has already moved the card and
        only needs to know whether to keep it there. The fold has no
        JavaScript to interpret one, so it says where it came from and gets a
        redirect. Same route, because they are the same change.
        """
        if state not in db.PROSPECT_STATES:
            return PlainTextResponse(f"unknown state {state!r}", status_code=400)
        conn = connect()
        try:
            scout_store.set_prospect(conn, company_id, state=state)
            conn.commit()
            return (
                RedirectResponse(f"/board?{back}", status_code=303)
                if back
                else PlainTextResponse("ok")
            )
        finally:
            conn.close()

    @app.post("/board/bring-in")
    def bring_in(
        band: str = Form("high"), segment: str = Form(""), canton: str = Form(""),
        sector: str = Form(""), limit: str = Form("25"),
    ) -> RedirectResponse:
        conn = connect()
        try:
            try:
                how_many = max(1, min(200, int(limit)))
            except ValueError:
                how_many = 25
            added = scout_store.bring_in(
                conn, band=band, segment=segment, sector=sector, canton=canton,
                limit=how_many
            )
            conn.commit()
            if not added:
                return _back(
                    "/board",
                    error="Nothing to bring in: every company matching that is "
                          "already on the board, or none has been scored in that band.",
                )
            return _back("/board", note=f"{ui.num(added)} companies put in new.")
        finally:
            conn.close()

    # --- people, and who reaches whom -----------------------------------------
    #
    # The survey answers "who exists". This answers "who do we already reach",
    # which decides whether a first approach is a cold letter or a word with
    # somebody's colleague.

    @app.get("/people", response_class=HTMLResponse)
    def people_view(request: Request) -> HTMLResponse:
        args = request.query_params
        try:
            min_boards = max(1, min(20, int(args.get("min_boards", "1"))))
        except ValueError:
            min_boards = 1
        conn = connect()
        try:
            return HTMLResponse(
                people_view_page.shell(
                    people_view_page.people_page(
                        scout_store.parties(
                            conn, q=args.get("q", ""), min_boards=min_boards
                        ),
                        q=args.get("q", ""),
                        min_boards=min_boards,
                        counts=scout_store.party_counts(conn),
                    ),
                    at("/people"),
                )
            )
        finally:
            conn.close()

    @app.get("/party/{party_id}", response_class=HTMLResponse)
    def party_view(party_id: int) -> HTMLResponse:
        conn = connect()
        try:
            row = scout_store.party(conn, party_id)
            if row is None:
                return shell(render.not_found(), status=404)
            return HTMLResponse(
                people_view_page.shell(
                    people_view_page.party_page(
                        row,
                        scout_store.roles_for_party(conn, party_id),
                        also_named=scout_store.same_name_parties(conn, party_id),
                    ),
                    at("/people"),
                )
            )
        finally:
            conn.close()

    @app.get("/company/{company_id}/route/{target_id}", response_class=HTMLResponse)
    def route_view(company_id: int, target_id: int) -> HTMLResponse:
        """The chain of shared officers from one company to another."""
        conn = connect()
        try:
            start = scout_store.company(conn, company_id)
            target = scout_store.company(conn, target_id)
            if start is None or target is None:
                return shell(render.not_found(), status=404)
            routes = scout_store.path_between(conn, company_id, target_id)
            named = {
                int(row["id"]): row["name"]
                for row in scout_store.companies_named(
                    conn, sorted({int(c) for r in routes for c in r["path"]})
                )
            }
            body = (
                f'<p class="crumbs"><a href="/company/{company_id}">'
                f'{render.esc(start["name"])}</a> / route</p>'
                f'<h1>{render.esc(start["name"])} to {render.esc(target["name"])}</h1>'
                + people_view_page.route_panel(routes, named, target["name"])
            )
            return HTMLResponse(
                people_view_page.shell(body, at("/people"))
            )
        finally:
            conn.close()

    # --- the map --------------------------------------------------------------
    #
    # Markers as a GeoJSON fetch rather than as rendered HTML: the map draws
    # them on the GPU, pans and zooms without asking again, and the same URL is
    # something an analyst can open with anything that reads GeoJSON.

    def _map_filters(args: Any) -> dict[str, str]:
        return {
            "canton": args.get("canton", ""), "segment": args.get("segment", ""),
            "sector": args.get("sector", ""),
            "band": args.get("band", ""), "state": args.get("state", ""),
        }

    @app.get("/map", response_class=HTMLResponse)
    def map_view(request: Request) -> HTMLResponse:
        conn = connect()
        try:
            chosen = _map_filters(request.query_params)
            counts = geo_store.counts(conn)
            shown = len(geo_store.points(conn, **chosen))
            return HTMLResponse(
                map_view_page.shell(
                    map_view_page.map_page(
                        count=shown,
                        placed=counts["placed"],
                        with_address=counts["with_address"],
                        cantons=scout_store.cantons_present(conn),
                        segments=sorted(settings.segments),
                        sectors=sorted(settings.sectors),
                        states=list(db.PROSPECT_STATES),
                        attribution_notice=attribution.notice(
                            ["zefix", "geoadmin", "swissboundaries"]
                        ),
                        regions=len(geo_store.regions(conn, "canton")),
                        **chosen,
                    ),
                    at("/map"),
                )
            )
        finally:
            conn.close()

    @app.get("/map/regions.geojson")
    def map_regions() -> Any:
        """The cantonal outlines, simplified for drawing.

        Separate from the points so the browser can start drawing markers
        without waiting for a megabyte of boundary, and so a boundary that has
        not been fetched is an absent layer rather than a failed page.
        """
        conn = connect()
        try:
            return geo_store.region_outlines(conn)
        finally:
            conn.close()

    @app.get("/map/points.geojson")
    def map_points(request: Request) -> JSONResponse:
        conn = connect()
        try:
            rows = geo_store.points(conn, **_map_filters(request.query_params))
            return JSONResponse(map_view_page.collection(rows))
        finally:
            conn.close()

    @app.post("/client/{client_id}/sites")
    def add_site(
        client_id: int,
        name: str = Form(""),
        street: str = Form(""),
        house_number: str = Form(""),
        postal_code: str = Form(""),
        city: str = Form(""),
        kind: str = Form(""),
    ) -> RedirectResponse:
        """Record one of a client's sites, and place it.

        Typed by a person, because this is the half of the geography the
        register does not hold: it knows one registered address per legal
        entity, and a foundation running seven sites has seven. Geocoded
        inline: it is one request to a free service and finishes while the
        redirect is being written.
        """
        conn = connect()
        try:
            client = clients_store.client(conn, client_id)
            if client is None:
                return RedirectResponse("/clients", status_code=303)
            asked = {
                "street": street.strip() or None,
                "house_number": house_number.strip() or None,
                "postal_code": postal_code.strip() or None,
                "city": city.strip() or None,
            }
            address_id = geo_store.save_address(
                conn, {**asked, "query": geoadmin.one_line(asked), "method": "manual"}
            )
            site_id = geo_store.save_establishment(
                conn, int(client["company_id"]), address_id=address_id,
                name=name.strip() or None, kind=kind.strip() or None,
                method="manual",
            )
            note = ""
            try:
                with geocode.client_for(settings) as http:
                    found = geoadmin.geocode(http, asked)
                if found is None:
                    note = (
                        f"{name or 'The site'} was saved, but the address did not "
                        "resolve to a point. Check the street and the postcode; "
                        "nothing is placed on a guess."
                    )
                else:
                    geo_store.place(
                        conn, site_id, lat=found.lat, lon=found.lon,
                        precision=found.precision, confidence=found.confidence,
                        source_url=geoadmin.source_url(geoadmin.one_line(asked)),
                    )
            except Exception as exc:
                note = f"Saved, but the geocoder did not answer: {exc}"
            conn.commit()
            if note:
                return RedirectResponse(
                    f"/client/{client_id}?error={quote(note)}#sites", status_code=303
                )
            saved = f"{name.strip() or 'The site'} was saved and placed on the map."
            return RedirectResponse(
                f"/client/{client_id}?note={quote(saved)}#sites", status_code=303
            )
        finally:
            conn.close()

    @app.post("/establishment/{establishment_id}/delete")
    def remove_site(establishment_id: int) -> RedirectResponse:
        conn = connect()
        try:
            company_id = geo_store.delete_establishment(conn, establishment_id)
            conn.commit()
            client = (
                clients_store.client_for_company(conn, company_id)
                if company_id else None
            )
            if client is None:
                return _back("/clients", note="The site was removed.")
            return _back(f"/client/{client['id']}", note="The site was removed.")
        finally:
            conn.close()

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

    # --- clients, projects, and the record -------------------------------------
    #
    # The half of the product that starts after a company says yes. See
    # migrations/0012_clients.sql for why the audit trail, the client timeline
    # and the calendar are one table read three ways.

    def _stamp(value: str) -> str:
        """A browser datetime-local into the one shape this database stores.

        `2026-08-11T09:20` arrives from the control; `db.now()` writes
        `2026-08-11T09:20:00+00:00`. Storing one with a space and the other
        with a T looks harmless and sorts wrong: a space is below `T` in
        ASCII, so a 09:20 typed by hand would order before an 09:00 recorded
        by the system on the same day, and the timeline is ordered by this
        column.
        """
        text = (value or "").strip().replace(" ", "T")
        if len(text) == 16:  # no seconds
            text += ":00"
        return text or db.now()

    def client_shell(body: str) -> HTMLResponse:
        return HTMLResponse(
            clients_view.shell("", body, at("/clients"))
        )

    @app.get("/mail", response_class=HTMLResponse)
    def mail_queue(request: Request) -> HTMLResponse:
        conn = connect()
        try:
            state = request.query_params.get("state", "pending")
            if state not in clients_store.EMAIL_STATES:
                state = "pending"
            return HTMLResponse(
                mail_view.mail_page(
                    clients_store.email_queue(conn, state=state),
                    db.clients(conn),
                    clients_store.email_counts(conn),
                    state=state,
                    chrome=at("/mail"),
                    configured=settings.mail.configured,
                )
            )
        finally:
            conn.close()

    @app.post("/mail/{email_id}/file")
    def file_mail(email_id: int, client_id: int = Form(...)) -> RedirectResponse:
        """Accept a message into a client's timeline.

        The body is fetched here rather than at import for anything the matcher
        did not recognise: until this click there was no reason to believe the
        message was correspondence, and copying the text of somebody's private
        mail into the database on a guess is not a thing to do quietly. A
        mailbox that cannot be reached now is not a failure; the message is
        filed with what is already held.
        """
        conn = connect()
        try:
            if settings.mail.configured:
                try:
                    mail_stage.fetch_body(settings, conn, email_id)
                except Exception:
                    # Reported nowhere on purpose: the filing is what the
                    # person asked for, and it succeeds without the text.
                    conn.rollback()
            clients_store.file_email(
                conn, email_id, client_id=client_id, by="console",
                own_domains=settings.mail.domains,
                free_domains=mail_source.FREE_MAIL,
            )
            # What this filing taught applies to the rest of the queue as well
            # as to the future: the earlier messages of the same thread are
            # already held, and an import would never look at them again.
            clients_store.rematch_pending(
                conn, own_domains=settings.mail.domains,
                free_domains=mail_source.FREE_MAIL,
            )
            conn.commit()
        finally:
            conn.close()
        return _back("/mail", note="The message is filed to the client's timeline.")

    @app.post("/mail/{email_id}/ignore")
    def ignore_mail(email_id: int) -> RedirectResponse:
        conn = connect()
        try:
            clients_store.ignore_email(conn, email_id, by="console")
            conn.commit()
        finally:
            conn.close()
        return _back("/mail", note="The message is ignored. The Ignored tab "
                                   "holds it if you want it back.")

    @app.post("/mail/{email_id}/reopen")
    def reopen_mail(email_id: int) -> RedirectResponse:
        conn = connect()
        try:
            clients_store.reopen_email(conn, email_id)
            conn.commit()
        finally:
            conn.close()
        return _back("/mail", note="The message is back in the queue.")

    @app.post("/mail/link/{link_id}/delete")
    def forget_link(link_id: int) -> RedirectResponse:
        conn = connect()
        try:
            company_id = clients_store.delete_email_link(conn, link_id)
            conn.commit()
        finally:
            conn.close()
        return _back(f"/company/{company_id}" if company_id else "/mail",
                     note="The link is forgotten.")

    # --- money ----------------------------------------------------------------

    def _address_of(conn, company_id: int):
        row = scout_store.company(conn, company_id)
        return (row["address_json"] if row else None) or None

    def _vat_for(conn, when=None):
        """The rate to apply, or None when the issuer is not registered.

        Not being subject to TVA is the ordinary state of a business under the
        CHF 100'000 threshold, and it is a different thing from a rate of zero:
        the document has to say so in words rather than print 0.00 beside a
        rate, which reads as an exemption claim.
        """
        from datetime import date as _date

        if not settings.issuer.subject_to_vat:
            return None
        return money_lib.vat_rate(when or _date.today())

    def _budget(text: str) -> float | None:
        """A budget as typed, or a refusal naming it; never a 500."""
        if not text.strip():
            return None
        try:
            return float(money_lib.parse_decimal(text))
        except ValueError:
            raise ValueError(f"{text.strip()!r} is not an amount.") from None

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

    @app.post("/client/{client_id}/quotes")
    def start_quote(client_id: int) -> RedirectResponse:
        conn = connect()
        try:
            quote_id = sales_store.create_quote(conn, client_id)
            sales_store.add_version(conn, quote_id)
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/quote/{quote_id}", status_code=303)

    @app.get("/quotes", response_class=HTMLResponse)
    def quotes_ledger(request: Request) -> HTMLResponse:
        """What is out, and who has not come back.

        A quote used to be reachable only from the client it belonged to, so
        this question could be answered by opening every client in turn or by
        reading an aggregate on the Reports page. Neither is a list you can act
        on: a total says there is money out and not whose.
        """
        args = request.query_params
        status, q = args.get("status", ""), args.get("q", "")
        conn = connect()
        try:
            rows = sales_store.all_quotes(conn, status=status, q=q)
            if request.headers.get("HX-Request"):
                return HTMLResponse(
                    '<div id="ledger">'
                    + money_view._quote_table([money_view.quote_row(r) for r in rows])
                    + "</div>"
                )
            return HTMLResponse(money_view.quotes_page(
                rows, sales_store.pipeline_value(conn),
                status=status, q=q, chrome=at("/quotes"),
            ))
        finally:
            conn.close()

    @app.get("/invoices", response_class=HTMLResponse)
    def invoices_ledger(request: Request) -> HTMLResponse:
        args = request.query_params
        status, q = args.get("status", ""), args.get("q", "")
        conn = connect()
        try:
            rows = sales_store.all_invoices(conn, status=status, q=q)
            if request.headers.get("HX-Request"):
                return HTMLResponse(
                    '<div id="ledger">'
                    + money_view._invoice_table(
                        [money_view.invoice_row(r) for r in rows])
                    + "</div>"
                )
            return HTMLResponse(money_view.invoices_page(
                rows, status=status, q=q, chrome=at("/invoices"),
            ))
        finally:
            conn.close()

    # Declared before /invoice/{invoice_id} is reached and before the .csv
    # sibling, because routes match in order and "register.csv" is not an int.
    @app.get("/invoices/register.csv", response_class=PlainTextResponse)
    def invoice_register_csv(request: Request) -> PlainTextResponse:
        conn = connect()
        try:
            rows = sales_store.register(
                conn, year=request.query_params.get("year", "")
            )
            return PlainTextResponse(
                money_view.register_csv(rows), media_type="text/csv",
                headers={"Content-Disposition":
                         'attachment; filename="register.csv"'},
            )
        finally:
            conn.close()

    @app.get("/invoices/register", response_class=HTMLResponse)
    def invoice_register(request: Request) -> HTMLResponse:
        year = request.query_params.get("year", "")
        conn = connect()
        try:
            rows = sales_store.register(conn, year=year)
            return HTMLResponse(money_view.register_page(
                rows, sales_store.register_gaps(rows), year=year,
                years=sales_store.register_years(conn), chrome=at("/invoices"),
            ))
        finally:
            conn.close()

    @app.get("/quote/{quote_id}", response_class=HTMLResponse)
    def one_quote(quote_id: int, request: Request) -> HTMLResponse:
        conn = connect()
        try:
            quote = sales_store.quote(conn, quote_id)
            if quote is None:
                return shell(render.not_found(), status=404)
            versions = sales_store.versions_for(conn, quote_id)
            if not versions:
                return shell(render.not_found("version of this quote"), status=404)
            wanted = request.query_params.get("v", "")
            shown = next(
                (v for v in versions if str(v["version"]) == wanted), versions[0]
            )
            full = sales_store.version(conn, int(shown["id"]))
            return HTMLResponse(money_view.quote_page(
                quote, versions, full,
                sales_store.lines_for(conn, int(shown["id"])),
                sales_store.services(conn),
                chrome=at("/quotes"),
            ))
        finally:
            conn.close()

    @app.post("/quote/{quote_id}/versions")
    def new_version(quote_id: int) -> RedirectResponse:
        conn = connect()
        try:
            previous = sales_store.latest_version(conn, quote_id)
            version_id = sales_store.add_version(conn, quote_id)
            if previous is not None:
                sales_store.copy_lines(conn, int(previous["id"]), version_id)
                sales_store.recompute(conn, version_id, vat_rate=_vat_for(conn))
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/quote/{quote_id}", status_code=303)

    @app.post("/quote-version/{version_id}/lines")
    def add_quote_line(
        version_id: int, service_id: str = Form(""), label: str = Form(""),
        quantity: str = Form("1"), unit_price: str = Form(""), at: str = Form("low"),
    ) -> RedirectResponse:
        conn = connect()
        try:
            held = sales_store.version(conn, version_id)
            if held is None:
                return RedirectResponse("/clients", status_code=303)
            try:
                if service_id:
                    sales_store.line_from_service(
                        conn, version_id, int(service_id), quantity=quantity or 1,
                        unit_price=unit_price or None, at=at,
                    )
                elif label.strip():
                    sales_store.add_line(conn, version_id, {
                        "label": label.strip(), "quantity": quantity or 1,
                        "unit_price": unit_price or 0,
                    })
                else:
                    raise ValueError("A line needs a catalogue entry or a label.")
                sales_store.recompute(conn, version_id, vat_rate=_vat_for(conn))
                conn.commit()
            except ValueError as refusal:
                # A price the card does not cover, a sent version, an amount
                # that is not one. Refusals the store makes on purpose, shown
                # on the page rather than swallowed.
                conn.rollback()
                return _back(f"/quote/{held['quote_id']}", error=str(refusal))
            return RedirectResponse(f"/quote/{held['quote_id']}", status_code=303)
        finally:
            conn.close()

    @app.post("/quote-line/{line_id}/delete")
    def drop_quote_line(line_id: int) -> RedirectResponse:
        conn = connect()
        try:
            try:
                held = sales_store.delete_line(conn, line_id)
            except ValueError as refusal:
                conn.rollback()
                owner = conn.execute(
                    "SELECT v.quote_id FROM commercial.quote_line l"
                    " JOIN commercial.quote_version v ON v.id = l.quote_version_id"
                    " WHERE l.id = %s", (line_id,)
                ).fetchone()
                if owner is None:
                    return RedirectResponse("/quotes", status_code=303)
                return _back(f"/quote/{owner['quote_id']}", error=str(refusal))
            sales_store.recompute(conn, int(held["version_id"]), vat_rate=_vat_for(conn))
            conn.commit()
            return RedirectResponse(f"/quote/{held['quote_id']}", status_code=303)
        finally:
            conn.close()

    @app.post("/quote-version/{version_id}/order")
    def order_quote_lines(
        version_id: int, order: list[str] = Form(default=[]),
    ) -> JSONResponse:
        """The lines in the order somebody put them in.

        Answers with a status rather than a page, because all three ways of
        moving a line have already moved it on screen: the list waits for
        nothing, and a page swapped in behind a drag would fight the drag.

        A sent version is refused here and not only in the interface. The
        buttons are absent and the rows are not draggable, but a refusal that
        exists only in the markup is a refusal anybody can post around.
        """
        conn = connect()
        try:
            held = sales_store.version(conn, version_id)
            if held is None:
                return JSONResponse({"error": "no such version"}, status_code=404)
            if held["sent_at"]:
                return JSONResponse(
                    {"error": "This version was sent. What the client is "
                              "holding has to go on saying what it said."},
                    status_code=409,
                )
            sales_store.reorder_lines(conn, version_id, order)
            conn.commit()
            return JSONResponse({"ok": True, "lines": len(order)})
        finally:
            conn.close()

    @app.post("/invoice/{invoice_id}/order")
    def order_invoice_lines(
        invoice_id: int, order: list[str] = Form(default=[]),
    ) -> JSONResponse:
        conn = connect()
        try:
            held = sales_store.invoice(conn, invoice_id)
            if held is None:
                return JSONResponse({"error": "no such invoice"}, status_code=404)
            if held["status"] != "draft":
                return JSONResponse(
                    {"error": "An issued invoice is never edited. A correction "
                              "is a credit note."},
                    status_code=409,
                )
            sales_store.reorder_invoice_lines(conn, invoice_id, order)
            conn.commit()
            return JSONResponse({"ok": True, "lines": len(order)})
        finally:
            conn.close()

    @app.post("/quote-version/{version_id}/sent")
    def mark_quote_sent(version_id: int, to: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            held = sales_store.version(conn, version_id)
            if held is None:
                return RedirectResponse("/clients", status_code=303)
            try:
                sales_store.recompute(conn, version_id, vat_rate=_vat_for(conn))
                sales_store.mark_sent(conn, version_id, to=to)
                conn.commit()
            except ValueError as refusal:
                conn.rollback()
                return _back(f"/quote/{held['quote_id']}", error=str(refusal))
            return _back(f"/quote/{held['quote_id']}",
                         note=f"Version {held['version']} is marked as sent and frozen.")
        finally:
            conn.close()

    @app.post("/quote/{quote_id}/status")
    def set_quote_state(quote_id: int, status: str = Form(...),
                        reason: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            sales_store.set_quote_status(conn, quote_id, status, reason=reason,
                                         by="console")
            conn.commit()
        except ValueError as refusal:
            conn.rollback()
            return _back(f"/quote/{quote_id}", error=str(refusal))
        finally:
            conn.close()
        return RedirectResponse(f"/quote/{quote_id}", status_code=303)

    @app.get("/quote/{quote_id}/print", response_class=HTMLResponse)
    def print_quote(quote_id: int, request: Request) -> HTMLResponse:
        conn = connect()
        try:
            quote = sales_store.quote(conn, quote_id)
            versions = sales_store.versions_for(conn, quote_id)
            if quote is None or not versions:
                return shell(render.not_found(), status=404)
            wanted = request.query_params.get("v", "")
            shown = next(
                (v for v in versions if str(v["version"]) == wanted), versions[0]
            )
            client = clients_store.client(conn, int(quote["client_id"]))
            if client is None:
                return shell(render.not_found("client of this quote"), status=404)
            return HTMLResponse(money_view.quote_print(
                quote, sales_store.version(conn, int(shown["id"])),
                sales_store.lines_for(conn, int(shown["id"])),
                _address_of(conn, int(client["company_id"])),
                issuer=settings.issuer,
            ))
        finally:
            conn.close()

    @app.post("/client/{client_id}/invoices")
    def start_invoice(
        client_id: int, what: str = Form(""), series: str = Form(""),
    ) -> RedirectResponse:
        """A draft, named for the work it bills.

        The reference is asked for here rather than at issue because it is how
        the work is already spoken about, and because an invoice that reached
        the issue button without one would stop there.
        """
        conn = connect()
        try:
            invoice_id = sales_store.create_invoice(
                conn, client_id,
                reference=sales_store.next_invoice_reference(
                    conn, client_id, what or "work", series=bool(series)),
            )
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/invoice/{invoice_id}", status_code=303)

    @app.post("/invoice/{invoice_id}/reference")
    def rename_invoice(invoice_id: int, reference: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            try:
                sales_store.set_invoice_reference(conn, invoice_id, reference)
                conn.commit()
            except ValueError as refusal:
                conn.rollback()
                return _back(f"/invoice/{invoice_id}", error=str(refusal))
        finally:
            conn.close()
        return RedirectResponse(f"/invoice/{invoice_id}", status_code=303)

    @app.post("/invoice/{invoice_id}/delete")
    def delete_invoice(invoice_id: int) -> RedirectResponse:
        conn = connect()
        try:
            try:
                client_id = sales_store.delete_invoice(conn, invoice_id, by="console")
                conn.commit()
            except ValueError as refusal:
                conn.rollback()
                return _back(f"/invoice/{invoice_id}", error=str(refusal))
        finally:
            conn.close()
        return _back(f"/client/{client_id}", note="The draft invoice was deleted.")

    @app.post("/quote/{quote_id}/invoice")
    def invoice_from_quote(quote_id: int) -> RedirectResponse:
        """Bill an accepted quote, line for line.

        Copied rather than referenced: an invoice is its own document and must
        go on saying what it said even if the quote is later revised.
        """
        conn = connect()
        try:
            quote = sales_store.quote(conn, quote_id)
            latest = sales_store.latest_version(conn, quote_id)
            if quote is None or latest is None:
                return RedirectResponse("/clients", status_code=303)
            invoice_id = sales_store.create_invoice(
                conn, int(quote["client_id"]), project_id=quote["project_id"],
                quote_id=quote_id,
                # Named after the quote it bills, which is what the client has
                # been reading the whole time.
                reference=sales_store.next_invoice_reference(
                    conn, int(quote["client_id"]), str(quote["code"]).split("-")[-1]),
            )
            for line in sales_store.lines_for(conn, int(latest["id"])):
                sales_store.add_invoice_line(conn, invoice_id, {
                    "service_id": line["service_id"], "label": line["label"],
                    "detail": line["detail"], "unit": line["unit"],
                    "quantity": line["quantity"], "unit_price": line["unit_price"],
                })
            sales_store.recompute_invoice(conn, invoice_id, vat_rate=_vat_for(conn))
            conn.commit()
            return RedirectResponse(f"/invoice/{invoice_id}", status_code=303)
        finally:
            conn.close()

    @app.get("/invoice/{invoice_id}", response_class=HTMLResponse)
    def one_invoice(invoice_id: int, request: Request) -> HTMLResponse:
        conn = connect()
        try:
            invoice = sales_store.invoice(conn, invoice_id)
            if invoice is None:
                return shell(render.not_found(), status=404)
            return HTMLResponse(money_view.invoice_page(
                invoice, sales_store.invoice_lines(conn, invoice_id),
                sales_store.services(conn), issuer=settings.issuer,
                chrome=at("/invoices"),
                credit_note=sales_store.credit_note_for(conn, invoice_id),
            ))
        finally:
            conn.close()

    @app.post("/invoice/{invoice_id}/lines")
    def add_bill_line(
        invoice_id: int, service_id: str = Form(""), label: str = Form(""),
        quantity: str = Form("1"), unit_price: str = Form("0"),
    ) -> RedirectResponse:
        conn = connect()
        try:
            name = label.strip()
            if service_id and not name:
                found = sales_store.service(conn, int(service_id))
                name = found["name"] if found else ""
            if not name:
                raise ValueError("A line needs a catalogue entry or a label.")
            sales_store.add_invoice_line(conn, invoice_id, {
                "service_id": int(service_id) if service_id else None,
                "label": name, "quantity": quantity or 1,
                "unit_price": unit_price or 0,
            })
            sales_store.recompute_invoice(conn, invoice_id, vat_rate=_vat_for(conn))
            conn.commit()
        except ValueError as refusal:
            conn.rollback()
            return _back(f"/invoice/{invoice_id}", error=str(refusal))
        finally:
            conn.close()
        return RedirectResponse(f"/invoice/{invoice_id}", status_code=303)

    @app.post("/invoice-line/{line_id}/delete")
    def drop_bill_line(line_id: int) -> RedirectResponse:
        conn = connect()
        try:
            try:
                invoice_id = sales_store.delete_invoice_line(conn, line_id)
            except ValueError as refusal:
                conn.rollback()
                owner = conn.execute(
                    "SELECT invoice_id FROM commercial.invoice_line WHERE id = %s",
                    (line_id,)
                ).fetchone()
                if owner is None:
                    return RedirectResponse("/invoices", status_code=303)
                return _back(f"/invoice/{owner['invoice_id']}", error=str(refusal))
            sales_store.recompute_invoice(conn, invoice_id, vat_rate=_vat_for(conn))
            conn.commit()
            return RedirectResponse(f"/invoice/{invoice_id}", status_code=303)
        finally:
            conn.close()

    @app.post("/invoice/{invoice_id}/issue")
    def issue(invoice_id: int) -> RedirectResponse:
        conn = connect()
        try:
            sales_store.recompute_invoice(conn, invoice_id, vat_rate=_vat_for(conn))
            issued = sales_store.issue_invoice(
                conn, invoice_id, days=settings.issuer.payment_days,
                qr_iban=settings.issuer.iban, by="console",
            )
            conn.commit()
        except ValueError as refusal:
            conn.rollback()
            return _back(f"/invoice/{invoice_id}", error=str(refusal))
        finally:
            conn.close()
        return _back(f"/invoice/{invoice_id}",
                     note=f"Issued: {issued['reference']} takes rank {issued['number']} "
                          f"in the register, due {str(issued['due_on'])[:10]}.")

    @app.post("/invoice/{invoice_id}/payment")
    def pay(invoice_id: int, amount: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            status = sales_store.record_payment(conn, invoice_id, amount=amount,
                                                by="console")
            conn.commit()
        except ValueError as refusal:
            conn.rollback()
            return _back(f"/invoice/{invoice_id}", error=str(refusal))
        finally:
            conn.close()
        return _back(f"/invoice/{invoice_id}",
                     note=f"Payment recorded; the invoice is {status.replace('_', ' ')}.")

    @app.post("/invoice/{invoice_id}/credit")
    def credit(invoice_id: int, reason: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            note_id = sales_store.credit_invoice(
                conn, invoice_id, reason=reason, by="console",
                days=settings.issuer.payment_days,
            )
            conn.commit()
            issued = sales_store.invoice(conn, note_id)
            return _back(f"/invoice/{note_id}",
                         note=f"Credit note {issued['reference']} issued at rank "
                              f"{issued['number']}; the invoice is marked credited.")
        except ValueError as refusal:
            conn.rollback()
            return _back(f"/invoice/{invoice_id}", error=str(refusal))
        finally:
            conn.close()

    @app.get("/invoice/{invoice_id}/print", response_class=HTMLResponse)
    def print_invoice(invoice_id: int) -> HTMLResponse:
        """The document, with a payment part when one can honestly be made.

        A QR-bill that is malformed prints perfectly and is refused at the
        bank, which is the worst failure available here because it is invisible
        until a client tries to pay. So the payload is built inside a try and a
        refusal becomes words on the page rather than a payment part nobody can
        use.
        """
        conn = connect()
        try:
            invoice = sales_store.invoice(conn, invoice_id)
            if invoice is None:
                return shell(render.not_found(), status=404)
            client = clients_store.client(conn, int(invoice["client_id"]))
            if client is None:
                return shell(render.not_found("client of this invoice"), status=404)
            address = _address_of(conn, int(client["company_id"]))
            issuer = settings.issuer
            qr, problem = "", ""
            if invoice["credits_id"] is not None:
                problem = ("A credit note carries no payment part: nothing is "
                           "owed on it.")
            elif invoice["status"] == "draft":
                problem = ("A draft has no number and no payment reference. "
                           "Issue it to produce a payment part.")
            elif not issuer.can_bill:
                problem = ("The [issuer] section of the configuration needs a "
                           "name and an IBAN before a payment part can be "
                           "produced.")
            else:
                # The register writes `city`; a client typed by hand may say
                # `town`. A debtor the standard would refuse (no postcode or
                # town) is left off the payment part, which the standard
                # allows, rather than encoded wrong, which it does not.
                where = address or {}
                town = str(where.get("town") or where.get("city") or "")
                postcode = str(where.get("swissZipCode") or "")
                debtor = money_lib.EMPTY
                if town and postcode:
                    debtor = money_lib.Party(
                        name=invoice["company_name"],
                        street=str(where.get("street") or ""),
                        building=str(where.get("houseNumber") or ""),
                        postcode=postcode, town=town,
                    )
                try:
                    payload = money_lib.qr_payload(
                        iban=invoice["qr_iban"] or issuer.iban,
                        creditor=money_lib.Party(
                            name=issuer.name, street=issuer.street,
                            building=issuer.building, postcode=issuer.postcode,
                            town=issuer.town, country=issuer.country,
                        ),
                        amount=invoice["gross_total"],
                        currency=invoice["currency"],
                        debtor=debtor,
                        reference=invoice["qr_reference"] or "",
                        message=f"Invoice {invoice['reference']}",
                    )
                    qr = money_view.qr_bill(
                        invoice, address, issuer=issuer,
                        svg=money_lib.qr_svg(payload), debtor=debtor,
                    )
                except money_lib.MoneyError as exc:
                    problem = str(exc)
            return HTMLResponse(money_view.invoice_print(
                invoice, sales_store.invoice_lines(conn, invoice_id), address,
                issuer=issuer, qr=qr, payload_error=problem,
            ))
        finally:
            conn.close()

    # --- reports ----------------------------------------------------------------

    @app.get("/reports", response_class=HTMLResponse)
    def reports(request: Request) -> HTMLResponse:
        conn = connect()
        try:
            have = analytics_store.available(conn)
            sections, missing = [], []
            for name, title, module in analytics_store.REPORTS:
                if name in have:
                    sections.append((name, title, analytics_store.read(conn, name)))
                else:
                    missing.append((name, module))
            return HTMLResponse(reports_view.reports_page(
                sections, missing=missing,
                fresh=analytics_store.freshness(conn),
                chrome=at("/reports"),
            ))
        finally:
            conn.close()

    @app.post("/reports/refresh")
    def refresh_reports() -> RedirectResponse:
        conn = connect()
        try:
            done = analytics_store.refresh(conn)
        except Exception as exc:
            return _back("/reports", error=f"The refresh failed: {exc}")
        finally:
            conn.close()
        return _back("/reports", note=(
            f"Refreshed {len(done)} view(s): {', '.join(done)}."
            if done else "Nothing materialised to refresh."
        ))

    @app.get("/clients", response_class=HTMLResponse)
    def portfolio() -> HTMLResponse:
        conn = connect()
        try:
            rows = db.clients(conn)
            projects = clients_store.project_count(conn)
            upcoming = db.upcoming(conn, 30)
            return client_shell(clients_view.portfolio(rows, projects, upcoming))
        finally:
            conn.close()

    @app.post("/clients/import")
    def import_client(uid: str = Form("")) -> RedirectResponse:
        """Fetch a company from the register and make it a client.

        The door for a client the survey never saw, which is the normal case
        rather than the exception: the survey covers a region and a set of
        legal forms, and a client does not have to be inside either.
        """
        conn = connect()
        try:
            run_id = db.start_run(conn, "import", {"uid": uid}, started_by="console")
            try:
                found = source.import_company(
                    settings, conn, uid, log=lambda m: db.append_line(conn, run_id, m)
                )
                client_id = db.promote_to_client(conn, found["company_id"])
                conn.commit()
                db.finish_run(conn, run_id, found)
            except Exception as exc:
                db.finish_run(conn, run_id, error=str(exc))
                return _back("/clients", error=str(exc))
            return _back(f"/client/{client_id}",
                         note=f"{found['name']} was fetched from the register "
                              "and is now a client.")
        finally:
            conn.close()

    @app.post("/company/{company_id}/client")
    def make_client(company_id: int) -> RedirectResponse:
        """Promote a surveyed company. A reference, not a copy: the register
        entry, the officers, the research and the notes are exactly what is
        worth having on the first day of an engagement."""
        conn = connect()
        try:
            client_id = db.promote_to_client(conn, company_id)
            conn.commit()
            return RedirectResponse(f"/client/{client_id}", status_code=303)
        finally:
            conn.close()

    def _client_rows(conn: Connection, client_id: int):
        client = clients_store.client(conn, client_id)
        if client is None:
            return None, None
        return client, scout_store.company(conn, client["company_id"])

    @app.get("/client/{client_id}", response_class=HTMLResponse)
    def client_card(client_id: int, request: Request) -> HTMLResponse:
        conn = connect()
        try:
            client, company = _client_rows(conn, client_id)
            if client is None:
                return shell(render.not_found(), status=404)
            projects = clients_store.projects_for(conn, client_id)
            officers = scout_store.people_for(conn, client["company_id"])
            methods = ["zefix"] + (["shab"] if officers else [])
            sections = db.dossier_for(conn, client_id)
            return client_shell(
                clients_view.client_page(
                    client, company, projects,
                    clients_view.merge_timeline(
                        db.timeline(conn, client_id),
                        # The notes written while this was still a prospect.
                        # They are the file the engagement started from and
                        # they used to be left on the survey side.
                        scout_store.notes_for(conn, client["company_id"]),
                    ),
                    scout_store.contacts_for(conn, client["company_id"]),
                    officers,
                    attribution.notice(methods),
                    dossier_filled=len(dossier.filled(sections)),
                    dossier_total=len(dossier.SECTIONS),
                    documents=clients_store.attachment_count(conn, client_id),
                    has_approach=clients_store.has_approach(conn, client_id),
                    notes=notes_view.strip(
                        client_id,
                        clients_store.note_count(conn, client_id),
                        next(iter(clients_store.notes_for(
                            conn, client_id, with_body=False, limit=1
                        )), None),
                    ),
                    sites=geo_store.establishments_for(conn, client["company_id"]),
                    moves=scout_store.history_for_company(conn, client["company_id"]),
                    money=(
                        money_view.quotes_panel(
                            sales_store.quotes_for(conn, client_id), client_id
                        )
                        + money_view.invoices_panel(
                            sales_store.invoices_for(conn, client_id), client_id
                        )
                    ),
                )
            )
        finally:
            conn.close()

    @app.post("/client/{client_id}/brief")
    def save_brief(
        client_id: int,
        brief: str = Form(""),
        owner: str = Form(""),
        status: str = Form(""),
    ) -> RedirectResponse:
        conn = connect()
        try:
            db.update_client(
                conn, client_id, brief=brief, owner=owner, status=status or None
            )
            conn.commit()
            return _back(f"/client/{client_id}", note="The standing brief is saved.")
        finally:
            conn.close()

    @app.post("/client/{client_id}/projects")
    def create_project(
        client_id: int,
        name: str = Form(...),
        billing: str = Form(""),
        budget: str = Form(""),
        started_at: str = Form(""),
        due_at: str = Form(""),
        summary: str = Form(""),
    ) -> RedirectResponse:
        conn = connect()
        try:
            try:
                amount = _budget(budget)
            except ValueError as refusal:
                return _back(f"/client/{client_id}", error=str(refusal))
            project_id = db.add_project(
                conn, client_id, name.strip(),
                billing=billing or None,
                budget=amount,
                started_at=started_at or None, due_at=due_at or None,
                summary=summary or None,
            )
            # A project starting is itself part of the record, so it is logged
            # rather than only stored: the timeline should answer "what
            # happened" without anyone having to remember to say it.
            db.record_event(
                conn, client_id, kind="milestone", occurred_at=db.now(),
                subject=f"Project opened: {name.strip()}",
                project_id=project_id, author="console", source="manual",
            )
            conn.commit()
            return _back(f"/project/{project_id}",
                         note=f"Project {name.strip()} is open.")
        finally:
            conn.close()

    @app.get("/project/{project_id}", response_class=HTMLResponse)
    def project_space(project_id: int) -> HTMLResponse:
        conn = connect()
        try:
            project = clients_store.project(conn, project_id)
            if project is None:
                return shell(render.not_found(), status=404)
            client, company = _client_rows(conn, project["client_id"])
            return client_shell(
                clients_view.project_page(
                    project, client, company,
                    db.timeline(conn, project["client_id"], project_id=project_id),
                )
            )
        finally:
            conn.close()

    @app.post("/project/{project_id}")
    def save_project(
        project_id: int,
        name: str = Form(""),
        status: str = Form(""),
        billing: str = Form(""),
        budget: str = Form(""),
        due_at: str = Form(""),
        summary: str = Form(""),
    ) -> RedirectResponse:
        conn = connect()
        try:
            before = clients_store.project(conn, project_id)
            try:
                amount = _budget(budget)
            except ValueError as refusal:
                return _back(f"/project/{project_id}", error=str(refusal))
            db.update_project(
                conn, project_id, name=name or None, status=status or None,
                billing=billing, budget=amount,
                due_at=due_at or None, summary=summary,
            )
            if before and status and status != before["status"]:
                db.record_event(
                    conn, before["client_id"], kind="decision", occurred_at=db.now(),
                    subject=f"Status: {before['status']} to {status}",
                    project_id=project_id, author="console",
                )
            conn.commit()
            return _back(f"/project/{project_id}", note="The project is saved.")
        finally:
            conn.close()

    @app.post("/client/{client_id}/events")
    def log_event(
        client_id: int,
        kind: str = Form("note"),
        state: str = Form("done"),
        occurred_at: str = Form(...),
        direction: str = Form(""),
        project_id: str = Form(""),
        with_whom: str = Form(""),
        subject: str = Form(""),
        body: str = Form(""),
        external_id: str = Form(""),
    ) -> RedirectResponse:
        conn = connect()
        try:
            db.record_event(
                conn, client_id, kind=kind, state=state,
                occurred_at=_stamp(occurred_at),
                direction=direction or None,
                project_id=int(project_id) if project_id.strip() else None,
                with_whom=with_whom or None, subject=subject or None,
                body=body or None, author="console",
                external_id=external_id.strip() or None,
            )
            conn.commit()
            return RedirectResponse(f"/client/{client_id}", status_code=303)
        finally:
            conn.close()

    @app.post("/event/{event_id}/state")
    def move_event(event_id: int, state: str = Form(...)) -> RedirectResponse:
        """A planned meeting becomes done, or cancelled. Never removed: an
        audit trail with a delete button is a diary."""
        conn = connect()
        try:
            row = db.event(conn, event_id)
            db.set_event_state(conn, event_id, state)
            conn.commit()
            target = f"/client/{row['client_id']}" if row else "/clients"
            return RedirectResponse(target, status_code=303)
        finally:
            conn.close()

    # --- drafts -----------------------------------------------------------------
    #
    # The review surface for what the draft stage writes. Reading them was
    # a psql session, which is how a queue of twenty-five goes out unread.

    def draft_shell(body: str) -> HTMLResponse:
        return HTMLResponse(draft_view.shell(body, at("/drafts")))

    @app.get("/drafts", response_class=HTMLResponse)
    def drafts_queue(request: Request, language: str = "") -> HTMLResponse:
        with db.open_db(settings.database_url) as conn:
            # A campaign's drafts are approved a wave at a time on the wave's
            # own page; here they would bury the ones a model wrote.
            rows = scout_store.drafts_awaiting(
                conn, limit=200, language=language.strip().lower() or "",
                exclude_model_prefix="template:",
            )
            tally = scout_store.draft_tally(conn)
            # The digest of the brief on disk now, so a draft written against
            # an older one is named rather than left looking current.
            try:
                digest = draft_stage.brief_digest(
                    draft_stage.brief_text(settings)
                )
                stale = scout_store.stale_drafts(conn, digest)
            except Exception:
                stale = 0
            return draft_shell(
                draft_view.queue_page(
                    rows, tally, stale=stale,
                    language=language.strip().lower(),
                )
            )

    @app.get("/draft/{draft_id}", response_class=HTMLResponse)
    def one_draft(request: Request, draft_id: int) -> HTMLResponse:
        with db.open_db(settings.database_url) as conn:
            row = scout_store.draft(conn, draft_id)
            if row is None:
                return shell(render.not_found(), status=404)
            return draft_shell(draft_view.one_page(row))

    @app.post("/draft/{draft_id}/review")
    def approve_draft(draft_id: int) -> RedirectResponse:
        with db.open_db(settings.database_url) as conn:
            row = scout_store.draft(conn, draft_id)
            if row is not None and draft_view.flawed(row):
                # Belt as well as braces: the page withholds the button, and
                # the route refuses the request anyway, because a form can be
                # posted without the page that drew it.
                conn.rollback()
                return _back(
                    f"/draft/{draft_id}",
                    error="This draft carries a [[ MANQUE ]] marker and "
                          "cannot be approved.",
                )
            scout_store.review_draft(conn, draft_id)
            conn.commit()
        return _back("/drafts", note="Approved. A person has read it; it is "
                                     "not sent.")

    @app.post("/draft/{draft_id}/unreview")
    def unapprove_draft(draft_id: int) -> RedirectResponse:
        with db.open_db(settings.database_url) as conn:
            scout_store.unreview_draft(conn, draft_id)
            conn.commit()
        return _back(f"/draft/{draft_id}", note="The approval is withdrawn.")

    @app.post("/draft/{draft_id}/sent")
    def draft_was_sent(draft_id: int) -> RedirectResponse:
        """Record that a message left, and move the card that says so.

        Two records of one fact, which is why they are written together.
        `intel.draft.sent_at` says this proposal was sent; the programme card
        says the firm has been contacted. Writing only the first is what left
        the campaign's own tracker empty while messages went out.
        """
        said = "Recorded as sent."
        with db.open_db(settings.database_url) as conn:
            row = scout_store.mark_draft_sent(conn, draft_id)
            # A campaign's draft moves its own programme's card. Reading
            # `[enrol]` for every draft would credit Limen with a Drymos letter.
            code = (campaign_store.programme_for_draft(conn, draft_id)
                    or settings.enrol.programme)
            if row is not None and code:
                found = clients_store.programme_by_code(conn, code)
                if found is None:
                    said += (
                        f" No programme is called {code}, so no card moved."
                        " The Programmes page names the ones there are."
                    )
                else:
                    _member, moved = clients_store.note_contacted(
                        conn, int(found["id"]), int(row["company_id"]),
                        address=row["address"] or "", by="console:sent",
                    )
                    said += (
                        f" {code} card moved to contacted." if moved
                        else f" On {code} already, past contacted."
                    )
            conn.commit()
        return _back("/drafts", note=said)

    @app.post("/draft/{draft_id}/refuse")
    def refuse_one_draft(
        draft_id: int, reason: str = Form(""),
    ) -> RedirectResponse:
        with db.open_db(settings.database_url) as conn:
            scout_store.refuse_draft(conn, draft_id, reason=reason)
            conn.commit()
        return _back(
            "/drafts",
            note="Refused, and kept: a later run will not propose that "
                 "mailbox again.",
        )

    # --- programmes -------------------------------------------------------------
    #
    # One endeavour across many companies, each member at its own stage. The
    # board's twin, and it borrows the board's drag handler on purpose: the
    # columns come from the programme rather than from a constant, but the card
    # and the move are the same job and should not become two.

    def programme_shell(body: str) -> HTMLResponse:
        return HTMLResponse(programme_view.shell(body, at("/programmes")))

    def ledger_shell(body: str) -> HTMLResponse:
        return HTMLResponse(ledger_view.shell(body, at("/programmes")))

    @app.get("/programmes", response_class=HTMLResponse)
    def programmes_list(request: Request) -> HTMLResponse:
        conn = connect()
        try:
            return programme_shell(
                programme_view.programmes_page(clients_store.programmes(conn))
            )
        finally:
            conn.close()

    @app.post("/programmes/new")
    def new_programme(
        code: str = Form(""), name: str = Form(""), what: str = Form("")
    ) -> RedirectResponse:
        conn = connect()
        try:
            try:
                programme_id = clients_store.add_programme(
                    conn, code, name, what=what, started_at=db.now()[:10]
                )
            except ValueError as exc:
                return _back("/programmes", error=str(exc))
            conn.commit()
            return _back(f"/programme/{programme_id}",
                         note=f"Programme {code.strip().upper()} is started.")
        finally:
            conn.close()

    @app.get("/programme/{programme_id}", response_class=HTMLResponse)
    def programme_card(programme_id: int) -> HTMLResponse:
        conn = connect()
        try:
            row = clients_store.programme(conn, programme_id)
            if row is None:
                return shell(render.not_found(), status=404)
            members = clients_store.members_for(conn, programme_id)
            company_ids = [int(m["company_id"]) for m in members]
            today = date.fromisoformat(
                conn.execute("SELECT current_date AS d").fetchone()["d"]
            )
            return programme_shell(
                programme_view.programme_page(
                    row, members,
                    # The client chip is looked up here rather than in the
                    # store, exactly as the prospect board does it: the console
                    # is allowed to know about both modules where the page is
                    # not.
                    clients=clients_store.clients_by_company(conn, company_ids),
                    today=today,
                    moved=scout_store.time_in_state(
                        conn, "programme_member", [int(m["id"]) for m in members]
                    ),
                    feedback=clients_store.feedback_on(conn, programme_id),
                    sectors=sorted(settings.sectors),
                    cantons=scout_store.cantons_present(conn),
                )
            )
        finally:
            conn.close()

    # --- lists and campaigns -------------------------------------------------
    #
    # The only part of the console that can transmit. Two routes do, the test
    # and the send of a wave, and both start a registered stage through the
    # runner after its precheck: see TRANSMITTING below.

    def campaign_shell(body: str, here: str = "/campaigns") -> HTMLResponse:
        return HTMLResponse(campaign_view.shell(body, at(here)))

    def back(path: str, *, note: str = "", error: str = "") -> RedirectResponse:
        query = ("?note=" + quote(note)) if note else ("?error=" + quote(error) if error else "")
        return RedirectResponse(path + query, status_code=303)

    @app.get("/audiences", response_class=HTMLResponse)
    def audiences_list(request: Request) -> HTMLResponse:
        conn = connect()
        try:
            return campaign_shell(campaign_view.audiences_page(
                campaign_store.audiences(conn), list(settings.segments),
                clients_store.programmes(conn),
            ), "/audiences")
        finally:
            conn.close()

    @app.post("/audiences/new")
    def audience_new(
        code: str = Form(""), name: str = Form(""), segments: str = Form(""),
        cantons: str = Form(""), programme: str = Form(""),
        members_only: str = Form(""), allow_unclear: str = Form(""),
        allow_hearsay: str = Form(""),
    ) -> RedirectResponse:
        from ..sources import audience as rules
        conn = connect()
        try:
            rule = rules.Rule(
                segments=audience_stage.parse_segments(segments),
                cantons=tuple(c.strip().upper() for c in cantons.split(",") if c.strip()),
                programme=programme.strip().upper(), members_only=bool(members_only),
                kinds=("desk", "unclear") if allow_unclear else ("desk",),
                provenance=("page", "hearsay") if allow_hearsay else ("page",),
            )
            new_id = audience_stage.create(conn, code=code, name=name, rule=rule)
        except ValueError as exc:
            conn.rollback()
            return back("/audiences", error=str(exc))
        finally:
            conn.close()
        return back(f"/audience/{new_id}", note="List created. Refresh its addresses, then build it.")

    @app.get("/audience/{audience_id}", response_class=HTMLResponse)
    def audience_view(audience_id: int, request: Request) -> HTMLResponse:
        reason = request.query_params.get("reason", "")
        conn = connect()
        try:
            row = campaign_store.audience(conn, audience_id)
            if row is None:
                return shell(render.not_found(), status=404)
            members = campaign_store.members(
                conn, audience_id, verdict=None if reason else "kept", reason=reason or None)
            return campaign_shell(campaign_view.audience_page(
                row, members, audience_stage.funnel(conn, audience_id), reason=reason,
            ), "/audiences")
        finally:
            conn.close()

    @app.post("/audience/{audience_id}/build")
    def audience_build(audience_id: int) -> RedirectResponse:
        lines: list[str] = []
        conn = connect()
        try:
            stats = audience_stage.build(settings, conn, audience=audience_id,
                                         log=lines.append)
        except ValueError as exc:
            conn.rollback()
            return back(f"/audience/{audience_id}", error=str(exc))
        finally:
            conn.close()
        return back(f"/audience/{audience_id}",
                    note=f"Built: {stats['kept']} kept, {stats['dropped']} dropped.")

    @app.post("/audience/{audience_id}/refresh")
    def audience_refresh(audience_id: int) -> RedirectResponse:
        try:
            run_id = runner.start("audience-verify", {"audience": str(audience_id)},
                                  internal=True)
        except (ValueError, RuntimeError) as exc:
            return back(f"/audience/{audience_id}", error=str(exc))
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.get("/audience/{audience_id}/export.csv")
    def audience_export(audience_id: int) -> Response:
        import csv
        import io
        conn = connect()
        try:
            rows = campaign_store.members(conn, audience_id)
        finally:
            conn.close()
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["uid", "name", "canton", "verdict", "reason_code", "reason",
                         "address", "language", "language_basis", "segment", "score",
                         "covered_by"])
        for r in rows:
            writer.writerow([r["uid"], r["company_name"], r["canton"], r["verdict"],
                             r["reason_code"], r["reason"], r["address"], r["language"],
                             r["language_basis"], r["segment"], r["score"],
                             r["covered_by_name"]])
        return Response(buffer.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition":
                                 f"attachment; filename=list-{audience_id}.csv"})

    @app.post("/audience-row/{member_id}/pin")
    def audience_pin(member_id: int, verdict: str = Form(""), why: str = Form(""),
                     language: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            row = campaign_store.member(conn, member_id)
            if row is None:
                return back("/audiences", error="No such row.")
            target = f"/audience/{row['audience_id']}"
            if row["audience_frozen_at"]:
                return back(target, error="The list is frozen: a campaign has sent from it.")
            if not why.strip():
                return back(target, error="Say why: a pin nobody can explain gets undone.")
            pin = dict(row["pinned"] or {})
            if verdict in ("kept", "dropped"):
                pin["verdict"] = verdict
            if language.strip():
                pin["language"] = language.strip().lower()
            pin.update(why=why.strip(), by="console", at=db.now())
            campaign_store.set_pin(conn, member_id, pin)
            conn.commit()
        finally:
            conn.close()
        return back(target, note=f"{row['company_name']} pinned. It applies at the next build.")

    @app.post("/audience-row/{member_id}/unpin")
    def audience_unpin(member_id: int) -> RedirectResponse:
        conn = connect()
        try:
            row = campaign_store.member(conn, member_id)
            if row is None:
                return back("/audiences", error="No such row.")
            campaign_store.set_pin(conn, member_id, None)
            conn.commit()
        finally:
            conn.close()
        return back(f"/audience/{row['audience_id']}",
                    note=f"{row['company_name']} unpinned. It applies at the next build.")

    @app.get("/campaigns", response_class=HTMLResponse)
    def campaigns_list() -> HTMLResponse:
        conn = connect()
        try:
            return campaign_shell(campaign_view.campaigns_page(
                campaign_store.campaigns(conn), campaign_store.audiences(conn),
                clients_store.programmes(conn),
            ))
        finally:
            conn.close()

    def _start_from_example(code: str) -> str:
        """A new templates folder named after the campaign, filled from the example.

        Copied rather than pointed at: the example is never sent, and a
        campaign whose folder is the example would be one edit away from
        sending the example.
        """
        import shutil

        folder_name = code.strip().lower()
        if not folder_name or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", folder_name):
            raise ValueError("The campaign's code names its folder, so it needs a code "
                             "of letters, digits and dashes.")
        source = settings.root / "templates" / "example"
        target = settings.root / "templates" / folder_name
        if target.exists():
            raise ValueError(f"templates/{folder_name} already exists. Name it in the "
                             "templates field instead of starting from the example.")
        if not source.is_dir():
            raise ValueError("The example templates folder is not there to copy from.")
        target.mkdir(parents=True)
        for file in sorted(source.glob("*.md")):
            shutil.copy(file, target / file.name)
        return f"templates/{folder_name}"

    @app.post("/campaigns/new")
    def campaign_new(code: str = Form(""), name: str = Form(""), audience: str = Form(""),
                     templates: str = Form(""), programme: str = Form(""),
                     strategy: str = Form("canton-blocks"),
                     start_from_example: str = Form("")) -> RedirectResponse:
        made_folder = ""
        if start_from_example:
            try:
                templates = made_folder = _start_from_example(code)
            except ValueError as exc:
                return back("/campaigns", error=str(exc))
        elif not templates.strip():
            return back("/campaigns", error="Name the templates folder, or tick "
                                            "\"Start from the example\".")
        conn = connect()
        try:
            new_id = campaign_stage.create(settings, conn, code=code, name=name,
                                           audience=audience, templates=templates,
                                           strategy=strategy, programme=programme)
        except ValueError as exc:
            conn.rollback()
            if made_folder:
                import shutil

                shutil.rmtree(settings.root / made_folder, ignore_errors=True)
            return back("/campaigns", error=str(exc))
        finally:
            conn.close()
        return back(
            f"/campaign/{new_id}",
            note="Campaign created. Rewrite the three example messages, then plan its waves."
            if made_folder else "Campaign created. Plan its waves.",
        )

    def _template_files(row: Any) -> list[dict[str, Any]]:
        """Each message file of a campaign, with its state, and the standard
        languages that are not written yet."""
        from ..sources import template as template_source

        try:
            folder = campaign_stage._templates_dir(settings, row["templates"])
        except ValueError:
            return []
        found: list[dict[str, Any]] = []
        seen: set[str] = set()
        if folder.is_dir():
            for path in sorted(folder.glob("*.md")):
                if not template_source.is_template_name(path.name):
                    continue
                language = template_source.language_of_name(path.name)
                seen.add(path.name)
                item: dict[str, Any] = {
                    "name": path.name, "variant": path.name[:-3], "subject": "",
                    "problem": "", "state": "ok",
                }
                try:
                    subject, _body = template_source.parse(
                        path.read_text(encoding="utf-8"), language=language,
                        path=f"{row['templates']}/{path.name}",
                    )
                    item["subject"] = subject
                except (template_source.TemplateError, UnicodeDecodeError) as exc:
                    item["state"] = "broken"
                    item["problem"] = str(exc)
                found.append(item)
        for language in ("fr", "de", "it"):
            name = f"{language}.md"
            if name not in seen:
                found.append({"name": name, "variant": language, "subject": "",
                              "problem": "", "state": "absent"})
        return found

    @app.get("/campaign/{campaign_id}", response_class=HTMLResponse)
    def campaign_view_page(campaign_id: int, request: Request) -> HTMLResponse:
        from ..sources import waves as wave_rules
        conn = connect()
        try:
            row = campaign_store.campaign(conn, campaign_id)
            if row is None:
                return shell(render.not_found(), status=404)
            wave_rows = campaign_store.waves(conn, campaign_id)
            return campaign_shell(campaign_view.campaign_page(
                row, wave_rows, campaign_stage.results(settings, conn, campaign=campaign_id),
                wave_rules.campaign_state(row, wave_rows), files=_template_files(row),
            ))
        finally:
            conn.close()

    def _template_target(campaign_id: int, name: str) -> tuple[Any, Any] | None:
        """The campaign and the file a name points at inside its folder, or None."""
        from ..sources import template as template_source

        if not template_source.is_template_name(name):
            return None
        conn = connect()
        try:
            row = campaign_store.campaign(conn, campaign_id)
        finally:
            conn.close()
        if row is None:
            return None
        try:
            folder = campaign_stage._templates_dir(settings, row["templates"])
        except ValueError:
            return None
        return row, folder / name

    @app.get("/campaign/{campaign_id}/template/{name}", response_class=HTMLResponse)
    def template_editor(campaign_id: int, name: str) -> HTMLResponse:
        """One message file of a campaign, as a form."""
        target = _template_target(campaign_id, name)
        if target is None:
            return shell(render.not_found("message file"), status=404)
        row, path = target
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        return campaign_shell(campaign_view.template_editor_page(
            row, name, text, exists=path.is_file(),
        ))

    @app.post("/campaign/{campaign_id}/template/{name}", response_class=HTMLResponse)
    def template_save(campaign_id: int, name: str, text: str = Form("")) -> Response:
        """Write the file when it parses; show it again with the reason when not.

        The page is drawn again rather than redirected to, so a refused text
        stays in the box: a redirect would throw away what was typed.
        """
        from ..sources import template as template_source

        target = _template_target(campaign_id, name)
        if target is None:
            return shell(render.not_found("message file"), status=404)
        row, path = target
        text = text.replace("\r\n", "\n")
        try:
            template_source.parse(
                text, language=template_source.language_of_name(name),
                path=f"{row['templates']}/{name}",
            )
        except template_source.TemplateError as exc:
            body = campaign_view.template_editor_page(row, name, text, exists=path.is_file())
            return HTMLResponse(campaign_view.shell(
                body, at("/campaigns").with_flash(error=str(exc)),
            ))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return back(f"/campaign/{campaign_id}", note=f"{name} saved.")

    @app.post("/campaign/{campaign_id}/plan")
    def campaign_plan(campaign_id: int, strategy: str = Form(""),
                      pilot_size: str = Form(""), wave_size: str = Form(""),
                      gap_days: str = Form(""), start_on: str = Form(""),
                      dry_run: str = Form("")) -> RedirectResponse:
        changes: dict[str, Any] = {}
        if strategy:
            changes["strategy"] = strategy
        for key, raw in (("pilot_size", pilot_size), ("wave_size", wave_size),
                         ("gap_days", gap_days)):
            if raw.strip():
                try:
                    changes[key] = int(raw)
                except ValueError:
                    return back(f"/campaign/{campaign_id}",
                                error=f"{key.replace('_', ' ')} must be a whole number, "
                                      f"not {raw.strip()!r}.")
        if start_on.strip():
            changes["start_on"] = start_on.strip()
        lines: list[str] = []
        conn = connect()
        try:
            stats = campaign_stage.plan(settings, conn, campaign=campaign_id,
                                        dry_run=bool(dry_run), log=lines.append, **changes)
        except ValueError as exc:
            conn.rollback()
            return back(f"/campaign/{campaign_id}", error=str(exc))
        finally:
            conn.close()
        said = (f"{stats['waves']} waves of {', '.join(map(str, stats['sizes']))} firms"
                + (" (shown, not kept)" if dry_run else " planned."))
        return back(f"/campaign/{campaign_id}", note=said)

    @app.post("/campaign/{campaign_id}/pause")
    def campaign_pause(campaign_id: int, why: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            campaign_store.pause(conn, campaign_id, reason=why.strip() or "paused by hand",
                                 by="console")
            conn.commit()
        finally:
            conn.close()
        return back(f"/campaign/{campaign_id}", note="Paused.")

    @app.post("/campaign/{campaign_id}/resume")
    def campaign_resume(campaign_id: int, why: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            campaign_store.resume(conn, campaign_id, reason=why, by="console")
            conn.commit()
        except ValueError as exc:
            conn.rollback()
            return back(f"/campaign/{campaign_id}", error=str(exc))
        finally:
            conn.close()
        return back(f"/campaign/{campaign_id}", note="Resumed.")

    @app.get("/campaign/{campaign_id}/wave/{number}", response_class=HTMLResponse)
    def wave_view(campaign_id: int, number: int, request: Request) -> HTMLResponse:
        conn = connect()
        try:
            row = campaign_store.campaign(conn, campaign_id)
            wave = campaign_store.wave(conn, campaign_id, number) if row else None
            if wave is None:
                return shell(render.not_found(), status=404)
            messages = campaign_store.wave_messages(conn, int(wave["id"]))
            seen: dict[str, Any] = {}
            for m in messages:
                if m["draft_id"] and not m["dropped_at"] and m["variant"] not in seen:
                    seen[m["variant"]] = m
            confirm = campaign_stage.confirmation(row, wave)
            block = campaign_stage.why_not_send(settings, conn, row, wave, confirm, act=False)
            room = settings.smtp.daily_quota - campaign_store.attempts_since(conn, hours=24)
            return campaign_shell(campaign_view.wave_page(
                row, wave, messages, list(seen.values()),
                campaign_store.tests_of_wave(conn, int(wave["id"])),
                signature=signature_view.for_preview(send_stage.signature_html(settings)),
                quota_room=room, pause_seconds=settings.smtp.pause_seconds,
                confirmation=confirm, block=block,
            ))
        finally:
            conn.close()

    @app.post("/campaign/{campaign_id}/wave/{number}/render")
    def wave_render(campaign_id: int, number: int) -> RedirectResponse:
        base = f"/campaign/{campaign_id}/wave/{number}"
        conn = connect()
        try:
            stats = campaign_stage.render_wave(settings, conn, campaign=campaign_id,
                                               wave=number, log=lambda _m: None)
        except ValueError as exc:
            conn.rollback()
            return back(base, error=str(exc))
        finally:
            conn.close()
        return back(base, note=f"{stats['rendered']} drafts rendered. Send the test next.")

    @app.post("/campaign/{campaign_id}/wave/{number}/approve")
    def wave_approve(campaign_id: int, number: int, read: str = Form("")) -> RedirectResponse:
        base = f"/campaign/{campaign_id}/wave/{number}"
        conn = connect()
        try:
            stats = campaign_stage.approve(settings, conn, campaign=campaign_id, wave=number,
                                           by="console", read=bool(read))
        except ValueError as exc:
            conn.rollback()
            return back(base, error=str(exc))
        finally:
            conn.close()
        return back(base, note=f"Approved: {stats['approved']} message(s).")

    @app.post("/campaign/{campaign_id}/wave/{number}/unapprove")
    def wave_unapprove(campaign_id: int, number: int) -> RedirectResponse:
        conn = connect()
        try:
            campaign_stage.unapprove(settings, conn, campaign=campaign_id, wave=number)
        finally:
            conn.close()
        return back(f"/campaign/{campaign_id}/wave/{number}", note="Approval withdrawn.")

    def transmit(stage: str, campaign_id: int, number: int, extra: dict) -> RedirectResponse:
        """Start one of the two stages that hand anything to an SMTP server."""
        base = f"/campaign/{campaign_id}/wave/{number}"
        try:
            run_id = runner.start(stage, {"campaign": str(campaign_id), "wave": str(number),
                                          **extra}, internal=True)
        except (ValueError, RuntimeError) as exc:
            return back(base, error=str(exc))
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.post("/campaign/{campaign_id}/wave/{number}/test")
    def wave_test(campaign_id: int, number: int) -> RedirectResponse:
        return transmit("campaign-test", campaign_id, number, {})

    @app.post("/campaign/{campaign_id}/wave/{number}/send")
    def wave_send(campaign_id: int, number: int, confirm: str = Form("")) -> RedirectResponse:
        return transmit("campaign-send", campaign_id, number, {"confirm": confirm})

    @app.post("/outbound/{outbound_id}/settle")
    def outbound_settle(outbound_id: int, posted: str = Form(""),
                        back_to: str = Form("/campaigns")) -> RedirectResponse:
        conn = connect()
        try:
            campaign_store.settle(conn, outbound_id, posted=posted == "yes")
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(ui.local_path(back_to, "/campaigns"),
                                status_code=303)

    @app.get("/signature/image")
    def signature_image() -> Response:
        """The signature's own picture, for the preview only.

        A message carries it as an attachment and the fragment points at
        `cid:signature`, which a browser cannot resolve. Without this route the
        editor shows a broken icon where the logo is and the operator has no way
        to see what a recipient will.
        """
        pictures = send_stage.signature_images(settings)
        blob = pictures.get("signature")
        if not blob:
            return Response(status_code=404)
        return Response(blob, media_type=f"image/{smtp_source._image_subtype(blob)}")

    @app.get("/signature", response_class=HTMLResponse)
    def signature_editor() -> HTMLResponse:
        return HTMLResponse(signature_view.shell(
            signature_view.signature_page(send_stage.signature_html(settings)),
            at("/signature"),
        ))

    @app.post("/signature")
    def save_signature(html: str = Form("")) -> RedirectResponse:
        """Store what the editor produced, cleaned.

        The cleaning happens in `letter.store_signature` rather than here, so
        that a signature written straight into the file by hand is reduced by
        the same rule on the way back out.
        """
        try:
            letter_source.store_signature(
                send_stage.signature_path(settings), html
            )
        except OSError as exc:
            return _back(
                "/signature",
                error=f"The signature could not be written: {exc.strerror or 'the disk refused it'}.",
            )
        return _back("/signature", note="Signature saved.")

    @app.get("/programme/{programme_id}/ledger", response_class=HTMLResponse)
    def programme_ledger(programme_id: int, request: Request) -> HTMLResponse:
        """Every firm's status, with the answers stage's reading beside it.

        The reading is computed per request and stored nowhere. It is a
        proposal, so caching it would turn a guess into a record, and the whole
        point is that a person accepts it.
        """
        args = request.query_params
        conn = connect()
        try:
            row = clients_store.programme(conn, programme_id)
            if row is None:
                return shell(render.not_found(), status=404)
            flag = args.get("flag", "")
            rows = clients_store.campaign_ledger(
                conn, programme_id, flag=flag, stage=args.get("stage", ""),
            )
            readings = {
                item.member_id: item.reading
                for item in answers_stage.propose(
                    conn, programme_id, own_domains=settings.mail.domains
                )
            }
            return ledger_shell(
                ledger_view.ledger_page(
                    row, rows,
                    clients_store.ledger_tally(conn, programme_id),
                    readings=readings, flag=flag, stage=args.get("stage", ""),
                )
            )
        finally:
            conn.close()

    @app.get("/member/{member_id}/history", response_class=HTMLResponse)
    def member_history(member_id: int) -> HTMLResponse:
        conn = connect()
        try:
            row = clients_store.member(conn, member_id)
            if row is None:
                return shell(render.not_found(), status=404)
            return ledger_shell(
                ledger_view.history_page(
                    row,
                    clients_store.history_for(conn, int(row["company_id"])),
                )
            )
        finally:
            conn.close()

    @app.post("/member/{member_id}/answer")
    def member_answered(
        member_id: int, platform: str = Form(""), account: str = Form(""),
        window_ok: str = Form(""), stage: str = Form("replied"),
    ) -> RedirectResponse:
        """Accept a reading of what one firm answered.

        The checkbox is absent from the form when it is unticked, which here
        means "nobody said" rather than "declined". So an unticked box leaves
        `window_ok` as it was, and declining the dates is recorded from the
        command line, where it can be said rather than left out.
        """
        with db.open_db(settings.database_url) as conn:
            row = clients_store.member(conn, member_id)
            if row is None:
                return RedirectResponse("/programmes", status_code=303)
            back = f"/programme/{row['programme_id']}/ledger"
            try:
                clients_store.record_answer(
                    conn, member_id, platform=platform, account=account,
                    window_ok=True if window_ok else None, stage=stage,
                    by="console:answer",
                )
            except ValueError as exc:
                conn.rollback()
                return RedirectResponse(
                    f"{back}?error=" + quote(str(exc)), status_code=303
                )
            conn.commit()
        return RedirectResponse(
            f"{back}?note=" + quote(f"Recorded for {row['company_name']}."),
            status_code=303,
        )

    @app.post("/programme/{programme_id}/move")
    def move_member(
        programme_id: int, member_id: int = Form(...), state: str = Form(...)
    ) -> Any:
        """Where a card lands. Called by the drag, and by the fold under it.

        Same shape as `/board/move`: the drag has already moved the card and
        only needs to know whether to keep it there, so it gets a bare `ok`.
        """
        conn = connect()
        try:
            try:
                moved = clients_store.move_member(conn, member_id, state)
            except ValueError as exc:
                return PlainTextResponse(str(exc), status_code=400)
            if moved is None:
                return PlainTextResponse("no such member", status_code=404)
            conn.commit()
            return PlainTextResponse("ok")
        finally:
            conn.close()

    @app.post("/member/{member_id}/move")
    def move_member_form(member_id: int, stage: str = Form(...)) -> Any:
        """The fold under the card, which works without JavaScript.

        A separate route from the drag's because this one has a page to go back
        to and the drag does not, and because a refusal has to be readable here
        rather than swallowed by a fetch.
        """
        conn = connect()
        try:
            row = clients_store.member(conn, member_id)
            if row is None:
                return PlainTextResponse("no such member", status_code=404)
            where = f"/programme/{row['programme_id']}"
            try:
                clients_store.move_member(conn, member_id, stage)
            except ValueError as exc:
                return _back(where, error=str(exc))
            conn.commit()
            return _back(where, note=f"{row['company_name']} moved to {stage}.")
        finally:
            conn.close()

    @app.post("/programme/{programme_id}/members")
    def add_programme_member(
        programme_id: int, uid: str = Form(""), contact: str = Form("")
    ) -> RedirectResponse:
        conn = connect()
        try:
            where = f"/programme/{programme_id}"
            company = scout_store.company_by_uid(conn, uid.strip())
            if company is None:
                found = conn.execute(
                    "SELECT id FROM core.company WHERE name ILIKE %s"
                    " ORDER BY name LIMIT 2",
                    (f"%{uid.strip()}%",),
                ).fetchall()
                if len(found) != 1:
                    said = (
                        "no company matches that"
                        if not found
                        else "more than one company matches that, give the UID"
                    )
                    return RedirectResponse(
                        f"{where}?error={quote(said)}", status_code=303
                    )
                company = {"id": found[0]["id"]}
            _member, existed = clients_store.add_member(
                conn, programme_id, int(company["id"]), contact=contact
            )
            conn.commit()
            note = "already a member" if existed else "added"
            return RedirectResponse(f"{where}?note={quote(note)}", status_code=303)
        finally:
            conn.close()

    @app.post("/programme/{programme_id}/bring-in")
    def bring_in_members(
        programme_id: int, sector: str = Form(""), canton: str = Form(""),
        band: str = Form(""), limit: str = Form("25"),
    ) -> RedirectResponse:
        conn = connect()
        try:
            try:
                how_many = max(1, min(200, int(limit)))
            except ValueError:
                how_many = 25
            added = clients_store.bring_in_members(
                conn, programme_id, sector=sector, canton=canton, band=band,
                limit=how_many,
            )
            conn.commit()
            note = (
                f"{added} companies added."
                if added
                else "Nothing to add: every company matching that is already a "
                     "member, or none matches."
            )
            return RedirectResponse(
                f"/programme/{programme_id}?note={quote(note)}", status_code=303
            )
        finally:
            conn.close()

    @app.post("/programme/{programme_id}/stages")
    def set_programme_stages(
        programme_id: int, stages: str = Form("")
    ) -> RedirectResponse:
        """Rewrite the pipeline from the textarea.

        One stage per line, an optional gloss after a colon. A stage that
        members are standing in cannot be removed, and the store's refusal is
        shown rather than swallowed: it names the count, which is what the
        operator needs to act on it.
        """
        wanted: list[str] = []
        notes: dict[str, str] = {}
        for line in stages.splitlines():
            name, _, gloss = line.partition(":")
            name = name.strip()
            if not name:
                continue
            wanted.append(name)
            if gloss.strip():
                notes[name] = gloss.strip()
        conn = connect()
        try:
            where = f"/programme/{programme_id}"
            try:
                clients_store.set_stages(conn, programme_id, wanted, notes=notes)
            except ValueError as exc:
                return _back(where, error=str(exc))
            conn.commit()
            return _back(where, note="The stages are saved.")
        finally:
            conn.close()

    @app.post("/member/{member_id}/feedback")
    def say_feedback(
        member_id: int, body: str = Form(""), kind: str = Form("note")
    ) -> RedirectResponse:
        conn = connect()
        try:
            row = clients_store.member(conn, member_id)
            if row is None:
                return RedirectResponse("/programmes", status_code=303)
            where = f"/programme/{row['programme_id']}"
            try:
                clients_store.add_feedback(
                    conn, member_id, body, kind=kind, author="console"
                )
            except ValueError as exc:
                return _back(where, error=str(exc))
            conn.commit()
            return _back(where, note=f"What {row['company_name']} said is kept.")
        finally:
            conn.close()

    # --- notes ------------------------------------------------------------------
    #
    # The operator's own writing about a client, in Markdown, stored exactly as
    # typed. HTML is made on the way to a page and never kept: a stored
    # rendering drifts from its source the first time the source is edited.

    def _tags(raw: str) -> list[str]:
        return [t.strip() for t in (raw or "").replace(";", ",").split(",") if t.strip()]

    @app.get("/client/{client_id}/notes", response_class=HTMLResponse)
    def client_notes(client_id: int, request: Request) -> HTMLResponse:
        conn = connect()
        try:
            client, company = _client_rows(conn, client_id)
            if client is None:
                return shell(render.not_found(), status=404)
            args = request.query_params
            return HTMLResponse(
                notes_view.shell(
                    notes_view.notes_page(
                        client, company,
                        clients_store.notes_for(conn, client_id, q=args.get("q", "")),
                        q=args.get("q", ""),
                        projects=clients_store.projects_for(conn, client_id),
                    ),
                    at("/clients"),
                )
            )
        finally:
            conn.close()

    @app.post("/client/{client_id}/notes")
    async def add_client_note(client_id: int, request: Request) -> RedirectResponse:
        """A note pasted into the form, or a Markdown file uploaded.

        A file keeps its name as the source path, so importing it again updates
        this note rather than adding a second one. That is the same rule the
        command line follows, and it is the reason the path is stored at all.
        """
        form = await request.form()
        upload = form.get("file")
        body = str(form.get("body") or "")
        source = None
        if upload is not None and getattr(upload, "filename", ""):
            raw = await upload.read()
            try:
                body = raw.decode("utf-8")
            except UnicodeDecodeError:
                return _back(
                    f"/client/{client_id}/notes",
                    error=f"{upload.filename} is not UTF-8 text. Notes are "
                          "Markdown; a PDF or a Word file belongs under the "
                          "dossier as a document.",
                )
            source = upload.filename
        if not body.strip():
            return _back(
                f"/client/{client_id}/notes",
                error="Nothing to save: paste some Markdown or choose a file.",
            )
        project = str(form.get("project_id") or "").strip()
        conn = connect()
        try:
            note_id, what = clients_store.save_note(
                conn, client_id,
                title=md.title_of(body),
                body=body,
                source_path=source,
                project_id=int(project) if project else None,
                tags=_tags(str(form.get("tags") or "")),
                digest=md.digest(body),
            )
            if what == "created":
                # A note arriving is part of the record, like a document. The
                # timeline should be able to answer "when did we write down the
                # access conditions" without anyone remembering to say so.
                clients_store.record_interaction(
                    conn, client_id, kind="note", occurred_at=db.now(),
                    subject=f"Note: {md.title_of(body)}",
                    project_id=int(project) if project else None,
                    author="console",
                )
            conn.commit()
            said = {
                "created": f"Note saved: {md.title_of(body)}.",
                "updated": f"Note updated: {md.title_of(body)}.",
            }.get(what, f"Note unchanged: {md.title_of(body)}.")
            return RedirectResponse(
                f"/client/{client_id}/notes?note={quote(said)}#note-{note_id}",
                status_code=303,
            )
        finally:
            conn.close()

    # Declared before /note/{note_id}: routes match in order, and `1.md` is
    # not an integer, so the other way round every markdown fetch was a 422.
    @app.get("/note/{note_id}.md", response_class=PlainTextResponse)
    def note_markdown(note_id: int) -> PlainTextResponse:
        """The note as it was written.

        A stable URL that is the document rather than a page about it, so an
        agent asked to read a client has something to fetch, and so a note
        typed here can be got back out as a file.
        """
        conn = connect()
        try:
            row = clients_store.note(conn, note_id)
            if row is None:
                return PlainTextResponse("Not found", status_code=404)
            return PlainTextResponse(row["body"], media_type="text/markdown")
        finally:
            conn.close()

    @app.get("/note/{note_id}", response_class=HTMLResponse)
    def one_note(note_id: int) -> HTMLResponse:
        conn = connect()
        try:
            row = clients_store.note(conn, note_id)
            if row is None:
                return shell(render.not_found(), status=404)
            client, company = _client_rows(conn, row["client_id"])
            return HTMLResponse(
                notes_view.shell(
                    notes_view.one_note(client, company, row),
                    at("/clients"),
                )
            )
        finally:
            conn.close()

    @app.post("/note/{note_id}")
    def edit_note(
        note_id: int, body: str = Form(""), tags: str = Form("")
    ) -> RedirectResponse:
        conn = connect()
        try:
            row = clients_store.note(conn, note_id)
            if row is None:
                return _back("/clients", error="That note no longer exists.")
            if not body.strip():
                return _back(
                    f"/note/{note_id}",
                    error="A note needs some text. Delete it to remove it.",
                )
            conn.execute(
                "UPDATE crm.note SET title = %s, body = %s, sha256 = %s,"
                " tags = %s, updated_at = %s WHERE id = %s",
                (md.title_of(body), body, md.digest(body), _tags(tags),
                 db.now(), note_id),
            )
            conn.commit()
            return _back(f"/note/{note_id}", note="The note is saved.")
        finally:
            conn.close()

    @app.post("/note/{note_id}/pin")
    def pin_note(note_id: int, pinned: str = Form("1")) -> RedirectResponse:
        conn = connect()
        try:
            client_id = clients_store.set_note_pinned(conn, note_id, pinned == "1")
            conn.commit()
            return RedirectResponse(
                f"/client/{client_id}/notes" if client_id else "/clients",
                status_code=303,
            )
        finally:
            conn.close()

    @app.post("/note/{note_id}/delete")
    def remove_note(note_id: int, reason: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            client_id = clients_store.delete_note(conn, note_id, reason=reason)
            conn.commit()
            return _back(
                f"/client/{client_id}/notes" if client_id else "/clients",
                note="The note is deleted.",
            )
        finally:
            conn.close()

    # --- the dossier, the advice and the client's papers ------------------------
    #
    # See migrations/0014_dossier.sql. What is known, what follows from it, and
    # what it was built on, on one page because they are one thing in use.

    def dossier_shell(body: str) -> HTMLResponse:
        return HTMLResponse(
            dossier_view.shell(body, at("/clients"))
        )

    @app.get("/client/{client_id}/dossier", response_class=HTMLResponse)
    def client_dossier(client_id: int, request: Request) -> HTMLResponse:
        conn = connect()
        try:
            client, company = _client_rows(conn, client_id)
            if client is None:
                return shell(render.not_found(), status=404)
            history = db.approaches_for(conn, client_id)
            return dossier_shell(
                dossier_view.dossier_page(
                    client,
                    company,
                    db.dossier_for(conn, client_id),
                    history[0] if history else None,
                    history[1:],
                    clients_store.attachments_for(conn, client_id),
                    conn.execute(
                        "SELECT * FROM crm.project WHERE client_id = %s ORDER BY code",
                        (client_id,),
                    ).fetchall(),
                    bool(_reload().rate_card.strip()),
                    clients_store.folders_for(conn, client_id),
                )
            )
        finally:
            conn.close()

    @app.post("/dossier/{client_id}/section")
    def save_section(
        client_id: int,
        section: str = Form(...),
        body: str = Form(""),
        source_line: str = Form("", alias="source"),
    ) -> RedirectResponse:
        conn = connect()
        try:
            db.save_dossier_section(
                conn, client_id, section, body, source=source_line, author="console"
            )
            conn.commit()
            return RedirectResponse(
                f"/client/{client_id}/dossier#{quote(section)}", status_code=303
            )
        finally:
            conn.close()

    @app.post("/dossier/{client_id}/advise")
    def suggest_approach(client_id: int, dry_run: str = Form("")) -> RedirectResponse:
        """Read the dossier and the client's documents, and suggest an approach.

        Through the stage runner, on a lane of its own. This was inline, which
        held the request open for the whole model call and left no log of it.
        The runner keeps it in the history and lets the page be reloaded, and
        its own lane means it neither waits behind a canton-wide sweep nor
        blocks one: it is one model call about one client, for somebody about
        to walk into a meeting.
        """
        _reload()
        try:
            run_id = runner.start(
                "advise", {"client_id": client_id, "dry_run": dry_run}, internal=True,
            )
        except (ValueError, RuntimeError) as exc:
            return _back(f"/client/{client_id}/dossier", error=str(exc))
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.post("/approach/{approach_id}/accept")
    def accept_approach(approach_id: int) -> RedirectResponse:
        conn = connect()
        try:
            row = clients_store.approach(conn, approach_id)
            db.accept_approach(conn, approach_id)
            conn.commit()
            target = f"/client/{row['client_id']}/dossier" if row else "/clients"
            return RedirectResponse(target, status_code=303)
        finally:
            conn.close()

    @app.post("/dossier/{client_id}/order")
    def order_dossier(
        client_id: int, order: list[str] = Form(default=[]),
    ) -> JSONResponse:
        """The sections of one group, in the order somebody put them in.

        The order changes more than where a box sits: `dossier.as_text` reads
        the sections this way too, so the advice stage and the CLI dump both
        see the dossier the way its author arranged it.

        The vocabulary is passed in as the allowed set rather than trusted from
        the request, so a name that is not a section stores nothing.
        """
        conn = connect()
        try:
            moved = clients_store.reorder_dossier(
                conn, client_id, order, known=[s.name for s in dossier.SECTIONS]
            )
            conn.commit()
            return JSONResponse({"ok": True, "sections": moved})
        finally:
            conn.close()

    @app.post("/dossier/{client_id}/documents")
    async def upload_documents(
        client_id: int,
        request: Request,
        file: list[UploadFile] = File(default=[]),
        kind: str = Form(""),
        note: str = Form(""),
        project_id: str = Form(""),
        folder_id: str = Form(""),
        new_folder: str = Form(""),
    ) -> Response:
        """Store what the client sent, and extract the text.

        Takes several files, because dropping a folder of them is the ordinary
        case and uploading them one at a time was the interface deciding how
        somebody organises their week.

        Which means the answer changed shape. One file has one outcome and a
        redirect carrying a note could report it; four files have four, and
        three of them being fine is not a thing a single banner can say. So
        this reports one line per file and, when the browser asked with htmx,
        answers with the documents panel redrawn rather than a whole page.

        Extraction happens here rather than at read time so that a file nothing
        can open is a recorded state on the row instead of a silent absence
        every time an agent asks for it.
        """
        back = f"/client/{client_id}/dossier"
        said: list[str] = []
        kept = 0
        conn = connect()
        try:
            # A folder typed into the drop is made before anything is stored,
            # so a batch cannot half-arrive into a folder that does not exist
            # yet. Naming one that is already there is that folder, which is
            # what a second delivery of the same dossier should do.
            into = int(folder_id) if folder_id.strip() else None
            if new_folder.strip():
                # The note is not passed on. It says where these papers came
                # from, which is a fact about the documents; the folder's own
                # note says what the batch is, and is written where a folder
                # is made deliberately.
                into, existed = clients_store.add_folder(
                    conn, client_id, new_folder
                )
                said.append(
                    f"Filed in the existing folder {new_folder.strip()}."
                    if existed else f"Made the folder {new_folder.strip()}."
                )
            for item in file:
                content = await item.read()
                name = item.filename or "file"
                if not content:
                    continue
                try:
                    stored = att.store(settings.data_dir, content, name)
                except ValueError as exc:
                    said.append(f"{name}: {exc}")
                    continue
                text, error = att.extract(content, name, item.content_type or "")
                _id, existed = db.add_attachment(
                    conn,
                    client_id,
                    {
                        "filename": name,
                        "media_type": item.content_type or "",
                        "bytes": stored.bytes,
                        "sha256": stored.sha256,
                        "kind": kind or None,
                        "note": note.strip() or None,
                        "project_id": int(project_id) if project_id.strip() else None,
                        "folder_id": into,
                        "text": text,
                        "extract_error": error,
                    },
                )
                kept += 1
                if existed:
                    said.append(
                        f"{name} is byte for byte a document already held, so "
                        "the label and note were updated rather than a second "
                        "copy stored."
                    )
                    continue
                # A document arriving is part of the record. The timeline
                # should be able to answer "when did we get the accounts"
                # without anyone remembering to write a note saying so.
                db.record_event(
                    conn, client_id, kind="document", occurred_at=db.now(),
                    subject=f"Document received: {name}",
                    body=note.strip() or None,
                    project_id=int(project_id) if project_id.strip() else None,
                    author="console",
                )
                said.append(
                    f"{name}: stored, but not readable. {error}" if error
                    else f"{name}: stored and read."
                )
            conn.commit()
            if not said:
                said.append("Nothing was sent, so nothing was stored.")
            if request.headers.get("HX-Request"):
                return HTMLResponse(dossier_view.documents_panel(
                    clients_store.attachments_for(conn, client_id),
                    folders=clients_store.folders_for(conn, client_id),
                    client_id=client_id, note=" ".join(said),
                ))
            # Red when nothing was kept, green otherwise: a batch of four with
            # one refused is mostly a success, and the line for the refused
            # file is in the banner either way.
            if kept:
                return _back(back, note=" ".join(said))
            return _back(back, error=" ".join(said))
        finally:
            conn.close()

    @app.get("/attachment/{attachment_id}/text", response_class=PlainTextResponse)
    def attachment_text(attachment_id: int) -> PlainTextResponse:
        """The extracted text, as plain text.

        A stable URL rather than a rendered page, so an agent asked to analyse
        a client's documents has something to fetch that is the document and
        not a page about the document.
        """
        conn = connect()
        try:
            row = clients_store.attachment(conn, attachment_id)
            if row is None:
                return PlainTextResponse("Not found", status_code=404)
            if not row["text"]:
                return PlainTextResponse(
                    f"{row['filename']}: no text was extracted.\n"
                    f"{row['extract_error'] or ''}\n",
                    status_code=409,
                )
            return PlainTextResponse(row["text"])
        finally:
            conn.close()

    # --- preparing for the meeting ---------------------------------------------

    @app.get("/client/{client_id}/prep", response_class=HTMLResponse)
    def prep_view_page(client_id: int, request: Request) -> HTMLResponse:
        conn = connect()
        try:
            client, company = _client_rows(conn, client_id)
            if client is None:
                return shell(render.not_found(), status=404)
            live = _reload()
            briefings = db.briefings_for(conn, client_id, limit=1)
            return HTMLResponse(
                prep_view.shell(
                    prep_view.prep_page(
                        client, company,
                        db.glossary_for(conn, client_id),
                        briefings[0] if briefings else None,
                        len(dossier.filled(db.dossier_for(conn, client_id))),
                        live.llm.prep_model or live.llm.match_model,
                        live.llm.prep_effort,
                    ),
                    at("/clients"),
                )
            )
        finally:
            conn.close()

    @app.post("/client/{client_id}/prep")
    def extract_vocabulary(client_id: int, dry_run: str = Form("")) -> RedirectResponse:
        """The same run as advice, on the same lane, for the same reasons."""
        _reload()
        try:
            run_id = runner.start(
                "prep", {"client_id": client_id, "dry_run": dry_run}, internal=True,
            )
        except (ValueError, RuntimeError) as exc:
            return _back(f"/client/{client_id}/prep", error=str(exc))
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.post("/term/{term_id}/known")
    def tick_term(term_id: int, known: str = Form("1")) -> RedirectResponse:
        """The one writable field on that page, and no run ever overwrites it.

        Same rule as a hand-set segment label: what a person decided outranks
        what a model produced, permanently. Without that, re-running after the
        client sends a document would silently reset an evening's revision.
        """
        conn = connect()
        try:
            owner = db.term_owner(conn, term_id)
            db.set_term_known(conn, term_id, known=known == "1")
            conn.commit()
            return RedirectResponse(
                f"/client/{owner}/prep#terms" if owner else "/clients",
                status_code=303,
            )
        finally:
            conn.close()

    # --- removing what is not true ---------------------------------------------
    #
    # This schema was written saying nothing is ever deleted, on the grounds
    # that an audit trail with a delete button is a diary. That was half right,
    # and the half it got wrong cost more: on 12.08.2026 rows describing a
    # meeting and a project that never existed were written into a live record
    # and there was no way to take them out. A log that cannot be corrected is
    # not more truthful than one that can, only more confident.
    #
    # Cancelling and deleting are different corrections and the record needs
    # both. Cancelled says a thing was true and then did not happen; deleted
    # says it was never true, which is the claim that has to stop being made.
    # Every deletion leaves a tombstone naming what went and why.

    @app.post("/event/{event_id}/delete")
    def remove_event(event_id: int, reason: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            row = db.event(conn, event_id)
            db.delete_event(conn, event_id, reason=reason)
            conn.commit()
            return _back(
                f"/client/{row['client_id']}" if row else "/clients",
                note="The entry is deleted. A note in the record says why.",
            )
        finally:
            conn.close()

    @app.post("/project/{project_id}/delete")
    def remove_project(project_id: int, reason: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            client_id = db.delete_project(conn, project_id, reason=reason)
            conn.commit()
            return _back(
                f"/client/{client_id}" if client_id else "/clients",
                note="The project is deleted. What was logged against it stays.",
            )
        finally:
            conn.close()

    @app.post("/attachment/{attachment_id}/delete")
    def remove_attachment(
        attachment_id: int, reason: str = Form("")
    ) -> RedirectResponse:
        conn = connect()
        try:
            removed = db.delete_attachment(conn, attachment_id, reason=reason)
            if removed is None:
                return _back("/clients", error="That document no longer exists.")
            client_id, digest = removed
            conn.commit()
            # Storage is content-addressed, so the file only goes when nobody
            # else references those bytes: another client may legitimately hold
            # the same standard document and unlinking would take theirs too.
            if db.sha_is_orphaned(conn, digest):
                folder = att.root_for(settings.data_dir) / digest[:2]
                for path in folder.glob(f"{digest}*"):
                    path.unlink(missing_ok=True)
            return _back(f"/client/{client_id}/dossier",
                         note="The document is deleted.")
        finally:
            conn.close()

    # --- folders --------------------------------------------------------------
    #
    # Filing over the documents already held. A folder is made, documents are
    # moved into it, and removing it takes the filing and not the papers. See
    # modules/clients/migrations/0007_folder.sql for why this is not a project.

    @app.post("/dossier/{client_id}/folder")
    def make_folder(
        client_id: int, name: str = Form(...), note: str = Form("")
    ) -> RedirectResponse:
        conn = connect()
        try:
            try:
                _id, existed = clients_store.add_folder(
                    conn, client_id, name, note=note
                )
            except ValueError as exc:
                return _back(f"/client/{client_id}/dossier", error=str(exc))
            conn.commit()
            said = (
                f"{name.strip()} already existed, so nothing was made."
                if existed else f"Folder {name.strip()} made."
            )
            return _back(f"/client/{client_id}/dossier", note=said)
        finally:
            conn.close()

    @app.post("/attachment/{attachment_id}/folder")
    def move_attachment(
        attachment_id: int, folder_id: str = Form("")
    ) -> RedirectResponse:
        """File a document, or take it out of its folder with a blank value."""
        conn = connect()
        try:
            held = clients_store.attachment(conn, attachment_id)
            if held is None:
                return _back("/clients", error="That document no longer exists.")
            where = f"/client/{held['client_id']}/dossier"
            try:
                client_id = clients_store.file_attachment(
                    conn, attachment_id,
                    int(folder_id) if folder_id.strip() else None,
                )
            except ValueError as exc:
                conn.rollback()
                return _back(where, error=str(exc))
            if client_id is None:
                return _back("/clients", error="That document no longer exists.")
            conn.commit()
            return _back(
                f"/client/{client_id}/dossier",
                note=f"{held['filename']} is filed."
                if folder_id.strip() else f"{held['filename']} is taken out of its folder.",
            )
        finally:
            conn.close()

    @app.post("/folder/{folder_id}/delete")
    def remove_folder(folder_id: int, reason: str = Form("")) -> RedirectResponse:
        """Remove the folder. What was in it stays, unfiled."""
        conn = connect()
        try:
            client_id = clients_store.delete_folder(conn, folder_id, reason=reason)
            if client_id is None:
                return _back("/clients", error="That folder no longer exists.")
            conn.commit()
            return _back(f"/client/{client_id}/dossier",
                         note="The folder is deleted. Its documents stay, unfiled.")
        finally:
            conn.close()

    @app.get("/calendar", response_class=HTMLResponse)
    def diary(month: str = "") -> HTMLResponse:
        conn = connect()
        try:
            today = date.fromisoformat(
                conn.execute("SELECT date('now')").fetchone()[0]
            )
            try:
                year, mon = (int(p) for p in (month or today.strftime("%Y-%m")).split("-"))
                first = date(year, mon, 1)
            except ValueError:
                # `2026-13` parses as two integers and fails only at the date;
                # both faults mean the same thing, which is this month.
                year, mon = today.year, today.month
                first = date(year, mon, 1)
            last = (first + timedelta(days=32)).replace(day=1)
            return client_shell(
                clients_view.calendar_page(
                    year, mon,
                    db.diary(
                        conn,
                        since=(first - timedelta(days=7)).isoformat(),
                        until=(last + timedelta(days=7)).isoformat(),
                    ),
                    today,
                )
            )
        finally:
            conn.close()

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
        settings = load_settings(settings.root)
        runner.settings = settings
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

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
