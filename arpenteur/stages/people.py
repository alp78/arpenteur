"""The people stage: who decides, from the register's own notices.

Zefix has no officer field, so the authority here is `sogcPub`: the Feuille
officielle suisse du commerce notice filed on every mutation, which names
associés, gérants and their signature rights in plain registrar's French.
Those notices are already stored by the hydrate pass, so this stage costs no
requests at all: it reads the `publication` table and writes `person` rows.

Reading rather than fetching is the point. The parser will improve, and
reparsing stored strings is seconds where re-fetching them is a day of polite
traffic.

The cantonal excerpt (company.cantonal_excerpt_url) remains the more complete
source: it lists every current officer, where a notice lists only what changed.
It is also per-canton web scraping, so it stays a later phase. What this stage
gets for free covers roughly half of companies.
"""

from __future__ import annotations

from typing import Any, Callable

from .. import db
from ..config import Settings
from ..kernel.db import Connection
from ..sources import shab


def run(
    settings: Settings,
    conn: Connection,
    *,
    limit: int | None = None,
    reparse: bool = False,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Parse stored notices into people.

    `reparse` re-reads companies that already have person rows, which is what
    to use after improving the parser. Without it only companies with notices
    and no people yet are considered, so a re-run is cheap.
    """
    where = "WHERE EXISTS (SELECT 1 FROM core.publication p WHERE p.company_id = c.id)"
    if not reparse:
        where += " AND c.id NOT IN (SELECT DISTINCT company_id FROM core.person)"
    sql = f"SELECT c.id, c.uid, c.name FROM core.company c {where} ORDER BY c.id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    companies = conn.execute(sql).fetchall()

    stats: dict[str, Any] = {
        "considered": len(companies),
        "with_people": 0,
        "people": 0,
        "past": 0,
        "pruned": 0,
    }
    if not companies:
        log("No stored publications to parse. Run hydrate first.")
        return stats

    for index, company in enumerate(companies, 1):
        publications = conn.execute(
            """
            SELECT sogc_id, sogc_date, message, source_url
            FROM core.publication WHERE company_id = %s ORDER BY sogc_date DESC
            """,
            (company["id"],),
        ).fetchall()
        if not publications:
            continue

        officers = shab.from_publications(
            [dict(row) for row in publications], include_past=True
        )
        if not officers:
            # Most notices are address or capital changes naming nobody. Not
            # an error, and not worth logging one line per company. On a
            # reparse it is still the whole answer: what an earlier parser
            # read into these notices by mistake goes with it.
            if reparse and not dry_run:
                stats["pruned"] += db.prune_people(conn, company["id"], [])
            continue

        newest = publications[0]["sogc_date"]
        source = publications[0]["source_url"]
        people = [
            {
                "full_name": officer.full_name,
                "role": officer.role,
                "origin": officer.origin,
                "domicile": officer.domicile,
                "signature": officer.signature,
                "first_seen_date": officer.first_seen,
                "last_seen_date": officer.last_seen or newest,
                "left_date": officer.left_on,
                "source_url": source,
                "method": "shab",
            }
            for officer in officers
        ]

        if dry_run:
            log(f"  {company['name'][:44]}")
            for officer in officers:
                log(
                    f"      {officer.role or '?':16} {officer.full_name:32}"
                    f" {officer.signature or ''}"
                )
        else:
            db.save_people(conn, company["id"], people, authoritative=reparse)
            if reparse:
                # A reparse states the whole answer for this company, so what
                # the improved parser no longer produces goes. Without this a
                # fixed regex leaves its mistakes in the table for ever.
                stats["pruned"] += db.prune_people(
                    conn, company["id"],
                    [(p["full_name"], p["role"]) for p in people],
                )

        stats["with_people"] += 1
        stats["people"] += sum(1 for p in people if not p["left_date"])
        stats["past"] += sum(1 for p in people if p["left_date"])

        if not dry_run and index % 200 == 0:
            conn.commit()
            log(f"  {index}/{len(companies)}")

    if not dry_run:
        conn.commit()
    return stats
