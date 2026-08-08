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
from .stages import draft, people, profile, reach, source


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
        count = source.hydrate_pending(settings, conn, limit=args.limit)
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
    p.set_defaults(func=cmd_source)

    p = sub.add_parser("hydrate", help="fetch detail for companies missing it")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_hydrate)

    p = sub.add_parser("profile", help="sort companies into segments")
    p.add_argument("--limit", type=int)
    p.add_argument("--reclassify", action="store_true", help="include already classified")
    p.add_argument("--dry-run", action="store_true", help="print, write nothing")
    p.set_defaults(func=cmd_profile)

    sub.add_parser("stats", help="what the survey holds").set_defaults(func=cmd_stats)

    for name, helptext in (
        ("people", "decision makers from public sources (not built yet)"),
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
