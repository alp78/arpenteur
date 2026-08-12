"""SQLite storage: connection, migrations, and the few write helpers.

No ORM. The schema is small, the queries are the interesting part, and a
migration is a numbered .sql file applied in order against PRAGMA
user_version. That keeps the whole storage story readable in one sitting.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def now() -> str:
    """UTC, ISO-8601, seconds. Every timestamp in the database uses this."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # WAL so a long sweep writing rows does not block a `stats` read in
    # another terminal; foreign_keys because the schema relies on cascades.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def latest_version() -> int:
    """The highest migration number on disk.

    Lets a caller assert "fully migrated" without naming a version that every
    later migration would have to update.
    """
    files = migration_files()
    return int(files[-1].name[:4]) if files else 0


def migrate(conn: sqlite3.Connection) -> int:
    """Apply every migration newer than the database's user_version.

    Returns the version arrived at. Safe to call on every command: a database
    already at the latest version does nothing.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for path in migration_files():
        number = int(path.name[:4])
        if number <= version:
            continue
        with conn:
            conn.executescript(path.read_text(encoding="utf-8"))
            # executescript commits, so the version bump is its own statement.
            conn.execute(f"PRAGMA user_version = {number}")
        version = number
    backfill_content_hashes(conn)
    backfill_site_hashes(conn)
    return version


def backfill_site_hashes(conn: sqlite3.Connection) -> int:
    """Digest any web research that predates the column.

    The same trap as company.content_hash, and it caught me twice. A null
    digest compares equal to the null on an old fit row, so every score taken
    before this column existed would report itself current for ever, which is
    precisely the staleness the column was added to detect.
    """
    if not conn.execute(
        "SELECT 1 FROM pragma_table_info('site') WHERE name = 'content_hash'"
    ).fetchone():
        return 0
    rows = conn.execute("SELECT * FROM site WHERE content_hash IS NULL").fetchall()
    if not rows:
        return 0
    conn.executemany(
        "UPDATE site SET content_hash = ? WHERE company_id = ?",
        [(site_digest(row), row["company_id"]) for row in rows],
    )
    conn.commit()
    return len(rows)


def backfill_content_hashes(conn: sqlite3.Connection, batch: int = 5000) -> int:
    """Give every company a digest, so the first sync after a migration is honest.

    Without this the column arrives null on 183'361 existing rows, every one of
    them compares unequal on first touch, and a week of the change feed reports
    a thousand amendments where the register made none. The digest is only
    meaningful against a previous digest, so the previous one has to exist.

    Idempotent and cheap: it looks for nulls, and on a database that has none
    it is a single indexed count.
    """
    if not conn.execute(
        "SELECT 1 FROM pragma_table_info('company') WHERE name = 'content_hash'"
    ).fetchone():
        return 0
    filled = 0
    while True:
        rows = conn.execute(
            "SELECT * FROM company WHERE content_hash IS NULL LIMIT ?", (batch,)
        ).fetchall()
        if not rows:
            break
        conn.executemany(
            "UPDATE company SET content_hash = ? WHERE id = ?",
            [
                (company_content_hash({key: row[key] for key in HASHED_FIELDS}), row["id"])
                for row in rows
            ],
        )
        conn.commit()
        filled += len(rows)
    return filled


def open_db(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    migrate(conn)
    return conn


# --- writes -----------------------------------------------------------------


# The registry fields a change is measured over. Provenance and timestamps
# are excluded on purpose: fetched_at moves on every read, so including it
# would make every row look changed and the digest would measure nothing.
HASHED_FIELDS = (
    "name", "legal_form_uid", "legal_form_label", "legal_seat", "legal_seat_id",
    "canton", "status", "purpose", "address_json", "capital", "capital_currency",
)


def company_content_hash(payload: dict[str, Any]) -> str:
    """A digest of what the register says, ignoring when we asked.

    Two reads of an unchanged company must produce the same digest, and any
    real amendment must produce a different one. That is the whole test.
    """
    material = json.dumps(
        [payload.get(field) for field in HASHED_FIELDS],
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# The site fields a fit score is actually computed from. Provenance and spend
# are excluded: re-running enrich moves fetched_at and the search counts
# without changing a word the judge read.
SITE_JUDGED_FIELDS = (
    "status", "url", "confidence", "summary", "offerings", "signals",
    "partnerships", "tech_stack", "size_hint",
)


def site_digest(site: dict[str, Any] | sqlite3.Row | None) -> str | None:
    """What the judge saw, as a digest. None when there was no research.

    Compared against the digest stored on a fit row to decide whether the
    score still stands. `restricted` is not included because the judge is
    never shown it, and including it would expire scores on a change that
    could not have affected them.
    """
    if site is None:
        return None
    keys = set(site.keys())
    values = [
        site[field] if field in keys else None for field in SITE_JUDGED_FIELDS
    ]
    material = json.dumps(values, ensure_ascii=False, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def upsert_company(conn: sqlite3.Connection, row: dict[str, Any]) -> int:
    """Insert or update one company, keyed on UID.

    first_seen is written once and never overwritten: excluded.first_seen
    would reset it on every sweep, which would destroy the only signal
    telling a new registration from a re-sweep. Hydration fields are only
    overwritten when the incoming row actually carries them, so a cheap
    search result cannot blank out a detail already fetched.
    """
    stamp = now()
    payload = {
        "uid": row["uid"],
        "chid": row.get("chid"),
        "ehraid": row.get("ehraid"),
        "name": row["name"],
        "legal_form_uid": row.get("legal_form_uid"),
        "legal_form_label": row.get("legal_form_label"),
        "legal_seat": row.get("legal_seat"),
        "legal_seat_id": row.get("legal_seat_id"),
        "canton": row.get("canton"),
        "status": row.get("status"),
        "purpose": row.get("purpose"),
        "address_json": json.dumps(row["address"], ensure_ascii=False)
        if row.get("address")
        else None,
        "capital": row.get("capital"),
        "capital_currency": row.get("capital_currency"),
        "cantonal_excerpt_url": row.get("cantonal_excerpt_url"),
        "zefix_detail_url": row.get("zefix_detail_url"),
        "source_url": row.get("source_url"),
        "method": row.get("method"),
        "fetched_at": stamp,
        "first_seen": stamp,
        "hydrated_at": stamp if row.get("hydrated") else None,
    }
    # A translated field that arrives as {de, fr, it, en} instead of a string
    # otherwise surfaces as "Error binding parameter 16", which names no
    # column and sends you looking in the wrong place. Fail with the name.
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            raise TypeError(
                f"company.{key} is {type(value).__name__}, not a scalar: "
                f"{value!r:.120}. A Zefix field is probably translated; "
                f"pass it through _text() in sources/zefix.py."
            )
    conn.execute(
        """
        INSERT INTO company (
            uid, chid, ehraid, name, legal_form_uid, legal_form_label,
            legal_seat, legal_seat_id, canton, status, purpose, address_json,
            capital, capital_currency, cantonal_excerpt_url, zefix_detail_url,
            source_url, method, fetched_at, first_seen, hydrated_at
        ) VALUES (
            :uid, :chid, :ehraid, :name, :legal_form_uid, :legal_form_label,
            :legal_seat, :legal_seat_id, :canton, :status, :purpose,
            :address_json, :capital, :capital_currency, :cantonal_excerpt_url,
            :zefix_detail_url, :source_url, :method, :fetched_at, :first_seen,
            :hydrated_at
        )
        ON CONFLICT (uid) DO UPDATE SET
            chid = COALESCE(excluded.chid, company.chid),
            ehraid = COALESCE(excluded.ehraid, company.ehraid),
            name = excluded.name,
            legal_form_uid = COALESCE(excluded.legal_form_uid, company.legal_form_uid),
            legal_form_label = COALESCE(excluded.legal_form_label, company.legal_form_label),
            legal_seat = COALESCE(excluded.legal_seat, company.legal_seat),
            legal_seat_id = COALESCE(excluded.legal_seat_id, company.legal_seat_id),
            canton = COALESCE(excluded.canton, company.canton),
            status = COALESCE(excluded.status, company.status),
            purpose = COALESCE(excluded.purpose, company.purpose),
            address_json = COALESCE(excluded.address_json, company.address_json),
            capital = COALESCE(excluded.capital, company.capital),
            capital_currency = COALESCE(excluded.capital_currency, company.capital_currency),
            cantonal_excerpt_url = COALESCE(excluded.cantonal_excerpt_url, company.cantonal_excerpt_url),
            zefix_detail_url = COALESCE(excluded.zefix_detail_url, company.zefix_detail_url),
            source_url = COALESCE(excluded.source_url, company.source_url),
            method = COALESCE(excluded.method, company.method),
            fetched_at = excluded.fetched_at,
            hydrated_at = COALESCE(excluded.hydrated_at, company.hydrated_at)
        """,
        payload,
    )
    # Hashed from the stored row rather than the incoming payload. They are
    # not the same thing: the COALESCE clauses above mean a cheap search
    # result leaves an already-hydrated purpose in place, so hashing what
    # arrived would report a change on a row that did not move.
    stored = conn.execute("SELECT * FROM company WHERE uid = ?", (row["uid"],)).fetchone()
    conn.execute(
        "UPDATE company SET content_hash = ? WHERE id = ?",
        (company_content_hash({key: stored[key] for key in HASHED_FIELDS}), stored["id"]),
    )
    return int(stored["id"])


def synced_days(conn: sqlite3.Connection) -> set[str]:
    """The FOSC days already consumed. What makes a re-run free."""
    return {row[0] for row in conn.execute("SELECT sogc_date FROM sync_day")}


def last_synced_day(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(sogc_date) FROM sync_day").fetchone()
    return row[0] if row and row[0] else None


def record_sync_day(conn: sqlite3.Connection, date: str, counts: dict[str, int]) -> None:
    """Mark a day consumed. Rewriting an existing day is deliberate: a range
    re-run must converge on the same numbers rather than accumulate them."""
    conn.execute(
        """
        INSERT INTO sync_day (
            sogc_date, notices, tracked, inserted, updated, deletions, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (sogc_date) DO UPDATE SET
            notices = excluded.notices,
            tracked = excluded.tracked,
            inserted = excluded.inserted,
            updated = excluded.updated,
            deletions = excluded.deletions,
            fetched_at = excluded.fetched_at
        """,
        (
            date,
            counts.get("notices", 0),
            counts.get("tracked", 0),
            counts.get("inserted", 0),
            counts.get("updated", 0),
            counts.get("deletions", 0),
            now(),
        ),
    )


def mark_stale(conn: sqlite3.Connection, company_id: int, mutation_date: str | None) -> None:
    """Flag a company for re-hydration, without resetting an older flag.

    stale_since keeps the moment we first learned the local copy was behind,
    so a company mentioned in five notices before anyone runs hydrate is one
    piece of work, not five, and the oldest debt is visible.
    """
    conn.execute(
        """
        UPDATE company
           SET stale_since = COALESCE(stale_since, ?),
               last_mutation_date = MAX(COALESCE(last_mutation_date, ''), COALESCE(?, ''))
         WHERE id = ?
        """,
        (now(), mutation_date, company_id),
    )


def clear_stale(conn: sqlite3.Connection, company_id: int) -> None:
    conn.execute("UPDATE company SET stale_since = NULL WHERE id = ?", (company_id,))


def record_sweep(
    conn: sqlite3.Connection,
    canton: str,
    legal_form_uid: str,
    prefix: str,
    result_count: int,
    deepened: bool = False,
) -> None:
    conn.execute(
        """
        INSERT INTO sweep (canton, legal_form_uid, prefix, result_count,
                           deepened, completed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (canton, legal_form_uid, prefix) DO UPDATE SET
            result_count = excluded.result_count,
            deepened = excluded.deepened,
            completed_at = excluded.completed_at
        """,
        (canton, legal_form_uid, prefix, result_count, int(deepened), now()),
    )


def completed_prefixes(
    conn: sqlite3.Connection, canton: str, legal_form_uid: str
) -> set[str]:
    """Prefixes already fully covered, so a resumed sweep can skip them.

    A deepened prefix is not complete in itself: its children hold the rows,
    and the resume logic has to walk into it again to check they all finished.
    """
    rows = conn.execute(
        """
        SELECT prefix FROM sweep
        WHERE canton = ? AND legal_form_uid = ? AND deepened = 0
        """,
        (canton, legal_form_uid),
    ).fetchall()
    return {r[0] for r in rows}


def save_segments(
    conn: sqlite3.Connection,
    company_id: int,
    segments: list[dict[str, Any]],
    model: str,
) -> None:
    """Store the classifier's verdict, without touching a human's.

    The WHERE on the conflict clause is the whole point. Without it, correcting
    a label in the console and then re-running `profile` would silently restore
    the model's answer, which makes the correction feel like work and be
    worthless. A person who has looked at a company knows more than a sentence
    of registered purpose does.
    """
    stamp = now()
    for item in segments:
        conn.execute(
            """
            INSERT INTO segment (company_id, segment, confidence, rationale,
                                 model, classified_at, source)
            VALUES (?, ?, ?, ?, ?, ?, 'model')
            ON CONFLICT (company_id, segment) DO UPDATE SET
                confidence = excluded.confidence,
                rationale = excluded.rationale,
                model = excluded.model,
                classified_at = excluded.classified_at
            WHERE segment.source <> 'manual'
            """,
            (
                company_id,
                item["segment"],
                item.get("confidence"),
                item.get("rationale"),
                model,
                stamp,
            ),
        )


def set_segment_manually(
    conn: sqlite3.Connection,
    company_id: int,
    segment: str,
    *,
    present: bool = True,
    rationale: str | None = None,
    author: str | None = None,
) -> None:
    """Add or remove a label by hand, and mark it as a person's doing.

    Removal deletes the row rather than storing a negative, because a label
    that is not there is the same fact however it came to be absent. The cost
    is that re-running `profile` can re-add it; that is why `reclassify` is
    opt-in and why the console shows what the model last said.
    """
    if not present:
        conn.execute(
            "DELETE FROM segment WHERE company_id = ? AND segment = ?",
            (company_id, segment),
        )
        return
    stamp = now()
    conn.execute(
        """
        INSERT INTO segment (company_id, segment, confidence, rationale,
                             model, classified_at, source)
        VALUES (?, ?, 1.0, ?, ?, ?, 'manual')
        ON CONFLICT (company_id, segment) DO UPDATE SET
            confidence = 1.0,
            rationale = excluded.rationale,
            model = excluded.model,
            classified_at = excluded.classified_at,
            source = 'manual'
        """,
        (company_id, segment, rationale, author or "manual", stamp),
    )


def save_fit(conn: sqlite3.Connection, company_id: int, row: dict[str, Any]) -> None:
    """Store a fit score, without overwriting one a person set.

    Same guard as save_segments, for the same reason: a score corrected by
    hand must survive the stage running again, or correcting it is wasted
    effort and the console is a display rather than a tool.
    """
    def as_json(value: Any) -> str | None:
        if value in (None, [], ""):
            return None
        return json.dumps(value, ensure_ascii=False)

    conn.execute(
        """
        INSERT INTO fit (company_id, score, band, reasoning, advice, against,
                         matched, source, model, brief_hash, site_hash, scored_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'model', ?, ?, ?, ?)
        ON CONFLICT (company_id) DO UPDATE SET
            score = excluded.score,
            band = excluded.band,
            reasoning = excluded.reasoning,
            advice = excluded.advice,
            against = excluded.against,
            matched = excluded.matched,
            model = excluded.model,
            brief_hash = excluded.brief_hash,
            site_hash = excluded.site_hash,
            scored_at = excluded.scored_at
        WHERE fit.source <> 'manual'
        """,
        (
            company_id,
            int(row.get("score") or 0),
            row.get("band") or "low",
            row.get("reasoning"),
            row.get("advice"),
            as_json(row.get("against")),
            as_json(row.get("matched")),
            row.get("model"),
            row.get("brief_hash"),
            row.get("site_hash"),
            now(),
        ),
    )


def set_fit_manually(
    conn: sqlite3.Connection,
    company_id: int,
    *,
    score: int,
    band: str,
    reasoning: str | None = None,
    author: str | None = None,
) -> None:
    """Override a score by hand. Marked manual, so no run undoes it."""
    conn.execute(
        """
        INSERT INTO fit (company_id, score, band, reasoning, source, model, scored_at)
        VALUES (?, ?, ?, ?, 'manual', ?, ?)
        ON CONFLICT (company_id) DO UPDATE SET
            score = excluded.score,
            band = excluded.band,
            reasoning = excluded.reasoning,
            source = 'manual',
            model = excluded.model,
            scored_at = excluded.scored_at
        """,
        (company_id, int(score), band, reasoning, author or "manual", now()),
    )


# --- the follow-up log ------------------------------------------------------


def add_note(
    conn: sqlite3.Connection,
    company_id: int,
    body: str,
    *,
    kind: str = "note",
    author: str | None = None,
    occurred_at: str | None = None,
) -> int:
    """Record something that happened, or something worth remembering.

    Never deduplicated and never updated in place. Two calls to the same
    company on the same day are two facts, and a note that turned out to be
    wrong is still what was believed at the time.
    """
    stamp = now()
    cur = conn.execute(
        """
        INSERT INTO note (company_id, kind, body, author, occurred_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (company_id, kind, body, author, occurred_at or stamp, stamp),
    )
    return int(cur.lastrowid or 0)


def notes_for(conn: sqlite3.Connection, company_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM note WHERE company_id = ? ORDER BY occurred_at DESC, id DESC",
        (company_id,),
    ).fetchall()


# States a campaign moves a company through. Not a database constraint: a
# recruiting pipeline and a membership drive want different words, and the
# console offers this list rather than enforcing it.
PROSPECT_STATES = (
    "new", "researching", "contacted", "replied", "meeting", "won", "lost", "parked",
)


def set_prospect(
    conn: sqlite3.Connection,
    company_id: int,
    *,
    state: str | None = None,
    owner: str | None = None,
    next_action: str | None = None,
    next_at: str | None = None,
) -> None:
    """Create or update where a company stands.

    Only the fields given are written, so setting a next action does not
    silently reset the state, which is the mistake a single UPDATE with
    defaults would make on every partial edit from a form.
    """
    # Two statements rather than one upsert. An upsert has to supply a value
    # for the NOT NULL state column on the insert path, and that value then
    # arrives in `excluded` on the update path indistinguishable from one the
    # caller asked for: COALESCE(excluded.state, ...) would read the default
    # 'new' as a deliberate reset, so editing a next action would move a
    # company at "meeting" back to the start of the pipeline.
    stamp = now()
    conn.execute(
        """
        INSERT OR IGNORE INTO prospect (company_id, state, created_at, updated_at)
        VALUES (?, 'new', ?, ?)
        """,
        (company_id, stamp, stamp),
    )
    conn.execute(
        """
        UPDATE prospect
           SET state       = COALESCE(?, state),
               owner       = COALESCE(?, owner),
               next_action = COALESCE(?, next_action),
               next_at     = COALESCE(?, next_at),
               updated_at  = ?
         WHERE company_id  = ?
        """,
        (state, owner, next_action, next_at, stamp, company_id),
    )


def save_publications(
    conn: sqlite3.Connection, company_id: int, publications: list[dict[str, Any]]
) -> int:
    """Store a company's SHAB notices, keyed on the SHAB id.

    A notice is immutable once issued, so a re-hydrate finds the same rows and
    updates nothing. Notices without a sogc_id fall back to an insert guarded
    by the message text, which is the only other thing that identifies them.
    """
    stamp = now()
    written = 0
    for item in publications:
        if item.get("sogc_id") is None:
            existing = conn.execute(
                "SELECT 1 FROM publication WHERE company_id = ? AND message = ?",
                (company_id, item["message"]),
            ).fetchone()
            if existing:
                continue
        conn.execute(
            """
            INSERT INTO publication (
                company_id, sogc_id, sogc_date, journal_id, journal_date,
                canton, message, source_url, method, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (company_id, sogc_id) DO NOTHING
            """,
            (
                company_id,
                item.get("sogc_id"),
                item.get("sogc_date"),
                item.get("journal_id"),
                item.get("journal_date"),
                item.get("canton"),
                item["message"],
                item.get("source_url"),
                item.get("method"),
                stamp,
            ),
        )
        written += 1
    return written


def save_people(
    conn: sqlite3.Connection, company_id: int, people: list[dict[str, Any]]
) -> int:
    """Store officers parsed from notices.

    Keyed on (company_id, full_name, role) by the schema, so re-running the
    parser refreshes attributes rather than duplicating a person. Attributes
    are only overwritten when the incoming row carries them, so a notice that
    names someone without repeating their origin cannot blank it.
    """
    stamp = now()
    written = 0
    for item in people:
        conn.execute(
            """
            INSERT INTO person (
                company_id, full_name, role, origin, domicile, signature,
                last_seen_date, source_url, method, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (company_id, full_name, role) DO UPDATE SET
                origin = COALESCE(excluded.origin, person.origin),
                domicile = COALESCE(excluded.domicile, person.domicile),
                signature = COALESCE(excluded.signature, person.signature),
                last_seen_date = MAX(
                    COALESCE(excluded.last_seen_date, ''),
                    COALESCE(person.last_seen_date, '')
                ),
                source_url = COALESCE(excluded.source_url, person.source_url),
                fetched_at = excluded.fetched_at
            """,
            (
                company_id,
                item["full_name"],
                item.get("role"),
                item.get("origin"),
                item.get("domicile"),
                item.get("signature"),
                item.get("last_seen_date"),
                item.get("source_url"),
                item.get("method", "shab"),
                stamp,
            ),
        )
        written += 1
    return written


def save_site(conn: sqlite3.Connection, company_id: int, row: dict[str, Any]) -> None:
    """Store what the web said about a company. One row per company.

    Re-enriching replaces the row wholesale rather than merging: unlike a
    registry fact, a stale offering list is not worth preserving next to a
    fresh one, and a company that has redesigned its site should not carry
    both readings.
    """

    def as_json(value: Any) -> str | None:
        if value in (None, [], ""):
            return None
        return json.dumps(value, ensure_ascii=False)

    conn.execute(
        """
        INSERT INTO site (
            company_id, status, url, confidence, language, summary, offerings,
            signals, partnerships, tech_stack, size_hint, restricted,
            source_url, method, fetched_at, model, searches, fetches
        ) VALUES (
            :company_id, :status, :url, :confidence, :language, :summary,
            :offerings, :signals, :partnerships, :tech_stack, :size_hint,
            :restricted, :source_url, :method, :fetched_at, :model, :searches,
            :fetches
        )
        ON CONFLICT (company_id) DO UPDATE SET
            status = excluded.status,
            url = excluded.url,
            confidence = excluded.confidence,
            language = excluded.language,
            summary = excluded.summary,
            offerings = excluded.offerings,
            signals = excluded.signals,
            partnerships = excluded.partnerships,
            tech_stack = excluded.tech_stack,
            size_hint = excluded.size_hint,
            restricted = excluded.restricted,
            source_url = excluded.source_url,
            method = excluded.method,
            fetched_at = excluded.fetched_at,
            model = excluded.model,
            searches = excluded.searches,
            fetches = excluded.fetches,
            -- Having looked for an address survives a re-reading of the same
            -- site and does not survive a different one. Clearing it
            -- unconditionally would make every enrich --refresh re-fetch
            -- pages whose answer had not changed; never clearing it would
            -- leave a company pointing at a new domain nobody had opened.
            reached_at = CASE WHEN site.url IS excluded.url
                              THEN site.reached_at ELSE NULL END,
            reach_pages = CASE WHEN site.url IS excluded.url
                               THEN site.reach_pages ELSE NULL END
        """,
        {
            "company_id": company_id,
            "status": row["status"],
            "url": row.get("url"),
            "confidence": row.get("confidence"),
            "language": row.get("language"),
            "summary": row.get("summary"),
            "offerings": as_json(row.get("offerings")),
            "signals": as_json(row.get("signals")),
            "partnerships": as_json(row.get("partnerships")),
            "tech_stack": as_json(row.get("tech_stack")),
            "size_hint": row.get("size_hint"),
            "restricted": as_json(row.get("restricted")),
            "source_url": row.get("source_url"),
            "method": row.get("method"),
            "fetched_at": now(),
            "model": row.get("model"),
            "searches": row.get("searches"),
            "fetches": row.get("fetches"),
        },
    )
    # Digested from the stored row, not the payload, for the same reason
    # upsert_company does: the caller passes Python lists and the column holds
    # JSON text, so hashing what arrived would never equal hashing what a
    # later reader sees, and every fit score would look permanently stale.
    stored = conn.execute(
        "SELECT * FROM site WHERE company_id = ?", (company_id,)
    ).fetchone()
    conn.execute(
        "UPDATE site SET content_hash = ? WHERE company_id = ?",
        (site_digest(stored), company_id),
    )


# Columns of `site` that may inform a judgement but must never be quoted
# back to the company. See migrations/0004_restricted.sql for why this is a
# column rather than a convention.
RESTRICTED_SITE_COLUMNS = frozenset({"restricted"})


def site_for_draft(conn: sqlite3.Connection, company_id: int) -> dict[str, Any] | None:
    """The web research, with everything unquotable removed.

    The single accessor any outward-facing path must use: drafting, exporting,
    anything that puts words in front of the company. Reading the table
    directly is the mistake this exists to make hard, so the filtering lives
    here rather than in each caller's SELECT, where the next caller would
    forget it.
    """
    row = conn.execute(
        "SELECT * FROM site WHERE company_id = ?", (company_id,)
    ).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys() if k not in RESTRICTED_SITE_COLUMNS}


def save_contact(
    conn: sqlite3.Connection,
    *,
    company_id: int,
    kind: str,
    value: str,
    person_id: int | None = None,
    source_url: str | None = None,
    method: str | None = None,
) -> int:
    """Record a point of contact. Returns 1 if it was new, 0 if already known.

    Never clears `suppressed`: an address a human has suppressed must stay
    suppressed even when a later run finds it published again.

    The count is taken by looking first rather than from the cursor, which is
    the same correction `set_prospect` needed. On the conflict path an upsert
    still reports rowcount 1, and `lastrowid` keeps whatever the connection's
    last real insert left there, so the obvious test is true for a row that
    was only updated: every re-run would report addresses it already had as
    newly found.
    """
    known = conn.execute(
        "SELECT 1 FROM contact WHERE company_id = ? AND kind = ? AND value = ?",
        (company_id, kind, value),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO contact (
            person_id, company_id, kind, value, source_url, method, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (company_id, kind, value) DO UPDATE SET
            person_id = COALESCE(contact.person_id, excluded.person_id),
            source_url = COALESCE(excluded.source_url, contact.source_url),
            method = COALESCE(excluded.method, contact.method),
            fetched_at = excluded.fetched_at
        """,
        (person_id, company_id, kind, value, source_url, method, now()),
    )
    return 0 if known else 1


def mark_reached(
    conn: sqlite3.Connection, company_id: int, *, pages: int
) -> None:
    """Record that this company's site was read for an address.

    Distinct from having found one. See migrations/0010_reach.sql: without
    it, a site that publishes only a web form is indistinguishable from one
    nobody has opened, and every run re-fetches it.
    """
    conn.execute(
        "UPDATE site SET reached_at = ?, reach_pages = ? WHERE company_id = ?",
        (now(), int(pages), company_id),
    )


def start_run(
    conn: sqlite3.Connection,
    stage: str,
    args: dict[str, Any],
    *,
    started_by: str = "cli",
) -> int:
    cur = conn.execute(
        "INSERT INTO run (stage, args_json, started_at, started_by) VALUES (?, ?, ?, ?)",
        (stage, json.dumps(args, ensure_ascii=False, default=str), now(), started_by),
    )
    conn.commit()
    return int(cur.lastrowid)


def append_line(conn: sqlite3.Connection, run_id: int, text: str) -> int:
    """Record one line a running stage emitted. Returns its sequence number.

    The sink behind the `log` callable every stage already accepts. See
    migrations/0011_run_lines.sql for why this is a table: a hydration of
    sixty thousand companies is otherwise one row reading "running" for eight
    hours.
    """
    seq = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM run_line WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO run_line (run_id, seq, at, text) VALUES (?, ?, ?, ?)",
        (run_id, seq, now(), text),
    )
    return int(seq)


def lines_after(
    conn: sqlite3.Connection, run_id: int, after: int = 0, limit: int = 500
) -> list[sqlite3.Row]:
    """Lines a watcher has not seen yet. A range scan on (run_id, seq)."""
    return conn.execute(
        "SELECT seq, at, text FROM run_line WHERE run_id = ? AND seq > ? "
        "ORDER BY seq LIMIT ?",
        (run_id, after, limit),
    ).fetchall()


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    stats: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        "UPDATE run SET finished_at = ?, stats_json = ?, error = ? WHERE id = ?",
        (now(), json.dumps(stats or {}, ensure_ascii=False, default=str), error, run_id),
    )
    conn.commit()


# --- clients, projects, and the one event log ---------------------------------
#
# See migrations/0012_clients.sql for why the audit trail, the client timeline
# and the calendar are one table rather than three.

CLIENT_STATES = ("active", "prospect", "dormant", "former")
PROJECT_STATES = ("proposed", "active", "paused", "delivered", "cancelled")
EVENT_KINDS = ("email", "call", "meeting", "note", "milestone", "document", "decision")
EVENT_STATES = ("planned", "done", "cancelled")


def make_code(name: str, taken: set[str], *, length: int = 14) -> str:
    """A quotable reference from a company name: BOIS-GENTIL.

    Generated rather than typed because the first thing an operator does with
    a blank required field is put something inconsistent in it, and this ends
    up on invoices. Editable afterwards, because a generated code is a
    starting point and not a decision.
    """
    words = re.findall(r"[A-Za-z0-9]+", unicodedata.normalize("NFKD", name))
    skip = {"la", "le", "les", "de", "du", "des", "sa", "sarl", "ag", "gmbh",
            "fondation", "association", "the", "of"}
    kept = [w.upper() for w in words if w.lower() not in skip] or [w.upper() for w in words]
    base = "-".join(kept)[:length].strip("-") or "CLIENT"
    if base not in taken:
        return base
    for suffix in range(2, 100):
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return candidate
    raise ValueError(f"could not derive a free code from {name!r}")


def promote_to_client(
    conn: sqlite3.Connection,
    company_id: int,
    *,
    code: str | None = None,
    owner: str | None = None,
    brief: str | None = None,
    status: str = "active",
) -> int:
    """Make a company a client, keeping everything already known about it.

    A reference rather than a copy. The comparisons of professional-services
    tools call the alternative the handoff gap: a system that stops at "closed
    won" makes the first day of the engagement a copy-paste job, and the
    register entry, the officers, the research and the notes are exactly what
    is worth having on day one.

    Idempotent: promoting twice returns the existing client rather than
    failing, because the button that does it is on a page anyone may reload.
    """
    existing = conn.execute(
        "SELECT id FROM client WHERE company_id = ?", (company_id,)
    ).fetchone()
    if existing:
        return int(existing[0])

    name = conn.execute(
        "SELECT name FROM company WHERE id = ?", (company_id,)
    ).fetchone()
    if name is None:
        raise ValueError(f"no company with id {company_id}")
    taken = {row[0] for row in conn.execute("SELECT code FROM client")}
    cur = conn.execute(
        "INSERT INTO client (company_id, code, status, since, owner, brief, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (company_id, code or make_code(name[0], taken), status, now()[:10],
         owner, brief, now()),
    )
    return int(cur.lastrowid)


def update_client(conn: sqlite3.Connection, client_id: int, **fields: Any) -> None:
    allowed = {"code", "status", "since", "owner", "brief"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not sets:
        return
    assignments = ", ".join(f"{k} = :{k}" for k in sets)
    conn.execute(
        f"UPDATE client SET {assignments}, updated_at = :now WHERE id = :id",
        {**sets, "now": now(), "id": client_id},
    )


def add_project(
    conn: sqlite3.Connection,
    client_id: int,
    name: str,
    *,
    code: str | None = None,
    status: str = "proposed",
    billing: str | None = None,
    budget: float | None = None,
    summary: str | None = None,
    started_at: str | None = None,
    due_at: str | None = None,
) -> int:
    """A piece of work, with its own reference.

    The code is the client's plus a sequence: BOIS-GENTIL-01. A project
    outlives the conversation that started it and has to be nameable in a
    contract, an invoice and a file name.
    """
    client = conn.execute(
        "SELECT code FROM client WHERE id = ?", (client_id,)
    ).fetchone()
    if client is None:
        raise ValueError(f"no client with id {client_id}")
    if not code:
        used = conn.execute(
            "SELECT COUNT(*) FROM project WHERE client_id = ?", (client_id,)
        ).fetchone()[0]
        taken = {row[0] for row in conn.execute("SELECT code FROM project")}
        for n in range(used + 1, used + 100):
            candidate = f"{client[0]}-{n:02d}"
            if candidate not in taken:
                code = candidate
                break
    cur = conn.execute(
        "INSERT INTO project (client_id, code, name, status, billing, budget,"
        " summary, started_at, due_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (client_id, code, name, status, billing, budget, summary,
         started_at, due_at, now()),
    )
    return int(cur.lastrowid)


def update_project(conn: sqlite3.Connection, project_id: int, **fields: Any) -> None:
    allowed = {"name", "status", "billing", "budget", "summary", "started_at",
               "due_at", "closed_at", "code"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not sets:
        return
    # Closing a project stamps the date if the caller did not, so a delivered
    # project always says when rather than only that.
    if sets.get("status") in ("delivered", "cancelled") and "closed_at" not in sets:
        sets["closed_at"] = now()[:10]
    assignments = ", ".join(f"{k} = :{k}" for k in sets)
    conn.execute(
        f"UPDATE project SET {assignments}, updated_at = :now WHERE id = :id",
        {**sets, "now": now(), "id": project_id},
    )


def record_event(
    conn: sqlite3.Connection,
    client_id: int,
    *,
    kind: str,
    occurred_at: str,
    subject: str | None = None,
    body: str | None = None,
    project_id: int | None = None,
    state: str = "done",
    ends_at: str | None = None,
    direction: str | None = None,
    with_whom: str | None = None,
    author: str | None = None,
    source: str = "manual",
    external_id: str | None = None,
) -> int:
    """Write one line of the record. Returns its id, or the existing one.

    Correspondence carries its Message-ID in `external_id`, so importing the
    same message twice is one row rather than two. Every established tool
    surveyed loses this and duplicates correspondence on re-import; it costs
    one column not to.
    """
    if external_id:
        seen = conn.execute(
            "SELECT id FROM event WHERE client_id = ? AND external_id = ?",
            (client_id, external_id),
        ).fetchone()
        if seen:
            return int(seen[0])
    cur = conn.execute(
        "INSERT INTO event (client_id, project_id, kind, state, occurred_at,"
        " ends_at, subject, body, direction, with_whom, author, source,"
        " external_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (client_id, project_id, kind, state, occurred_at, ends_at, subject,
         body, direction, with_whom, author, source, external_id, now()),
    )
    return int(cur.lastrowid)


def set_event_state(conn: sqlite3.Connection, event_id: int, state: str) -> None:
    """A planned meeting becomes done, or cancelled. Never removed.

    An audit trail with a delete button is a diary, so this is the only way an
    event ever changes state.
    """
    if state not in EVENT_STATES:
        raise ValueError(f"unknown event state {state!r}")
    conn.execute("UPDATE event SET state = ? WHERE id = ?", (state, event_id))


def timeline(
    conn: sqlite3.Connection,
    client_id: int,
    *,
    project_id: int | None = None,
    limit: int = 200,
) -> list[sqlite3.Row]:
    """The record for one client, or one project within it, newest first."""
    where = ["e.client_id = ?"]
    params: list[Any] = [client_id]
    if project_id is not None:
        where.append("e.project_id = ?")
        params.append(project_id)
    return conn.execute(
        f"""SELECT e.*, p.code AS project_code, p.name AS project_name
              FROM event e LEFT JOIN project p ON p.id = e.project_id
             WHERE {' AND '.join(where)}
             ORDER BY e.occurred_at DESC, e.id DESC LIMIT ?""",
        params + [limit],
    ).fetchall()


def diary(
    conn: sqlite3.Connection, *, since: str, until: str
) -> list[sqlite3.Row]:
    """Everything in the calendar between two dates, across every client.

    The same table as the timeline. A meeting cannot be in the diary and
    missing from the file, because they are one row read two ways.
    """
    return conn.execute(
        """SELECT e.*, c.code AS client_code, p.code AS project_code,
                  co.name AS company_name
             FROM event e
             JOIN client c ON c.id = e.client_id
             JOIN company co ON co.id = c.company_id
             LEFT JOIN project p ON p.id = e.project_id
            WHERE e.state <> 'cancelled'
              AND date(e.occurred_at) BETWEEN date(?) AND date(?)
            ORDER BY e.occurred_at, e.id""",
        (since, until),
    ).fetchall()


def clients(conn: sqlite3.Connection, *, status: str = "") -> list[sqlite3.Row]:
    """The portfolio, with the numbers that say whether anything is adrift."""
    where = "WHERE c.status = ?" if status else ""
    params = [status] if status else []
    return conn.execute(
        f"""SELECT c.*, co.name, co.uid, co.canton, co.legal_form_label,
                   (SELECT COUNT(*) FROM project p
                     WHERE p.client_id = c.id AND p.status = 'active') AS active_projects,
                   (SELECT COUNT(*) FROM project p WHERE p.client_id = c.id) AS projects,
                   (SELECT MAX(e.occurred_at) FROM event e
                     WHERE e.client_id = c.id AND e.state = 'done') AS last_contact,
                   (SELECT MIN(e.occurred_at) FROM event e
                     WHERE e.client_id = c.id AND e.state = 'planned'
                       AND date(e.occurred_at) >= date('now')) AS next_at
              FROM client c JOIN company co ON co.id = c.company_id
              {where}
             ORDER BY c.status, co.name""",
        params,
    ).fetchall()


# --- operator settings --------------------------------------------------------
#
# See migrations/0013_settings.sql. arpenteur.toml keeps its comments and its
# defaults; a row here is this deployment's decision, overlaid on them.

# Only these may be set from the console. A whitelist rather than free keys,
# so a typo becomes an error instead of a setting that silently does nothing.
SETTABLE = ("cantons", "legal_forms", "statuses", "admit_new", "segments",
            "signal_brief")


def settings_overlay(conn: sqlite3.Connection) -> dict[str, Any]:
    """Everything an operator has changed, ready to apply over the file."""
    out: dict[str, Any] = {}
    for row in conn.execute("SELECT key, value_json FROM setting"):
        if row[0] not in SETTABLE:
            continue
        try:
            out[row[0]] = json.loads(row[1])
        except (TypeError, ValueError):
            continue
    return out


def save_setting(
    conn: sqlite3.Connection, key: str, value: Any, *, by: str = "console"
) -> None:
    if key not in SETTABLE:
        raise ValueError(f"{key!r} is not an operator setting")
    conn.execute(
        "INSERT INTO setting (key, value_json, updated_at, updated_by)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value_json = excluded.value_json,"
        " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (key, json.dumps(value, ensure_ascii=False), now(), by),
    )


def setting_history(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """When each setting was last changed, and by whom.

    Widening the territory changes what every later stage will spend money on,
    and that is a question asked after the bill rather than before it.
    """
    return conn.execute(
        "SELECT key, updated_at, updated_by FROM setting ORDER BY updated_at DESC"
    ).fetchall()


# The register's legal form catalogue, cached so a settings page can offer it
# as a list without a network call on every render. Not a setting: it is a
# copy of something the register owns, so it is written by a refresh rather
# than by a person and is deliberately outside SETTABLE.
CATALOGUE_KEY = "_legal_form_catalogue"


def save_catalogue(conn: sqlite3.Connection, forms: list[dict[str, Any]]) -> None:
    conn.execute(
        "INSERT INTO setting (key, value_json, updated_at, updated_by)"
        " VALUES (?, ?, ?, 'register')"
        " ON CONFLICT (key) DO UPDATE SET value_json = excluded.value_json,"
        " updated_at = excluded.updated_at",
        (CATALOGUE_KEY, json.dumps(forms, ensure_ascii=False), now()),
    )
    conn.commit()


def catalogue(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    row = conn.execute(
        "SELECT value_json FROM setting WHERE key = ?", (CATALOGUE_KEY,)
    ).fetchone()
    if row is None:
        return []
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return []
