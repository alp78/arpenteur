"""The sync stage: keep the local copy current without re-asking the register.

A local copy of 183'361 companies goes stale the moment it is taken. The
obvious repair is to re-fetch everything, which is 183'361 requests against a
service that asks users to renounce mass querying, and which would have to be
repeated for ever.

It is also unnecessary. Nothing changes in the Swiss commercial register
without a notice in the Feuille officielle suisse du commerce, and Zefix
publishes those a day at a time, nationwide, at /api/v1/sogc/bydate/{date}.
One request returns every mutation of that day: 960 notices for the whole
country on 10.08.2026. So the local copy is a subscription, not a poll, and
staying current costs one request per elapsed day regardless of how many
companies are held.

Each notice arrives with a company short record attached, so an identity
change (renamed, moved, struck off) is applied from the feed itself. Only the
fields the short record does not carry, purpose and address, need the detail
call, and only for the companies actually named. A quiet week costs seven
requests.

Idempotency is the property the whole design turns on. Running the same range
twice inserts nothing, changes nothing and reports the same numbers, so a cron
job, a retry after a crash and an impatient operator all converge on the same
database. Days already consumed are recorded and skipped; companies are keyed
on UID and upserted; notices are keyed on (company, sogc_id) and cannot
duplicate. Nothing is ever deleted: a striking-off is a status, because a
prospect that has been struck off is a fact worth keeping rather than a row to
drop.
"""

from __future__ import annotations

import sqlite3
from datetime import date as Date
from datetime import timedelta
from typing import Any, Callable

from .. import db
from ..config import Settings
from ..sources.zefix import ZefixClient
from .source import _client

# The feed for a past day is closed and can be cached for ever. Today's is
# still filling: the cantonal registers file through the working day, so a
# copy taken this morning is not the day's news, and re-running in the evening
# must re-ask.
TODAY_MAX_AGE = 0.0

# How far back to look when there is no sync history and the caller named no
# start. Deliberately short. A first run should be cheap and obviously
# correct, and anyone wanting the back catalogue can ask for it by date.
DEFAULT_LOOKBACK_DAYS = 7

# One request per day of history, so a wide backfill is still modest, but an
# accidental --since 2010 is 6000 requests and should have to be asked for.
POLITE_DAY_LIMIT = 400


def _days(start: Date, end: Date) -> list[str]:
    span = (end - start).days
    return [(start + timedelta(days=offset)).isoformat() for offset in range(span + 1)]


def _start_date(conn: sqlite3.Connection, since: str | None, today: Date) -> Date:
    if since:
        return Date.fromisoformat(since)
    last = db.last_synced_day(conn)
    if last:
        # Resume from the day after the last one consumed.
        return Date.fromisoformat(last) + timedelta(days=1)
    return today - timedelta(days=DEFAULT_LOOKBACK_DAYS)


def run(
    settings: Settings,
    conn: sqlite3.Connection,
    *,
    since: str | None = None,
    until: str | None = None,
    today: Date | None = None,
    resume: bool = True,
    tracked_only: bool = True,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Consume the FOSC change feed for a range of days.

    tracked_only decides what a notice about an unknown company means. Left
    true, the survey stays the territory the sweep defined and other cantons
    are counted but not stored. Set false, every new registration nationwide
    is added, which is how the survey would grow itself.
    """
    today = today or Date.today()
    end = Date.fromisoformat(until) if until else today
    start = _start_date(conn, since, today)

    if start > end:
        log("Already up to date.")
        return {"days": 0, "notices": 0, "tracked": 0, "inserted": 0,
                "updated": 0, "unchanged": 0, "deletions": 0, "stale": 0}

    wanted = _days(start, end)
    if resume:
        done = db.synced_days(conn)
        # Today is never treated as done: it is still filling, and a run this
        # morning must not stop the evening's run from seeing the afternoon.
        wanted = [d for d in wanted if d not in done or d == today.isoformat()]

    if len(wanted) > POLITE_DAY_LIMIT:
        raise RuntimeError(
            f"{len(wanted)} days to sync, one request each. Narrow the range "
            f"with --since, or raise the limit deliberately if a backfill of "
            f"this size is really what is wanted."
        )

    stats: dict[str, Any] = {
        "days": 0, "notices": 0, "tracked": 0, "inserted": 0,
        "updated": 0, "unchanged": 0, "deletions": 0, "stale": 0,
    }
    if not wanted:
        log("Nothing to sync: every day in range is already consumed.")
        return stats

    if dry_run:
        log(f"{len(wanted)} days would be fetched, {wanted[0]} to {wanted[-1]}.")
        return stats

    with _client(settings) as http:
        zefix = ZefixClient(http, settings.zefix.base_url)
        for day in wanted:
            counts = _sync_day(
                conn, zefix, day,
                is_today=(day == today.isoformat()),
                tracked_only=tracked_only,
                stats=stats,
            )
            db.record_sync_day(conn, day, counts)
            conn.commit()
            stats["days"] += 1
            if counts["tracked"] or counts["inserted"]:
                log(
                    f"  {day}: {counts['notices']:>4} notices, "
                    f"{counts['tracked']:>3} ours"
                    + (f", {counts['inserted']} new" if counts["inserted"] else "")
                    + (f", {counts['deletions']} struck off" if counts["deletions"] else "")
                )

    stats["stale"] = conn.execute(
        "SELECT COUNT(*) FROM company WHERE stale_since IS NOT NULL"
    ).fetchone()[0]
    return stats


def _sync_day(
    conn: sqlite3.Connection,
    zefix: ZefixClient,
    day: str,
    *,
    is_today: bool,
    tracked_only: bool,
    stats: dict[str, Any],
) -> dict[str, int]:
    mutations = zefix.sogc_by_date(day, max_age=TODAY_MAX_AGE if is_today else None)
    counts = {"notices": len(mutations), "tracked": 0, "inserted": 0,
              "updated": 0, "deletions": 0}
    stats["notices"] += len(mutations)

    for mutation in mutations:
        uid = mutation.company.uid
        known = conn.execute("SELECT id FROM company WHERE uid = ?", (uid,)).fetchone()
        if known is None and tracked_only:
            # A notice about a canton or legal form this survey does not
            # cover. Counted, so a quiet day is distinguishable from a
            # filtered one, and otherwise left alone.
            continue

        counts["tracked"] += 1
        stats["tracked"] += 1
        company_id, verdict = db.apply_company(conn, mutation.company.as_row())
        counts[verdict] = counts.get(verdict, 0) + 1
        stats[verdict] += 1

        # The notice itself, keyed on (company, sogc_id): re-reading the same
        # day cannot duplicate it, which is most of what idempotent means here.
        db.save_publications(conn, company_id, [mutation.as_publication()])

        if mutation.is_deletion:
            counts["deletions"] += 1
            stats["deletions"] += 1

        # Flagged for a detail call. The short record settles name, seat,
        # legal form and status; purpose and address it does not carry, and a
        # notice is the only evidence they may have moved.
        db.mark_stale(conn, company_id, mutation.sogc_date)

    return counts
