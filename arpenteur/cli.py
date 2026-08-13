"""Command line: one subcommand per pipeline stage.

argparse rather than a framework. The surface is small, the dependency list
stays short, and `arpenteur --help` prints the whole pipeline in one screen.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from . import attachments as att
from . import db, dossier, markdown
# Notes are new and the storage façade is meant to shrink, so this one reaches
# for the module store it means rather than growing the shim.
from .modules.clients import store as clients_store
from .config import load_settings
from .stages import (
    advise,
    draft,
    enrich,
    geocode,
    mail,
    match,
    people,
    prep,
    profile,
    reach,
    resolve,
    source,
    sync,
)


def _split(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


def _database_url(settings: Any, fixture: bool) -> str:
    """Keep recorded rows out of the database that holds live registry data.

    A separate database on the same server, created on demand. Under SQLite
    this was a second file beside the first; the guarantee is unchanged.
    """
    if not fixture:
        return settings.database_url
    from .kernel.db import ensure_database, sibling

    return ensure_database(sibling(settings.database_url, "_fixture"))


def cmd_init_db(args: argparse.Namespace) -> int:
    settings = load_settings()
    url = _database_url(settings, args.fixture)
    conn = db.open_db(url)
    print(f"{url} ready: " + ", ".join(
        f"{module} v{version}" for module, version in db.versions(conn).items()
    ))
    conn.close()
    return 0


def _scoped(args: argparse.Namespace):
    """Settings narrowed by whatever territory flags were given.

    Resolved once, at the edge, so every stage below sees the same territory
    instead of each deciding for itself what --region meant.
    """
    return load_settings().scoped(
        region=getattr(args, "region", None),
        cantons=_split(getattr(args, "canton", None)),
        legal_forms=_split(getattr(args, "legal_form", None)),
    )


def cmd_source(args: argparse.Namespace) -> int:
    settings = _scoped(args)
    conn = db.open_db(_database_url(settings, args.fixture))
    stage = f"source:{args.provider}" + (":fixture" if args.fixture else "")
    run_id = db.start_run(conn, stage, vars(args))
    # The fixture set is its own small territory, so announcing the configured
    # one would name cantons the run is not going to touch.
    print(
        "territory: recorded fixture set (VD, Sàrl)"
        if args.fixture
        else f"territory: {settings.territory.describe()}"
    )
    try:
        stats = source.run_zefix(
            settings,
            conn,
            resume=not args.no_resume,
            hydrate=not args.no_hydrate,
            limit=args.limit,
            bulk=args.bulk,
            fixture=args.fixture,
        )
    except Exception as exc:
        db.finish_run(conn, run_id, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    db.finish_run(conn, run_id, stats)
    print(
        f"searched {stats['searched']} prefixes, "
        f"{stats['seen']} company rows, {stats['hydrated']} hydrated"
    )
    conn.close()
    return 0


def cmd_hydrate(args: argparse.Namespace) -> int:
    settings = _scoped(args)
    conn = db.open_db(_database_url(settings, args.fixture))
    run_id = db.start_run(conn, "hydrate" + (":fixture" if args.fixture else ""), vars(args))
    try:
        count = source.hydrate_pending(
            settings,
            conn,
            # The fixture database holds only what the fixture sweep put there,
            # so narrowing it to the configured territory would filter a set
            # that is already the whole of it.
            cantons=None if args.fixture else settings.cantons,
            legal_forms=None if args.fixture else settings.legal_forms,
            limit=args.limit,
            bulk=args.bulk,
            fixture=args.fixture,
        )
    except Exception as exc:
        db.finish_run(conn, run_id, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    db.finish_run(conn, run_id, {"hydrated": count})
    print(f"hydrated {count} companies")
    conn.close()
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    settings = _scoped(args)
    conn = db.open_db(settings.database_url)
    run_id = db.start_run(conn, "sync", vars(args))
    print(f"territory: {settings.territory.describe()}")
    try:
        stats = sync.run(
            settings,
            conn,
            since=args.since,
            until=args.until,
            resume=not args.no_resume,
            admit_new=False if args.no_admit else None,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        db.finish_run(conn, run_id, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    db.finish_run(conn, run_id, stats)
    print(
        f"{stats['days']} days, {stats['notices']} notices, "
        f"{stats['tracked']} ours: {stats['inserted']} new, "
        f"{stats['updated']} changed, {stats['unchanged']} unchanged, "
        f"{stats['deletions']} struck off"
    )
    if stats["stale"]:
        print(f"{stats['stale']} companies await re-hydration (run: arpenteur hydrate)")
    conn.close()
    return 0


def cmd_legal_forms(args: argparse.Namespace) -> int:
    """Print the registry's own legal form catalogue.

    Config takes eCH-0097 uid codes, which are not memorable, and the
    alternative to printing them is guessing at a list that a registry change
    could quietly invalidate. Resolved live for the same reason the sweep
    resolves them live.
    """
    settings = load_settings()
    from .sources.zefix import ZefixClient
    from .stages.source import _client

    with _client(settings) as http:
        forms = ZefixClient(http, settings.zefix.base_url).legal_forms()

    chosen = set(settings.territory.legal_forms)
    print(f"{'uid':6} {'short':8} name")
    for form in sorted(forms, key=lambda f: str(f.get("uid"))):
        uid = str(form.get("uid") or "")
        name = form.get("name")
        short = form.get("shortName")
        label = name.get("fr") if isinstance(name, dict) else name
        tag = short.get("fr") if isinstance(short, dict) else short
        mark = "*" if uid in chosen else " "
        print(f"{mark}{uid:5} {str(tag or ''):8} {label or ''}")
    print(f"\n* in survey.legal_forms. Territory: {settings.territory.describe()}")
    return 0


def cmd_console(args: argparse.Namespace) -> int:
    """Serve the console.

    Bound to localhost by default. The database holds contact details for
    thousands of companies and, where sole proprietorships are included, for
    identifiable people; there is no authentication here because there is no
    intent that it be reachable. Putting it on 0.0.0.0 is a deliberate act and
    wants a reverse proxy with auth in front of it.
    """
    import uvicorn

    from .console import create_app

    settings = load_settings()
    print(f"territory: {settings.territory.describe()}")
    print(f"console:   http://{args.host}:{args.port}")
    if args.host not in ("127.0.0.1", "localhost"):
        print(
            "warning: this binds beyond localhost and the console has no "
            "authentication. Put a proxy in front of it.",
            file=sys.stderr,
        )
    uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_regions(args: argparse.Namespace) -> int:
    settings = load_settings()
    print(f"active: {settings.territory.describe()}")
    print(f"forms:  {', '.join(settings.territory.legal_forms) or 'all'}")
    print(f"admit new companies: {'yes' if settings.admit_new else 'no'}")
    print("\ndefined in arpenteur.toml:")
    for name in sorted(settings.regions):
        members = settings.regions[name]
        print(f"  {name:14} {len(members):>2} cantons  {', '.join(members)}")
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = db.open_db(_database_url(settings, args.fixture))
    run_id = db.start_run(conn, "profile" + (":fixture" if args.fixture else ""), vars(args))
    try:
        stats = profile.run(
            settings,
            conn,
            limit=args.limit,
            reclassify=args.reclassify,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        db.finish_run(conn, run_id, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    db.finish_run(conn, run_id, stats)
    print(
        f"classified {stats['classified']} companies, {stats['labels']} labels"
        + (" (dry run, nothing written)" if args.dry_run else "")
    )
    conn.close()
    return 0


def cmd_people(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = db.open_db(settings.database_url)
    run_id = db.start_run(conn, "people", vars(args))
    try:
        stats = people.run(
            settings,
            conn,
            limit=args.limit,
            reparse=args.reparse,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        db.finish_run(conn, run_id, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    db.finish_run(conn, run_id, stats)
    print(
        f"{stats['people']} officers across {stats['with_people']} companies "
        f"(of {stats['considered']} with publications)"
        + (f", {stats['pruned']} withdrawn by the improved parser"
           if stats.get("pruned") else "")
        + (" (dry run, nothing written)" if args.dry_run else "")
    )
    conn.close()
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = db.open_db(settings.database_url)
    run_id = db.start_run(conn, "resolve", vars(args))
    try:
        stats = resolve.run(settings, conn, dry_run=args.dry_run)
    except Exception as exc:
        db.finish_run(conn, run_id, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    db.finish_run(conn, run_id, stats)
    conn.close()
    return 0


def cmd_enrich(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = db.open_db(settings.database_url)
    run_id = db.start_run(conn, "enrich", vars(args))
    try:
        stats = enrich.run(
            settings,
            conn,
            uids=_split(args.uid),
            canton=args.canton,
            segment=args.segment,
            limit=args.limit,
            refresh=args.refresh,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        db.finish_run(conn, run_id, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    db.finish_run(conn, run_id, stats)
    print(
        f"found {stats['found']}, ambiguous {stats['ambiguous']}, "
        f"not found {stats['not_found']}, failed {stats['failed']}; "
        f"{stats['emails']} new emails; "
        f"{stats['searches']} searches, {stats['fetches']} fetches, "
        f"{stats['turns']} turns, "
        f"{stats['input_tokens']}/{stats['output_tokens']} tokens in/out"
    )
    conn.close()
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = db.open_db(settings.database_url)
    run_id = db.start_run(conn, "match", vars(args))
    try:
        stats = match.run(
            settings,
            conn,
            uids=_split(args.uid),
            segment=args.segment,
            researched_only=not args.include_unresearched,
            limit=args.limit,
            rescore=args.rescore,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        db.finish_run(conn, run_id, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    db.finish_run(conn, run_id, stats)
    print(
        f"scored {stats['scored']} against brief {stats['brief']}: "
        f"{stats['high']} high, {stats['medium']} medium, {stats['low']} low, "
        f"{stats['excluded']} excluded, {stats['failed']} failed"
    )
    conn.close()
    return 0


def cmd_reach(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = db.open_db(settings.database_url)
    run_id = db.start_run(conn, "reach", vars(args))
    try:
        stats = reach.run(
            settings,
            conn,
            uids=_split(args.uid),
            segment=args.segment,
            band=args.band,
            limit=args.limit,
            refresh=args.refresh,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        db.finish_run(conn, run_id, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    db.finish_run(conn, run_id, stats)
    print(
        f"read {stats['pages']} pages: {stats['with_address']} companies with a "
        f"published address ({stats['new']} new of {stats['addresses']} found), "
        f"{stats['without']} without, {stats['blocked']} declined by robots.txt, "
        f"{stats['unreadable']} unreadable; "
        f"{stats['for_pattern']} left for the pattern tier"
    )
    conn.close()
    return 0


def cmd_mail(args: argparse.Namespace) -> int:
    """Read the mailbox into the review queue. Files nothing, sends nothing."""
    settings = load_settings()
    conn = db.open_db(settings.database_url)
    run_id = db.start_run(conn, "mail", vars(args))
    try:
        stats = mail.run(
            settings,
            conn,
            folders=_split(args.folder) or None,
            days=args.days,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        db.finish_run(conn, run_id, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    db.finish_run(conn, run_id, stats)
    # The count that matters is what a person now has to read, not what was
    # fetched: a run that queues two hundred messages has made work, not
    # progress.
    print(
        f"{stats['queued']} queued for review, {stats['matched']} with a "
        f"company proposed, {stats['known']} already held. Nothing filed."
    )
    conn.close()
    return 0


def cmd_geocode(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = db.open_db(settings.database_url)
    run_id = db.start_run(conn, "geocode", vars(args))
    try:
        stats = geocode.run(
            settings,
            conn,
            uids=_split(args.uid),
            canton=args.canton or "",
            segment=args.segment or "",
            band=args.band or "",
            worked=args.worked,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        db.finish_run(conn, run_id, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    db.finish_run(conn, run_id, stats)
    # Reported by precision rather than as one total. "412 placed" says nothing
    # about whether the map is worth looking at; "380 on a door, 32 on a town"
    # says exactly how much of it to believe.
    print(
        f"placed {stats['placed']} of {stats['considered']}: "
        f"{stats['address']} on an address, {stats['street']} on a street, "
        f"{stats['postcode']} on a town only; {stats['unplaced']} unplaced, "
        f"{stats['no_domicile']} with no domicile on the register"
    )
    conn.close()
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """One company by UID, whatever the territory says.

    A client does not have to be inside the surveyed region and legal forms,
    and requiring one to be would let the survey dictate who may be a client.
    """
    settings = load_settings()
    conn = db.open_db(settings.database_url)
    run_id = db.start_run(conn, "import", vars(args))
    try:
        found = source.import_company(settings, conn, args.uid)
        if args.client:
            client_id = db.promote_to_client(conn, found["company_id"])
            conn.commit()
            code = conn.execute(
                "SELECT code FROM crm.client WHERE id = %s", (client_id,)
            ).fetchone()[0]
            print(f"client {code} created")
    except Exception as exc:
        db.finish_run(conn, run_id, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    db.finish_run(conn, run_id, found)
    conn.close()
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = db.open_db(_database_url(settings, args.fixture))

    total = conn.execute("SELECT COUNT(*) FROM core.company").fetchone()[0]
    hydrated = conn.execute(
        "SELECT COUNT(*) FROM core.company WHERE hydrated_at IS NOT NULL"
    ).fetchone()[0]
    print(f"companies: {total} ({hydrated} hydrated)")

    rows = conn.execute(
        """
        SELECT canton, COUNT(*) AS n FROM core.company
        WHERE canton IS NOT NULL GROUP BY canton ORDER BY n DESC
        """
    ).fetchall()
    if rows:
        print("\nby canton")
        for row in rows:
            print(f"  {row['canton'] or '?':4} {row['n']:>7}")

    rows = conn.execute(
        """
        SELECT COALESCE(legal_form_label, legal_form_uid, '%s') AS form,
               COUNT(*) AS n
        FROM core.company GROUP BY form ORDER BY n DESC LIMIT 10
        """
    ).fetchall()
    if rows:
        print("\nby legal form")
        for row in rows:
            print(f"  {row['form'][:28]:30} {row['n']:>7}")

    publications = conn.execute("SELECT COUNT(*) FROM core.publication").fetchone()[0]
    if publications:
        officers = conn.execute("SELECT COUNT(*) FROM core.person").fetchone()[0]
        companies_with = conn.execute(
            "SELECT COUNT(DISTINCT company_id) FROM core.person"
        ).fetchone()[0]
        print(f"\npublications: {publications}")
        print(f"officers: {officers} across {companies_with} companies")
        rows = conn.execute(
            "SELECT role, COUNT(*) AS n FROM core.person GROUP BY role ORDER BY n DESC LIMIT 8"
        ).fetchall()
        for row in rows:
            print(f"  {row['role'] or '?':20} {row['n']:>7}")

    sites = conn.execute("SELECT COUNT(*) FROM intel.site").fetchone()[0]
    if sites:
        print("\nweb research")
        for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM intel.site GROUP BY status ORDER BY n DESC"
        ):
            print(f"  {row['status']:20} {row['n']:>7}")
        spend = conn.execute(
            "SELECT COALESCE(SUM(searches), 0) s, COALESCE(SUM(fetches), 0) f FROM intel.site"
        ).fetchone()
        print(f"  {'searches / fetches':20} {spend['s']:>7} / {spend['f']}")
        emails = conn.execute(
            "SELECT COUNT(*) FROM core.contact WHERE kind = 'email' AND suppressed = 0"
        ).fetchone()[0]
        print(f"  {'usable emails':20} {emails:>7}")

    rows = conn.execute(
        "SELECT segment, COUNT(*) AS n FROM intel.segment GROUP BY segment ORDER BY n DESC"
    ).fetchall()
    if rows:
        print("\nby segment")
        for row in rows:
            print(f"  {row['segment']:20} {row['n']:>7}")
    else:
        print("\nno segments yet (run: arpenteur profile)")

    rows = conn.execute(
        """
        SELECT stage, started_at, finished_at, stats_json, error
        FROM intel.run ORDER BY id DESC LIMIT 5
        """
    ).fetchall()
    if rows:
        print("\nrecent runs")
        for row in rows:
            state = "error" if row["error"] else ("done" if row["finished_at"] else "running")
            print(f"  {row['started_at']}  {row['stage']:16} {state}")

    conn.close()
    return 0


def _local_path(given: str) -> Path:
    """A path as typed, including a Windows one under WSL.

    `C:\\Users\\aperi\\DEV\\note.md` is where the file is as far as its author is
    concerned, and it is `/mnt/c/Users/aperi/DEV/note.md` as far as this
    process is concerned. Translating it here costs four lines and removes a
    step that would otherwise be got wrong at the end of every day.
    """
    text = given.strip().strip('"')
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", text)
    if match:
        drive, rest = match.groups()
        return Path(f"/mnt/{drive.lower()}/" + rest.replace("\\", "/"))
    return Path(text)


def _find_client(conn: Any, reference: str) -> Any:
    """A client by its code, its UID or its name. Any of the three."""
    wanted = reference.strip()
    row = conn.execute(
        """SELECT c.*, co.name, co.uid FROM crm.client c
             JOIN core.company co ON co.id = c.company_id
            WHERE c.code = %s OR co.uid = %s OR REPLACE(co.uid,'-','') = %s
               OR co.name LIKE %s
            ORDER BY c.id LIMIT 2""",
        (wanted.upper(), wanted, wanted.replace("-", "").replace(".", ""), f"%{wanted}%"),
    ).fetchall()
    if not row:
        raise RuntimeError(f"No client matching {reference!r}. Try: arpenteur clients")
    return row[0]


def cmd_note(args: argparse.Namespace) -> int:
    """Notes on a client, in Markdown, from the editor already open.

    `add` takes a path. The path is stored, so running the same command again
    after editing the file updates that note instead of writing a second one,
    and an unchanged file says so rather than claiming an update. A Windows
    path is accepted as typed: this runs under WSL as often as not, and
    retyping `C:\\Users\\...` as `/mnt/c/Users/...` is a translation the
    program can do and a person should not have to.
    """
    settings = load_settings()
    conn = db.open_db(settings.database_url)
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


def cmd_clients(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = db.open_db(settings.database_url)
    for row in db.clients(conn):
        print(f"{row['code']:<18} {row['status']:<9} {row['name']}")
    conn.close()
    return 0


def cmd_dossier(args: argparse.Namespace) -> int:
    """Print everything held on a client, as text.

    This exists so an agent can be pointed at a client without a browser and
    without a schema. It prints exactly what the advise stage is given, in the
    same order, so a surprising suggestion can be read against its own input
    rather than guessed at. `--documents` adds the extracted text of every
    paper the client sent, which is what makes "analyse their contract"
    something an agent can actually do.
    """
    settings = load_settings()
    conn = db.open_db(settings.database_url)
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
                  f"{paper['bytes']} bytes, {state}")
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
            import json as _json

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
    settings = load_settings()
    conn = db.open_db(settings.database_url)
    try:
        client = _find_client(conn, args.client)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    run_id = db.start_run(conn, "advise", vars(args))
    try:
        stats = advise.run(
            settings, conn, client_id=client["id"], dry_run=args.dry_run
        )
    except Exception as exc:
        db.finish_run(conn, run_id, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    db.finish_run(conn, run_id, stats)
    print(
        f"{client['code']}: read {stats['sections']} sections and "
        f"{stats['documents']} documents"
        + ("" if stats.get("saved") else " (dry run, nothing saved)")
    )
    conn.close()
    return 0


def cmd_prep(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = db.open_db(settings.database_url)
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
    run_id = db.start_run(conn, "prep", vars(args))
    try:
        stats = prep.run(settings, conn, client_id=client["id"], dry_run=args.dry_run)
    except Exception as exc:
        db.finish_run(conn, run_id, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    db.finish_run(conn, run_id, stats)
    conn.close()
    return 0


def cmd_modules(args: argparse.Namespace) -> int:
    """What is installed, what it owns, and how far its schema has got.

    `drop` is the only destructive command in this tool, and it says so twice
    before doing anything: modules are removable by design, but removing one
    deletes its data and nothing here can bring that back.
    """
    from .kernel import db as kernel
    from .kernel import modules as registry

    settings = load_settings()
    if args.action == "list":
        conn = db.open_db(settings.database_url)
        versions = kernel.versions(conn)
        enabled = set(settings.modules)
        print(f"{'module':<10} {'state':<10} {'schema':<18} version  what")
        for name in registry.AVAILABLE:
            module = getattr(
                __import__(f"arpenteur.modules.{name}", fromlist=["MODULE"]), "MODULE"
            )
            state = "enabled" if name in enabled else "off"
            schemas = ", ".join(module.schemas)
            version = versions.get(name, "-")
            print(f"{name:<10} {state:<10} {schemas:<18} {str(version):>7}  {module.what[:60]}")
        print("\nEdit [modules] enabled in arpenteur.toml to change what loads.")
        conn.close()
        return 0

    module = registry.load(registry.AVAILABLE).by_name.get(args.name)
    if module is None:
        print(f"error: unknown module {args.name!r}", file=sys.stderr)
        return 1
    if not module.removable:
        print(
            f"error: {module.name} cannot be dropped. It owns the company "
            "table and every other module is downstream of it.",
            file=sys.stderr,
        )
        return 1
    conn = db.open_db(settings.database_url)
    held = {
        schema: conn.execute(
            "SELECT COUNT(*) AS n FROM information_schema.tables"
            " WHERE table_schema = %s", (schema,)
        ).fetchone()["n"]
        for schema in module.schemas
    }
    print(f"This deletes {module.name}: schema " + ", ".join(
        f"{s} ({n} tables)" for s, n in held.items()
    ))
    print("Every row in them goes. There is no undo without a backup.")
    if not args.yes:
        print("Re-run with --yes to proceed.", file=sys.stderr)
        return 1
    kernel.drop_module(conn, module.name, module.schemas)
    print(f"dropped {module.name}. Remove it from [modules] enabled in arpenteur.toml too.")
    conn.close()
    return 0


def cmd_stub(args: argparse.Namespace) -> int:
    {"draft": draft}[args.command].run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arpenteur",
        description="Survey a market from the Swiss public register.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="create or migrate the database")
    p.add_argument(
        "--fixture",
        action="store_true",
        help="use the separate database reserved for recorded fixture data",
    )
    p.set_defaults(func=cmd_init_db)

    def territory_flags(parser: argparse.ArgumentParser) -> None:
        """The same three flags on every command that acts on a territory.

        One vocabulary rather than three: --region on `source` and --canton on
        `hydrate` would make the two disagree about what a run covered.
        """
        group = parser.add_argument_group("territory")
        group.add_argument(
            "--region", help="a name from [regions] in arpenteur.toml, e.g. romandie"
        )
        group.add_argument(
            "--canton", help="comma separated, e.g. VD,GE (overrides --region)"
        )
        group.add_argument(
            "--legal-form", help="comma separated eCH-0097 uids, e.g. 0107,0106"
        )

    p = sub.add_parser("source", help="fill the company table from a register")
    p.add_argument("provider", choices=["zefix"], nargs="?", default="zefix")
    territory_flags(p)
    p.add_argument("--no-resume", action="store_true", help="re-sweep completed prefixes")
    p.add_argument("--no-hydrate", action="store_true", help="skip the detail pass")
    p.add_argument("--limit", type=int, help="cap the detail pass")
    p.add_argument(
        "--bulk",
        action="store_true",
        help=f"accept a detail pass over {source.POLITE_HYDRATION_LIMIT}+ companies",
    )
    p.add_argument(
        "--fixture",
        action="store_true",
        help="replay bundled VD/Sàrl responses without credentials or network",
    )
    p.set_defaults(func=cmd_source)

    p = sub.add_parser("hydrate", help="fetch detail for companies missing it")
    territory_flags(p)
    p.add_argument("--limit", type=int)
    p.add_argument(
        "--bulk",
        action="store_true",
        help=f"accept a run over {source.POLITE_HYDRATION_LIMIT} companies "
        "(Zefix asks users to avoid regular mass querying)",
    )
    p.add_argument(
        "--fixture",
        action="store_true",
        help="replay bundled details from the separate fixture database",
    )
    p.set_defaults(func=cmd_hydrate)

    p = sub.add_parser(
        "sync",
        help="consume the FOSC change feed: one request per day, not per company",
    )
    territory_flags(p)
    p.add_argument("--since", help="first FOSC date, YYYY-MM-DD (default: resume)")
    p.add_argument("--until", help="last FOSC date, YYYY-MM-DD (default: today)")
    p.add_argument(
        "--no-resume", action="store_true", help="re-read days already consumed"
    )
    p.add_argument(
        "--no-admit",
        action="store_true",
        help="do not add companies not already held, even inside the territory",
    )
    p.add_argument("--dry-run", action="store_true", help="say what would be fetched")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("profile", help="sort companies into segments")
    p.add_argument("--limit", type=int)
    p.add_argument("--reclassify", action="store_true", help="include already classified")
    p.add_argument("--dry-run", action="store_true", help="print, write nothing")
    p.add_argument(
        "--fixture",
        action="store_true",
        help="read from the separate fixture database",
    )
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("people", help="officers from the register's SHAB notices")
    p.add_argument("--limit", type=int)
    p.add_argument(
        "--reparse",
        action="store_true",
        help="re-read companies that already have people (use after parser changes)",
    )
    p.add_argument("--dry-run", action="store_true", help="print, write nothing")
    p.set_defaults(func=cmd_people)

    p = sub.add_parser(
        "enrich",
        help="find and read company websites (costs money per company)",
    )
    p.add_argument("--uid", help="comma separated UIDs, e.g. CHE123...,CHE456...")
    p.add_argument("--canton")
    p.add_argument("--segment", help="only companies carrying this segment")
    p.add_argument("--limit", type=int, help="required for runs over 200 companies")
    p.add_argument("--refresh", action="store_true", help="re-research companies already done")
    p.add_argument("--dry-run", action="store_true", help="list the shortlist, spend nothing")
    p.set_defaults(func=cmd_enrich)

    p = sub.add_parser(
        "match", help="score companies against campaign.md, with approach advice"
    )
    p.add_argument("--uid", help="comma separated UIDs")
    p.add_argument("--segment", help="only companies carrying this segment")
    p.add_argument(
        "--include-unresearched",
        action="store_true",
        help="score on the register alone, without waiting for enrich",
    )
    p.add_argument("--limit", type=int)
    p.add_argument(
        "--rescore", action="store_true", help="re-score companies already scored"
    )
    p.add_argument("--dry-run", action="store_true", help="list the shortlist, spend nothing")
    p.set_defaults(func=cmd_match)

    p = sub.add_parser(
        "import", help="fetch one company by UID, inside the territory or not"
    )
    p.add_argument("uid", help="e.g. CHE-103.895.771 or CHE103895771")
    p.add_argument(
        "--client", action="store_true", help="also open a client record for it"
    )
    p.set_defaults(func=cmd_import)

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

    p = sub.add_parser("modules", help="what is installed, and what it owns")
    msub = p.add_subparsers(dest="action", required=True)
    msub.add_parser("list", help="every module, its schema and its version")
    drop = msub.add_parser(
        "drop", help="delete a module's schema and all of its data"
    )
    drop.add_argument("name")
    drop.add_argument(
        "--yes", action="store_true", help="required: this cannot be undone"
    )
    p.set_defaults(func=cmd_modules)

    p = sub.add_parser("console", help="serve the browser console on localhost")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_console)

    p = sub.add_parser("stats", help="what the survey holds")
    p.add_argument(
        "--fixture",
        action="store_true",
        help="read from the separate fixture database",
    )
    p.set_defaults(func=cmd_stats)
    sub.add_parser(
        "regions", help="territories defined in arpenteur.toml, and the active one"
    ).set_defaults(func=cmd_regions)
    sub.add_parser(
        "legal-forms", help="the registry's legal form catalogue, with uid codes"
    ).set_defaults(func=cmd_legal_forms)

    p = sub.add_parser(
        "reach",
        help="read a company's own site for a published address (no model cost)",
    )
    p.add_argument("--uid", help="comma separated UIDs")
    p.add_argument("--segment", help="only companies carrying this segment")
    p.add_argument("--band", help="only companies whose fit is in this band")
    p.add_argument("--limit", type=int, help="required for runs over 200 companies")
    p.add_argument(
        "--refresh", action="store_true", help="re-read sites already looked at"
    )
    p.add_argument("--dry-run", action="store_true", help="list the sites, fetch nothing")
    p.set_defaults(func=cmd_reach)

    p = sub.add_parser(
        "geocode",
        help="place companies on the map from the address the register gives (free)",
    )
    p.add_argument("--uid", help="comma separated UIDs")
    p.add_argument("--canton", help="only companies in this canton")
    p.add_argument("--segment", help="only companies carrying this segment")
    p.add_argument("--band", help="only companies whose fit is in this band")
    p.add_argument(
        "--worked", action="store_true",
        help="only companies with a pipeline state, which is the shortlist a map is read for",
    )
    p.add_argument("--limit", type=int, help="required for runs over 500 companies")
    p.add_argument("--dry-run", action="store_true", help="list the addresses, ask nothing")
    p.set_defaults(func=cmd_geocode)

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
    p.add_argument("--limit", type=int, help="stop after this many new messages")
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
        "resolve",
        help="work out which officer rows are the same person (free, seconds)",
    )
    p.add_argument("--dry-run", action="store_true", help="say what it would do")
    p.set_defaults(func=cmd_resolve)

    sub.add_parser(
        "draft", help="propose messages for review (not built yet)"
    ).set_defaults(func=cmd_stub)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Any = args.func
    try:
        return int(handler(args) or 0)
    except RuntimeError as exc:
        # Configuration is resolved before a stage's own error handling, so a
        # misspelled region or auth mode would otherwise reach the user as a
        # traceback. It is a typo, not a crash, and should read like one.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        # Every stage commits as it goes and resumes where it stopped, so an
        # interrupted run is a normal way to end one.
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
