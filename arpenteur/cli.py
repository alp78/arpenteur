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
    boundaries,
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
    # `\b` is a word boundary in every regex flavour a person is likely to have
    # used before this one, and in Postgres it is a backspace. So `\bimmo\b`
    # does not narrow the alternative, it deletes it: the pattern still
    # compiles, the run still succeeds, and the companies simply never appear.
    # A national sweep hid 5,251 firms behind exactly this and reported done.
    if args.name_like and re.search(r"\\[bB]", args.name_like):
        print("warning: \\b is a backspace in Postgres regex, not a word "
              "boundary — that alternative will match nothing. Use \\m and "
              "\\M for the start and end of a word.", file=sys.stderr)
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
            name_like=args.name_like or None,
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
        f"classified {stats['classified']} companies, "
        f"{stats['sectors']} sectors, {stats['labels']} segment labels"
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
            sector=args.sector,
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
            sector=args.sector,
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
            sector=args.sector,
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


def cmd_rates(args: argparse.Namespace) -> int:
    """The catalogue: load it from arpenteur.toml, or read back what is held."""
    from .modules.sales import store as sales

    settings = load_settings()
    conn = db.open_db(settings.database_url)
    if args.action == "show":
        card = sales.current_card(conn)
        if card is None:
            print(
                "No rate card loaded. The [[rates.service]] entries in "
                "arpenteur.toml become one with `arpenteur rates load`."
            )
            conn.close()
            return 0
        print(f"{card['name']}, in force from {card['valid_from']}")
        print()
        print(sales.catalogue_text(conn))
        conn.close()
        return 0

    if not settings.catalogue:
        print(
            "No [[rates.service]] entries in arpenteur.toml. Nothing is "
            "invented here: prices come from you.",
            file=sys.stderr,
        )
        conn.close()
        return 1
    card_id, count = sales.load_catalogue(
        conn, list(settings.catalogue), note=settings.card_note,
    )
    conn.commit()
    # A new card rather than an edit of the old one, so a quote sent last month
    # is still explainable by the card it cites.
    print(f"rate card {card_id} opened with {count} services, in force from today")
    conn.close()
    return 0


def cmd_weights(args: argparse.Namespace) -> int:
    """The weight set: what the six parts of a score are worth to this campaign."""
    from .modules.scout import scoring, store as scout

    settings = load_settings()
    conn = db.open_db(settings.database_url)
    if args.action == "show":
        set_id, weights = scout.active_weights(conn)
        if set_id is None:
            print(
                "No weight set in force. Scoring uses the default, which is "
                "named a starting point rather than a finding:"
            )
        for component in scoring.COMPONENTS:
            weight = weights.get(component.key)
            if weight is None:
                continue
            print(f"  {component.key:14} {weight}  ({component.origin})")
        conn.close()
        return 0

    if not settings.weights:
        print(
            "No [scoring.weights] in arpenteur.toml. Nothing is invented here.",
            file=sys.stderr,
        )
        conn.close()
        return 1
    try:
        set_id = scout.save_weight_set(
            conn, settings.weight_set_name, settings.weights,
            note=settings.weight_set_note,
        )
    except scoring.ScoringError as exc:
        print(f"error: {exc}", file=sys.stderr)
        conn.close()
        return 1
    conn.commit()
    # A new version, never an edit: every score cites the set it used, and a
    # re-weighting must not rewrite what last month's ranking was.
    print(f"weight set {set_id} opened and in force")
    conn.close()
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Read a report, or bring the materialised ones up to date."""
    from .modules.analytics import store as analytics

    settings = load_settings()
    conn = db.open_db(settings.database_url)
    if args.action == "refresh":
        done = analytics.refresh(conn)
        print(f"refreshed {len(done)} view(s): {', '.join(done) or 'none'}")
        conn.close()
        return 0

    have = analytics.available(conn)
    if args.action == "list":
        for name, title, module in analytics.REPORTS:
            mark = " " if name in have else "-"
            print(f" {mark} {name:22} {title}"
                  + ("" if name in have else f"  (needs {module})"))
        conn.close()
        return 0

    name = args.name or ""
    if name not in have:
        print(
            f"No report called {name!r}. `arpenteur report list` shows what "
            "this deployment has.",
            file=sys.stderr,
        )
        conn.close()
        return 1
    rows = analytics.read(conn, name, limit=args.limit)
    if not rows:
        print("(nothing to report yet)")
        conn.close()
        return 0
    columns = list(rows[0].keys())
    if args.csv:
        # Straight to a spreadsheet, which is where a number like this ends up
        # anyway. Written with the csv module rather than by joining commas,
        # because a company name with a comma in it is not an edge case here.
        import csv

        out = csv.writer(sys.stdout)
        out.writerow(columns)
        for row in rows:
            out.writerow(["" if row[c] is None else row[c] for c in columns])
        conn.close()
        return 0
    widths = {
        c: max(len(c), *(len(str(row[c] if row[c] is not None else "")) for row in rows))
        for c in columns
    }
    print("  ".join(c.ljust(widths[c]) for c in columns))
    print("  ".join("-" * widths[c] for c in columns))
    for row in rows:
        print("  ".join(
            str(row[c] if row[c] is not None else "").ljust(widths[c])
            for c in columns
        ))
    conn.close()
    return 0


def cmd_boundaries(args: argparse.Namespace) -> int:
    """Fetch the cantonal outlines. Free, one request each, one-off.

    Not `cmd_regions`, which already exists and prints the territories named
    in arpenteur.toml. Two different things, and the first version of this
    shadowed the other one silently: a function defined later in a module
    simply replaces the earlier one, and argparse only complained because the
    subcommand name collided too.
    """
    settings = load_settings()
    conn = db.open_db(settings.database_url)
    run_id = db.start_run(conn, "boundaries", vars(args))
    try:
        stats = boundaries.run(
            settings, conn,
            cantons=_split(args.canton) or None,
            refresh=args.refresh,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        db.finish_run(conn, run_id, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    db.finish_run(conn, run_id, stats)
    print(
        f"{stats['stored']} boundary(ies) stored, {stats['held']} already held, "
        f"{stats['failed']} failed"
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
            sector=args.sector or "",
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
            # `NOT suppressed`, not `suppressed = 0`: the column is a boolean
            # in Postgres and the integer comparison is a SQLite habit that
            # survived the port. It raised before reaching anything below it,
            # so every section after this one was unreachable.
            "SELECT COUNT(*) FROM core.contact WHERE kind = 'email' AND NOT suppressed"
        ).fetchone()[0]
        print(f"  {'usable emails':20} {emails:>7}")

    rows = conn.execute(
        "SELECT sector, COUNT(*) AS n FROM intel.sector GROUP BY sector ORDER BY n DESC"
    ).fetchall()
    if rows:
        print("\nby sector")
        for row in rows:
            print(f"  {row['sector']:20} {row['n']:>7}")

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


def cmd_docs(args: argparse.Namespace) -> int:
    """The client's papers from the shell, and the folders they go in.

    The console does this by dropping files on a page, which is the right
    interface for two documents and the wrong one for a consultation dossier
    of ten that is already sitting in a directory. Windows paths are accepted
    as typed, for the same reason `note` accepts them.
    """
    settings = load_settings()
    conn = db.open_db(settings.database_url)
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
    settings = load_settings()
    conn = db.open_db(settings.database_url)
    for row in db.clients(conn):
        print(f"{row['code']:<18} {row['status']:<9} {row['name']}")
    conn.close()
    return 0


def _find_company(conn: Any, reference: str) -> Any:
    """A company by its UID or by part of its name.

    Separate from `_find_client` because a programme's members are companies
    and most of them will never be clients: looking one up through `crm.client`
    would find nothing for exactly the rows this command exists to add.
    """
    wanted = reference.strip()
    rows = conn.execute(
        "SELECT id, uid, name FROM core.company"
        " WHERE uid = %s OR REPLACE(uid, '-', '') = %s OR name ILIKE %s"
        " ORDER BY name LIMIT 3",
        (wanted, wanted.replace("-", "").replace(".", ""), f"%{wanted}%"),
    ).fetchall()
    if not rows:
        raise RuntimeError(f"No company matching {reference!r}")
    if len(rows) > 1 and rows[0]["uid"] != wanted:
        named = ", ".join(f"{r['name']} ({r['uid']})" for r in rows)
        raise RuntimeError(f"{reference!r} matches more than one: {named}")
    return rows[0]


def cmd_programme(args: argparse.Namespace) -> int:
    """A programme: one endeavour across many companies.

    Distinct from a project, which is one client's piece of work. Sending an
    application to fifty agencies is one programme with fifty members, each at
    its own stage, and the same agency can be a member of a second programme
    without either knowing about the other.

    Every action but `new` and `list` takes the programme's code first.
    """
    settings = load_settings()
    conn = db.open_db(settings.database_url)

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
            band=args.band, segment=args.segment, limit=args.limit,
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
        "--name-like",
        default="",
        help="only companies whose name matches this POSIX regex, which is how "
             "a national run stays inside the politeness budget. Word "
             "boundaries are \\m and \\M; \\b is a backspace and matches nothing",
    )
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

    p = sub.add_parser("profile", help="sort companies into sectors and segments")
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
    p.add_argument("--sector", help="only companies in this line of business")
    p.add_argument("--limit", type=int, help="required for runs over 200 companies")
    p.add_argument("--refresh", action="store_true", help="re-research companies already done")
    p.add_argument("--dry-run", action="store_true", help="list the shortlist, spend nothing")
    p.set_defaults(func=cmd_enrich)

    p = sub.add_parser(
        "match", help="score companies against campaign.md, with approach advice"
    )
    p.add_argument("--uid", help="comma separated UIDs")
    p.add_argument("--segment", help="only companies carrying this segment")
    p.add_argument("--sector", help="only companies in this line of business")
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
    p.add_argument("--sector", help="only companies in this line of business")
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
    p.add_argument("--sector", help="only companies in this line of business")
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
        "rates", help="the service catalogue and its prices"
    )
    p.add_argument(
        "action", choices=("load", "show"),
        help="load the [[rates.service]] entries from arpenteur.toml into a new "
             "rate card, or print the card in force",
    )
    p.set_defaults(func=cmd_rates)

    p = sub.add_parser(
        "weights", help="what each part of a score is worth to this campaign"
    )
    p.add_argument(
        "action", choices=("load", "show"),
        help="open a new version from [scoring.weights] in arpenteur.toml, or "
             "print the set in force",
    )
    p.set_defaults(func=cmd_weights)

    p = sub.add_parser(
        "report", help="the analytics views: the funnel, the spend, the pipeline"
    )
    p.add_argument(
        "action", choices=("list", "show", "refresh"),
        help="list what this deployment has, print one, or bring the "
             "materialised views up to date",
    )
    p.add_argument("name", nargs="?", help="which report, for show")
    p.add_argument("--limit", type=int, default=200, help="rows to print")
    p.add_argument("--csv", action="store_true", help="write CSV to stdout")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser(
        "boundaries",
        help="fetch the cantonal outlines from swisstopo (free, one-off)",
    )
    p.add_argument(
        "--canton",
        help="comma separated cantons; blank takes the configured territory",
    )
    p.add_argument(
        "--refresh", action="store_true", help="take boundaries already held again"
    )
    p.add_argument("--dry-run", action="store_true", help="say what would be fetched")
    p.set_defaults(func=cmd_boundaries)

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
        choices=("list", "new", "show", "add", "bring-in", "move", "say", "stages"),
        help="list them; start one; show one; add companies; add a shortlist "
             "from the survey; move a member; keep what a member said; read or "
             "rewrite the stages",
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
    p.add_argument("--limit", type=int, default=25, help="how many, for bring-in")
    p.set_defaults(func=cmd_programme)

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
