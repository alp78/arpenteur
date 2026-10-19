"""The survey: sweep, detail, officers, sectors, research, scoring, addresses, drafts.

Commands of the scout module, registered when it is enabled. Moved out of
`cli.py` verbatim; what they share is in `common.py`.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from .. import db
from ..modules.scout import store as scout_store
from ..sources import mailcheck
from ..stages import (
    draft,
    enrich,
    match,
    people,
    profile,
    reach,
    replace,
    resolve,
    source,
    sync,
    verify,
)
from . import common
from .common import (
    _database_url,
    _scoped,
    _search_words,
    _split,
    positive_int,
    run_scope,
    territory_flags,
)


def cmd_source(args: argparse.Namespace) -> int:
    settings = _scoped(args)
    words = _search_words(args)
    conn = db.open_db(_database_url(settings, args.fixture), settings.modules)
    stage = f"source:{args.provider}" + (":fixture" if args.fixture else "")
    if words:
        stage += ":by-name"
    try:
        with run_scope(conn, stage, args) as run:
            # The fixture set is its own small territory, so announcing the configured
            # one would name cantons the run is not going to touch.
            print(
                "territory: recorded fixture set (VD, Sàrl)"
                if args.fixture
                else f"territory: {settings.territory.describe()}"
            )
            if words:
                unit = "word" if len(words) == 1 else "words"
                print(f"searching {len(words)} {unit}, every legal form")
            if words:
                stats = source.run_by_name(
                    settings,
                    conn,
                    words=words,
                    hydrate=not args.no_hydrate,
                    limit=args.limit,
                    bulk=args.bulk,
                    fixture=args.fixture,
                    dry_run=args.dry_run,
                )
            else:
                stats = source.run_zefix(
                    settings,
                    conn,
                    resume=not args.no_resume,
                    hydrate=not args.no_hydrate,
                    limit=args.limit,
                    bulk=args.bulk,
                    fixture=args.fixture,
                )
            run.stats = stats
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if words:
        print(
            f"searched {stats['searched']} canton-word "f"{'pair' if stats['searched'] == 1 else 'pairs'}, "
            f"{stats['found']} distinct companies, {stats['hydrated']} hydrated"
            + (" (dry run, nothing written)" if args.dry_run else "")
        )
        if stats["refused"]:
            print(f"{stats['refused']} searches refused as too broad")
    else:
        print(
            f"searched {stats['searched']} prefixes, "
            f"{stats['seen']} company rows, {stats['hydrated']} hydrated"
        )
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
    conn = db.open_db(_database_url(settings, args.fixture), settings.modules)
    try:
        with run_scope(conn, "hydrate" + (":fixture" if args.fixture else ""), args) as run:
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
            run.stats = {"hydrated": count}
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"hydrated {count} companies")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    settings = _scoped(args)
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        with run_scope(conn, "sync", args) as run:
            print(f"territory: {settings.territory.describe()}")
            stats = sync.run(
                settings,
                conn,
                since=args.since,
                until=args.until,
                resume=not args.no_resume,
                admit_new=False if args.no_admit else None,
                dry_run=args.dry_run,
            )
            run.stats = stats
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"{stats['days']} days, {stats['notices']} notices, "
        f"{stats['tracked']} ours: {stats['inserted']} new, "
        f"{stats['updated']} changed, {stats['unchanged']} unchanged, "
        f"{stats['deletions']} struck off"
    )
    if stats["stale"]:
        print(f"{stats['stale']} companies await re-hydration (run: arpenteur hydrate)")
    return 0


def cmd_legal_forms(args: argparse.Namespace) -> int:
    """Print the registry's own legal form catalogue.

    Config takes eCH-0097 uid codes, which are not memorable, and the
    alternative to printing them is guessing at a list that a registry change
    could quietly invalidate. Resolved live for the same reason the sweep
    resolves them live.
    """
    settings = common.load_settings()
    from ..sources.zefix import ZefixClient
    from ..stages.source import _client

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


def cmd_verify(args: argparse.Namespace) -> int:
    """Record what can be established about each address without sending.

    One DNS answer per domain, written to every address at it. A dead domain
    is the one certain verdict; a live one says the domain takes mail and says
    nothing about the mailbox, which only a send settles.
    """
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        with run_scope(conn, "verify", args) as run:
            stats = verify.run(
                conn,
                refresh=args.refresh,
                stale_after_days=args.stale_after,
                limit=args.limit,
                dry_run=args.dry_run,
            )
            run.stats = stats
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        print("(dry run, nothing written)")
    return 0


def cmd_sendlist(args: argparse.Namespace) -> int:
    """One address per firm, ready for a staged send.

    A desk address wins over a named person's where the firm has both. That
    ordering is the point: writing to `info@` about a product and writing to an
    employee by name are different acts under the nLPD and the UWG, so the
    second is reported separately rather than mixed in.
    """
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    rows = scout_store.send_list(
        conn,
        desk_parts=mailcheck.desk_parts(),
        segments=[s.strip() for s in args.segment.split(",") if s.strip()]
        if args.segment else None,
        cantons=[c.strip() for c in args.canton.split(",") if c.strip()]
        if args.canton else None,
        statuses=tuple(
            s.strip() for s in args.status.split(",") if s.strip()
        ),
    )
    conn.close()

    # The store picked one address per firm by the desk vocabulary. Labelling
    # it is the caller's job, because the three-way answer needs the company
    # name and `scout` may not import the address vocabulary to read it.
    kind = {
        r["address"]: mailcheck.mailbox_kind(r["address"], r["name"])
        for r in rows
    }
    desks = [r for r in rows if kind[r["address"]] == mailcheck.DESK]
    people = [r for r in rows if kind[r["address"]] == mailcheck.PERSON]
    unclear = [r for r in rows if kind[r["address"]] == mailcheck.UNCLEAR]

    if args.out:
        import csv

        out = Path(args.out)
        with out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["uid", "name", "canton", "seat", "address", "mailbox",
                 "method", "status", "source_url"]
            )
            for r in rows:
                writer.writerow([
                    r["uid"], r["name"], r["canton"], r["legal_seat"] or "",
                    r["address"], kind[r["address"]],
                    r["method"] or "", r["status"] or "", r["source_url"] or "",
                ])
        print(f"{out}  {len(rows):,} firm(s)")
    else:
        mark = {mailcheck.DESK: "  ", mailcheck.PERSON: " !",
                mailcheck.UNCLEAR: " ?"}
        for r in rows[: args.limit or 40]:
            print(f"{mark[kind[r['address']]]} {r['canton']:<3} "
                  f"{r['name'][:38]:40} {r['address']}")
        if not args.out and len(rows) > (args.limit or 40):
            print(f"   ... {len(rows) - (args.limit or 40):,} more")

    print(f"\n{len(rows):,} firm(s) reachable")
    print(f"  {len(desks):,} at a desk address")
    print(f"  {len(people):,} at a named person (!)")
    print(f"  {len(unclear):,} neither clearly (?)")
    if people or unclear:
        print("\nOnly the first group is a desk. Writing to an identified")
        print("individual is a different act under the nLPD and UWG Art.")
        print("3(1)(o), and the unclear group has not been established")
        print("either way, so neither belongs in a first send by default.")
    return 0


def cmd_draft(args: argparse.Namespace) -> int:
    """Propose one message per firm, for a person to judge.

    Nothing is sent here. Rows land in `intel.draft` with `sent_at` null,
    which is the stage's first design constraint: posting is a separate act,
    performed by `arpenteur send` and only on a draft somebody approved.
    """
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        with run_scope(conn, "draft", args) as run:
            stats = draft.run(
                settings, conn,
                desk_parts=mailcheck.desk_parts(),
                segments=_split(args.segment),
                cantons=_split(args.canton),
                limit=args.limit,
                confirmed=args.yes,
                dry_run=args.dry_run,
            )
            run.stats = stats
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    settings = common.load_settings()
    conn = db.open_db(_database_url(settings, args.fixture), settings.modules)
    try:
        with run_scope(conn, "profile" + (":fixture" if args.fixture else ""), args) as run:
            stats = profile.run(
                settings,
                conn,
                limit=args.limit,
                reclassify=args.reclassify,
                dry_run=args.dry_run,
            )
            run.stats = stats
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"classified {stats['classified']} companies, "
        f"{stats['sectors']} sectors, {stats['labels']} segment labels"
        + (" (dry run, nothing written)" if args.dry_run else "")
    )
    return 0


def cmd_people(args: argparse.Namespace) -> int:
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        with run_scope(conn, "people", args) as run:
            stats = people.run(
                settings,
                conn,
                limit=args.limit,
                reparse=args.reparse,
                dry_run=args.dry_run,
            )
            run.stats = stats
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"{stats['people']} officers across {stats['with_people']} companies "
        f"(of {stats['considered']} with publications)"
        + (f", {stats['pruned']} withdrawn by the improved parser"
           if stats.get("pruned") else "")
        + (" (dry run, nothing written)" if args.dry_run else "")
    )
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        with run_scope(conn, "resolve", args) as run:
            stats = resolve.run(settings, conn, dry_run=args.dry_run)
            run.stats = stats
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_enrich(args: argparse.Namespace) -> int:
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        with run_scope(conn, "enrich", args) as run:
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
            run.stats = stats
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"found {stats['found']}, ambiguous {stats['ambiguous']}, "
        f"not found {stats['not_found']}, failed {stats['failed']}; "
        f"{stats['emails']} new emails; "
        f"{stats['searches']} searches, {stats['fetches']} fetches, "
        f"{stats['turns']} turns, "
        f"{stats['input_tokens']}/{stats['output_tokens']} tokens in/out"
    )
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        with run_scope(conn, "match", args) as run:
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
            run.stats = stats
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"scored {stats['scored']} against brief {stats['brief']}: "
        f"{stats['high']} high, {stats['medium']} medium, {stats['low']} low, "
        f"{stats['excluded']} excluded, {stats['failed']} failed"
    )
    return 0


def cmd_reach(args: argparse.Namespace) -> int:
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        with run_scope(conn, "reach", args) as run:
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
            run.stats = stats
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"read {stats['pages']} pages: {stats['with_address']} companies with a "
        f"published address ({stats['new']} new of {stats['addresses']} found), "
        f"{stats['without']} without, {stats['blocked']} declined by robots.txt, "
        f"{stats['unreadable']} unreadable; "
        f"{stats['for_pattern']} left for the pattern tier"
    )
    return 0


def cmd_replace(args: argparse.Namespace) -> int:
    """Look again for a firm whose address bounced, or decide what was found.

    Without a flag it runs the search: the firm's mail domains, its own pages
    under robots.txt, one web search, and the desk address at a domain that
    takes mail. `--show` lists what is proposed without searching. `--accept`
    and `--refuse` decide one proposal, named by its address.
    """
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        firm = common._find_company(conn, args.company)
        company_id = int(firm["id"])
        decide = args.accept or args.refuse
        if decide:
            match = [p for p in scout_store.address_proposals(conn, company_id)
                     if p["address"].lower() == decide.strip().lower()]
            if not match:
                print(f"error: {decide} is not proposed for {firm['name']}", file=sys.stderr)
                return 1
            row = scout_store.decide_address(conn, int(match[0]["id"]),
                                             accept=bool(args.accept), by="cli")
            conn.commit()
            print(f"{row['address']}: {row['decision']}")
            return 0
        if not args.show:
            with run_scope(conn, "replace", args, close=False) as run:
                run.stats = replace.search(settings, conn, company=company_id)
        rows = scout_store.address_proposals(conn, company_id)
        if not rows:
            print(f"{firm['name']}: nothing proposed")
        for p in rows:
            print(f"  {p['address']:<40} {p['kind']:<8} {p['found_by']:<9} "
                  f"{p['decision'] or 'undecided':<10} {p['source_url'] or ''}")
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


def cmd_weights(args: argparse.Namespace) -> int:
    """The weight set: what the six parts of a score are worth to this campaign."""
    from ..modules.scout import scoring, store as scout

    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
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


def cmd_import(args: argparse.Namespace) -> int:
    """One company by UID, whatever the territory says.

    A client does not have to be inside the surveyed region and legal forms,
    and requiring one to be would let the survey dictate who may be a client.
    """
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        with run_scope(conn, "import", args) as run:
            found = source.import_company(settings, conn, args.uid)
            if args.client:
                client_id = db.promote_to_client(conn, found["company_id"])
                conn.commit()
                code = conn.execute(
                    "SELECT code FROM crm.client WHERE id = %s", (client_id,)
                ).fetchone()[0]
                print(f"client {code} created")
            run.stats = found
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    settings = common.load_settings()
    conn = db.open_db(_database_url(settings, args.fixture), settings.modules)

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


def add_parsers(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("source", help="fill the company table from a register")
    p.add_argument("provider", choices=["zefix"], nargs="?", default="zefix",
                   help="the register to read; zefix is the only one")
    territory_flags(p)
    p.add_argument("--no-resume", action="store_true", help="re-sweep completed prefixes")
    p.add_argument("--no-hydrate", action="store_true", help="skip the detail pass")
    p.add_argument("--limit", type=positive_int, help="cap the detail pass")
    p.add_argument(
        "--name",
        default="",
        help="comma separated name patterns, e.g. '*tourisme*,*bergbahn*'. The "
             "registry accepts a leading wildcard, so this finds a trade "
             "instead of sweeping a territory, across every legal form",
    )
    p.add_argument(
        "--name-file",
        default="",
        help="a file of name patterns, one per line, # for a comment",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="with --name: search and count, write nothing",
    )
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
    p.add_argument("--limit", type=positive_int,
                   help="stop after this many companies; the rest wait for the next run")
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
    p.add_argument("--limit", type=positive_int,
                   help="stop after this many companies; the rest wait for the next run")
    p.add_argument("--reclassify", action="store_true", help="include already classified")
    p.add_argument("--dry-run", action="store_true", help="print, write nothing")
    p.add_argument(
        "--fixture",
        action="store_true",
        help="read from the separate fixture database",
    )
    p.set_defaults(func=cmd_profile)


    p = sub.add_parser("people", help="officers from the register's SHAB notices")
    p.add_argument("--limit", type=positive_int,
                   help="stop after this many companies; the rest wait for the next run")
    p.add_argument(
        "--reparse",
        action="store_true",
        help="re-read companies that already have people (use after parser changes)",
    )
    p.add_argument("--dry-run", action="store_true", help="print, write nothing")
    p.set_defaults(func=cmd_people)


    p = sub.add_parser(
        "resolve",
        help="work out which officer rows are the same person (free, seconds)",
    )
    p.add_argument("--dry-run", action="store_true", help="say what it would do")
    p.set_defaults(func=cmd_resolve)


    p = sub.add_parser(
        "enrich",
        help="find and read company websites (costs money per company)",
    )
    p.add_argument("--uid", help="comma separated UIDs, e.g. CHE123...,CHE456...")
    p.add_argument("--canton", help="comma separated, e.g. VD,GE; narrows the shortlist")
    p.add_argument("--segment", help="only companies carrying this segment")
    p.add_argument("--sector", help="only companies in this line of business")
    p.add_argument("--limit", type=positive_int, help="required for runs over 200 companies")
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
    p.add_argument("--limit", type=positive_int,
                   help="score at most this many companies; a run that is not a dry run "
                        "and names no UIDs needs this, because each company is one model call")
    p.add_argument(
        "--rescore", action="store_true", help="re-score companies already scored"
    )
    p.add_argument("--dry-run", action="store_true", help="list the shortlist, spend nothing")
    p.set_defaults(func=cmd_match)


    p = sub.add_parser(
        "import", help="fetch one company by UID, inside the territory or not"
    )
    p.add_argument("uid", help="e.g. CHE-100.000.011 or CHE100000011")
    p.add_argument(
        "--client", action="store_true", help="also open a client record for it"
    )
    p.set_defaults(func=cmd_import)


    p = sub.add_parser("stats", help="what the survey holds")
    p.add_argument(
        "--fixture",
        action="store_true",
        help="read from the separate fixture database",
    )
    p.set_defaults(func=cmd_stats)


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
    p.add_argument("--limit", type=positive_int, help="required for runs over 200 companies")
    p.add_argument(
        "--refresh", action="store_true", help="re-read sites already looked at"
    )
    p.add_argument("--dry-run", action="store_true", help="list the sites, fetch nothing")
    p.set_defaults(func=cmd_reach)

    p = sub.add_parser(
        "replace",
        help="look again for a firm whose address bounced, and decide what was found",
    )
    p.add_argument("company", help="the firm's UID or name")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--show", action="store_true",
                       help="list what is proposed, without searching")
    group.add_argument("--accept", default="", metavar="ADDRESS",
                       help="store this proposed address as the firm's")
    group.add_argument("--refuse", default="", metavar="ADDRESS",
                       help="refuse this proposed address; no search offers it again")
    p.set_defaults(func=cmd_replace)


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
        "verify",
        help="check every address as far as DNS can settle it, and record it",
    )
    p.add_argument(
        "--refresh", action="store_true",
        help="re-check every domain, not only the ones with a stale verdict",
    )
    p.add_argument(
        "--stale-after", type=int, default=30, metavar="DAYS",
        help="how old a verdict may be before it is checked again (default 30)",
    )
    p.add_argument("--limit", type=positive_int, default=0, help="stop after N domains")
    p.add_argument("--dry-run", action="store_true", help="list, resolve nothing")
    p.set_defaults(func=cmd_verify)


    p = sub.add_parser(
        "sendlist",
        help="one address per firm, desk address preferred, for a staged send",
    )
    p.add_argument("--segment", default="", help="comma separated")
    p.add_argument("--canton", default="", help="comma separated")
    p.add_argument(
        "--status", default="deliverable",
        help="which verdicts to accept (default deliverable; add maybe to widen)",
    )
    p.add_argument("--out", default="", help="write a CSV here instead of printing")
    p.add_argument("--limit", type=positive_int, default=0, help="how many to print")
    p.set_defaults(func=cmd_sendlist)


    p = sub.add_parser(
        "draft", help="propose one message per firm, for review; sends nothing"
    )
    p.add_argument("--segment", default="", help="comma separated")
    p.add_argument("--canton", default="", help="comma separated")
    p.add_argument(
        "--limit", type=positive_int, default=draft.SAFE_BATCH,
        help=f"how many to draft (default {draft.SAFE_BATCH}, the safe batch)",
    )
    p.add_argument(
        "--yes", action="store_true",
        help="acknowledge the batch size; see LCD art. 3 al. 1 let. o",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="list what would be drafted, call no model",
    )
    p.set_defaults(func=cmd_draft)

