"""Companies, the board, people and drafts: the survey as it is read and corrected.

Routes of the scout module, mounted when it is enabled. Moved out of
`app.py` verbatim: the bodies close over `ctx` where they closed over the
factory's locals, and read `ctx.settings` at request time so a saved
setting reaches the next request.
"""

from __future__ import annotations


from datetime import date
from typing import Any


from fastapi import Form, Request
from fastapi.responses import (
    HTMLResponse, PlainTextResponse, RedirectResponse,
)

from ... import attribution, db
from ...kernel.db import Connection
from ...modules.scout import store as scout_store
from ...stages import (
    draft as draft_stage,
)
from .. import ui
from .. import board as board_view
from .. import score_page as score_view
from .. import people_page as people_view_page
from .. import draft_page as draft_view
from .. import render
from fastapi import APIRouter

from ..context import Console


def _chips(ctx: Console, conn: Connection, company_ids: list[int]) -> dict[int, Any]:
    """Which of these companies are clients, from whichever module knows."""
    chips: dict[int, Any] = {}
    for found in ctx.registry.gather("board.clients", conn, company_ids):
        chips.update(found or {})
    return chips


def router(ctx: Console) -> APIRouter:
    router = APIRouter()
    connect = ctx.connect
    at = ctx.at
    shell = ctx.shell
    _back = ctx.back
    runner = ctx.runner

    # --- the company list -----------------------------------------------------

    def _filtered(args: Any) -> bool:
        """Whether any filter is set, which decides what an empty list says."""
        return any(
            str(args.get(key, "")).strip()
            for key in ("q", "canton", "segment", "sector", "band", "state", "stage")
        )

    @router.get("/companies", response_class=HTMLResponse)
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
                    segments=sorted(ctx.settings.segments), states=list(db.PROSPECT_STATES),
                    sector=args.get("sector", ""),
                    sectors=sorted(ctx.settings.sectors),
                )
                + f'<div id="list">{render.company_table(rows, total, filtered=_filtered(args))}</div>'
            )
            return shell(body, "/companies")
        finally:
            conn.close()

    @router.get("/companies/rows", response_class=HTMLResponse)
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

    @router.get("/company/{company_id}", response_class=HTMLResponse)
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
                sorted(ctx.settings.segments),
                list(db.PROSPECT_STATES),
                # Zefix grants access on condition that origin is stated and
                # any modification declared; the methods behind this page
                # decide which notices apply.
                attribution.notice(methods),
                runner.running(),
                ctx.registry.fill("company.actions", conn, company_id),
                parties=scout_store.parties_for_company(conn, company_id),
                overlap=people_view_page.overlap_panel(
                    scout_store.board_overlap(conn, company_id), company_id
                ),
                panels=ctx.registry.fill("company.panels", conn, company_id),
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
                sector_vocabulary=sorted(ctx.settings.sectors),
            )
            return shell(body, "/companies")
        finally:
            conn.close()

    # --- corrections ----------------------------------------------------------

    @router.post("/company/{company_id}/segments", response_class=HTMLResponse)
    async def set_segments(company_id: int, request: Request) -> HTMLResponse:
        """Labels as a person sees them, marked so no re-run undoes it."""
        form = await request.form()
        chosen = set(form.getlist("segment"))
        conn = connect()
        try:
            for name in ctx.settings.segments:
                db.set_segment_manually(
                    conn, company_id, name, present=name in chosen, author="console"
                )
            conn.commit()
            return HTMLResponse(
                render._segments_card(
                    company_id,
                    scout_store.segments_for(conn, company_id),
                    sorted(ctx.settings.segments),
                )
            )
        finally:
            conn.close()

    @router.post("/company/{company_id}/sector", response_class=HTMLResponse)
    async def set_sector(company_id: int, request: Request) -> HTMLResponse:
        """The trade as a person sees it, marked so no re-run undoes it.

        A name outside the vocabulary is refused rather than stored. The only
        way to send one is to have edited the form, and accepting it would put
        a sector in the filter dropdown that no definition anywhere explains.
        """
        form = await request.form()
        chosen = str(form.get("sector") or "").strip()
        if chosen and chosen not in ctx.settings.sectors:
            chosen = ""
        conn = connect()
        try:
            db.set_sector_manually(conn, company_id, chosen, author="console")
            conn.commit()
            return HTMLResponse(
                render._sector_card(
                    company_id,
                    scout_store.sector_for(conn, company_id),
                    sorted(ctx.settings.sectors),
                )
            )
        finally:
            conn.close()

    @router.post("/company/{company_id}/prospect", response_class=HTMLResponse)
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
                state=state or None, owner=owner.strip(),
                next_action=next_action.strip(), next_at=next_at.strip(),
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

    @router.post("/company/{company_id}/notes", response_class=HTMLResponse)
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

    @router.post("/contact/{contact_id}/suppress", response_class=HTMLResponse)
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

    @router.get("/board", response_class=HTMLResponse)
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
                        clients=_chips(ctx, conn, [int(r["id"]) for r in rows]),
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
                        segments=sorted(ctx.settings.segments),
                        sector=args.get("sector", ""),
                        sectors=sorted(ctx.settings.sectors),
                        owners=scout_store.prospect_owners(conn),
                        back=str(request.url.query or ""),
                    ),
                    at("/board"),
                )
            )
        finally:
            conn.close()

    @router.post("/board/move")
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

    @router.post("/board/bring-in")
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

    @router.get("/people", response_class=HTMLResponse)
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

    @router.get("/party/{party_id}", response_class=HTMLResponse)
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

    @router.get("/company/{company_id}/route/{target_id}", response_class=HTMLResponse)
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

    # --- drafts -----------------------------------------------------------------
    #
    # The review surface for what the draft stage writes. Reading them was
    # a psql session, which is how a queue of twenty-five goes out unread.

    def draft_shell(body: str) -> HTMLResponse:
        return HTMLResponse(draft_view.shell(body, at("/drafts")))

    @router.get("/drafts", response_class=HTMLResponse)
    def drafts_queue(request: Request, language: str = "") -> HTMLResponse:
        with connect() as conn:
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
                    draft_stage.brief_text(ctx.settings)
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

    @router.get("/draft/{draft_id}", response_class=HTMLResponse)
    def one_draft(request: Request, draft_id: int) -> HTMLResponse:
        with connect() as conn:
            row = scout_store.draft(conn, draft_id)
            if row is None:
                return shell(render.not_found(), status=404)
            return draft_shell(draft_view.one_page(row))

    @router.post("/draft/{draft_id}/review")
    def approve_draft(draft_id: int) -> RedirectResponse:
        with connect() as conn:
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

    @router.post("/draft/{draft_id}/unreview")
    def unapprove_draft(draft_id: int) -> RedirectResponse:
        with connect() as conn:
            scout_store.unreview_draft(conn, draft_id)
            conn.commit()
        return _back(f"/draft/{draft_id}", note="The approval is withdrawn.")

    @router.post("/draft/{draft_id}/sent")
    def draft_was_sent(draft_id: int) -> RedirectResponse:
        """Record that a message left, and move the card that says so.

        Two records of one fact, which is why they are written together.
        `intel.draft.sent_at` says this proposal was sent; the programme card
        says the firm has been contacted. Writing only the first is what left
        the campaign's own tracker empty while messages went out.
        """
        said = "Recorded as sent."
        with connect() as conn:
            row = scout_store.mark_draft_sent(conn, draft_id)
            if row is not None:
                # The programme the draft belongs to moves its card, and the
                # programmes are the clients module's: it answers through the
                # `draft.sent` slot with the sentence that says what moved.
                said += "".join(ctx.registry.gather(
                    "draft.sent", conn, draft_id, row, ctx.settings.enrol.programme
                ))
            conn.commit()
        return _back("/drafts", note=said)

    @router.post("/draft/{draft_id}/refuse")
    def refuse_one_draft(
        draft_id: int, reason: str = Form(""),
    ) -> RedirectResponse:
        with connect() as conn:
            scout_store.refuse_draft(conn, draft_id, reason=reason)
            conn.commit()
        return _back(
            "/drafts",
            note="Refused, and kept: a later run will not propose that "
                 "mailbox again.",
        )

    return router
