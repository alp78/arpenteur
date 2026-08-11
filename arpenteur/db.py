"""SQLite storage: connection, migrations, and the few write helpers.

No ORM. The schema is small, the queries are the interesting part, and a
migration is a numbered .sql file applied in order against PRAGMA
user_version. That keeps the whole storage story readable in one sitting.
"""

from __future__ import annotations

import json
import sqlite3
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


def migrate(conn: sqlite3.Connection) -> int:
    """Apply every migration newer than the database's user_version.

    Returns the version arrived at. Safe to call on every command: a database
    already at the latest version does nothing.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    for path in files:
        number = int(path.name[:4])
        if number <= version:
            continue
        with conn:
            conn.executescript(path.read_text(encoding="utf-8"))
            # executescript commits, so the version bump is its own statement.
            conn.execute(f"PRAGMA user_version = {number}")
        version = number
    return version


def open_db(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    migrate(conn)
    return conn


# --- writes -----------------------------------------------------------------


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
    row_id = conn.execute(
        "SELECT id FROM company WHERE uid = ?", (row["uid"],)
    ).fetchone()[0]
    return int(row_id)


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
    stamp = now()
    for item in segments:
        conn.execute(
            """
            INSERT INTO segment (company_id, segment, confidence, rationale,
                                 model, classified_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (company_id, segment) DO UPDATE SET
                confidence = excluded.confidence,
                rationale = excluded.rationale,
                model = excluded.model,
                classified_at = excluded.classified_at
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


def start_run(conn: sqlite3.Connection, stage: str, args: dict[str, Any]) -> int:
    cur = conn.execute(
        "INSERT INTO run (stage, args_json, started_at) VALUES (?, ?, ?)",
        (stage, json.dumps(args, ensure_ascii=False, default=str), now()),
    )
    conn.commit()
    return int(cur.lastrowid)


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
