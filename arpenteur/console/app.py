"""The console: read the survey, correct it, and keep notes on it.

Server-rendered HTML with HTMX. Every route reads or writes through the same
`db` helpers the stages use, so a correction made here is the same row a
re-run has to respect, and there is no second implementation of what a manual
label means.

Two things it deliberately does not do.

**It does not send.** There is no route that transmits anything to a company,
no mail configuration, and no `sent_at` write. The draft advice is shown for a
human to rewrite and send from their own mail client, under their own name.
Swiss law (LCD art. 3 al. 1 let. o) draws the line at *mass* advertising email
without consent, and the defence against ever crossing it is that the software
has no mechanism, not that nobody clicks the button.

**It runs stages, under the guards that made the command line safe.** One at a
time, the paid stages refusing to start without a declared size, and a dry run
offered first. The objection was never to the button but to spending real
money from a page that looks like a filter, so the guards are enforced on the
server in `runner.py` rather than by a disabled field. Every stage's own cost
refusal is untouched and still applies.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi import Response
from fastapi.responses import (
    HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse,
)

from .. import attachments as att
from .. import attribution, db, dossier
from .. import markdown as md
from ..kernel import modules as module_registry
from ..kernel.db import Connection, Row
from ..modules.clients import store as clients_store
from ..modules.geo import store as geo_store
from ..modules.analytics import store as analytics_store
from ..modules.sales import money as money_lib
from ..modules.sales import store as sales_store
from ..modules.scout import store as scout_store
from ..config import Settings, load_settings
from ..sources import geoadmin
from ..sources import mail as mail_source
from ..stages import advise, geocode, mail as mail_stage, match, prep, source
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
from . import dossier_page as dossier_view
from . import prep_page as prep_view
from . import render
from . import settings_page as settings_view_page
from .runner import ORDER, STAGES, Runner, close_orphans, options_for, status_of


def create_app(settings: Settings | None = None) -> FastAPI:
    """Built by a factory so tests can point it at a temporary database."""
    settings = settings or load_settings()
    app = FastAPI(title="arpenteur", docs_url=None, redoc_url=None)
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
        """The header, as seen from one section of the console."""
        return render.Chrome.of(registry, settings.territory.describe(), here)

    def shell(body: str, here: str = "") -> HTMLResponse:
        # The score breakdown's own rules travel with the shell rather than
        # with the panel: a <style> block inside the body would have to survive
        # an f-string, and the doubled braces that takes reach the browser and
        # kill the whole stylesheet. See `render.page`.
        return HTMLResponse(
            render.page("arpenteur", body, at(here), css=score_view.STYLES)
        )

    # --- the company list -----------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        conn = connect()
        try:
            args = request.query_params
            rows, total = scout_store.search_companies(
                conn, q=args.get("q", ""), canton=args.get("canton", ""),
                segment=args.get("segment", ""), band=args.get("band", ""),
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
                )
                + f'<div id="list">{render.company_table(rows, total)}</div>'
            )
            return shell(body, "/")
        finally:
            conn.close()

    @app.get("/companies", response_class=HTMLResponse)
    def companies(request: Request) -> HTMLResponse:
        """The filter's HTMX target: the table alone, no page furniture.

        It also names the address the browser should be showing. The filter
        fetches this route, but the page that renders the same result whole is
        `/`, so pushing this URL would put something in the bar that answers
        with a bare fragment when anybody opens it. `HX-Push-Url` is how the
        server says where the reader actually is, which is what makes a
        filtered list something you can send to somebody.
        """
        conn = connect()
        try:
            args = request.query_params
            rows, total = scout_store.search_companies(
                conn, q=args.get("q", ""), canton=args.get("canton", ""),
                segment=args.get("segment", ""), band=args.get("band", ""),
                state=args.get("state", ""), stage=args.get("stage", ""),
            )
            query = str(request.url.query or "")
            return HTMLResponse(
                f'<div id="list">{render.company_table(rows, total)}</div>',
                headers={"HX-Push-Url": f"/?{query}" if query else "/"},
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
                return HTMLResponse("<h1>Not found</h1>", status_code=404)
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
            )
            return shell(body, "/")
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
    def add_note(
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
                        owners=scout_store.prospect_owners(conn),
                        back=str(request.url.query or ""),
                        note=args.get("note", ""),
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
        limit: str = Form("25"),
    ) -> RedirectResponse:
        conn = connect()
        try:
            try:
                how_many = max(1, min(200, int(limit)))
            except ValueError:
                how_many = 25
            added = scout_store.bring_in(
                conn, band=band, segment=segment, canton=canton, limit=how_many
            )
            conn.commit()
            note = (
                f"{added} companies put in new."
                if added
                else "Nothing to bring in: every company matching that is "
                     "already on the board, or none has been scored in that band."
            )
            return RedirectResponse(f"/board?note={quote(note)}", status_code=303)
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
                return HTMLResponse("<h1>Not found</h1>", status_code=404)
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
                return HTMLResponse("<h1>Not found</h1>", status_code=404)
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
            back = f"/client/{client_id}#sites"
            return RedirectResponse(
                f"/client/{client_id}?note={quote(note)}#sites" if note else back,
                status_code=303,
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
            return RedirectResponse(
                f"/client/{client['id']}#sites" if client else "/clients",
                status_code=303,
            )
        finally:
            conn.close()

    # --- watching the pipeline ------------------------------------------------

    @app.get("/runs", response_class=HTMLResponse)
    def runs() -> HTMLResponse:
        conn = connect()
        try:
            return shell(render.runs_page(db.runs(conn)), "/runs")
        finally:
            conn.close()

    @app.get("/runs/rows", response_class=HTMLResponse)
    def runs_rows() -> HTMLResponse:
        """Polled every few seconds while a stage is running.

        Polling rather than SSE. The stages are separate processes writing to
        SQLite, so there is no in-process event to stream; a three second poll
        of one indexed table is honest about that and cannot leave a hanging
        connection when a run dies.
        """
        conn = connect()
        try:
            return HTMLResponse(render.runs_table(db.runs(conn)))
        finally:
            conn.close()

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def one_run(run_id: int) -> HTMLResponse:
        """One run, read line by line as it happens."""
        conn = connect()
        try:
            run = db.run(conn, run_id)
            if run is None:
                return HTMLResponse("<h1>Not found</h1>", status_code=404)
            rows = db.lines_after(conn, run_id, 0)
            after = rows[-1]["seq"] if rows else 0
            return shell(render.run_page(run, rows, after), "/runs")
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
            run = db.run(conn, run_id)
            rows = db.lines_after(conn, run_id, after)
            reached = rows[-1]["seq"] if rows else after
            # Finished is read after the lines, never before: a run that ended
            # between the two queries must still have its last lines delivered,
            # and the cost of the other order is a log missing its own ending.
            done = run is not None and run["finished_at"] is not None
            return HTMLResponse(render.run_log(rows, run_id, done, reached))
        finally:
            conn.close()

    # --- starting one ---------------------------------------------------------

    @app.get("/pipeline", response_class=HTMLResponse)
    def pipeline(request: Request) -> HTMLResponse:
        """The operations page: every stage, where it stands, and how to run it.

        One page rather than a form page and a counts page. The number that
        makes a button meaningful is how much work is queued for it, and that
        was on the other page: "3 done, 41'238 waiting" says what to press
        next, where a bare button says nothing at all.
        """
        conn = connect()
        try:
            # Only the stages whose module is installed. A geocode button on a
            # console with no `geo` schema is one that can only fail.
            stages = [STAGES[name] for name in ORDER if STAGES[name].module in enabled]
            return shell(
                render.pipeline_page(
                    stages,
                    {stage.name: status_of(conn, stage) for stage in stages},
                    options_for(conn, settings),
                    runner.running(),
                    request.query_params.get("note", ""),
                ),
                "/pipeline",
            )
        finally:
            conn.close()

    @app.get("/run", response_class=HTMLResponse)
    def run_redirect() -> RedirectResponse:
        """The old separate form. Its contents live on the operations page."""
        return RedirectResponse("/pipeline", status_code=307)

    @app.post("/run/{name}")
    async def start_run(name: str, request: Request) -> RedirectResponse:
        form = dict(await request.form())
        try:
            run_id = runner.start(name, form)
        except (ValueError, RuntimeError) as exc:
            # Back to the operations page with the reason, rather than an error
            # page: the answer is always to change something and try again.
            return RedirectResponse(f"/pipeline?note={quote(str(exc))}", status_code=303)
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
            clients_view.shell("arpenteur", body, at("/clients"))
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
        return RedirectResponse("/mail", status_code=303)

    @app.post("/mail/{email_id}/ignore")
    def ignore_mail(email_id: int) -> RedirectResponse:
        conn = connect()
        try:
            clients_store.ignore_email(conn, email_id, by="console")
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse("/mail", status_code=303)

    @app.post("/mail/{email_id}/reopen")
    def reopen_mail(email_id: int) -> RedirectResponse:
        conn = connect()
        try:
            clients_store.reopen_email(conn, email_id)
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse("/mail", status_code=303)

    @app.post("/mail/link/{link_id}/delete")
    def forget_link(link_id: int) -> RedirectResponse:
        conn = connect()
        try:
            company_id = clients_store.delete_email_link(conn, link_id)
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/company/{company_id}" if company_id else "/mail",
                                status_code=303)

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

    @app.get("/quote/{quote_id}", response_class=HTMLResponse)
    def one_quote(quote_id: int, request: Request) -> HTMLResponse:
        conn = connect()
        try:
            quote = sales_store.quote(conn, quote_id)
            if quote is None:
                return HTMLResponse("<h1>Not found</h1>", status_code=404)
            versions = sales_store.versions_for(conn, quote_id)
            if not versions:
                return HTMLResponse("<h1>No versions</h1>", status_code=404)
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
                sales_store.recompute(conn, version_id, vat_rate=_vat_for(conn))
                conn.commit()
            except ValueError:
                # A price the card does not cover, or a sent version. Both are
                # refusals the store makes on purpose; the page redraws showing
                # what is actually there.
                conn.rollback()
            return RedirectResponse(f"/quote/{held['quote_id']}", status_code=303)
        finally:
            conn.close()

    @app.post("/quote-line/{line_id}/delete")
    def drop_quote_line(line_id: int) -> RedirectResponse:
        conn = connect()
        try:
            row = conn.execute(
                "SELECT v.id AS version_id, v.quote_id FROM commercial.quote_line l"
                " JOIN commercial.quote_version v ON v.id = l.quote_version_id"
                " WHERE l.id = %s", (line_id,)
            ).fetchone()
            if row is None:
                return RedirectResponse("/clients", status_code=303)
            sales_store.delete_line(conn, line_id)
            sales_store.recompute(conn, int(row["version_id"]), vat_rate=_vat_for(conn))
            conn.commit()
            return RedirectResponse(f"/quote/{row['quote_id']}", status_code=303)
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
            sales_store.recompute(conn, version_id, vat_rate=_vat_for(conn))
            sales_store.mark_sent(conn, version_id, to=to)
            conn.commit()
            return RedirectResponse(f"/quote/{held['quote_id']}", status_code=303)
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
                return HTMLResponse("<h1>Not found</h1>", status_code=404)
            wanted = request.query_params.get("v", "")
            shown = next(
                (v for v in versions if str(v["version"]) == wanted), versions[0]
            )
            client = clients_store.client(conn, int(quote["client_id"]))
            return HTMLResponse(money_view.quote_print(
                quote, sales_store.version(conn, int(shown["id"])),
                sales_store.lines_for(conn, int(shown["id"])),
                _address_of(conn, int(client["company_id"])),
                issuer=settings.issuer,
            ))
        finally:
            conn.close()

    @app.post("/client/{client_id}/invoices")
    def start_invoice(client_id: int) -> RedirectResponse:
        conn = connect()
        try:
            invoice_id = sales_store.create_invoice(conn, client_id)
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/invoice/{invoice_id}", status_code=303)

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
    def one_invoice(invoice_id: int) -> HTMLResponse:
        conn = connect()
        try:
            invoice = sales_store.invoice(conn, invoice_id)
            if invoice is None:
                return HTMLResponse("<h1>Not found</h1>", status_code=404)
            return HTMLResponse(money_view.invoice_page(
                invoice, sales_store.invoice_lines(conn, invoice_id),
                sales_store.services(conn), issuer=settings.issuer,
                chrome=at("/invoices"),
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
            if name:
                sales_store.add_invoice_line(conn, invoice_id, {
                    "service_id": int(service_id) if service_id else None,
                    "label": name, "quantity": quantity or 1,
                    "unit_price": unit_price or 0,
                })
                sales_store.recompute_invoice(conn, invoice_id, vat_rate=_vat_for(conn))
                conn.commit()
        except ValueError:
            conn.rollback()
        finally:
            conn.close()
        return RedirectResponse(f"/invoice/{invoice_id}", status_code=303)

    @app.post("/invoice-line/{line_id}/delete")
    def drop_bill_line(line_id: int) -> RedirectResponse:
        conn = connect()
        try:
            row = conn.execute(
                "SELECT invoice_id FROM commercial.invoice_line WHERE id = %s",
                (line_id,)
            ).fetchone()
            if row is None:
                return RedirectResponse("/clients", status_code=303)
            conn.execute("DELETE FROM commercial.invoice_line WHERE id = %s", (line_id,))
            sales_store.recompute_invoice(conn, int(row["invoice_id"]),
                                          vat_rate=_vat_for(conn))
            conn.commit()
            return RedirectResponse(f"/invoice/{row['invoice_id']}", status_code=303)
        finally:
            conn.close()

    @app.post("/invoice/{invoice_id}/issue")
    def issue(invoice_id: int) -> RedirectResponse:
        conn = connect()
        try:
            sales_store.recompute_invoice(conn, invoice_id, vat_rate=_vat_for(conn))
            sales_store.issue_invoice(
                conn, invoice_id, days=settings.issuer.payment_days,
                qr_iban=settings.issuer.iban, by="console",
            )
            conn.commit()
        except ValueError:
            conn.rollback()
        finally:
            conn.close()
        return RedirectResponse(f"/invoice/{invoice_id}", status_code=303)

    @app.post("/invoice/{invoice_id}/payment")
    def pay(invoice_id: int, amount: str = Form(...)) -> RedirectResponse:
        conn = connect()
        try:
            sales_store.record_payment(conn, invoice_id, amount=amount, by="console")
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/invoice/{invoice_id}", status_code=303)

    @app.post("/invoice/{invoice_id}/credit")
    def credit(invoice_id: int, reason: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            note_id = sales_store.credit_invoice(conn, invoice_id, reason=reason,
                                                 by="console")
            conn.commit()
            return RedirectResponse(f"/invoice/{note_id}", status_code=303)
        except ValueError:
            conn.rollback()
            return RedirectResponse(f"/invoice/{invoice_id}", status_code=303)
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
                return HTMLResponse("<h1>Not found</h1>", status_code=404)
            client = clients_store.client(conn, int(invoice["client_id"]))
            address = _address_of(conn, int(client["company_id"]))
            issuer = settings.issuer
            qr, problem = "", ""
            if invoice["status"] == "draft":
                problem = ("A draft has no number and no payment reference. "
                           "Issue it to produce a payment part.")
            elif not issuer.can_bill:
                problem = ("[issuer] in arpenteur.toml needs a name and an "
                           "IBAN before a payment part can be produced.")
            else:
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
                        debtor=money_lib.Party(
                            name=invoice["company_name"],
                            street=str((address or {}).get("street", "") or ""),
                            building=str((address or {}).get("houseNumber", "") or ""),
                            postcode=str((address or {}).get("swissZipCode", "") or ""),
                            town=str((address or {}).get("town", "") or ""),
                        ),
                        reference=invoice["qr_reference"] or "",
                        message=f"Invoice {invoice['number']}",
                    )
                    qr = money_view.qr_bill(
                        invoice, address, issuer=issuer,
                        svg=money_lib.qr_svg(payload),
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
                note=request.query_params.get("note", ""),
            ))
        finally:
            conn.close()

    @app.post("/reports/refresh")
    def refresh_reports() -> RedirectResponse:
        conn = connect()
        try:
            done = analytics_store.refresh(conn)
            note = (f"Refreshed {len(done)} view(s): {', '.join(done)}."
                    if done else "Nothing materialised to refresh.")
        except Exception as exc:
            note = f"The refresh failed: {exc}"
        finally:
            conn.close()
        return RedirectResponse(f"/reports?note={quote(note)}", status_code=303)

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
                return RedirectResponse(
                    f"/clients?note={quote(str(exc))}", status_code=303
                )
            return RedirectResponse(f"/client/{client_id}", status_code=303)
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
                return HTMLResponse("<h1>Not found</h1>", status_code=404)
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
                    note=request.query_params.get("note", ""),
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
            return RedirectResponse(f"/client/{client_id}", status_code=303)
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
            project_id = db.add_project(
                conn, client_id, name.strip(),
                billing=billing or None,
                budget=float(budget) if budget.strip() else None,
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
            return RedirectResponse(f"/project/{project_id}", status_code=303)
        finally:
            conn.close()

    @app.get("/project/{project_id}", response_class=HTMLResponse)
    def project_space(project_id: int) -> HTMLResponse:
        conn = connect()
        try:
            project = clients_store.project(conn, project_id)
            if project is None:
                return HTMLResponse("<h1>Not found</h1>", status_code=404)
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
            db.update_project(
                conn, project_id, name=name or None, status=status or None,
                billing=billing, budget=float(budget) if budget.strip() else None,
                due_at=due_at or None, summary=summary,
            )
            if before and status and status != before["status"]:
                db.record_event(
                    conn, before["client_id"], kind="decision", occurred_at=db.now(),
                    subject=f"Status: {before['status']} to {status}",
                    project_id=project_id, author="console",
                )
            conn.commit()
            return RedirectResponse(f"/project/{project_id}", status_code=303)
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
                return HTMLResponse("<h1>Not found</h1>", status_code=404)
            args = request.query_params
            return HTMLResponse(
                notes_view.shell(
                    notes_view.notes_page(
                        client, company,
                        clients_store.notes_for(conn, client_id, q=args.get("q", "")),
                        q=args.get("q", ""),
                        note=args.get("note", ""),
                        projects=clients_store.projects_for(conn, client_id),
                    ),
                    at("/clients"),
                )
            )
        finally:
            conn.close()

    @app.post("/client/{client_id}/notes")
    async def add_note(client_id: int, request: Request) -> RedirectResponse:
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
                return RedirectResponse(
                    f"/client/{client_id}/notes?note="
                    + quote(
                        f"{upload.filename} is not UTF-8 text. Notes are "
                        "Markdown; a PDF or a Word file belongs under the "
                        "dossier as a document."
                    ),
                    status_code=303,
                )
            source = upload.filename
        if not body.strip():
            return RedirectResponse(
                f"/client/{client_id}/notes?note="
                + quote("Nothing to save: paste some Markdown or choose a file."),
                status_code=303,
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
            return RedirectResponse(
                f"/client/{client_id}/notes#note-{note_id}", status_code=303
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
                return HTMLResponse("<h1>Not found</h1>", status_code=404)
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
            if row is None or not body.strip():
                return RedirectResponse("/clients", status_code=303)
            conn.execute(
                "UPDATE crm.note SET title = %s, body = %s, sha256 = %s,"
                " tags = %s, updated_at = %s WHERE id = %s",
                (md.title_of(body), body, md.digest(body), _tags(tags),
                 db.now(), note_id),
            )
            conn.commit()
            return RedirectResponse(f"/note/{note_id}", status_code=303)
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
            return RedirectResponse(
                f"/client/{client_id}/notes" if client_id else "/clients",
                status_code=303,
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
                return HTMLResponse("<h1>Not found</h1>", status_code=404)
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
                    bool(_fresh().rate_card.strip()),
                    request.query_params.get("note", ""),
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

        Run inline rather than through the stage runner. The runner exists to
        keep one long expensive sweep from colliding with another; this is one
        model call about one client and finishes while the request is open, so
        queueing it behind a canton-wide sweep would be an odd thing to do to
        someone who is about to walk into a meeting.
        """
        conn = connect()
        try:
            run_id = db.start_run(
                conn, "advise", {"client_id": client_id, "dry_run": bool(dry_run)},
                started_by="console",
            )
            try:
                stats = advise.run(
                    _fresh(), conn, client_id=client_id, dry_run=bool(dry_run),
                    log=lambda m: db.append_line(conn, run_id, m),
                )
                db.finish_run(conn, run_id, stats)
            except Exception as exc:
                db.finish_run(conn, run_id, error=str(exc))
                return RedirectResponse(
                    f"/client/{client_id}/dossier?note={quote(str(exc))}",
                    status_code=303,
                )
            if dry_run:
                return RedirectResponse(f"/runs/{run_id}", status_code=303)
            return RedirectResponse(f"/client/{client_id}/dossier", status_code=303)
        finally:
            conn.close()

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
        conn = connect()
        try:
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
                        "text": text,
                        "extract_error": error,
                    },
                )
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
                    clients_store.attachments_for(conn, client_id), note=" ".join(said)
                ))
            return RedirectResponse(
                f"{back}?note=" + quote(" ".join(said)), status_code=303
            )
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
                return HTMLResponse("<h1>Not found</h1>", status_code=404)
            live = _fresh()
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
                        request.query_params.get("note", ""),
                    ),
                    at("/clients"),
                )
            )
        finally:
            conn.close()

    @app.post("/client/{client_id}/prep")
    def extract_vocabulary(client_id: int, dry_run: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            run_id = db.start_run(
                conn, "prep", {"client_id": client_id, "dry_run": bool(dry_run)},
                started_by="console",
            )
            try:
                stats = prep.run(
                    _fresh(), conn, client_id=client_id, dry_run=bool(dry_run),
                    log=lambda m: db.append_line(conn, run_id, m),
                )
                db.finish_run(conn, run_id, stats)
            except Exception as exc:
                db.finish_run(conn, run_id, error=str(exc))
                return RedirectResponse(
                    f"/client/{client_id}/prep?note={quote(str(exc))}", status_code=303
                )
            if dry_run:
                return RedirectResponse(f"/runs/{run_id}", status_code=303)
            return RedirectResponse(f"/client/{client_id}/prep", status_code=303)
        finally:
            conn.close()

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
            return RedirectResponse(
                f"/client/{row['client_id']}" if row else "/clients", status_code=303
            )
        finally:
            conn.close()

    @app.post("/project/{project_id}/delete")
    def remove_project(project_id: int, reason: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            client_id = db.delete_project(conn, project_id, reason=reason)
            conn.commit()
            return RedirectResponse(
                f"/client/{client_id}" if client_id else "/clients", status_code=303
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
                return RedirectResponse("/clients", status_code=303)
            client_id, digest = removed
            conn.commit()
            # Storage is content-addressed, so the file only goes when nobody
            # else references those bytes: another client may legitimately hold
            # the same standard document and unlinking would take theirs too.
            if db.sha_is_orphaned(conn, digest):
                folder = att.root_for(settings.data_dir) / digest[:2]
                for path in folder.glob(f"{digest}*"):
                    path.unlink(missing_ok=True)
            return RedirectResponse(f"/client/{client_id}/dossier", status_code=303)
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
            except ValueError:
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

    def _fresh() -> Settings:
        """Settings re-read after a change, so a redirect shows the new value."""
        return load_settings(settings.root)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_view(request: Request) -> HTMLResponse:
        conn = connect()
        try:
            live = _fresh()
            counts = scout_store.counts_by_legal_form(conn)
            brief_path = live.root / "campaign.md"
            text = brief_path.read_text(encoding="utf-8") if brief_path.is_file() else ""
            return HTMLResponse(
                settings_view_page.shell(
                    settings_view_page.settings_page(
                        cantons=list(live.territory.cantons),
                        forms=list(live.territory.legal_forms),
                        catalogue=db.catalogue(conn),
                        counts=counts,
                        segments=dict(live.segments),
                        signal_brief=live.signal_brief,
                        campaign=text,
                        campaign_digest=match.brief_digest(text.strip()) if text.strip() else "—",
                        scored=scout_store.scored_count(conn),
                        campaign_path="campaign.md",
                        history=db.setting_history(conn),
                        rate_card=live.rate_card,
                        note=request.query_params.get("note", ""),
                    ),
                    live.territory.describe(),
                )
            )
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
            return RedirectResponse(
                "/settings?note=" + quote(
                    "Pick at least one canton and one legal form. An empty list "
                    "means no restriction, which would widen the survey rather "
                    "than narrow it."
                ),
                status_code=303,
            )
        conn = connect()
        try:
            db.save_setting(conn, "cantons", cantons)
            db.save_setting(conn, "legal_forms", forms)
            conn.commit()
            return RedirectResponse("/settings", status_code=303)
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
            return RedirectResponse("/settings", status_code=303)
        except Exception as exc:
            return RedirectResponse(
                f"/settings?note={quote(str(exc))}", status_code=303
            )
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
            return RedirectResponse(
                "/settings?note=" + quote(
                    "A survey with no segments cannot be sliced. Keep at least one."
                ),
                status_code=303,
            )
        conn = connect()
        try:
            db.save_setting(conn, "segments", segments)
            conn.commit()
            return RedirectResponse("/settings", status_code=303)
        finally:
            conn.close()

    @app.post("/settings/signal")
    def save_signal(signal_brief: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            db.save_setting(conn, "signal_brief", " ".join(signal_brief.split()))
            conn.commit()
            return RedirectResponse("/settings", status_code=303)
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
            return RedirectResponse("/settings", status_code=303)
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
            return RedirectResponse(
                "/settings?note=" + quote(
                    "An empty brief would produce confident scores built on "
                    "nothing, which is worse than no scores."
                ),
                status_code=303,
            )
        (settings.root / "campaign.md").write_text(campaign, encoding="utf-8")
        return RedirectResponse("/settings", status_code=303)

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

            companies, _total = scout_store.search_companies(conn, q=q, limit=6)
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
