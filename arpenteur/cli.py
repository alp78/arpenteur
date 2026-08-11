"""Command line: one subcommand per pipeline stage.

argparse rather than a framework. The surface is small, the dependency list
stays short, and `arpenteur --help` prints the whole pipeline in one screen.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from . import db
from .config import load_settings
from .stages import draft, enrich, people, profile, reach, source


def _split(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


def cmd_init_db(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = db.connect(settings.db_path)
    version = db.migrate(conn)
    conn.close()
    print(f"{settings.db_path} ready, schema version {version}")
    return 0


def cmd_source(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = db.open_db(settings.db_path)
    run_id = db.start_run(conn, f"source:{args.provider}", vars(args))
    try:
        stats = source.run_zefix(
            settings,
            conn,
            cantons=_split(args.canton),
            legal_forms=_split(args.legal_form),
            resume=not args.no_resume,
            hydrate=not args.no_hydrate,
            limit=args.limit,
            bulk=args.bulk,
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
    settings = load_settings()
    conn = db.open_db(settings.db_path)
    run_id = db.start_run(conn, "hydrate", vars(args))
    try:
        count = source.hydrate_pending(
            settings,
            conn,
            cantons=_split(args.canton),
            legal_forms=_split(args.legal_form),
            limit=args.limit,
            bulk=args.bulk,
        )
    except Exception as exc:
        db.finish_run(conn, run_id, error=str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 1
    db.finish_run(conn, run_id, {"hydrated": count})
    print(f"hydrated {count} companies")
    conn.close()
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = db.open_db(settings.db_path)
    run_id = db.start_run(conn, "profile", vars(args))
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
    conn = db.open_db(settings.db_path)
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
        + (" (dry run, nothing written)" if args.dry_run else "")
    )
    conn.close()
    return 0


def cmd_enrich(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = db.open_db(settings.db_path)
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
        f"{stats['searches']} searches, {stats['fetches']} fetches"
    )
    conn.close()
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = db.open_db(settings.db_path)

    total = conn.execute("SELECT COUNT(*) FROM company").fetchone()[0]
    hydrated = conn.execute(
        "SELECT COUNT(*) FROM company WHERE hydrated_at IS NOT NULL"
    ).fetchone()[0]
    print(f"companies: {total} ({hydrated} hydrated)")

    rows = conn.execute(
        """
        SELECT canton, COUNT(*) AS n FROM company
        WHERE canton IS NOT NULL GROUP BY canton ORDER BY n DESC
        """
    ).fetchall()
    if rows:
        print("\nby canton")
        for row in rows:
            print(f"  {row['canton'] or '?':4} {row['n']:>7}")

    rows = conn.execute(
        """
        SELECT COALESCE(legal_form_label, legal_form_uid, '?') AS form,
               COUNT(*) AS n
        FROM company GROUP BY form ORDER BY n DESC LIMIT 10
        """
    ).fetchall()
    if rows:
        print("\nby legal form")
        for row in rows:
            print(f"  {row['form'][:28]:30} {row['n']:>7}")

    publications = conn.execute("SELECT COUNT(*) FROM publication").fetchone()[0]
    if publications:
        officers = conn.execute("SELECT COUNT(*) FROM person").fetchone()[0]
        companies_with = conn.execute(
            "SELECT COUNT(DISTINCT company_id) FROM person"
        ).fetchone()[0]
        print(f"\npublications: {publications}")
        print(f"officers: {officers} across {companies_with} companies")
        rows = conn.execute(
            "SELECT role, COUNT(*) AS n FROM person GROUP BY role ORDER BY n DESC LIMIT 8"
        ).fetchall()
        for row in rows:
            print(f"  {row['role'] or '?':20} {row['n']:>7}")

    sites = conn.execute("SELECT COUNT(*) FROM site").fetchone()[0]
    if sites:
        print("\nweb research")
        for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM site GROUP BY status ORDER BY n DESC"
        ):
            print(f"  {row['status']:20} {row['n']:>7}")
        spend = conn.execute(
            "SELECT COALESCE(SUM(searches), 0) s, COALESCE(SUM(fetches), 0) f FROM site"
        ).fetchone()
        print(f"  {'searches / fetches':20} {spend['s']:>7} / {spend['f']}")
        emails = conn.execute(
            "SELECT COUNT(*) FROM contact WHERE kind = 'email' AND suppressed = 0"
        ).fetchone()[0]
        print(f"  {'usable emails':20} {emails:>7}")

    rows = conn.execute(
        "SELECT segment, COUNT(*) AS n FROM segment GROUP BY segment ORDER BY n DESC"
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
        FROM run ORDER BY id DESC LIMIT 5
        """
    ).fetchall()
    if rows:
        print("\nrecent runs")
        for row in rows:
            state = "error" if row["error"] else ("done" if row["finished_at"] else "running")
            print(f"  {row['started_at']}  {row['stage']:16} {state}")

    conn.close()
    return 0


def cmd_stub(args: argparse.Namespace) -> int:
    {"people": people, "reach": reach, "draft": draft}[args.command].run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arpenteur",
        description="Survey the Swiss SME market from public sources.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create or migrate the database").set_defaults(
        func=cmd_init_db
    )

    p = sub.add_parser("source", help="fill the company table from a register")
    p.add_argument("provider", choices=["zefix"], nargs="?", default="zefix")
    p.add_argument("--canton", help="comma separated, e.g. VD,GE (default: config)")
    p.add_argument("--legal-form", help="comma separated, e.g. sarl,sa (default: config)")
    p.add_argument("--no-resume", action="store_true", help="re-sweep completed prefixes")
    p.add_argument("--no-hydrate", action="store_true", help="skip the detail pass")
    p.add_argument("--limit", type=int, help="cap the detail pass")
    p.add_argument(
        "--bulk",
        action="store_true",
        help=f"accept a detail pass over {source.POLITE_HYDRATION_LIMIT}+ companies",
    )
    p.set_defaults(func=cmd_source)

    p = sub.add_parser("hydrate", help="fetch detail for companies missing it")
    p.add_argument("--canton", help="comma separated, e.g. GE,VD")
    p.add_argument("--legal-form", help="comma separated uids, e.g. 0107,0106")
    p.add_argument("--limit", type=int)
    p.add_argument(
        "--bulk",
        action="store_true",
        help=f"accept a run over {source.POLITE_HYDRATION_LIMIT} companies "
        "(Zefix asks users to avoid regular mass querying)",
    )
    p.set_defaults(func=cmd_hydrate)

    p = sub.add_parser("profile", help="sort companies into segments")
    p.add_argument("--limit", type=int)
    p.add_argument("--reclassify", action="store_true", help="include already classified")
    p.add_argument("--dry-run", action="store_true", help="print, write nothing")
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

    sub.add_parser("stats", help="what the survey holds").set_defaults(func=cmd_stats)

    for name, helptext in (
        ("reach", "resolve a point of contact (not built yet)"),
        ("draft", "propose messages for review (not built yet)"),
    ):
        sub.add_parser(name, help=helptext).set_defaults(func=cmd_stub)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Any = args.func
    return int(handler(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
