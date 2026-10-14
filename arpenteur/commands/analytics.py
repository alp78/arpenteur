"""The reports.

Commands of the analytics module, registered when it is enabled. Moved out of
`cli.py` verbatim; what they share is in `common.py`.
"""

from __future__ import annotations

import argparse
import sys
from .. import db
from . import common
from .common import (
    positive_int,
)


def cmd_report(args: argparse.Namespace) -> int:
    """Read a report, or bring the materialised ones up to date."""
    from ..modules.analytics import store as analytics

    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
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


def add_parsers(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "report", help="the analytics views: the funnel, the spend, the pipeline"
    )
    p.add_argument(
        "action", choices=("list", "show", "refresh"),
        help="list what this deployment has, print one, or bring the "
             "materialised views up to date",
    )
    p.add_argument("name", nargs="?", help="which report, for show")
    p.add_argument("--limit", type=positive_int, default=200, help="rows to print")
    p.add_argument("--csv", action="store_true", help="write CSV to stdout")
    p.set_defaults(func=cmd_report)

