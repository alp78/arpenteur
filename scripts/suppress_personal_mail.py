"""Suppress addresses at consumer mail providers.

    uv run python scripts/suppress_personal_mail.py --dry-run
    uv run python scripts/suppress_personal_mail.py

A one-person régie that prints `arben72@msn.com` on its own website really has
published it as its business contact, so the address is not a mistake and its
`published` provenance is honest. What it is not is a role address: it reaches
a named individual's private mailbox rather than a desk, and under the nLPD
that is a different kind of data to put in a cold outreach list.

Suppressed rather than deleted, and the difference is the point. The survey
still records that this is the only address the firm publishes — which is a
finding about that firm, not an absence — while every reader that honours
`NOT suppressed` leaves it out by default.

`reach.py` now suppresses these on the way in. This exists for the rows written
before it did, and to re-run after a sweep that used older code.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arpenteur import db  # noqa: E402
from arpenteur.config import load_settings  # noqa: E402
from arpenteur.sources import mailcheck  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = db.open_db(load_settings().database_url)
    rows = conn.execute(
        """SELECT k.id, k.value, c.name
             FROM core.contact k JOIN core.company c ON c.id = k.company_id
            WHERE k.kind = 'email' AND NOT k.suppressed
              AND split_part(k.value, '@', 2) = ANY(%s)
            ORDER BY c.name""",
        (sorted(mailcheck.FREE_MAIL),),
    ).fetchall()

    # A firm whose only address is a private one is the case worth seeing:
    # suppressing it takes the firm out of an export entirely, which is a
    # different outcome from suppressing one address among several.
    alone = 0
    for row in rows:
        others = conn.execute(
            """SELECT count(*) AS n FROM core.contact
                WHERE company_id = (SELECT company_id FROM core.contact WHERE id = %s)
                  AND kind = 'email' AND NOT suppressed AND id <> %s""",
            (row["id"], row["id"]),
        ).fetchone()["n"]
        if not others:
            alone += 1
        if args.dry_run:
            mark = "  (only address)" if not others else ""
            print(f"  {row['name'][:38]:40} {row['value']}{mark}")

    print(f"\n{len(rows)} personal address(es); {alone} firm(s) have no other")
    if args.dry_run:
        print("nothing written")
        conn.close()
        return 0

    conn.execute(
        """UPDATE core.contact SET suppressed = TRUE
            WHERE kind = 'email' AND NOT suppressed
              AND split_part(value, '@', 2) = ANY(%s)""",
        (sorted(mailcheck.FREE_MAIL),),
    )
    conn.commit()
    print(f"suppressed {len(rows)}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
