"""Clients, programmes, lists, campaigns, mail, notes, the dossier and the preparation.

Routes of the clients module, mounted when it is enabled. Moved out of
`app.py` verbatim: the bodies close over `ctx` where they closed over the
factory's locals, and read `ctx.settings` at request time so a saved
setting reaches the next request.
"""

from __future__ import annotations


import re
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote


from fastapi import File, Form, Request, UploadFile
from fastapi import Response
from fastapi.responses import (
    HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse,
)

from ... import attachments as att
from ... import attribution, db, dossier
from ... import markdown as md
from ...kernel.db import Connection
from ...modules.clients import store as clients_store
from ...modules.clients import campaigns as campaign_store
from ...modules.scout import store as scout_store
from ...sources import letter as letter_source
from ...sources import mail as mail_source
from ...sources import smtp as smtp_source
from ...stages import (
    answers as answers_stage,
    audience as audience_stage,
    campaign as campaign_stage,
    send as send_stage,
    mail as mail_stage,
    source,
)
from .. import ui
from .. import clients as clients_view
from .. import mail_page as mail_view
from .. import notes_page as notes_view
from .. import ledger_page as ledger_view
from .. import campaign_page as campaign_view
from .. import signature_page as signature_view
from .. import programme_page as programme_view
from .. import dossier_page as dossier_view
from .. import prep_page as prep_view
from .. import render
from fastapi import APIRouter

from ...kernel import figures
from ...kernel.modules import Contribution
from .. import home_page as home_view
from ..context import Console


# --- what this module puts on other modules' pages ---------------------------


def _company_action(conn: Connection, company_id: int) -> str:
    """Promote to client, or the link to the client it already is."""
    return clients_view.company_action(
        company_id, clients_store.client_for_company(conn, company_id)
    )


def _mail_links(conn: Connection, company_id: int) -> str:
    return mail_view.links_panel(clients_store.links_for(conn, company_id))


def _tiles(conn: Connection) -> str:
    crm = clients_store.home_counts(conn)
    return (
        home_view.tile("client", "clients", crm["clients"], "/clients")
        + home_view.tile("campaign under way", "campaigns under way",
                         crm["campaigns_running"], "/campaigns")
        + home_view.tile("message waiting", "messages waiting", crm["mail_waiting"], "/mail")
    )


def _board_clients(conn: Connection, company_ids: list[int]) -> dict[int, Any]:
    """The client chip on a board card, for the companies that are clients."""
    return clients_store.clients_by_company(conn, company_ids)


def _draft_sent(conn: Connection, draft_id: int, row: Any, fallback: str) -> str:
    """Move the sent draft's firm to contacted on its programme; say what moved.

    A campaign's draft moves its own programme's card. Reading the enrolment
    setting for every draft would credit one programme with another's letter,
    so the setting is only the fallback for a draft outside any campaign.
    """
    code = campaign_store.programme_for_draft(conn, draft_id) or fallback
    if not code:
        return ""
    found = clients_store.programme_by_code(conn, code)
    if found is None:
        return (
            f" No programme is called {code}, so no card moved."
            " The Programmes page names the ones there are."
        )
    _member, moved = clients_store.note_contacted(
        conn, int(found["id"]), int(row["company_id"]),
        address=row["address"] or "", by="console:sent",
    )
    return (f" {code} card moved to contacted." if moved
            else f" On {code} already, past contacted.")


CONTRIBUTIONS = (
    Contribution("company.actions", _company_action, order=10),
    Contribution("company.panels", _mail_links, order=50),
    Contribution("home.tiles", _tiles, order=20),
    Contribution("board.clients", _board_clients, order=10),
    Contribution("draft.sent", _draft_sent, order=10),
)


def router(ctx: Console) -> APIRouter:
    router = APIRouter()
    _reload = ctx.reload
    connect = ctx.connect
    at = ctx.at
    shell = ctx.shell
    _back = ctx.back
    back = ctx.back
    runner = ctx.runner

    # --- clients, projects, and the record -------------------------------------
    #
    # The half of the product that starts after a company says yes. See
    # modules/clients/migrations/0001_crm.sql for why the audit trail, the client timeline
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

    @router.get("/mail", response_class=HTMLResponse)
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
                    configured=ctx.settings.mail.configured,
                )
            )
        finally:
            conn.close()

    @router.post("/mail/{email_id}/file")
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
            if ctx.settings.mail.configured:
                try:
                    mail_stage.fetch_body(ctx.settings, conn, email_id)
                except Exception:
                    # Reported nowhere on purpose: the filing is what the
                    # person asked for, and it succeeds without the text.
                    conn.rollback()
            clients_store.file_email(
                conn, email_id, client_id=client_id, by="console",
                own_domains=ctx.settings.mail.domains,
                free_domains=mail_source.FREE_MAIL,
            )
            # What this filing taught applies to the rest of the queue as well
            # as to the future: the earlier messages of the same thread are
            # already held, and an import would never look at them again.
            clients_store.rematch_pending(
                conn, own_domains=ctx.settings.mail.domains,
                free_domains=mail_source.FREE_MAIL,
            )
            conn.commit()
        finally:
            conn.close()
        return _back("/mail", note="The message is filed to the client's timeline.")

    @router.post("/mail/{email_id}/ignore")
    def ignore_mail(email_id: int) -> RedirectResponse:
        conn = connect()
        try:
            clients_store.ignore_email(conn, email_id, by="console")
            conn.commit()
        finally:
            conn.close()
        return _back("/mail", note="The message is ignored. The Ignored tab "
                                   "holds it if you want it back.")

    @router.post("/mail/{email_id}/reopen")
    def reopen_mail(email_id: int) -> RedirectResponse:
        conn = connect()
        try:
            clients_store.reopen_email(conn, email_id)
            conn.commit()
        finally:
            conn.close()
        return _back("/mail", note="The message is back in the queue.")

    @router.post("/mail/link/{link_id}/delete")
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

    def _budget(text: str) -> float | None:
        """A budget as typed, or a refusal naming it; never a 500."""
        if not text.strip():
            return None
        try:
            return float(figures.parse_decimal(text))
        except ValueError:
            raise ValueError(f"{text.strip()!r} is not an amount.") from None

    @router.get("/clients", response_class=HTMLResponse)
    def portfolio() -> HTMLResponse:
        conn = connect()
        try:
            rows = db.clients(conn)
            projects = clients_store.project_count(conn)
            upcoming = db.upcoming(conn, 30)
            return client_shell(clients_view.portfolio(rows, projects, upcoming))
        finally:
            conn.close()

    @router.post("/clients/import")
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
                    ctx.settings, conn, uid, log=lambda m: db.append_line(conn, run_id, m)
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

    @router.post("/company/{company_id}/client")
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

    @router.get("/client/{client_id}", response_class=HTMLResponse)
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
                    moves=scout_store.history_for_company(conn, client["company_id"]),
                    panels=ctx.registry.fill(
                        "client.panels", conn, client_id, client["company_id"]
                    ),
                )
            )
        finally:
            conn.close()

    @router.post("/client/{client_id}/brief")
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

    @router.post("/client/{client_id}/projects")
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

    @router.get("/project/{project_id}", response_class=HTMLResponse)
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
                    panels=ctx.registry.fill(
                        "project.panels", conn, project_id, int(project["client_id"])
                    ),
                )
            )
        finally:
            conn.close()

    @router.post("/project/{project_id}")
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
                billing=billing, budget=(amount if budget.strip() else ""),
                due_at=due_at.strip(), summary=summary,
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

    @router.post("/client/{client_id}/events")
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

    @router.post("/event/{event_id}/state")
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

    @router.get("/programmes", response_class=HTMLResponse)
    def programmes_list(request: Request) -> HTMLResponse:
        conn = connect()
        try:
            return programme_shell(
                programme_view.programmes_page(clients_store.programmes(conn))
            )
        finally:
            conn.close()

    @router.post("/programmes/new")
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

    @router.get("/programme/{programme_id}", response_class=HTMLResponse)
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
                    sectors=sorted(ctx.settings.sectors),
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

    back = _back

    @router.get("/audiences", response_class=HTMLResponse)
    def audiences_list(request: Request) -> HTMLResponse:
        conn = connect()
        try:
            return campaign_shell(campaign_view.audiences_page(
                campaign_store.audiences(conn), list(ctx.settings.segments),
                clients_store.programmes(conn),
            ), "/audiences")
        finally:
            conn.close()

    @router.post("/audiences/new")
    def audience_new(
        code: str = Form(""), name: str = Form(""), segments: str = Form(""),
        cantons: str = Form(""), programme: str = Form(""),
        members_only: str = Form(""), allow_unclear: str = Form(""),
        allow_hearsay: str = Form(""),
    ) -> RedirectResponse:
        from ...sources import audience as rules
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

    @router.get("/audience/{audience_id}", response_class=HTMLResponse)
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

    @router.post("/audience/{audience_id}/build")
    def audience_build(audience_id: int) -> RedirectResponse:
        lines: list[str] = []
        conn = connect()
        try:
            stats = audience_stage.build(ctx.settings, conn, audience=audience_id,
                                         log=lines.append)
        except ValueError as exc:
            conn.rollback()
            return back(f"/audience/{audience_id}", error=str(exc))
        finally:
            conn.close()
        return back(f"/audience/{audience_id}",
                    note=f"Built: {stats['kept']} kept, {stats['dropped']} dropped.")

    @router.post("/audience/{audience_id}/refresh")
    def audience_refresh(audience_id: int) -> RedirectResponse:
        try:
            run_id = runner.start("audience-verify", {"audience": str(audience_id)},
                                  internal=True)
        except (ValueError, RuntimeError) as exc:
            return back(f"/audience/{audience_id}", error=str(exc))
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @router.get("/audience/{audience_id}/export.csv")
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

    @router.post("/audience-row/{member_id}/pin")
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

    @router.post("/audience-row/{member_id}/unpin")
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

    @router.get("/campaigns", response_class=HTMLResponse)
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
        source = ctx.settings.root / "templates" / "example"
        target = ctx.settings.root / "templates" / folder_name
        if target.exists():
            raise ValueError(f"templates/{folder_name} already exists. Name it in the "
                             "templates field instead of starting from the example.")
        if not source.is_dir():
            raise ValueError("The example templates folder is not there to copy from.")
        target.mkdir(parents=True)
        for file in sorted(source.glob("*.md")):
            shutil.copy(file, target / file.name)
        return f"templates/{folder_name}"

    @router.post("/campaigns/new")
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
            new_id = campaign_stage.create(ctx.settings, conn, code=code, name=name,
                                           audience=audience, templates=templates,
                                           strategy=strategy, programme=programme)
        except ValueError as exc:
            conn.rollback()
            if made_folder:
                import shutil

                shutil.rmtree(ctx.settings.root / made_folder, ignore_errors=True)
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
        from ...sources import template as template_source

        try:
            folder = campaign_stage._templates_dir(ctx.settings, row["templates"])
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

    @router.get("/campaign/{campaign_id}", response_class=HTMLResponse)
    def campaign_view_page(campaign_id: int, request: Request) -> HTMLResponse:
        from ...sources import waves as wave_rules
        conn = connect()
        try:
            row = campaign_store.campaign(conn, campaign_id)
            if row is None:
                return shell(render.not_found(), status=404)
            wave_rows = campaign_store.waves(conn, campaign_id)
            return campaign_shell(campaign_view.campaign_page(
                row, wave_rows, campaign_stage.results(ctx.settings, conn, campaign=campaign_id),
                wave_rules.campaign_state(row, wave_rows), files=_template_files(row),
            ))
        finally:
            conn.close()

    def _template_target(campaign_id: int, name: str) -> tuple[Any, Any] | None:
        """The campaign and the file a name points at inside its folder, or None."""
        from ...sources import template as template_source

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
            folder = campaign_stage._templates_dir(ctx.settings, row["templates"])
        except ValueError:
            return None
        return row, folder / name

    @router.get("/campaign/{campaign_id}/template/{name}", response_class=HTMLResponse)
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

    @router.post("/campaign/{campaign_id}/template/{name}", response_class=HTMLResponse)
    def template_save(campaign_id: int, name: str, text: str = Form("")) -> Response:
        """Write the file when it parses; show it again with the reason when not.

        The page is drawn again rather than redirected to, so a refused text
        stays in the box: a redirect would throw away what was typed.
        """
        from ...sources import template as template_source

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

    @router.post("/campaign/{campaign_id}/plan")
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
            stats = campaign_stage.plan(ctx.settings, conn, campaign=campaign_id,
                                        dry_run=bool(dry_run), log=lines.append, **changes)
        except ValueError as exc:
            conn.rollback()
            return back(f"/campaign/{campaign_id}", error=str(exc))
        finally:
            conn.close()
        said = (f"{stats['waves']} waves of {', '.join(map(str, stats['sizes']))} firms"
                + (" (shown, not kept)" if dry_run else " planned."))
        return back(f"/campaign/{campaign_id}", note=said)

    @router.post("/campaign/{campaign_id}/pause")
    def campaign_pause(campaign_id: int, why: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            campaign_store.pause(conn, campaign_id, reason=why.strip() or "paused by hand",
                                 by="console")
            conn.commit()
        finally:
            conn.close()
        return back(f"/campaign/{campaign_id}", note="Paused.")

    @router.post("/campaign/{campaign_id}/resume")
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

    @router.get("/campaign/{campaign_id}/wave/{number}", response_class=HTMLResponse)
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
            block = campaign_stage.why_not_send(ctx.settings, conn, row, wave, confirm, act=False)
            room = ctx.settings.smtp.daily_quota - campaign_store.attempts_since(conn, hours=24)
            return campaign_shell(campaign_view.wave_page(
                row, wave, messages, list(seen.values()),
                campaign_store.tests_of_wave(conn, int(wave["id"])),
                signature=signature_view.for_preview(send_stage.signature_html(ctx.settings)),
                quota_room=room, pause_seconds=ctx.settings.smtp.pause_seconds,
                confirmation=confirm, block=block,
            ))
        finally:
            conn.close()

    @router.post("/campaign/{campaign_id}/wave/{number}/render")
    def wave_render(campaign_id: int, number: int) -> RedirectResponse:
        base = f"/campaign/{campaign_id}/wave/{number}"
        conn = connect()
        try:
            stats = campaign_stage.render_wave(ctx.settings, conn, campaign=campaign_id,
                                               wave=number, log=lambda _m: None)
        except ValueError as exc:
            conn.rollback()
            return back(base, error=str(exc))
        finally:
            conn.close()
        return back(base, note=f"{stats['rendered']} drafts rendered. Send the test next.")

    @router.post("/campaign/{campaign_id}/wave/{number}/approve")
    def wave_approve(campaign_id: int, number: int, read: str = Form("")) -> RedirectResponse:
        base = f"/campaign/{campaign_id}/wave/{number}"
        conn = connect()
        try:
            stats = campaign_stage.approve(ctx.settings, conn, campaign=campaign_id, wave=number,
                                           by="console", read=bool(read))
        except ValueError as exc:
            conn.rollback()
            return back(base, error=str(exc))
        finally:
            conn.close()
        return back(base, note=f"Approved: {stats['approved']} message(s).")

    @router.post("/campaign/{campaign_id}/wave/{number}/unapprove")
    def wave_unapprove(campaign_id: int, number: int) -> RedirectResponse:
        conn = connect()
        try:
            campaign_stage.unapprove(ctx.settings, conn, campaign=campaign_id, wave=number)
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

    @router.post("/campaign/{campaign_id}/wave/{number}/test")
    def wave_test(campaign_id: int, number: int) -> RedirectResponse:
        return transmit("campaign-test", campaign_id, number, {})

    @router.post("/campaign/{campaign_id}/wave/{number}/send")
    def wave_send(campaign_id: int, number: int, confirm: str = Form("")) -> RedirectResponse:
        return transmit("campaign-send", campaign_id, number, {"confirm": confirm})

    @router.post("/outbound/{outbound_id}/settle")
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

    @router.get("/signature/image")
    def signature_image() -> Response:
        """The signature's own picture, for the preview only.

        A message carries it as an attachment and the fragment points at
        `cid:signature`, which a browser cannot resolve. Without this route the
        editor shows a broken icon where the logo is and the operator has no way
        to see what a recipient will.
        """
        pictures = send_stage.signature_images(ctx.settings)
        blob = pictures.get("signature")
        if not blob:
            return Response(status_code=404)
        return Response(blob, media_type=f"image/{smtp_source._image_subtype(blob)}")

    @router.get("/signature", response_class=HTMLResponse)
    def signature_editor() -> HTMLResponse:
        return HTMLResponse(signature_view.shell(
            signature_view.signature_page(send_stage.signature_html(ctx.settings)),
            at("/signature"),
        ))

    @router.post("/signature")
    def save_signature(html: str = Form("")) -> RedirectResponse:
        """Store what the editor produced, cleaned.

        The cleaning happens in `letter.store_signature` rather than here, so
        that a signature written straight into the file by hand is reduced by
        the same rule on the way back out.
        """
        try:
            letter_source.store_signature(
                send_stage.signature_path(ctx.settings), html
            )
        except OSError as exc:
            return _back(
                "/signature",
                error=f"The signature could not be written: {exc.strerror or 'the disk refused it'}.",
            )
        return _back("/signature", note="Signature saved.")

    @router.get("/programme/{programme_id}/ledger", response_class=HTMLResponse)
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
                    conn, programme_id, own_domains=ctx.settings.mail.domains
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

    @router.get("/member/{member_id}/history", response_class=HTMLResponse)
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

    @router.post("/member/{member_id}/answer")
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
        with connect() as conn:
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

    @router.post("/programme/{programme_id}/move")
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

    @router.post("/member/{member_id}/move")
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

    @router.post("/programme/{programme_id}/members")
    def add_programme_member(
        programme_id: int, uid: str = Form(""), contact: str = Form("")
    ) -> RedirectResponse:
        conn = connect()
        try:
            where = f"/programme/{programme_id}"
            try:
                company = scout_store.find_company(conn, uid)
            except ValueError as refusal:
                return RedirectResponse(f"{where}?error={quote(str(refusal))}", status_code=303)
            _member, existed = clients_store.add_member(
                conn, programme_id, int(company["id"]), contact=contact
            )
            conn.commit()
            note = "already a member" if existed else "added"
            return RedirectResponse(f"{where}?note={quote(note)}", status_code=303)
        finally:
            conn.close()

    @router.post("/programme/{programme_id}/bring-in")
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

    @router.post("/programme/{programme_id}/stages")
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

    @router.post("/member/{member_id}/feedback")
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

    @router.get("/client/{client_id}/notes", response_class=HTMLResponse)
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

    @router.post("/client/{client_id}/notes")
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
    @router.get("/note/{note_id}.md", response_class=PlainTextResponse)
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

    @router.get("/note/{note_id}", response_class=HTMLResponse)
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

    @router.post("/note/{note_id}")
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

    @router.post("/note/{note_id}/pin")
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

    @router.post("/note/{note_id}/delete")
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
    # See modules/clients/migrations/0002_dossier.sql. What is known, what follows from it, and
    # what it was built on, on one page because they are one thing in use.

    def dossier_shell(body: str) -> HTMLResponse:
        return HTMLResponse(
            dossier_view.shell(body, at("/clients"))
        )

    @router.get("/client/{client_id}/dossier", response_class=HTMLResponse)
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

    @router.post("/dossier/{client_id}/section")
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

    @router.post("/dossier/{client_id}/advise")
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

    @router.post("/approach/{approach_id}/accept")
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

    @router.post("/dossier/{client_id}/order")
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

    @router.post("/dossier/{client_id}/documents")
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
                    stored = att.store(ctx.settings.data_dir, content, name)
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

    @router.get("/attachment/{attachment_id}/text", response_class=PlainTextResponse)
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

    @router.get("/client/{client_id}/prep", response_class=HTMLResponse)
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

    @router.post("/client/{client_id}/prep")
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

    @router.post("/term/{term_id}/known")
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

    @router.post("/event/{event_id}/delete")
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

    @router.post("/project/{project_id}/delete")
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

    @router.post("/attachment/{attachment_id}/delete")
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
                folder = att.root_for(ctx.settings.data_dir) / digest[:2]
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

    @router.post("/dossier/{client_id}/folder")
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

    @router.post("/attachment/{attachment_id}/folder")
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

    @router.post("/folder/{folder_id}/delete")
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

    @router.get("/calendar", response_class=HTMLResponse)
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

    return router
