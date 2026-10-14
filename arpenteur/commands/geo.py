"""The map: geocoding and the cantonal boundaries.

Commands of the geo module, registered when it is enabled. Moved out of
`cli.py` verbatim; what they share is in `common.py`.
"""

from __future__ import annotations

import argparse
import sys
from .. import db
from ..stages import (
    geocode,
    boundaries,
)
from . import common
from .common import (
    _split,
    positive_int,
    run_scope,
)


def cmd_boundaries(args: argparse.Namespace) -> int:
    """Fetch the cantonal outlines. Free, one request each, one-off.

    Not `cmd_regions`, which already exists and prints the territories named
    in arpenteur.toml. Two different things, and the first version of this
    shadowed the other one silently: a function defined later in a module
    simply replaces the earlier one, and argparse only complained because the
    subcommand name collided too.
    """
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        with run_scope(conn, "boundaries", args) as run:
            stats = boundaries.run(
                settings, conn,
                cantons=_split(args.canton) or None,
                refresh=args.refresh,
                dry_run=args.dry_run,
            )
            run.stats = stats
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"{stats['stored']} boundary(ies) stored, {stats['held']} already held, "
        f"{stats['failed']} failed"
    )
    return 0


def cmd_geocode(args: argparse.Namespace) -> int:
    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        with run_scope(conn, "geocode", args) as run:
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
            run.stats = stats
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    # Reported by precision rather than as one total. "412 placed" says nothing
    # about whether the map is worth looking at; "380 on a door, 32 on a town"
    # says exactly how much of it to believe.
    print(
        f"placed {stats['placed']} of {stats['considered']}: "
        f"{stats['address']} on an address, {stats['street']} on a street, "
        f"{stats['postcode']} on a town only; {stats['unplaced']} unplaced, "
        f"{stats['no_domicile']} with no domicile on the register"
    )
    return 0


def add_parsers(sub: argparse._SubParsersAction) -> None:
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
    p.add_argument("--limit", type=positive_int, help="required for runs over 500 companies")
    p.add_argument("--dry-run", action="store_true", help="list the addresses, ask nothing")
    p.set_defaults(func=cmd_geocode)


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

