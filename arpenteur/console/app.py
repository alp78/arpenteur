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
from fastapi.responses import (
    HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse,
)

from .. import attachments as att
from .. import attribution, db, dossier
from ..kernel import modules as module_registry
from ..kernel.db import Connection, Row
from ..modules.clients import store as clients_store
from ..modules.geo import store as geo_store
from ..modules.scout import store as scout_store
from ..config import Settings, load_settings
from ..sources import geoadmin
from ..stages import advise, geocode, match, prep, source
from . import board as board_view
from . import clients as clients_view
from . import map_page as map_view_page
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

    def shell(body: str) -> HTMLResponse:
        return HTMLResponse(
            render.page("arpenteur", body, settings.territory.describe())
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
            return shell(body)
        finally:
            conn.close()

    @app.get("/companies", response_class=HTMLResponse)
    def companies(request: Request) -> HTMLResponse:
        """The filter's HTMX target: the table alone, no page furniture."""
        conn = connect()
        try:
            args = request.query_params
            rows, total = scout_store.search_companies(
                conn, q=args.get("q", ""), canton=args.get("canton", ""),
                segment=args.get("segment", ""), band=args.get("band", ""),
                state=args.get("state", ""), stage=args.get("stage", ""),
            )
            return HTMLResponse(
                f'<div id="list">{render.company_table(rows, total)}</div>'
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
            )
            return shell(body)
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
                    settings.territory.describe(),
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
                        attribution_notice=attribution.notice(["zefix", "geoadmin"]),
                        **chosen,
                    ),
                    settings.territory.describe(),
                )
            )
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
            return shell(render.runs_page(db.runs(conn)))
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
            return shell(render.run_page(run, rows, after))
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
            enabled = set(getattr(settings, "modules", None) or module_registry.DEFAULT)
            stages = [STAGES[name] for name in ORDER if STAGES[name].module in enabled]
            return shell(
                render.pipeline_page(
                    stages,
                    {stage.name: status_of(conn, stage) for stage in stages},
                    options_for(conn, settings),
                    runner.running(),
                    request.query_params.get("note", ""),
                )
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
            clients_view.shell("arpenteur", body, settings.territory.describe())
        )

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
                    db.timeline(conn, client_id),
                    scout_store.contacts_for(conn, client["company_id"]),
                    officers,
                    attribution.notice(methods),
                    dossier_filled=len(dossier.filled(sections)),
                    dossier_total=len(dossier.SECTIONS),
                    documents=clients_store.attachment_count(conn, client_id),
                    has_approach=clients_store.has_approach(conn, client_id),
                    sites=geo_store.establishments_for(conn, client["company_id"]),
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

    # --- the dossier, the advice and the client's papers ------------------------
    #
    # See migrations/0014_dossier.sql. What is known, what follows from it, and
    # what it was built on, on one page because they are one thing in use.

    def dossier_shell(body: str) -> HTMLResponse:
        return HTMLResponse(
            dossier_view.shell(body, settings.territory.describe())
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
                    db.attachments_for(conn, client_id),
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

    @app.post("/dossier/{client_id}/documents")
    async def upload_document(
        client_id: int,
        file: UploadFile = File(...),
        kind: str = Form(""),
        note: str = Form(""),
        project_id: str = Form(""),
    ) -> RedirectResponse:
        """Store a document the client sent, and extract its text.

        Extraction happens here rather than at read time so that a file nothing
        can open is a recorded state on the row instead of a silent absence
        every time an agent asks for it.
        """
        content = await file.read()
        back = f"/client/{client_id}/dossier"
        conn = connect()
        try:
            try:
                stored = att.store(settings.data_dir, content, file.filename or "file")
            except ValueError as exc:
                return RedirectResponse(f"{back}?note={quote(str(exc))}", status_code=303)
            text, error = att.extract(
                content, file.filename or "", file.content_type or ""
            )
            _id, existed = db.add_attachment(
                conn,
                client_id,
                {
                    "filename": file.filename or stored.sha256,
                    "media_type": file.content_type or "",
                    "bytes": stored.bytes,
                    "sha256": stored.sha256,
                    "kind": kind or None,
                    "note": note.strip() or None,
                    "project_id": int(project_id) if project_id.strip() else None,
                    "text": text,
                    "extract_error": error,
                },
            )
            if not existed:
                # A document arriving is part of the record. The timeline
                # should be able to answer "when did we get the accounts"
                # without anyone remembering to write a note saying so.
                db.record_event(
                    conn, client_id, kind="document", occurred_at=db.now(),
                    subject=f"Document received: {file.filename}",
                    body=note.strip() or None,
                    project_id=int(project_id) if project_id.strip() else None,
                    author="console",
                )
            conn.commit()
            if existed:
                return RedirectResponse(
                    f"{back}?note=" + quote(
                        f"{file.filename} is byte for byte a document already "
                        "held, so the label and note were updated rather than a "
                        "second copy stored."
                    ),
                    status_code=303,
                )
            if error:
                return RedirectResponse(
                    f"{back}?note=" + quote(f"Stored, but not readable. {error}"),
                    status_code=303,
                )
            return RedirectResponse(back, status_code=303)
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
                    settings.territory.describe(),
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

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
