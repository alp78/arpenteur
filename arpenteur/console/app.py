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

**It does not start expensive stages.** Running `enrich` or `match` from a web
form would make it far too easy to spend a lot of money from a page that looks
like a filter. The stages stay on the command line where the cost guards and
the `--dry-run` habit live; the console watches what they did.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import attribution, db
from ..config import Settings, load_settings
from . import render


def create_app(settings: Settings | None = None) -> FastAPI:
    """Built by a factory so tests can point it at a temporary database."""
    settings = settings or load_settings()
    app = FastAPI(title="arpenteur", docs_url=None, redoc_url=None)
    app.state.settings = settings

    def connect() -> sqlite3.Connection:
        # One connection per request. SQLite in WAL mode handles concurrent
        # readers, and a long-lived shared connection would serialise the
        # console behind whatever a stage is writing.
        return db.open_db(settings.db_path)

    def shell(body: str) -> HTMLResponse:
        return HTMLResponse(
            render.page("arpenteur", body, settings.territory.describe())
        )

    # --- the company list -----------------------------------------------------

    def _query(
        conn: sqlite3.Connection, *, q: str, canton: str, segment: str,
        band: str, state: str, limit: int = 200,
    ) -> tuple[list[sqlite3.Row], int]:
        where = ["1=1"]
        params: list[Any] = []
        if q:
            where.append("(c.name LIKE ? OR c.uid LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        if canton:
            where.append("c.canton = ?")
            params.append(canton)
        if segment:
            where.append(
                "EXISTS (SELECT 1 FROM segment s WHERE s.company_id = c.id AND s.segment = ?)"
            )
            params.append(segment)
        if band:
            where.append("f.band = ?")
            params.append(band)
        if state:
            where.append("p.state = ?")
            params.append(state)
        clause = " AND ".join(where)
        sql = f"""
            SELECT c.id, c.uid, c.name, c.canton,
                   (SELECT json_group_array(s.segment) FROM segment s
                     WHERE s.company_id = c.id) AS segments,
                   f.score, f.band, f.source, w.url, p.state
              FROM company c
              LEFT JOIN fit f ON f.company_id = c.id
              LEFT JOIN site w ON w.company_id = c.id
              LEFT JOIN prospect p ON p.company_id = c.id
             WHERE {clause}
             ORDER BY f.score DESC NULLS LAST, c.name
             LIMIT ?
        """
        rows = conn.execute(sql, params + [limit]).fetchall()
        total = conn.execute(
            f"""SELECT COUNT(*) FROM company c
                LEFT JOIN fit f ON f.company_id = c.id
                LEFT JOIN prospect p ON p.company_id = c.id
                WHERE {clause}""",
            params,
        ).fetchone()[0]
        return rows, total

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        conn = connect()
        try:
            args = request.query_params
            rows, total = _query(
                conn, q=args.get("q", ""), canton=args.get("canton", ""),
                segment=args.get("segment", ""), band=args.get("band", ""),
                state=args.get("state", ""),
            )
            cantons = [r[0] for r in conn.execute(
                "SELECT DISTINCT canton FROM company WHERE canton IS NOT NULL ORDER BY canton"
            )]
            body = (
                "<h1>Companies</h1>"
                + render.filter_form(
                    q=args.get("q", ""), canton=args.get("canton", ""),
                    segment=args.get("segment", ""), band_name=args.get("band", ""),
                    state=args.get("state", ""), cantons=cantons,
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
            rows, total = _query(
                conn, q=args.get("q", ""), canton=args.get("canton", ""),
                segment=args.get("segment", ""), band=args.get("band", ""),
                state=args.get("state", ""),
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
            row = conn.execute(
                "SELECT * FROM company WHERE id = ?", (company_id,)
            ).fetchone()
            if row is None:
                return HTMLResponse("<h1>Not found</h1>", status_code=404)
            site = conn.execute(
                "SELECT * FROM site WHERE company_id = ?", (company_id,)
            ).fetchone()
            methods = ["zefix"]
            if site is not None:
                methods.append("web-search")
            people = conn.execute(
                "SELECT * FROM person WHERE company_id = ? ORDER BY role", (company_id,)
            ).fetchall()
            if people:
                methods.append("shab")
            body = render.company_page(
                row,
                conn.execute(
                    "SELECT * FROM segment WHERE company_id = ? ORDER BY segment",
                    (company_id,),
                ).fetchall(),
                people,
                site,
                conn.execute(
                    "SELECT * FROM fit WHERE company_id = ?", (company_id,)
                ).fetchone(),
                conn.execute(
                    "SELECT * FROM contact WHERE company_id = ? ORDER BY id", (company_id,)
                ).fetchall(),
                db.notes_for(conn, company_id),
                conn.execute(
                    "SELECT * FROM prospect WHERE company_id = ?", (company_id,)
                ).fetchone(),
                conn.execute(
                    "SELECT * FROM publication WHERE company_id = ? ORDER BY sogc_date DESC",
                    (company_id,),
                ).fetchall(),
                sorted(settings.segments),
                list(db.PROSPECT_STATES),
                # Zefix grants access on condition that origin is stated and
                # any modification declared; the methods behind this page
                # decide which notices apply.
                attribution.notice(methods),
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
                    conn.execute(
                        "SELECT * FROM segment WHERE company_id = ? ORDER BY segment",
                        (company_id,),
                    ).fetchall(),
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
                    conn.execute(
                        "SELECT * FROM prospect WHERE company_id = ?", (company_id,)
                    ).fetchone(),
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
            conn.execute(
                "UPDATE contact SET suppressed = 1 - suppressed WHERE id = ?",
                (contact_id,),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM contact WHERE id = ?", (contact_id,)
            ).fetchone()
            return HTMLResponse(render.contact_row(row))
        finally:
            conn.close()

    # --- watching the pipeline ------------------------------------------------

    @app.get("/runs", response_class=HTMLResponse)
    def runs() -> HTMLResponse:
        conn = connect()
        try:
            rows = conn.execute(
                "SELECT * FROM run ORDER BY id DESC LIMIT 40"
            ).fetchall()
            return shell(render.runs_page(rows))
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
            rows = conn.execute(
                "SELECT * FROM run ORDER BY id DESC LIMIT 40"
            ).fetchall()
            return HTMLResponse(render.runs_table(rows))
        finally:
            conn.close()

    @app.get("/pipeline", response_class=HTMLResponse)
    def pipeline() -> HTMLResponse:
        conn = connect()
        try:
            counts = {
                "companies": "SELECT COUNT(*) FROM company",
                "hydrated": "SELECT COUNT(*) FROM company WHERE hydrated_at IS NOT NULL",
                "awaiting refresh": "SELECT COUNT(*) FROM company WHERE stale_since IS NOT NULL",
                "classified": "SELECT COUNT(DISTINCT company_id) FROM segment",
                "researched": "SELECT COUNT(*) FROM site",
                "scored": "SELECT COUNT(*) FROM fit",
                "in the pipeline": "SELECT COUNT(*) FROM prospect",
            }
            rows = "".join(
                f"<tr><td>{name}</td><td>{conn.execute(sql).fetchone()[0]}</td></tr>"
                for name, sql in counts.items()
            )
            return shell(
                "<h1>Pipeline</h1>"
                "<p class='dim small'>Stages run from the command line, where the "
                "cost guards and the dry runs are. This is what they have produced.</p>"
                f"<table><tbody>{rows}</tbody></table>"
                "<h2>next</h2><div class='card small' style='white-space:pre-line'>"
                "arpenteur sync            keep the register copy current\n"
                "arpenteur hydrate         fetch detail for new and changed companies\n"
                "arpenteur profile         sort into segments\n"
                "arpenteur enrich --limit  read websites (costs money per company)\n"
                "arpenteur match --limit   score against campaign.md</div>"
            )
        finally:
            conn.close()

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
