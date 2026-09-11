"""Where the Limen address survey stands, in one page.

Written as a script rather than a query typed at a prompt because the numbers
that matter are not the ones a single count gives. What decides the next move
is the split between work that is free and work that is paid: a company whose
website is known can be read for the cost of a fetch, and a company whose
website is not known is the only kind worth spending a model call on.

    uv run python scripts/limen_status.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arpenteur import db  # noqa: E402
from arpenteur.config import load_settings  # noqa: E402

ACTIVE = "c.status = 'ACTIVE'"
NO_MAIL = """NOT EXISTS (SELECT 1 FROM core.contact k
             WHERE k.company_id = c.id AND k.kind = 'email' AND NOT k.suppressed)"""
HAS_MAIL = NO_MAIL.replace("NOT EXISTS", "EXISTS")
HAS_SITE = """EXISTS (SELECT 1 FROM intel.site s
              WHERE s.company_id = c.id AND s.url IS NOT NULL AND s.url <> '')"""
NO_SITE = HAS_SITE.replace("EXISTS", "NOT EXISTS")
BANDS = ("regie-core", "regie-letting")


def main() -> int:
    conn = db.open_db(load_settings().database_url)

    def n(where: str, band: str | None = None) -> int:
        join = ("JOIN intel.segment g ON g.company_id = c.id AND g.segment = %s"
                if band else "")
        sql = f"SELECT count(*) AS n FROM core.company c {join} WHERE {where}"
        return conn.execute(sql, (band,) if band else ()).fetchone()["n"]

    def contacts(what: str) -> int:
        return conn.execute(
            f"SELECT {what} AS n FROM core.contact"
            " WHERE kind = %s AND NOT suppressed", ("email",)
        ).fetchone()["n"]

    print("addresses held")
    print(f"  email contacts            {contacts('count(*)'):>8,}")
    print(f"  firms reached             {contacts('count(DISTINCT company_id)'):>8,}")

    for band in BANDS:
        total = n(ACTIVE, band)
        have = n(f"{ACTIVE} AND {HAS_MAIL}", band)
        print(f"\n{band}")
        print(f"  total                     {total:>8,}")
        print(f"  with an address           {have:>8,}   ({100 * have // max(total, 1)}%)")
        # The two queues, and the whole point of this report: one is free to
        # work through, the other costs a model call each.
        free = n(f"{ACTIVE} AND {NO_MAIL} AND {HAS_SITE}", band)
        paid = n(f"{ACTIVE} AND {NO_MAIL} AND {NO_SITE}", band)
        print(f"  no address, site known    {free:>8,}   <- free: tier 1 reads it")
        print(f"  no address, no site       {paid:>8,}   <- needs the paid tier")

    print("\nany band, no address but a site on file: "
          f"{n(f'{ACTIVE} AND {NO_MAIL} AND {HAS_SITE}'):,}")
    print("\nhunt outcomes")
    for row in conn.execute("SELECT outcome, count(*) AS n FROM intel.address_hunt"
                            " GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {row['outcome']:12} {row['n']:>8,}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
