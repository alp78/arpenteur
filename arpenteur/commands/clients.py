"""Clients, programmes, lists, campaigns, the mailbox, notes, the dossier.

Commands of the clients module, registered when it is enabled. Moved out of
`cli.py` verbatim; what they share is in `common.py`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from .. import attachments as att
from .. import db, dossier, markdown
from ..modules.clients import store as clients_store
from ..stages import (
    advise,
    mail,
    prep,
)
from ..stages import answers as answers_stage
from ..stages import send as send_stage
from ..stages import bounces as bounce_stage
from . import common
from .common import (
    _find_client,
    _find_company,
    _local_path,
    _split,
    positive_int,
    run_scope,
)


def cmd_bounces(args: argparse.Namespace) -> int:
    """Read delivery failures out of the mailbox and record them.

    The mailbox-level half of `verify`: DNS says a domain takes mail, and only
    a delivery attempt says whether the mailbox at it exists. Nothing is sent
    from here; the send goes through the operator's own provider.
    """
    settings = common.load_settings()
    # Read before the run is opened: a date that does not parse is refused, and
    # a refusal leaves no run behind it to be finished.
    since = None
    if args.since:
        try:
            since = dt.datetime.fromisoformat(args.since)
        except ValueError:
            print(f"error: --since wants a date like 2026-10-01, not {args.since!r}",
                  file=sys.stderr)
            return 1
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        with run_scope(conn, "bounces", args) as run:
            stats = bounce_stage.run(
                settings, conn, folder=args.folder, limit=args.limit,
                since=since, dry_run=args.dry_run,
            )
            run.stats = stats
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    """Post a message, or report what would be posted.

    A dry run by default. The flag that sends is `--yes`, and it is named
    rather than implied because the mistake this guards against costs one
    message per firm on a list of 2'450.
    """
    settings = common.load_settings()
    letters: list[send_stage.Letter] = []
    if args.to:
        body = ""
        if args.body_file:
            try:
                body = Path(args.body_file).read_text(encoding="utf-8")
            except OSError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
        elif args.body:
            body = args.body
        else:
            body = sys.stdin.read()
        letters.append(send_stage.Letter(
            to=args.to, subject=args.subject, body=body,
        ))
    # Opened once the message is in hand: a body file that cannot be read, or
    # a stdin that is interrupted, leaves no run behind it to be finished.
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        with run_scope(conn, "send", args) as run:
            stats = send_stage.run(
                settings, conn, letters=letters or None,
                draft_ids=[int(d) for d in args.draft],
                confirm=args.yes, plain=args.plain, programme=args.programme,
            )
            run.stats = stats
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_answers(args: argparse.Namespace) -> int:
    """Read the replies to the enrolment message and say what each one holds.

    Nothing is recorded against a member here. The platform and the account
    come back as a proposal, and `arpenteur programme answer` or the programme
    page is where a person accepts one. The single exception is a refusal,
    which suppresses the address on sight, because the message this campaign
    sends promises that in writing.
    """
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        with run_scope(conn, "answers", args) as run:
            stats = answers_stage.run(
                settings, conn, programme=args.programme,
                from_stage=args.from_stage, limit=args.limit,
                dry_run=args.dry_run,
            )
            run.stats = stats
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_mail(args: argparse.Namespace) -> int:
    """Read the mailbox into the review queue. Files nothing, sends nothing."""
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        with run_scope(conn, "mail", args) as run:
            stats = mail.run(
                settings,
                conn,
                folders=_split(args.folder) or None,
                days=args.days,
                limit=args.limit,
                dry_run=args.dry_run,
            )
            run.stats = stats
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    # The count that matters is what a person now has to read, not what was
    # fetched: a run that queues two hundred messages has made work, not
    # progress.
    print(
        f"{stats['queued']} queued for review, {stats['matched']} with a "
        f"company proposed, {stats['known']} already held. Nothing filed."
    )
    return 0


def cmd_note(args: argparse.Namespace) -> int:
    """Notes on a client, in Markdown, from the editor already open.

    `add` takes a path. The path is stored, so running the same command again
    after editing the file updates that note instead of writing a second one,
    and an unchanged file says so rather than claiming an update. A Windows
    path is accepted as typed: this runs under WSL as often as not, and
    retyping `C:\\Users\\...` as `/mnt/c/Users/...` is a translation the
    program can do and a person should not have to.
    """
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        client = _find_client(conn, args.client)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.action == "list":
        rows = clients_store.notes_for(conn, client["id"], with_body=False)
        if not rows:
            print(f"No notes on {client['code']}.")
        for row in rows:
            mark = "*" if row["pinned"] else " "
            stamp = (row["updated_at"] or "")[:10]
            tags = " ".join(row["tags"] or [])
            print(f"{mark} {row['id']:>4}  {stamp}  {row['title'][:52]:54} {tags}")
            if row["source_path"]:
                print(f"        from {row['source_path']}")
        conn.close()
        return 0

    if args.action == "show":
        row = clients_store.note(conn, int(args.path))
        if row is None or row["client_id"] != client["id"]:
            print("error: no such note on that client", file=sys.stderr)
            return 1
        print(row["body"])
        conn.close()
        return 0

    path = _local_path(args.path)
    if not path.is_file():
        print(f"error: no such file: {path}", file=sys.stderr)
        return 1
    try:
        body = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(
            f"error: {path} is not UTF-8 text. Notes are Markdown; a PDF or a "
            "Word file belongs under the dossier as a document.",
            file=sys.stderr,
        )
        return 1

    title = args.title or markdown.title_of(body)
    note_id, what = clients_store.save_note(
        conn, client["id"], title=title, body=body,
        source_path=str(args.path), tags=_split(args.tag) or None,
        pinned=True if args.pin else None, author="cli",
        digest=markdown.digest(body),
    )
    if what == "created":
        db.record_event(
            conn, client["id"], kind="note", occurred_at=db.now(),
            subject=f"Note: {title}", author="cli",
        )
    conn.commit()
    print(
        f"{what}: note {note_id} on {client['code']} — {title}\n"
        f"  {len(body):,} characters from {path}"
    )
    if what == "unchanged":
        print("  the file has not changed since it was last imported")
    conn.close()
    return 0


def cmd_docs(args: argparse.Namespace) -> int:
    """The client's papers from the shell, and the folders they go in.

    The console does this by dropping files on a page, which is the right
    interface for two documents and the wrong one for a consultation dossier
    of ten that is already sitting in a directory. Windows paths are accepted
    as typed, for the same reason `note` accepts them.
    """
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        client = _find_client(conn, args.client)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    client_id = int(client["id"])

    if args.action == "folders":
        rows = clients_store.folders_for(conn, client_id)
        if not rows:
            print(f"No folders on {client['code']}.")
        for row in rows:
            print(f"{row['id']:>4}  {row['name'][:44]:46} "
                  f"{row['documents']:>3} document(s)  "
                  f"{row['bytes'] / 1024:>8,.0f} kB")
            if row["note"]:
                print(f"      {row['note']}")
        conn.close()
        return 0

    if args.action == "list":
        rows = clients_store.attachments_for(conn, client_id)
        if not rows:
            print(f"No documents on {client['code']}.")
        for row in rows:
            state = row["extract_error"] or f"{row['text_chars'] or 0:,} chars"
            print(f"{row['id']:>4}  {(row['created_at'] or '')[:10]}  "
                  f"{row['filename'][:44]:46} {(row['folder'] or '—')[:22]:24} "
                  f"{state}")
        conn.close()
        return 0

    if args.action == "mkdir":
        if not args.path:
            print("error: name the folder", file=sys.stderr)
            return 1
        folder_id, existed = clients_store.add_folder(
            conn, client_id, " ".join(args.path), note=args.note or None
        )
        conn.commit()
        print(f"{'exists' if existed else 'made'}: folder {folder_id} on "
              f"{client['code']} — {' '.join(args.path)}")
        conn.close()
        return 0

    if args.action == "file":
        if len(args.path) < 1:
            print("error: name the document id", file=sys.stderr)
            return 1
        target = None
        if args.folder:
            target, _ = clients_store.add_folder(conn, client_id, args.folder)
        try:
            owner = clients_store.file_attachment(conn, int(args.path[0]), target)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if owner is None:
            print("error: no such document", file=sys.stderr)
            return 1
        conn.commit()
        print(f"document {args.path[0]} "
              + (f"filed in {args.folder}" if target else "taken out of its folder"))
        conn.close()
        return 0

    # add
    if not args.path:
        print("error: name at least one file", file=sys.stderr)
        return 1
    folder_id = None
    if args.folder:
        # `--note` describes the papers, not the folder. A folder's own note is
        # written by `docs mkdir`, so a second batch dropped into an existing
        # folder cannot silently retitle it.
        folder_id, existed = clients_store.add_folder(conn, client_id, args.folder)
        print(f"{'folder' if existed else 'new folder'}: {args.folder}")
    stored = 0
    for given in args.path:
        path = _local_path(given)
        if not path.is_file():
            print(f"error: no such file: {path}", file=sys.stderr)
            continue
        content = path.read_bytes()
        try:
            kept = att.store(settings.data_dir, content, path.name)
        except ValueError as exc:
            print(f"  {path.name}: {exc}", file=sys.stderr)
            continue
        text, error = att.extract(content, path.name, "")
        _id, existed = clients_store.add_attachment(conn, client_id, {
            "filename": path.name,
            "media_type": "",
            "bytes": kept.bytes,
            "sha256": kept.sha256,
            "kind": args.kind or None,
            "note": args.note.strip() or None,
            "folder_id": folder_id,
            "text": text,
            "extract_error": error,
        })
        if existed:
            print(f"  {path.name}: already held byte for byte, labels updated")
            continue
        # A document arriving is part of the record, whichever door it came
        # in by. The console writes this event and so must the shell, or a
        # timeline says a paper appeared from nowhere.
        db.record_event(
            conn, client_id, kind="document", occurred_at=db.now(),
            subject=f"Document received: {path.name}",
            body=args.note.strip() or None, author="cli",
        )
        stored += 1
        print(f"  {path.name}: stored, but not readable. {error}" if error
              else f"  {path.name}: stored and read, {len(text):,} characters")
    conn.commit()
    print(f"{stored} document(s) stored on {client['code']}"
          + (f", filed in {args.folder}" if args.folder else ""))
    conn.close()
    return 0


def cmd_clients(args: argparse.Namespace) -> int:
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    for row in db.clients(conn):
        print(f"{row['code']:<18} {row['status']:<9} {row['name']}")
    conn.close()
    return 0


def cmd_programme(args: argparse.Namespace) -> int:
    """A programme: one endeavour across many companies.

    Distinct from a project, which is one client's piece of work. Sending an
    application to fifty agencies is one programme with fifty members, each at
    its own stage, and the same agency can be a member of a second programme
    without either knowing about the other.

    Every action but `new` and `list` takes the programme's code first.
    """
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)

    def close(code: int) -> int:
        conn.close()
        return code

    if args.action == "list":
        rows = clients_store.programmes(conn)
        if not rows:
            print("No programmes. Start one: arpenteur programme new CODE \"Name\"")
        for row in rows:
            by_stage = dict(row["by_stage"] or {})
            tally = "  ".join(
                f"{stage} {by_stage[stage]}"
                for stage in (row["stages"] or []) if by_stage.get(stage)
            )
            print(f"{row['code']:<16} {row['status']:<9} "
                  f"{row['members']:>4} member(s)  {row['name'][:34]:36} {tally}")
        return close(0)

    if args.action == "new":
        if len(args.rest) < 2:
            print("error: give a code and a name", file=sys.stderr)
            return close(1)
        try:
            programme_id = clients_store.add_programme(
                conn, args.rest[0], " ".join(args.rest[1:]),
                what=args.what or None, started_at=db.now()[:10],
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return close(1)
        conn.commit()
        row = clients_store.programme(conn, programme_id)
        print(f"created: programme {programme_id} — {row['code']}")
        print(f"  stages: {', '.join(row['stages'])}")
        return close(0)

    if not args.rest:
        print("error: name the programme", file=sys.stderr)
        return close(1)
    programme = clients_store.programme_by_code(conn, args.rest[0])
    if programme is None:
        print(f"error: no programme called {args.rest[0]!r}. "
              "Try: arpenteur programme list", file=sys.stderr)
        return close(1)
    programme_id = int(programme["id"])
    rest = args.rest[1:]

    if args.action == "show":
        members = clients_store.members_for(conn, programme_id)
        print(f"{programme['code']} — {programme['name']}  [{programme['status']}]")
        if programme["what"]:
            print(f"  {programme['what']}")
        print(f"  stages: {', '.join(programme['stages'])}")
        print(f"  {len(members)} member(s)")
        for row in members:
            mail = f"{row['inbound']} in" if row["inbound"] else "no reply"
            print(f"  {row['stage']:<12} {row['company_name'][:38]:40} "
                  f"{(row['canton'] or ''):<3} {mail:<9} "
                  f"{row['feedback']} said")
        said = clients_store.feedback_on(conn, programme_id, limit=20)
        if said:
            print("\n  what they said, newest first:")
            for row in said:
                print(f"  {(row['occurred_at'] or '')[:10]}  {row['kind']:<8} "
                      f"{row['company_name'][:26]:28} {row['body'][:60]}")
        return close(0)

    if args.action == "stages":
        if not rest:
            for stage in programme["stages"]:
                gloss = (programme["stage_notes"] or {}).get(stage, "")
                print(f"  {stage:<14} {gloss}")
            return close(0)
        try:
            kept = clients_store.set_stages(conn, programme_id, rest)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return close(1)
        conn.commit()
        print(f"stages: {', '.join(kept)}")
        return close(0)

    if args.action == "replies":
        # A proposal, and the accept is the flag.
        #
        # `members_for` states the rule this honours: nothing in this schema
        # files itself, and a stage that advanced because a message arrived
        # would be the first thing that did. So the move stays an act somebody
        # takes; what changes is that taking it for twenty-five cards no longer
        # means twenty-five drags. The operator reads the list, then says yes.
        found = clients_store.members_who_replied(
            conn, programme_id, from_stage=args.from_stage
        )
        if not found:
            print(f"No member at '{args.from_stage}' has written since being "
                  "moved there.")
            return close(0)
        for row in found:
            when = row["last_inbound"]
            print(f"  {row['company_name'][:34]:36} "
                  f"{(row['from_email'] or '')[:30]:32} "
                  f"{(when or '')[:10]:10} "
                  f"{(row['subject'] or '')[:36]}")
        print()
        if not args.yes:
            print(f"{len(found)} member(s) would move from "
                  f"'{args.from_stage}' to '{args.to_stage}'.")
            print("Nothing has moved. Pass --yes to apply, having read the "
                  "list: an inbound message is a prompt to move the card "
                  "rather than a reply, and an out-of-office is both.")
            return close(0)
        moved = 0
        for row in found:
            try:
                clients_store.move_member(
                    conn, int(row["id"]), args.to_stage, by="cli:replies",
                    reason=f"inbound mail from {row['from_email'] or 'the firm'}",
                )
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                conn.rollback()
                return close(1)
            moved += 1
        conn.commit()
        print(f"{moved} member(s) moved to '{args.to_stage}'. Each move wrote "
              "an audit.state_change row, so the board can date the card.")
        return close(0)

    if args.action == "ledger":
        flag = (args.flag or "").strip().lower()
        if flag and flag not in clients_store.LEDGER_FLAGS:
            print(f"error: unknown flag {flag!r}. One of: "
                  + ", ".join(clients_store.LEDGER_FLAGS), file=sys.stderr)
            return close(1)
        rows = clients_store.campaign_ledger(
            conn, programme_id, stage=args.stage, canton=args.canton,
            flag=flag, limit=args.limit if args.limit > 25 else 1000,
        )
        tally = clients_store.ledger_tally(conn, programme_id)
        print(f"{programme['code']} — {tally['members']} member(s), "
              f"{tally['answered']} answered, {tally['silent']} silent, "
              f"{tally['failed']} undeliverable, {tally['refused']} refused, "
              f"{tally['enrollable']} enrollable")
        if not rows:
            print("  nothing matches")
            return close(0)
        print(f"  {'stage':<12} {'firm':<34} {'ct':<3} {'out':>3} {'in':>3} "
              f"{'last in':<11} enrolment")
        for row in rows:
            last = row["last_inbound"]
            enrolment = ""
            if row["platform"]:
                enrolment = row["platform"]
                if row["tester_account"]:
                    enrolment += f" {row['tester_account']}"
                if row["window_ok"] is True:
                    enrolment += " dates ok"
                elif row["window_ok"] is False:
                    enrolment += " dates refused"
            elif row["refused"]:
                enrolment = "asked to stop"
            elif row["failed"]:
                enrolment = "address failed"
            print(f"  {row['stage']:<12} {row['company_name'][:34]:<34} "
                  f"{(row['canton'] or ''):<3} {row['sent']:>3} "
                  f"{row['inbound']:>3} "
                  f"{(last or '')[:10]:<11} {enrolment}")
        return close(0)

    if args.action == "history":
        if not rest:
            print("error: name the company, by UID or name", file=sys.stderr)
            return close(1)
        try:
            company = _find_company(conn, rest[0])
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return close(1)
        rows = clients_store.history_for(conn, int(company["id"]))
        print(f"{company['name']} — {len(rows)} message(s)")
        if not rows:
            print("  nothing. `arpenteur mail` reads the mailbox; a message is "
                  "attributed by a published address, a learnt link, or the "
                  "domain.")
        for row in rows:
            when = row["sent_at"]
            arrow = "->" if row["direction"] == "out" else "<-"
            who = (row["from_email"] or "") if row["direction"] == "in" else (
                ", ".join(row["to_emails"] or [])
            )
            print(f"  {(when or '')[:10]:<11} {arrow} "
                  f"{who[:34]:<34} {(row['state'] or ''):<9} "
                  f"{(row['subject'] or '')[:40]}")
        return close(0)

    if args.action == "answer":
        if not rest:
            print("error: name the company, by UID or name", file=sys.stderr)
            return close(1)
        try:
            company = _find_company(conn, rest[0])
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return close(1)
        found = conn.execute(
            "SELECT id FROM crm.programme_member"
            " WHERE programme_id = %s AND company_id = %s",
            (programme_id, int(company["id"])),
        ).fetchone()
        if found is None:
            print(f"error: {company['name']} is not a member of "
                  f"{programme['code']}. Add it first: arpenteur programme "
                  f"add {programme['code']} {company['uid']}", file=sys.stderr)
            return close(1)
        window = None
        if args.window_ok:
            window = True
        elif args.window_no:
            window = False
        try:
            clients_store.record_answer(
                conn, int(found["id"]), platform=args.platform,
                account=args.account, window_ok=window,
                stage=args.to_stage, by="cli:answer", note=args.why,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            conn.rollback()
            return close(1)
        conn.commit()
        said = []
        if args.platform:
            said.append(args.platform)
        if args.account:
            said.append(args.account)
        if window is not None:
            said.append("dates accepted" if window else "dates refused")
        print(f"{company['name']}: recorded {', '.join(said) or 'the answer'}, "
              f"now at '{args.to_stage}'")
        return close(0)

    if args.action == "add":
        if not rest:
            print("error: name at least one company, by UID or name",
                  file=sys.stderr)
            return close(1)
        added = 0
        for reference in rest:
            try:
                company = _find_company(conn, reference)
            except RuntimeError as exc:
                print(f"  {reference}: {exc}", file=sys.stderr)
                continue
            _id, existed = clients_store.add_member(
                conn, programme_id, int(company["id"]), contact=args.contact or None
            )
            print(f"  {company['name'][:44]:46} "
                  + ("already a member" if existed else "added"))
            added += 0 if existed else 1
        conn.commit()
        print(f"{added} added to {programme['code']}")
        return close(0)

    if args.action == "bring-in":
        added = clients_store.bring_in_members(
            conn, programme_id, sector=args.sector, canton=args.canton,
            band=args.band, segment=args.segment, legal_form=args.legal_form,
            limit=args.limit,
        )
        conn.commit()
        print(f"{added} companies added to {programme['code']}"
              if added else
              "Nothing to add: every company matching that is already a member, "
              "or none matches.")
        return close(0)

    if args.action == "move":
        if len(rest) < 2:
            print("error: name a company and a stage", file=sys.stderr)
            return close(1)
        try:
            company = _find_company(conn, rest[0])
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return close(1)
        row = conn.execute(
            "SELECT id FROM crm.programme_member"
            " WHERE programme_id = %s AND company_id = %s",
            (programme_id, int(company["id"])),
        ).fetchone()
        if row is None:
            print(f"error: {company['name']} is not a member of "
                  f"{programme['code']}", file=sys.stderr)
            return close(1)
        try:
            clients_store.move_member(
                conn, int(row["id"]), " ".join(rest[1:]), by="cli",
                reason=args.why or "",
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return close(1)
        conn.commit()
        print(f"{company['name']} -> {' '.join(rest[1:])}")
        return close(0)

    # say
    if len(rest) < 2:
        print("error: name a company and what they said", file=sys.stderr)
        return close(1)
    try:
        company = _find_company(conn, rest[0])
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return close(1)
    row = conn.execute(
        "SELECT id FROM crm.programme_member"
        " WHERE programme_id = %s AND company_id = %s",
        (programme_id, int(company["id"])),
    ).fetchone()
    if row is None:
        print(f"error: {company['name']} is not a member of {programme['code']}",
              file=sys.stderr)
        return close(1)
    try:
        clients_store.add_feedback(
            conn, int(row["id"]), " ".join(rest[1:]), kind=args.kind,
            occurred_at=args.on or None, author="cli",
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return close(1)
    conn.commit()
    print(f"kept against {company['name']}: {args.kind}")
    return close(0)


def cmd_dossier(args: argparse.Namespace) -> int:
    """Print everything held on a client, as text.

    This exists so an agent can be pointed at a client without a browser and
    without a schema. It prints exactly what the advise stage is given, in the
    same order, so a surprising suggestion can be read against its own input
    rather than guessed at. `--documents` adds the extracted text of every
    paper the client sent, which is what makes "analyse their contract"
    something an agent can actually do.
    """
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        client = _find_client(conn, args.client)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    company = conn.execute(
        "SELECT * FROM core.company WHERE id = %s", (client["company_id"],)
    ).fetchone()
    sections = db.dossier_for(conn, client["id"])
    papers = db.attachments_for(conn, client["id"], with_text=args.documents)

    print(f"# {company['name']}")
    print(f"{client['code']}  {company['uid']}  {company['legal_form_label'] or ''}  "
          f"{company['legal_seat'] or ''} ({company['canton'] or ''})  "
          f"status {client['status']}")
    if client["brief"]:
        print(f"\nStanding brief: {client['brief']}")
    print(f"\n{dossier.as_text(sections)}")

    notes = clients_store.notes_for(conn, client["id"])
    if notes:
        # Printed in full, and before the client's own papers. This command
        # exists so an agent can be pointed at a client without a browser, and
        # the operator's notes are the part of the file that says what was
        # decided rather than what was found.
        print("\n## Your own notes")
        for item in notes:
            print(f"\n--- begin note: {item['title']} ---")
            print(item["body"])
            print(f"--- end note: {item['title']} ---")

    if papers:
        print("\n## Documents held")
        for paper in papers:
            state = (
                f"{paper['text_chars'] or 0} characters extracted"
                if not paper["extract_error"] else f"NOT READABLE: {paper['extract_error']}"
            )
            print(f"- {paper['filename']} [{att.KIND_LABELS.get(paper['kind'] or '', '?')}] "
                  f"{paper['bytes']} bytes, {state}"
                  + (f", filed in {paper['folder']}" if paper.get("folder") else ""))
            if paper["note"]:
                print(f"  note: {paper['note']}")
            if args.documents and (paper["text"] or "").strip():
                print(f"\n--- begin {paper['filename']} ---")
                print(paper["text"])
                print(f"--- end {paper['filename']} ---\n")

    latest = db.approaches_for(conn, client["id"], limit=1)
    if latest:
        row = latest[0]
        print(f"\n## Suggested approach ({row['created_at'][:16]}, {row['model']})")
        print(f"\n{row['situation']}\n")
        print(f"Opening: {row['opening']}")
        for label, column in (("Discussion points", "points_json"),
                              ("Services", "services_json"),
                              ("Pricing", "pricing_json"),
                              ("Risks", "risks_json"),
                              ("Open questions", "questions_json")):
            try:
                items = row[column] or []
            except ValueError:
                continue
            if not items:
                continue
            print(f"\n{label}:")
            for item in items:
                if isinstance(item, dict):
                    print("  - " + "; ".join(
                        f"{k}: {v}" for k, v in item.items() if v is not None
                    ))
                else:
                    print(f"  - {item}")
    conn.close()
    return 0


def cmd_advise(args: argparse.Namespace) -> int:
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        client = _find_client(conn, args.client)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        with run_scope(conn, "advise", args) as run:
            stats = advise.run(
                settings, conn, client_id=client["id"], dry_run=args.dry_run
            )
            run.stats = stats
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"{client['code']}: read {stats['sections']} sections and "
        f"{stats['documents']} documents"
        + ("" if stats.get("saved") else " (dry run, nothing saved)")
    )
    return 0


def cmd_prep(args: argparse.Namespace) -> int:
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        client = _find_client(conn, args.client)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.list:
        rows = db.glossary_for(conn, client["id"])
        if not rows:
            print("Nothing extracted yet. Run without --list first.")
            return 0
        shelf = None
        for row in rows:
            if row["category"] != shelf:
                shelf = row["category"]
                print(f"\n## {shelf}")
            mark = "x" if row["known"] else " "
            print(f"[{mark}] {row['priority']:<6} {row['term']}"
                  + (f" ({row['expansion']})" if row["expansion"] else ""))
            for label, key in (("  what", "what_it_is"), ("  here", "why_here"),
                               ("  cold", "must_know"), ("  ask ", "question"),
                               ("  ref ", "reference")):
                if row[key]:
                    print(f"{label}: {row[key]}")
        conn.close()
        return 0
    try:
        with run_scope(conn, "prep", args) as run:
            stats = prep.run(settings, conn, client_id=client["id"], dry_run=args.dry_run)
            run.stats = stats
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_audience(args: argparse.Namespace) -> int:
    """A curated list: the firms worth writing to, one chosen mailbox each.

    Every action but `new` and `list` takes the list's code first. A list is
    rebuilt freely until a campaign sends from it; after that it is frozen.
    """
    from ..modules.clients import campaigns as campaign_store
    from ..sources import audience as rules
    from ..stages import audience as audience_stage

    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)

    def close(code: int) -> int:
        conn.close()
        return code

    try:
        if args.action == "list":
            rows = campaign_store.audiences(conn)
            if not rows:
                print('No lists. Start one: arpenteur audience new CODE "Name" '
                      "--segment tourism-office=1")
            for row in rows:
                frozen = " frozen" if row["frozen_at"] else ""
                print(f"{row['code']:<16} {row['kept']:>5} kept {row['dropped']:>5} dropped"
                      f"  {row['campaigns']} campaign(s){frozen}  {row['name'][:40]}")
            return close(0)

        if args.action == "new":
            if len(args.rest) < 2:
                print('error: give a code and a name, e.g. DRYMOS-1 "Tourisme"',
                      file=sys.stderr)
                return close(1)
            kinds = ["desk"] + [k for k in ("unclear",) if k in (args.allow or "")]
            provenance = ["page"] + [k for k in ("hearsay", "inferred")
                                     if k in (args.allow or "")]
            verdicts = ["deliverable"] + (["maybe"] if "maybe" in (args.allow or "") else [])
            rule = rules.Rule(
                segments=audience_stage.parse_segments(args.segment or ""),
                cantons=tuple(c.strip().upper() for c in (args.canton or "").split(",")
                              if c.strip()),
                programme=(args.programme or "").strip().upper(),
                members_only=args.members_only,
                exclude_legal_forms=() if "sole-traders" in (args.allow or "") else ("0101",),
                verdicts=tuple(verdicts), provenance=tuple(provenance), kinds=tuple(kinds),
                cooldown_days=args.cooldown, max_age_days=args.max_age,
                use_size=args.size, per_canton=args.per_canton or 0,
            )
            new_id = audience_stage.create(conn, code=args.rest[0],
                                           name=" ".join(args.rest[1:]), rule=rule)
            print(f"created: list {new_id}, {args.rest[0].upper()}. Next: "
                  f"arpenteur audience refresh {args.rest[0].upper()}, then build.")
            return close(0)

        if not args.rest:
            print("error: name the list, e.g. DRYMOS-1", file=sys.stderr)
            return close(1)
        code = args.rest[0]

        if args.action == "build":
            audience_stage.build(settings, conn, audience=code, dry_run=args.dry_run)
            return close(0)

        if args.action == "refresh":
            audience_stage.refresh(settings, conn, audience=code, dry_run=args.dry_run)
            return close(0)

        listed = audience_stage.resolve(conn, code)
        if args.action == "show":
            verdict = None if args.dropped or args.reason else "kept"
            rows = campaign_store.members(conn, int(listed["id"]), verdict=verdict,
                                          canton=args.canton or None,
                                          reason=args.reason or None)
            print(f"{listed['code']}  {listed['name']}"
                  + ("  [frozen]" if listed["frozen_at"] else ""))
            for code_, n in audience_stage.funnel(conn, int(listed["id"])):
                print(f"  {code_:16} {n:>5}")
            here = None
            for row in rows:
                if row["canton"] != here:
                    here = row["canton"]
                    print(f"\n{here or '??'}")
                score = f"{float(row['score']):5.1f}" if row["score"] is not None else "    -"
                where = row["address"] or "-"
                print(f"  {score}  {row['company_name'][:34]:34} {row['language'] or '?':2} "
                      f"{where[:34]:34} {row['reason'][:60]}")
            return close(0)

        if args.action == "export":
            rows = campaign_store.members(conn, int(listed["id"]))
            out = Path(args.out or f"{listed['code'].lower()}.csv")
            import csv
            with out.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["uid", "name", "canton", "verdict", "reason_code", "reason",
                                 "address", "language", "language_basis", "segment",
                                 "score", "covered_by"])
                for row in rows:
                    writer.writerow([row["uid"], row["company_name"], row["canton"],
                                     row["verdict"], row["reason_code"], row["reason"],
                                     row["address"], row["language"], row["language_basis"],
                                     row["segment"], row["score"], row["covered_by_name"]])
            print(f"{len(rows)} rows written to {out}")
            return close(0)

        if args.action in ("keep", "drop", "language", "address", "unpin"):
            if len(args.rest) < 2:
                print("error: give the list and the firm's UID", file=sys.stderr)
                return close(1)
            uid = args.rest[1].replace(".", "").replace("-", "").upper()
            match = [r for r in campaign_store.members(conn, int(listed["id"]))
                     if (r["uid"] or "").replace(".", "").replace("-", "").upper() == uid]
            if not match:
                print(f"error: {args.rest[1]} is not on {listed['code']}", file=sys.stderr)
                return close(1)
            row = match[0]
            pin = dict(row["pinned"] or {})
            if args.action == "unpin":
                campaign_store.set_pin(conn, int(row["id"]), None)
            else:
                if not (args.why or "").strip() and args.action in ("keep", "drop"):
                    print("error: say why, with --why", file=sys.stderr)
                    return close(1)
                if args.action in ("keep", "drop"):
                    pin["verdict"] = "kept" if args.action == "keep" else "dropped"
                elif args.action == "language":
                    pin["language"] = (args.rest[2] if len(args.rest) > 2 else "").lower()
                else:
                    wanted = (args.rest[2] if len(args.rest) > 2 else "").lower()
                    held = {a["value"]: a for a in campaign_store.member_addresses(conn, int(row["id"]))}
                    if wanted not in held:
                        print(f"error: the firm holds no {wanted}", file=sys.stderr)
                        return close(1)
                    pin["contact_id"] = int(held[wanted]["id"])
                    pin.setdefault("verdict", "kept")
                pin["why"] = (args.why or pin.get("why") or "").strip()
                pin["by"], pin["at"] = "cli", db.now()
                campaign_store.set_pin(conn, int(row["id"]), pin)
            conn.commit()
            print(f"{row['company_name']}: pin {'cleared' if args.action == 'unpin' else 'set'}."
                  f" It applies at the next build: arpenteur audience build {listed['code']}")
            return close(0)
    except ValueError as exc:
        conn.rollback()
        print(f"error: {exc}", file=sys.stderr)
        return close(1)
    print(f"error: unknown action {args.action!r}", file=sys.stderr)
    return close(1)


def cmd_campaign(args: argparse.Namespace) -> int:
    """A campaign: one message plan over one list, sent in waves.

    Each wave walks render, test, approve, send, and nothing skips a step. The
    send asks for the wave typed back, CODE/N, as the console does.
    """
    from ..modules.clients import campaigns as campaign_store
    from ..sources import waves as wave_rules
    from ..stages import campaign as campaign_stage

    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)

    def close(code: int) -> int:
        conn.close()
        return code

    plan_params = {k: v for k, v in (
        ("strategy", args.strategy), ("pilot_size", args.pilot),
        ("wave_size", args.wave_size), ("gap_days", args.gap),
        ("start_on", args.start), ("bounce_ceiling", args.bounce_ceiling),
        ("settle_hours", args.settle), ("cooldown_days", args.cooldown),
        ("bilingual", args.bilingual),
    ) if v is not None}

    try:
        if args.action == "strategies":
            for s in wave_rules.STRATEGIES.values():
                print(f"{s.name:<18} {s.label}\n{'':<18} {s.what}\n")
            return close(0)

        if args.action == "list":
            rows = campaign_store.campaigns(conn)
            if not rows:
                print("No campaigns. Start one: arpenteur campaign new CODE \"Name\" "
                      "--audience LIST --templates templates/x --strategy canton-blocks")
            for row in rows:
                state = wave_rules.campaign_state(row, campaign_store.waves(conn, int(row["id"])))
                print(f"{row['code']:<14} {state:<9} {row['waves_sent']}/{row['waves']} "
                      f"waves sent  list {row['audience_code']:<12} {row['name'][:40]}")
            return close(0)

        if args.action == "new":
            if len(args.rest) < 2 or not args.audience or not args.templates:
                print('error: give a code and a name, with --audience and --templates',
                      file=sys.stderr)
                return close(1)
            new_id = campaign_stage.create(
                settings, conn, code=args.rest[0], name=" ".join(args.rest[1:]),
                audience=args.audience, templates=args.templates,
                strategy=args.strategy or "canton-blocks",
                programme=args.programme or "",
                **{k: v for k, v in plan_params.items() if k != "strategy"},
            )
            print(f"created: campaign {new_id}, {args.rest[0].upper()}. Next: "
                  f"arpenteur campaign plan {args.rest[0].upper()} --dry-run")
            return close(0)

        if not args.rest:
            print("error: name the campaign, e.g. DRYMOS-A", file=sys.stderr)
            return close(1)
        row = campaign_stage.resolve(conn, args.rest[0])
        number = int(args.rest[1]) if len(args.rest) > 1 else 0

        if args.action == "plan":
            campaign_stage.plan(settings, conn, campaign=row["id"], dry_run=args.dry_run,
                                **plan_params)
            return close(0)

        if args.action in ("show", "results"):
            waves = campaign_store.waves(conn, int(row["id"]))
            outcome = campaign_stage.results(settings, conn, campaign=row["id"])
            print(f"{row['code']}  {row['name']}  "
                  f"[{wave_rules.campaign_state(row, waves)}]  list {row['audience_code']}, "
                  f"strategy {row['strategy']}, templates {row['templates']}"
                  + (f", both languages in {', '.join(row['bilingual'])}"
                     if row["bilingual"] else ""))
            if row["paused_at"]:
                print(f"  paused: {row['paused_reason']}")
            for w in waves:
                steps = "  ".join(f"{name}{'+' if w[col] else '-'}" for name, col in (
                    ("render", "rendered_at"), ("test", "tested_at"),
                    ("approve", "approved_at"), ("send", "sent_at")))
                done = outcome.get(int(w["number"]))
                tally = done["tally"] if done else None
                line = (f"  wave {w['number']:>2}  {int(w['live']):>3} firms  "
                        f"{w['planned_for'] or 'unscheduled':<10}  {steps}  {w['label']}")
                if tally and tally.sent:
                    line += (f"\n           sent {tally.sent}, bounced {tally.bounced}, "
                             f"replied {tally.replied}, refused {tally.refused}, "
                             f"enrolled {tally.enrolled}; {done['gate'].why}")
                print(line)
            return close(0)

        if args.action in ("pause", "resume"):
            if not (args.why or "").strip():
                print("error: say why, with --why", file=sys.stderr)
                return close(1)
            (campaign_store.pause if args.action == "pause" else campaign_store.resume)(
                conn, int(row["id"]), reason=args.why, by="cli")
            conn.commit()
            print(f"{row['code']} {args.action}d.")
            return close(0)

        if not number:
            print("error: name the wave, e.g. DRYMOS-A 1", file=sys.stderr)
            return close(1)

        if args.action == "render":
            campaign_stage.render_wave(settings, conn, campaign=row["id"], wave=number)
            return close(0)

        if args.action == "test":
            if not args.yes:
                print(f"Would send one test per variant of wave {number} to "
                      f"{args.to or settings.smtp.test_to or settings.smtp.sender}. "
                      "Pass --yes to send it.")
                return close(0)
            # The connection is `close`'s to shut: the command goes on using it.
            with run_scope(conn, "campaign-test", {"campaign": row["code"], "wave": number},
                           close=False) as run:
                run.stats = campaign_stage.test_wave(
                    settings, conn, campaign=row["id"], wave=number, to=args.to or "")
            return close(0)

        if args.action == "approve":
            stats = campaign_stage.approve(settings, conn, campaign=row["id"], wave=number,
                                           by="cli", read=args.read)
            print(f"wave {number} approved: {stats['approved']} message(s)")
            return close(0)

        if args.action == "send":
            expected = f"{row['code']}/{number}"
            if not args.yes:
                w = campaign_store.wave(conn, int(row["id"]), number)
                print(f"Wave {number} of {row['code']}: {w['live'] if w else 0} firm(s). "
                      f"To send it: arpenteur campaign send {row['code']} {number} "
                      f"--yes --confirm {expected}")
                return close(0)
            replacement = None
            if args.replacement:
                replacement = int(common._find_company(conn, args.replacement)["id"])
            with run_scope(conn, "campaign-send", {"campaign": row["code"], "wave": number,
                                                   "replacement": args.replacement or None},
                           close=False) as run:
                run.stats = campaign_stage.send_wave(
                    settings, conn, campaign=row["id"], wave=number,
                    confirm=args.confirm or "", replacement=replacement)
            return close(0)

    except ValueError as exc:
        conn.rollback()
        print(f"error: {exc}", file=sys.stderr)
        return close(1)
    print(f"error: unknown action {args.action!r}", file=sys.stderr)
    return close(1)


def add_parsers(sub: argparse._SubParsersAction) -> None:
    sub.add_parser(
        "clients", help="the client portfolio, one line each"
    ).set_defaults(func=cmd_clients)


    p = sub.add_parser(
        "dossier",
        help="print everything held on a client, for reading or for an agent",
    )
    p.add_argument("client", help="client code, UID, or part of the name")
    p.add_argument(
        "--documents",
        action="store_true",
        help="include the full extracted text of every document held",
    )
    p.set_defaults(func=cmd_dossier)


    p = sub.add_parser(
        "advise",
        help="suggest an approach for a client from their dossier and papers",
    )
    p.add_argument("client", help="client code, UID, or part of the name")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be read and its size, spend nothing",
    )
    p.set_defaults(func=cmd_advise)


    p = sub.add_parser(
        "prep",
        help="extract a client's vocabulary and what to revise before meeting them",
    )
    p.add_argument("client", help="client code, UID, or part of the name")
    p.add_argument(
        "--list", action="store_true",
        help="print what has already been extracted, without calling a model",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="report what would be read and its size, spend nothing",
    )
    p.set_defaults(func=cmd_prep)


    p = sub.add_parser(
        "mail",
        help="read the mailbox into the review queue (read-only, files nothing)",
    )
    p.add_argument(
        "--folder", help="comma separated folders; blank reads all but Spam and Trash"
    )
    p.add_argument(
        "--days", type=int, default=mail.DEFAULT_DAYS,
        help=f"how far back to look, in days (default {mail.DEFAULT_DAYS})",
    )
    p.add_argument("--limit", type=positive_int, help="stop after this many new messages")
    p.add_argument(
        "--dry-run", action="store_true", help="list what would be queued, write nothing"
    )
    p.set_defaults(func=cmd_mail)


    p = sub.add_parser(
        "note", help="notes on a client, written in Markdown"
    )
    p.add_argument("client", help="client code, UID or name")
    p.add_argument(
        "action", choices=("add", "list", "show"),
        help="add a file, list what is held, or print one back",
    )
    p.add_argument(
        "path", nargs="?", default="",
        help="the .md file for add, the note id for show",
    )
    p.add_argument("--title", help="overrides the note's own first heading")
    p.add_argument("--tag", help="comma separated")
    p.add_argument("--pin", action="store_true", help="keep it at the top")
    p.set_defaults(func=cmd_note)


    p = sub.add_parser(
        "docs", help="the client's documents, and the folders they are filed in"
    )
    p.add_argument("client", help="client code, UID or name")
    p.add_argument(
        "action", choices=("add", "list", "folders", "mkdir", "file"),
        help="store files, list what is held, list folders, make a folder, "
             "or move a document into one",
    )
    p.add_argument(
        "path", nargs="*", default=[],
        help="files for add; the folder name for mkdir; a document id and a "
             "folder name for file",
    )
    p.add_argument(
        "--folder", default="",
        help="file everything added into this folder, making it if needed",
    )
    p.add_argument(
        "--kind", default="",
        help=f"what it is: {', '.join(k for k, _ in att.KINDS if k)}",
    )
    p.add_argument("--note", default="", help="where it came from, for the model")
    p.set_defaults(func=cmd_docs)


    p = sub.add_parser(
        "programme",
        help="one endeavour across many companies, each member at its own stage",
    )
    p.add_argument(
        "action",
        choices=("list", "new", "show", "add", "bring-in", "move", "say",
                 "stages", "replies", "ledger", "history", "answer"),
        help="list them; start one; show one; add companies; add a shortlist "
             "from the survey; move a member; keep what a member said; read or "
             "rewrite the stages; list who has written back; read the status "
             "of every member; read one firm's mail; record what one answered",
    )
    p.add_argument(
        "rest", nargs="*", default=[],
        help="the programme code first, then what the action needs: a company "
             "and a stage for move, a company and the words for say",
    )
    p.add_argument("--what", default="", help="what the programme is, for new")
    p.add_argument("--contact", default="", help="who you deal with, for add")
    p.add_argument(
        "--kind", default="note", choices=clients_store.FEEDBACK_KINDS,
        help="what sort of feedback, for say",
    )
    p.add_argument("--on", default="", help="when they said it, for say")
    p.add_argument("--why", default="", help="the reason for a move")
    p.add_argument("--sector", default="", help="for bring-in")
    p.add_argument("--segment", default="", help="for bring-in")
    p.add_argument("--canton", default="", help="comma separated, for bring-in")
    p.add_argument("--band", default="", help="fit band, for bring-in")
    p.add_argument(
        "--legal-form",
        default="",
        help="comma separated eCH-0097 uids, for bring-in. Naming any excludes "
             "the rest, 0101 included: a sole trader is a named person, and "
             "writing to one carries nLPD obligations a company does not",
    )
    p.add_argument("--limit", type=positive_int, default=25, help="how many, for bring-in")
    p.add_argument(
        "--from-stage", dest="from_stage", default="contacted",
        help="for replies: the stage to look at (default contacted)",
    )
    p.add_argument(
        "--to-stage", dest="to_stage", default="replied",
        help="for replies: where they move (default replied)",
    )
    p.add_argument(
        "--yes", action="store_true",
        help="for replies: apply the moves, having read the list",
    )
    p.add_argument(
        "--flag", default="",
        help="for ledger: one of " + ", ".join(clients_store.LEDGER_FLAGS),
    )
    p.add_argument("--stage", default="", help="for ledger: one stage only")
    p.add_argument(
        "--platform", default="", choices=("", "ios", "android"),
        help="for answer: which store track the tester enrols on",
    )
    p.add_argument(
        "--account", default="",
        help="for answer: the tester's Google account, which Android needs",
    )
    p.add_argument(
        "--window-ok", dest="window_ok", action="store_true",
        help="for answer: the tester accepted the dated window",
    )
    p.add_argument(
        "--window-no", dest="window_no", action="store_true",
        help="for answer: the tester declined the dated window",
    )
    p.set_defaults(func=cmd_programme)


    p = sub.add_parser(
        "audience",
        help="a curated list: one chosen mailbox per firm, ranked, deduplicated",
    )
    p.add_argument("action", choices=["list", "new", "show", "refresh", "build", "keep",
                                      "drop", "language", "address", "unpin", "export"],
                   help="list: every list. new CODE \"Name\" --segment ...: start one. "
                        "refresh CODE: check its addresses with DNS again. build CODE: "
                        "choose one mailbox per firm. show CODE: what it keeps and drops. "
                        "keep, drop CODE UID --why ...: pin a firm either way. language, "
                        "address CODE UID VALUE: pin a firm's language or mailbox. unpin "
                        "CODE UID: let the rules decide again. export CODE --out FILE: "
                        "write it as CSV")
    p.add_argument("rest", nargs="*",
                   help="the list's code first, then a firm's UID for a pin")
    p.add_argument("--segment", default="",
                   help="bands and their weights, e.g. tourism-office=1,mountain-transport=0.7")
    p.add_argument("--canton", default="", help="comma separated")
    p.add_argument("--programme", default="", help="the programme the list feeds")
    p.add_argument("--members-only", action="store_true",
                   help="only firms already on the programme")
    p.add_argument("--allow", default="",
                   help="widen the defaults: unclear, hearsay, inferred, maybe, sole-traders")
    p.add_argument("--cooldown", type=int, default=180,
                   help="days a firm or domain rests after a message; a mailbox is "
                        "written to once")
    p.add_argument("--max-age", type=int, default=30,
                   help="days a DNS verdict stays good enough")
    p.add_argument("--size", action="store_true", help="let registered capital count")
    p.add_argument("--per-canton", type=positive_int, default=None,
                   help="for new: the most firms kept in each canton, best score first")
    p.add_argument("--dropped", action="store_true", help="for show: the dropped firms too")
    p.add_argument("--reason", default="", help="for show: one drop reason only")
    p.add_argument("--why", default="", help="for keep and drop: the reason, required")
    p.add_argument("--out", default="", help="for export: the CSV to write")
    p.add_argument("--dry-run", action="store_true",
                   help="for build and refresh: report, write nothing")
    p.set_defaults(func=cmd_audience)


    p = sub.add_parser(
        "campaign",
        help="a message plan over one list, sent in waves after a test and an approval",
    )
    p.add_argument("action", choices=["list", "strategies", "new", "show", "plan", "render",
                                      "test", "approve", "send", "pause", "resume",
                                      "results"],
                   help="list: every campaign. strategies: the ways waves can be cut. "
                        "new CODE \"Name\" --audience LIST --templates DIR: start one. "
                        "plan CODE: cut the list into waves. render, test, approve, send "
                        "CODE N: walk wave N through its four steps, none skipped. "
                        "show, results CODE: where it stands. pause, resume CODE --why: "
                        "hold the next wave or release it")
    p.add_argument("rest", nargs="*", help="the campaign's code first, then a wave number")
    p.add_argument("--audience", default="", help="for new: the list's code")
    p.add_argument("--templates", default="",
                   help="for new: the folder holding one message per language, "
                        "e.g. templates/example")
    p.add_argument("--programme", default="", help="for new: the programme it moves")
    p.add_argument("--strategy", default=None,
                   help="how waves are cut; `arpenteur campaign strategies` lists them")
    p.add_argument("--pilot", type=int, default=None, help="firms in the first wave")
    p.add_argument("--wave-size", type=int, default=None, help="firms per wave")
    p.add_argument("--gap", type=int, default=None, help="days between waves")
    p.add_argument("--start", default=None, help="the first wave's date, YYYY-MM-DD")
    p.add_argument("--bounce-ceiling", type=float, default=None,
                   help="pause above this share of bounces, e.g. 0.05")
    p.add_argument("--settle", type=int, default=None,
                   help="hours bounces get to arrive before the next wave")
    p.add_argument("--cooldown", type=int, default=None,
                   help="days a firm or domain rests after a message; a mailbox is "
                        "written to once")
    p.add_argument("--bilingual", default=None,
                   help="for new and plan: cantons whose firms read every official "
                        "language in one message, e.g. FR,VS; an empty value clears it")
    p.add_argument("--replacement", default="", metavar="FIRM",
                   help="for send: the firm, by UID or name, whose bounced message goes "
                        "again to the address accepted for it")
    p.add_argument("--to", default="", help="for test: where the test goes")
    p.add_argument("--read", action="store_true",
                   help="for approve: you received and read every test message")
    p.add_argument("--confirm", default="", help="for send: the wave typed back, CODE/N")
    p.add_argument("--why", default="", help="for pause and resume: the reason")
    p.add_argument("--yes", action="store_true", help="for test and send: actually post")
    p.add_argument("--dry-run", action="store_true", help="for plan: show the cut only")
    p.set_defaults(func=cmd_campaign)


    p = sub.add_parser(
        "bounces",
        help="read delivery failures from the mailbox into the address verdicts",
    )
    p.add_argument("--folder", default="INBOX", help="which folder to read")
    p.add_argument(
        "--since", default="", metavar="YYYY-MM-DD",
        help="only messages the server dates on or after this day",
    )
    p.add_argument(
        "--limit", type=positive_int, default=500,
        help="how many of the most recent messages to screen (default 500)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="report what would be recorded, write nothing",
    )
    p.set_defaults(func=cmd_bounces)


    p = sub.add_parser(
        "answers",
        help="read the replies to the enrolment message and propose what each "
             "one enrols",
    )
    p.add_argument(
        "--programme", default="",
        help="the programme code, or [enrol] programme from arpenteur.toml",
    )
    p.add_argument(
        "--from-stage", dest="from_stage", default="contacted",
        help="the stage whose members are read (default contacted)",
    )
    p.add_argument(
        "--limit", type=positive_int, default=200,
        help="how many messages to read (default 200)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="report everything, suppress nothing",
    )
    p.set_defaults(func=cmd_answers)


    p = sub.add_parser(
        "send",
        help="post an approved draft, or one message typed on the command line",
    )
    p.add_argument(
        "--draft", action="append", default=[], metavar="ID",
        help="an approved draft to post; repeatable",
    )
    p.add_argument("--to", default="", help="one recipient, for a one-off")
    p.add_argument("--subject", default="", help="the subject, for a one-off")
    p.add_argument("--body", default="", help="the text, for a one-off")
    p.add_argument(
        "--body-file", dest="body_file", default="",
        help="read the text from this file; stdin when neither is given",
    )
    p.add_argument(
        "--yes", action="store_true",
        help="actually post, having read what a dry run listed",
    )
    p.add_argument(
        "--plain", action="store_true",
        help="send text only, without the formatted part",
    )
    p.add_argument(
        "--programme", default=None,
        help="the programme whose card a sent draft moves; '' for none. "
             "Without it, the one [enrol] names, and the log says which",
    )
    p.set_defaults(func=cmd_send)

