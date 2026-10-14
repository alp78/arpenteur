"""The catalogue and its prices.

Commands of the sales module, registered when it is enabled. Moved out of
`cli.py` verbatim; what they share is in `common.py`.
"""

from __future__ import annotations

import argparse
import sys
from .. import db
from . import common


def cmd_rates(args: argparse.Namespace) -> int:
    """The catalogue: load it from arpenteur.toml, or read back what is held."""
    from ..modules.sales import store as sales

    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
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


def add_parsers(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "rates", help="the service catalogue and its prices"
    )
    p.add_argument(
        "action", choices=("load", "show"),
        help="load the [[rates.service]] entries from arpenteur.toml into a new "
             "rate card, or print the card in force",
    )
    p.set_defaults(func=cmd_rates)

