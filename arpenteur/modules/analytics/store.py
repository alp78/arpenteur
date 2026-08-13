"""Reading the reports, and bringing them up to date.

Thin on purpose. The queries are in the migration, where they are versioned
with the schema they read and where a reader can see them all at once; this is
the accessor and the refresh, and it holds no SQL that answers a question.

Every function here tolerates a view being absent. A deployment running the
survey alone has no `analytics.pipeline`, and a Reports page that crashes
because the sales module is not installed would be a worse answer than a page
that quietly shows the sections it can.
"""

from __future__ import annotations

from typing import Any

import psycopg

from ...kernel.db import Connection, Row

# The reports, in the order the page shows them, with the module each needs.
# Named here rather than discovered so a missing view is a visible gap rather
# than a section that silently disappeared.
REPORTS: tuple[tuple[str, str, str], ...] = (
    ("coverage", "How far the survey has been taken", "scout"),
    ("funnel", "The funnel, and how long each step took", "scout"),
    ("band_profile", "Score bands, and how reachable each one is", "scout"),
    ("survey_by_canton", "The survey by canton", "scout"),
    ("survey_by_segment", "The survey by segment", "scout"),
    ("selection_outcomes", "What was picked, and what came of it", "scout"),
    ("gone_quiet", "On the board and not moving", "scout"),
    ("spend_by_stage", "What each stage has cost", "scout"),
    ("mail_review", "Correspondence read, matched and filed", "clients"),
    ("client_coverage", "Each engagement's file", "clients"),
    ("pipeline", "What is out, and what it is worth", "sales"),
    ("revenue", "Billed and received, by month", "sales"),
    ("aged_debt", "Issued and not settled", "sales"),
    ("effort", "Hours logged against hours billed", "sales"),
)


def read(conn: Connection, name: str, *, limit: int = 200) -> list[Row]:
    """One report. An empty list when the view is not there.

    The rollback matters: a failed statement leaves the transaction unusable,
    and the page reads a dozen reports in a row. Without it the first missing
    view would take every report after it down with it, which would look like
    a broken page rather than an absent module.
    """
    try:
        return conn.execute(
            f"SELECT * FROM analytics.{_safe(name)} LIMIT %s", (limit,)
        ).fetchall()
    except psycopg.Error:
        conn.rollback()
        return []


def available(conn: Connection) -> set[str]:
    """Which reports this deployment actually has."""
    rows = conn.execute(
        "SELECT table_name AS name FROM information_schema.tables"
        " WHERE table_schema = 'analytics'"
        " UNION SELECT matviewname FROM pg_matviews WHERE schemaname = 'analytics'"
    ).fetchall()
    return {row["name"] for row in rows}


def refresh(conn: Connection) -> list[str]:
    """Bring the materialised views up to date. Returns what was refreshed.

    Committed first and run outside a transaction, because REFRESH
    MATERIALIZED VIEW CONCURRENTLY cannot run inside one. Concurrently is what
    lets a page go on reading the old rows while the new ones are built, which
    on the survey table is the difference between a refresh nobody notices and
    one that blocks the console for a minute.
    """
    names = [
        row["matviewname"]
        for row in conn.execute(
            "SELECT matviewname FROM pg_matviews WHERE schemaname = 'analytics'"
            " ORDER BY matviewname"
        )
    ]
    if not names:
        return []
    conn.commit()
    was = conn.autocommit
    conn.autocommit = True
    try:
        conn.execute("CALL analytics.refresh()")
    finally:
        conn.autocommit = was
    return names


def freshness(conn: Connection) -> dict[str, Any]:
    """How stale the materialised half is.

    Shown on the page because a number with no date is a number somebody will
    quote in a meeting. Postgres does not record when a matview was last
    refreshed, so this reports what can honestly be known: how many there are
    and whether any has never been populated.
    """
    rows = conn.execute(
        "SELECT matviewname AS name, ispopulated AS populated"
        "  FROM pg_matviews WHERE schemaname = 'analytics'"
    ).fetchall()
    return {
        "views": len(rows),
        "unpopulated": [row["name"] for row in rows if not row["populated"]],
    }


def _safe(name: str) -> str:
    """A report name that came from our own list, checked anyway.

    These names are constants in this file, so nothing user-supplied reaches
    the query. The check is here because that sentence has been true of every
    injection anybody ever shipped, right up until a caller passed a query
    parameter through.
    """
    if not name.replace("_", "").isalnum():
        raise ValueError(f"not a report name: {name!r}")
    return name
