"""Reading and writing the survey.

Every statement that touches `core` or `intel` lives here. Nothing above this
line writes SQL: the console and the stages ask for what they want by name, so
a schema change is one file to read rather than a search across seventeen.

Ported from the SQLite store. Four differences are worth knowing about because
they change what callers get back, not just how it is spelled.

**Timestamps arrive as ISO strings.** The columns are TIMESTAMPTZ, which is
what makes the date arithmetic and the analytics schema possible; the kernel's
row factory converts on the way out, because every caller here formats a stamp
rather than computing with one.

**JSON columns arrive parsed.** `site.offerings` is a real list now, not a
string that has to be json.loads-ed. Writers wrap with `json_param`, which
is required rather than optional: psycopg refuses to guess whether a dict means
json, hstore or a composite.

**Null-safe comparison is `IS NOT DISTINCT FROM`.** SQLite spells it `IS`, and
the difference bites exactly once, in save_site, where the whole point is that
a null url must compare equal to a null url.

**Money is Decimal.** NUMERIC rather than REAL, because a rate card in
floating point eventually produces a quote ending in .00000000001.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from typing import Any

from ...kernel.db import Connection, Row, json_param, now

# --- digests ------------------------------------------------------------------

# The registry fields a change is measured over. Provenance and timestamps are
# excluded on purpose: fetched_at moves on every read, so including it would
# make every row look changed and the digest would measure nothing.
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
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# The site fields a fit score is actually computed from. Provenance and spend
# are excluded: re-running enrich moves fetched_at and the search counts
# without changing a word the judge read.
SITE_JUDGED_FIELDS = (
    "status", "url", "confidence", "summary", "offerings", "signals",
    "partnerships", "tech_stack", "size_hint",
)


def site_digest(site: dict[str, Any] | None) -> str | None:
    """What the judge saw, as a digest. None when there was no research.

    Compared against the digest stored on a fit row to decide whether the score
    still stands. `restricted` is excluded because the judge is never shown it,
    and including it would expire scores on a change that could not have
    affected them.
    """
    if site is None:
        return None
    values = [site.get(field) for field in SITE_JUDGED_FIELDS]
    material = json.dumps(values, ensure_ascii=False, default=str, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


# --- companies ----------------------------------------------------------------


def upsert_company(conn: Connection, row: dict[str, Any]) -> int:
    """Insert or update one company, keyed on UID.

    first_seen is written once and never overwritten: `excluded.first_seen`
    would reset it on every sweep, destroying the only signal telling a new
    registration from a re-sweep. Hydration fields are overwritten only when
    the incoming row carries them, so a cheap search result cannot blank a
    detail already fetched.
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
        "address_json": json_param(row.get("address") or None),
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
    # A translated field arriving as {de, fr, it, en} instead of a string
    # otherwise surfaces as a driver error naming no column, and sends you
    # looking in the wrong place. Fail with the name.
    for key, value in payload.items():
        if key != "address_json" and isinstance(value, (dict, list)):
            raise TypeError(
                f"company.{key} is {type(value).__name__}, not a scalar: "
                f"{value!r:.120}. A Zefix field is probably translated; pass it "
                f"through _text() in sources/zefix.py."
            )
    stored = conn.execute(
        """
        INSERT INTO core.company (
            uid, chid, ehraid, name, legal_form_uid, legal_form_label,
            legal_seat, legal_seat_id, canton, status, purpose, address_json,
            capital, capital_currency, cantonal_excerpt_url, zefix_detail_url,
            source_url, method, fetched_at, first_seen, hydrated_at
        ) VALUES (
            %(uid)s, %(chid)s, %(ehraid)s, %(name)s, %(legal_form_uid)s,
            %(legal_form_label)s, %(legal_seat)s, %(legal_seat_id)s, %(canton)s,
            %(status)s, %(purpose)s, %(address_json)s, %(capital)s,
            %(capital_currency)s, %(cantonal_excerpt_url)s, %(zefix_detail_url)s,
            %(source_url)s, %(method)s, %(fetched_at)s, %(first_seen)s,
            %(hydrated_at)s
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
        RETURNING *
        """,
        payload,
    ).fetchone()
    # Hashed from the stored row rather than the incoming payload. They are not
    # the same thing: the COALESCE clauses mean a cheap search result leaves an
    # already-hydrated purpose in place, so hashing what arrived would report a
    # change on a row that did not move.
    conn.execute(
        "UPDATE core.company SET content_hash = %s WHERE id = %s",
        (company_content_hash({k: stored[k] for k in HASHED_FIELDS}), stored["id"]),
    )
    return int(stored["id"])


def company_by_uid(conn: Connection, uid: str) -> Row | None:
    return conn.execute(
        "SELECT * FROM core.company WHERE uid = %s", (uid,)
    ).fetchone()


def company(conn: Connection, company_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM core.company WHERE id = %s", (company_id,)
    ).fetchone()


# "Where has this company got to" as a filter, so the survey list can answer
# the question the operations page raises: which companies are the ones still
# waiting for research, and which are already scored.
STAGE_FILTERS: dict[str, str] = {
    "": "",
    "no-detail": "c.hydrated_at IS NULL",
    "detail": "c.hydrated_at IS NOT NULL",
    "changed": "c.stale_since IS NOT NULL",
    "unclassified": "c.hydrated_at IS NOT NULL AND NOT EXISTS "
                    "(SELECT 1 FROM intel.segment s WHERE s.company_id = c.id)",
    "classified": "EXISTS (SELECT 1 FROM intel.segment s WHERE s.company_id = c.id)",
    # The backlog the sector vocabulary created. Companies profiled before it
    # existed carry segments and no sector, and this is how an operator sees
    # exactly them rather than inferring the gap from two counts.
    "no-sector": "c.hydrated_at IS NOT NULL AND NOT EXISTS "
                 "(SELECT 1 FROM intel.sector x WHERE x.company_id = c.id)",
    "unresearched": "EXISTS (SELECT 1 FROM intel.segment s WHERE s.company_id = c.id) "
                    "AND w.company_id IS NULL",
    "researched": "w.company_id IS NOT NULL",
    "unscored": "w.company_id IS NOT NULL AND f.company_id IS NULL",
    "scored": "f.company_id IS NOT NULL",
    "with-address": "EXISTS (SELECT 1 FROM core.contact ct WHERE ct.company_id = c.id "
                    "AND ct.kind = 'email' AND NOT ct.suppressed)",
}

_LIST_JOINS = (
    "FROM core.company c "
    "LEFT JOIN intel.fit f ON f.company_id = c.id "
    "LEFT JOIN intel.site w ON w.company_id = c.id "
    "LEFT JOIN intel.prospect p ON p.company_id = c.id "
    # Joined rather than sub-selected because the sector is shown in every row
    # as well as filtered on, and one row per company makes that free.
    "LEFT JOIN intel.sector sec ON sec.company_id = c.id"
)


def search_companies(
    conn: Connection, *, q: str = "", canton: str = "", segment: str = "",
    sector: str = "", band: str = "", state: str = "", stage: str = "",
    limit: int = 200, with_total: bool = True,
) -> tuple[list[Row], int | None]:
    """The survey list, and how many rows the filter actually matched.

    The name search is trigram similarity, and everything about how it is
    written here is in aid of the GIN index staying usable.

    Measured on the live 184'939 rows: an `unaccent(lower(name)) LIKE '%…%'`
    branch OR-ed alongside the trigram operator took **just over a second**,
    because no index can serve it and Postgres falls back to reading the table
    for the whole predicate. Dropping it takes the same search to a few
    milliseconds and loses nothing, since `%` already tolerates the accent, the
    hyphen and the letter you got wrong.

    A UID is matched as a prefix on the digits alone rather than as a
    substring, for the same reason: `CHE-103.895.771`, `CHE103895771` and
    `103895771` are the same identifier typed three ways, and comparing the
    normalised form is both correct and indexable.

    Results are ordered by similarity when there is a query. Without it the
    list came back alphabetically among unscored companies, so searching for
    "Bois-Gentil" put "BOIS RIL SA" first.
    """
    where = ["TRUE"]
    params: list[Any] = []
    order = "f.score DESC NULLS LAST, c.name"
    clause_stage = STAGE_FILTERS.get(stage, "")
    if clause_stage:
        where.append(clause_stage)
    if q:
        digits = "".join(ch for ch in q if ch.isdigit())
        if len(digits) >= 6:
            where.append("regexp_replace(c.uid, '[^0-9]', '', 'g') LIKE %s")
            params.append(f"{digits}%")
        else:
            where.append("c.name %% %s")
            params.append(q)
            order = "similarity(c.name, %s) DESC, f.score DESC NULLS LAST, c.name"
    if canton:
        where.append("c.canton = %s")
        params.append(canton)
    if segment:
        where.append(
            "EXISTS (SELECT 1 FROM intel.segment s"
            " WHERE s.company_id = c.id AND s.segment = %s)"
        )
        params.append(segment)
    if sector:
        where.append("sec.sector = %s")
        params.append(sector)
    if band:
        where.append("f.band = %s")
        params.append(band)
    if state:
        where.append("p.state = %s")
        params.append(state)
    clause = " AND ".join(where)
    rows = conn.execute(
        f"""
        SELECT c.id, c.uid, c.name, c.canton,
               (SELECT COALESCE(json_agg(s.segment ORDER BY s.segment), '[]'::json)
                  FROM intel.segment s WHERE s.company_id = c.id) AS segments,
               sec.sector,
               f.score, f.band, f.source, w.url, p.state,
               -- The progress stamps, as five booleans rather than five round
               -- trips: this list is 200 rows and a query each would be a
               -- thousand.
               c.hydrated_at IS NOT NULL AS hydrated,
               EXISTS (SELECT 1 FROM intel.segment s WHERE s.company_id = c.id) AS classified,
               w.company_id IS NOT NULL AS researched,
               f.company_id IS NOT NULL AS scored,
               w.reached_at IS NOT NULL AS reached
          {_LIST_JOINS}
         WHERE {clause}
         ORDER BY {order}
         LIMIT %s
        """,
        params + ([q] if "similarity(" in order else []) + [limit],
    ).fetchall()
    if not with_total:
        # The count repeats all four joins over the whole table, and on a
        # trigram search of two or three characters that is half the cost of
        # the call. The command palette shows six rows and never prints a
        # total, so it asks for None rather than paying for a number it drops.
        return rows, None
    total = conn.execute(
        f"SELECT COUNT(*) AS n {_LIST_JOINS} WHERE {clause}", params
    ).fetchone()["n"]
    return rows, total


def cantons_present(conn: Connection) -> list[str]:
    """The cantons the survey holds companies in, as 26 index lookups.

    `SELECT DISTINCT canton` reads all 598'036 rows to find 26 values, because
    Postgres has no loose index scan of its own. Walking `company_canton_idx`
    with a recursive query asks it for one minimum at a time and costs 0.6 ms
    against 28, which matters because five pages call this on every load.
    """
    return [
        row["canton"]
        for row in conn.execute(
            """
            WITH RECURSIVE step AS (
                SELECT min(canton) AS canton
                  FROM core.company WHERE canton IS NOT NULL
                UNION ALL
                SELECT (SELECT min(c.canton) FROM core.company c
                         WHERE c.canton > step.canton AND c.canton IS NOT NULL)
                  FROM step WHERE step.canton IS NOT NULL
            )
            SELECT canton FROM step WHERE canton IS NOT NULL ORDER BY canton
            """
        )
    ]


def counts_by_legal_form(conn: Connection) -> dict[str, int]:
    return {
        str(row["legal_form_uid"]): row["n"]
        for row in conn.execute(
            "SELECT legal_form_uid, COUNT(*) AS n FROM core.company"
            " WHERE legal_form_uid IS NOT NULL GROUP BY 1"
        )
    }


def scored_count(conn: Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM intel.fit").fetchone()["n"]


def mark_stale(conn: Connection, company_id: int, mutation_date: str | None) -> None:
    """Flag a company for re-hydration, without resetting an older flag.

    stale_since keeps the moment we first learned the local copy was behind, so
    a company named in five notices before anyone runs hydrate is one piece of
    work rather than five, and the oldest debt stays visible.
    """
    conn.execute(
        """
        UPDATE core.company
           SET stale_since = COALESCE(stale_since, %s::timestamptz),
               last_mutation_date = GREATEST(last_mutation_date, %s::date)
         WHERE id = %s
        """,
        (now(), mutation_date, company_id),
    )


def clear_stale(conn: Connection, company_id: int) -> None:
    conn.execute(
        "UPDATE core.company SET stale_since = NULL WHERE id = %s", (company_id,)
    )


# --- the change feed and the sweep --------------------------------------------


def synced_days(conn: Connection) -> set[str]:
    """The FOSC days already consumed. What makes a re-run free."""
    return {
        row["sogc_date"]
        for row in conn.execute("SELECT sogc_date FROM intel.sync_day")
    }


def last_synced_day(conn: Connection) -> str | None:
    row = conn.execute("SELECT MAX(sogc_date) AS d FROM intel.sync_day").fetchone()
    return row["d"] if row and row["d"] else None


def record_sync_day(conn: Connection, date: str, counts: dict[str, int]) -> None:
    """Mark a day consumed. Rewriting an existing day is deliberate: a range
    re-run must converge on the same numbers rather than accumulate them."""
    conn.execute(
        """
        INSERT INTO intel.sync_day (
            sogc_date, notices, tracked, inserted, updated, deletions, fetched_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (sogc_date) DO UPDATE SET
            notices = excluded.notices, tracked = excluded.tracked,
            inserted = excluded.inserted, updated = excluded.updated,
            deletions = excluded.deletions, fetched_at = excluded.fetched_at
        """,
        (
            date, counts.get("notices", 0), counts.get("tracked", 0),
            counts.get("inserted", 0), counts.get("updated", 0),
            counts.get("deletions", 0), now(),
        ),
    )


def record_sweep(
    conn: Connection, canton: str, legal_form_uid: str, prefix: str,
    result_count: int, deepened: bool = False,
) -> None:
    conn.execute(
        """
        INSERT INTO intel.sweep (canton, legal_form_uid, prefix, result_count,
                                 deepened, completed_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (canton, legal_form_uid, prefix) DO UPDATE SET
            result_count = excluded.result_count,
            deepened = excluded.deepened,
            completed_at = excluded.completed_at
        """,
        (canton, legal_form_uid, prefix, result_count, bool(deepened), now()),
    )


def completed_prefixes(
    conn: Connection, canton: str, legal_form_uid: str
) -> set[str]:
    """Prefixes already fully covered, so a resumed sweep can skip them.

    A deepened prefix is not complete in itself: its children hold the rows, and
    the resume logic has to walk into it again to check they all finished.
    """
    rows = conn.execute(
        "SELECT prefix FROM intel.sweep"
        " WHERE canton = %s AND legal_form_uid = %s AND deepened = FALSE",
        (canton, legal_form_uid),
    ).fetchall()
    return {row["prefix"] for row in rows}


# --- segments and scores ------------------------------------------------------


def save_segments(
    conn: Connection, company_id: int, segments: list[dict[str, Any]], model: str
) -> None:
    """Store the classifier's verdict, without touching a human's.

    The WHERE on the conflict clause is the whole point. Without it, correcting
    a label in the console and re-running `profile` would silently restore the
    model's answer, which makes the correction feel like work and be worthless.
    A person who has looked at a company knows more than one sentence of
    registered purpose does.
    """
    stamp = now()
    for item in segments:
        conn.execute(
            """
            INSERT INTO intel.segment (company_id, segment, confidence, rationale,
                                       model, classified_at, source)
            VALUES (%s, %s, %s, %s, %s, %s, 'model')
            ON CONFLICT (company_id, segment) DO UPDATE SET
                confidence = excluded.confidence,
                rationale = excluded.rationale,
                model = excluded.model,
                classified_at = excluded.classified_at
            WHERE segment.source <> 'manual'
            """,
            (company_id, item["segment"], item.get("confidence"),
             item.get("rationale"), model, stamp),
        )


def set_segment_manually(
    conn: Connection, company_id: int, segment: str, *, present: bool = True,
    rationale: str | None = None, author: str | None = None,
) -> None:
    """Add or remove a label by hand, and mark it as a person's doing.

    Removal deletes the row rather than storing a negative, because a label
    that is not there is the same fact however it came to be absent.
    """
    if not present:
        conn.execute(
            "DELETE FROM intel.segment WHERE company_id = %s AND segment = %s",
            (company_id, segment),
        )
        return
    conn.execute(
        """
        INSERT INTO intel.segment (company_id, segment, confidence, rationale,
                                   model, classified_at, source)
        VALUES (%s, %s, 1.0, %s, %s, %s, 'manual')
        ON CONFLICT (company_id, segment) DO UPDATE SET
            confidence = 1.0, rationale = excluded.rationale,
            model = excluded.model, classified_at = excluded.classified_at,
            source = 'manual'
        """,
        (company_id, segment, rationale, author or "manual", now()),
    )


def segments_for(conn: Connection, company_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM intel.segment WHERE company_id = %s ORDER BY segment",
        (company_id,),
    ).fetchall()


# --- sector -------------------------------------------------------------------
#
# One row per company rather than many, which is the whole difference from
# segments: a company fits several campaigns and works in one trade.


def save_sector(
    conn: Connection, company_id: int, sector: str, *,
    confidence: float | None = None, rationale: str | None = None,
    model: str = "",
) -> None:
    """Store the classifier's sector, without touching a human's.

    The WHERE on the conflict clause does the same job it does in
    `save_segments`: a sector corrected in the console must survive the stage
    running again, or correcting it is work that the next run silently undoes.
    """
    conn.execute(
        """
        INSERT INTO intel.sector (company_id, sector, confidence, rationale,
                                  model, classified_at, source)
        VALUES (%s, %s, %s, %s, %s, %s, 'model')
        ON CONFLICT (company_id) DO UPDATE SET
            sector = excluded.sector,
            confidence = excluded.confidence,
            rationale = excluded.rationale,
            model = excluded.model,
            classified_at = excluded.classified_at
        WHERE sector.source <> 'manual'
        """,
        (company_id, sector, confidence, rationale, model, now()),
    )


def set_sector_manually(
    conn: Connection, company_id: int, sector: str, *,
    rationale: str | None = None, author: str | None = None,
) -> None:
    """Set the sector by hand, and mark it as a person's doing.

    An empty sector clears the row rather than storing a blank, because "not
    classified" and "classified as nothing" are the same fact and one of them
    would need the reader to know which.
    """
    if not sector:
        conn.execute("DELETE FROM intel.sector WHERE company_id = %s", (company_id,))
        return
    conn.execute(
        """
        INSERT INTO intel.sector (company_id, sector, confidence, rationale,
                                  model, classified_at, source)
        VALUES (%s, %s, 1.0, %s, %s, %s, 'manual')
        ON CONFLICT (company_id) DO UPDATE SET
            sector = excluded.sector, confidence = 1.0,
            rationale = excluded.rationale, model = excluded.model,
            classified_at = excluded.classified_at, source = 'manual'
        """,
        (company_id, sector, rationale, author or "manual", now()),
    )


def sector_for(conn: Connection, company_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM intel.sector WHERE company_id = %s", (company_id,)
    ).fetchone()


def sector_counts(conn: Connection) -> list[Row]:
    """How the survey divides by trade, commonest first."""
    return conn.execute(
        "SELECT sector, COUNT(*) AS n,"
        " COUNT(*) FILTER (WHERE source = 'manual') AS by_hand"
        " FROM intel.sector GROUP BY sector ORDER BY n DESC, sector"
    ).fetchall()


def save_fit(conn: Connection, company_id: int, row: dict[str, Any]) -> None:
    """Store a fit score, without overwriting one a person set.

    Same guard as save_segments, for the same reason: a score corrected by hand
    must survive the stage running again, or correcting it is wasted effort and
    the console is a display rather than a tool.
    """
    def blank_to_null(value: Any) -> Any:
        return None if value in (None, [], "") else json_param(value)

    conn.execute(
        """
        INSERT INTO intel.fit (company_id, score, band, reasoning, advice, against,
                               matched, source, model, brief_hash, site_hash,
                               scored_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'model', %s, %s, %s, %s)
        ON CONFLICT (company_id) DO UPDATE SET
            score = excluded.score, band = excluded.band,
            reasoning = excluded.reasoning, advice = excluded.advice,
            against = excluded.against, matched = excluded.matched,
            model = excluded.model, brief_hash = excluded.brief_hash,
            site_hash = excluded.site_hash, scored_at = excluded.scored_at
        WHERE fit.source <> 'manual'
        """,
        (
            company_id, int(row.get("score") or 0), row.get("band") or "low",
            row.get("reasoning"), row.get("advice"),
            blank_to_null(row.get("against")), blank_to_null(row.get("matched")),
            row.get("model"), row.get("brief_hash"), row.get("site_hash"), now(),
        ),
    )


def set_fit_manually(
    conn: Connection, company_id: int, *, score: int, band: str,
    reasoning: str | None = None, author: str | None = None,
) -> None:
    """Override a score by hand. Marked manual, so no run undoes it."""
    conn.execute(
        """
        INSERT INTO intel.fit (company_id, score, band, reasoning, source, model,
                               scored_at)
        VALUES (%s, %s, %s, %s, 'manual', %s, %s)
        ON CONFLICT (company_id) DO UPDATE SET
            score = excluded.score, band = excluded.band,
            reasoning = excluded.reasoning, source = 'manual',
            model = excluded.model, scored_at = excluded.scored_at
        """,
        (company_id, int(score), band, reasoning, author or "manual", now()),
    )


def fit_for(conn: Connection, company_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM intel.fit WHERE company_id = %s", (company_id,)
    ).fetchone()


# --- the follow-up log --------------------------------------------------------


def add_note(
    conn: Connection, company_id: int, body: str, *, kind: str = "note",
    author: str | None = None, occurred_at: str | None = None,
) -> int:
    """Record something that happened, or worth remembering.

    Never deduplicated and never updated in place. Two calls to the same company
    on the same day are two facts, and a note that turned out to be wrong is
    still what was believed at the time.
    """
    stamp = now()
    row = conn.execute(
        "INSERT INTO intel.note (company_id, kind, body, author, occurred_at,"
        " created_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (company_id, kind, body, author, occurred_at or stamp, stamp),
    ).fetchone()
    return int(row["id"])


def notes_for(conn: Connection, company_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM intel.note WHERE company_id = %s"
        " ORDER BY occurred_at DESC, id DESC",
        (company_id,),
    ).fetchall()


# States a campaign moves a company through. Not a database constraint: a
# recruiting pipeline and a membership drive want different words, and the
# console offers this list rather than enforcing it.
PROSPECT_STATES = (
    "new", "researching", "contacted", "replied", "meeting", "won", "lost", "parked",
)


def set_prospect(
    conn: Connection, company_id: int, *, state: str | None = None,
    owner: str | None = None, next_action: str | None = None,
    next_at: str | None = None, by: str | None = None,
) -> None:
    """Create or update where a company stands.

    Only the fields given are written, so setting a next action does not
    silently reset the state, which is the mistake a single UPDATE with
    defaults makes on every partial edit from a form.

    Two statements rather than one upsert. An upsert must supply a value for
    the NOT NULL state column on the insert path, and that value then arrives
    in `excluded` on the update path indistinguishable from one the caller
    asked for: COALESCE(excluded.state, ...) would read the default 'new' as a
    deliberate reset, moving a company at "meeting" back to the start.
    """
    stamp = now()
    before = conn.execute(
        "SELECT state FROM intel.prospect WHERE company_id = %s", (company_id,)
    ).fetchone()
    conn.execute(
        "INSERT INTO intel.prospect (company_id, state, created_at, updated_at)"
        " VALUES (%s, %s, %s, %s) ON CONFLICT (company_id) DO NOTHING",
        (company_id, state or "new", stamp, stamp),
    )
    # Arriving on the board is itself a transition. Without this the first
    # state a company is ever given has no date, and "how long has it been
    # sitting in new" is unanswerable for exactly the cards it is asked of.
    if before is None:
        record_state_change(
            conn, entity="prospect", entity_id=company_id, company_id=company_id,
            from_state=None, to_state=state or "new", by=by,
        )
    elif state is not None:
        record_state_change(
            conn, entity="prospect", entity_id=company_id, company_id=company_id,
            from_state=before["state"], to_state=state, by=by,
        )
    sets, params = [], []
    for column, value in (
        ("state", state), ("owner", owner),
        ("next_action", next_action), ("next_at", next_at),
    ):
        if value is not None:
            sets.append(f"{column} = %s")
            params.append(value)
    if not sets:
        return
    sets.append("updated_at = %s")
    params.extend([stamp, company_id])
    conn.execute(
        f"UPDATE intel.prospect SET {', '.join(sets)} WHERE company_id = %s",
        params,
    )
    # Leaving 'new' is the moment a person picked this company out of a ranked
    # list, and it is the only moment at which where it stood in that list can
    # be known. Recorded once, never updated: see `record_selection`.
    if state is not None and state != "new":
        record_selection(conn, company_id, by=by)


def prospect_for(conn: Connection, company_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM intel.prospect WHERE company_id = %s", (company_id,)
    ).fetchone()


# --- what moved, and when -----------------------------------------------------
#
# A status is a column, and a change to it is an overwrite. These functions are
# the record of the overwriting. See migrations/0007_audit.sql for why the
# table names its subject as text and holds no foreign key.


def record_state_change(
    conn: Connection, *, entity: str, entity_id: int, to_state: str,
    from_state: str | None = None, company_id: int | None = None,
    by: str | None = None, reason: str | None = None,
) -> None:
    """Note that something moved. Nothing is recorded when nothing moved.

    The guard matters more than it looks: a form that posts every field on
    every save re-sends the state it already had, and without this the history
    would fill with transitions from `contacted` to `contacted` and the one
    real move would be unfindable among them.
    """
    if from_state is not None and from_state == to_state:
        return
    conn.execute(
        "INSERT INTO audit.state_change (entity, entity_id, company_id,"
        " from_state, to_state, by, reason, at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (entity, entity_id, company_id, from_state, to_state, by, reason, now()),
    )


def state_history(
    conn: Connection, entity: str, entity_id: int, *, limit: int = 50
) -> list[Row]:
    return conn.execute(
        "SELECT * FROM audit.state_change WHERE entity = %s AND entity_id = %s"
        " ORDER BY at DESC, id DESC LIMIT %s",
        (entity, entity_id, limit),
    ).fetchall()


def history_for_company(conn: Connection, company_id: int, *, limit: int = 50) -> list[Row]:
    """Everything that moved for one company, whatever table it moved in.

    The reason `company_id` is on the audit row at all: a company's history
    crosses the prospect, the client and the project tables, and reading it
    should not require knowing which of them it passed through.
    """
    return conn.execute(
        "SELECT * FROM audit.state_change WHERE company_id = %s"
        " ORDER BY at DESC, id DESC LIMIT %s",
        (company_id, limit),
    ).fetchall()


def time_in_state(conn: Connection, entity: str, entity_ids: list[int]) -> dict[int, str]:
    """When each of these last moved, so a page can say how long it has sat.

    One query for a whole board. Asking per card is how a column of forty
    becomes forty round trips, and the number is only worth showing because it
    is cheap.
    """
    if not entity_ids:
        return {}
    return {
        int(row["entity_id"]): row["at"]
        for row in conn.execute(
            "SELECT DISTINCT ON (entity_id) entity_id, at FROM audit.state_change"
            " WHERE entity = %s AND entity_id = ANY(%s)"
            " ORDER BY entity_id, at DESC",
            (entity, list(entity_ids)),
        )
    }


# --- the board ----------------------------------------------------------------
#
# The working set, as opposed to the survey. 184'939 companies is a register;
# the few dozen being worked are a pipeline, and they are exactly the ones with
# a row in `intel.prospect`. Nothing arrives on the board by being scored well:
# it arrives because somebody put it there, which is what makes the column
# counts mean anything.


def board(
    conn: Connection, *, canton: str = "", segment: str = "", sector: str = "",
    band: str = "", owner: str = "", q: str = "", limit: int = 500,
) -> list[Row]:
    """Every company in the campaign, with what a card has to show.

    One query rather than one per card. A board of forty cards each fetching
    its own segments and its own address is forty round trips to draw one page,
    and it is the classic way a kanban view becomes the slowest screen in a
    CRM.
    """
    where = ["TRUE"]
    params: list[Any] = []
    if canton:
        where.append("c.canton = %s")
        params.append(canton)
    if segment:
        where.append(
            "EXISTS (SELECT 1 FROM intel.segment s"
            " WHERE s.company_id = c.id AND s.segment = %s)"
        )
        params.append(segment)
    if sector:
        where.append(
            "EXISTS (SELECT 1 FROM intel.sector x"
            " WHERE x.company_id = c.id AND x.sector = %s)"
        )
        params.append(sector)
    if band:
        where.append("f.band = %s")
        params.append(band)
    if owner:
        where.append("p.owner = %s")
        params.append(owner)
    if q:
        # A substring match, where the survey list uses trigram similarity.
        # The difference is the size of what is being searched: the survey
        # scans 184'939 rows and an `unaccent(...) LIKE` branch there measured
        # just over a second because no index can serve it. The board searches
        # the few dozen companies actually being worked, so it can afford the
        # match that finds "Bois" inside "Fondation Bois-Gentil" — which
        # similarity, over a long name, does not.
        where.append("unaccent(c.name) ILIKE unaccent(%s)")
        params.append(f"%{q}%")
    return conn.execute(
        f"""
        SELECT c.id, c.name, c.uid, c.canton, c.legal_form_label, c.status,
               p.state, p.owner, p.next_action, p.next_at, p.updated_at,
               f.score, f.band, w.url,
               (SELECT array_agg(s.segment ORDER BY s.segment)
                  FROM intel.segment s WHERE s.company_id = c.id) AS segments,
               (SELECT x.sector FROM intel.sector x
                 WHERE x.company_id = c.id) AS sector,
               EXISTS (SELECT 1 FROM core.contact ct
                        WHERE ct.company_id = c.id AND ct.kind = 'email'
                          AND NOT ct.suppressed) AS reachable
          FROM intel.prospect p
          JOIN core.company c ON c.id = p.company_id
          LEFT JOIN intel.fit f ON f.company_id = c.id
          LEFT JOIN intel.site w ON w.company_id = c.id
         WHERE {' AND '.join(where)}
         ORDER BY p.next_at NULLS LAST, f.score DESC NULLS LAST, c.name
         LIMIT %s
        """,
        params + [limit],
    ).fetchall()


def prospect_owners(conn: Connection) -> list[str]:
    return [
        row["owner"]
        for row in conn.execute(
            "SELECT DISTINCT owner FROM intel.prospect"
            " WHERE owner IS NOT NULL AND owner <> '' ORDER BY owner"
        )
    ]


def bring_in(
    conn: Connection, *, band: str = "high", segment: str = "", sector: str = "",
    canton: str = "", limit: int = 25,
) -> int:
    """Put the best-scoring companies not yet on the board into `new`.

    The step between a scored survey and a pipeline, done in bulk because doing
    it one company at a time is the reason shortlists sit unworked.

    It only ever inserts. A company already on the board keeps whatever state
    it has reached, so running this again after another sweep cannot walk
    something at "meeting" back to the start.
    """
    where = ["f.band = %s", "c.status = 'ACTIVE'",
             "NOT EXISTS (SELECT 1 FROM intel.prospect p WHERE p.company_id = c.id)"]
    params: list[Any] = [band]
    if segment:
        where.append(
            "EXISTS (SELECT 1 FROM intel.segment s"
            " WHERE s.company_id = c.id AND s.segment = %s)"
        )
        params.append(segment)
    if sector:
        where.append(
            "EXISTS (SELECT 1 FROM intel.sector x"
            " WHERE x.company_id = c.id AND x.sector = %s)"
        )
        params.append(sector)
    if canton:
        where.append("c.canton = %s")
        params.append(canton)
    stamp = now()
    cur = conn.execute(
        f"""
        INSERT INTO intel.prospect (company_id, state, created_at, updated_at)
        SELECT c.id, 'new', %s, %s
          FROM core.company c
          JOIN intel.fit f ON f.company_id = c.id
         WHERE {' AND '.join(where)}
         ORDER BY f.score DESC NULLS LAST
         LIMIT %s
        ON CONFLICT (company_id) DO NOTHING
        """,
        [stamp, stamp] + params + [limit],
    )
    return cur.rowcount


def remove_from_board(conn: Connection, company_id: int) -> None:
    """Take a company off the board without touching what is known about it.

    A prospect row is a working state, not a finding: the score, the research
    and the notes stay exactly where they are, and the company goes back to
    being one of the 184'939 in the register.
    """
    conn.execute("DELETE FROM intel.prospect WHERE company_id = %s", (company_id,))


# --- one human, across several companies ---------------------------------------
#
# `core.person` is what a notice said, where it said it. `core.party` is the
# human those rows are about. The resolution between them is the whole of the
# relationship graph's honesty, so the rule is stated once, here, and every
# party carries which branch of it produced them.


def sort_name(text: str) -> str:
    """The comparison form of a name: unaccented, lowercased, letters only.

    `Boccard Christian` and `BOCCARD Christian` are one person written twice;
    `Müller` and `Mueller` are not necessarily, and are deliberately left
    distinct. Folding further would start merging people to make the graph
    look better.
    """
    stripped = unicodedata.normalize("NFKD", text or "")
    ascii_only = "".join(c for c in stripped if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_only.lower()).split())


def resolve_parties(conn: Connection) -> dict[str, int]:
    """Build `core.party` and `core.party_role` from the officer rows.

    The rule, and why it errs where it does.

    Two officer rows are the same person when their names fold to the same
    string **and** their registered origins agree. The origin is what makes
    this possible: it is a legal attribute of a Swiss person, printed in the
    notice, and it does not change when they move house. The domicile does, so
    it is recorded and never keyed on.

    A row with no origin joins the others of that name only when exactly one
    origin is known among them. Two known origins under one name means two
    people, or a parse that went wrong, and a silent row cannot say which.

    Splitting when unsure is the deliberate direction. A person wrongly split
    is a missing edge, and a missing edge is a gap in a suggestion. A person
    wrongly merged is a false statement about a named individual in a tool
    that goes on to propose writing to them, and no later correction reaches
    the message that was already sent.

    Rebuilt whole each time rather than updated in place: the input is a few
    thousand rows, the rule will change, and a resolution that accumulated its
    own history would make every improvement a migration.
    """
    conn.execute("TRUNCATE core.party RESTART IDENTITY CASCADE")
    rows = conn.execute(
        "SELECT id, company_id, full_name, role, origin, domicile, signature,"
        "       first_seen_date, last_seen_date, left_date, source_url, method"
        "  FROM core.person ORDER BY full_name, company_id, id"
    ).fetchall()

    # Which origins are known for each folded name, so a silent row can be
    # attached only where there is exactly one candidate.
    origins: dict[str, set[str]] = {}
    for row in rows:
        key = sort_name(row["full_name"])
        if row["origin"]:
            origins.setdefault(key, set()).add(row["origin"].strip())
        else:
            origins.setdefault(key, set())

    # Group the officer rows into parties before writing any, so a party's
    # method can describe the group rather than whichever row arrived first.
    groups: dict[tuple[str, str, str], list[Row]] = {}
    for row in rows:
        key = sort_name(row["full_name"])
        known = origins.get(key, set())
        if row["origin"]:
            groups.setdefault((key, row["origin"].strip(), "name-and-origin"), []).append(row)
        elif len(known) == 1:
            groups.setdefault((key, next(iter(known)), "sole-origin"), []).append(row)
        else:
            groups.setdefault((key, "", "name-only"), []).append(row)

    # A silent row attached to the one known origin belongs with the rows that
    # stated it, not beside them: they are the same person and the merge is
    # the point. Folded together after grouping so the weaker method is
    # recorded on the party.
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for (key, origin, method), members in groups.items():
        target = merged.setdefault(
            (key, origin), {"rows": [], "methods": set()}
        )
        target["rows"].extend(members)
        target["methods"].add(method)

    stats = {"parties": 0, "roles": 0, "merged": 0, "uncertain": 0}
    for (key, origin), group in merged.items():
        members = group["rows"]
        companies = {row["company_id"] for row in members}
        if len(members) == 1:
            method, confidence = "single", 1.0
        elif "name-only" in group["methods"]:
            method, confidence = "name-only", 0.5
        elif "sole-origin" in group["methods"]:
            method, confidence = "sole-origin", 0.7
        else:
            method, confidence = "name-and-origin", 0.9

        # The display form is the commonest spelling, and the longest of those
        # when they tie: "de la Fontaine Marie" over "Fontaine Marie".
        spellings = Counter(row["full_name"] for row in members)
        best = max(spellings.items(), key=lambda item: (item[1], len(item[0])))[0]
        domicile = next(
            (row["domicile"] for row in reversed(members)
             if row["domicile"] and not row["left_date"]),
            next((row["domicile"] for row in reversed(members) if row["domicile"]),
                 None),
        )
        party = conn.execute(
            "INSERT INTO core.party (kind, full_name, sort_name, origin, domicile,"
            " method, confidence, created_at, updated_at)"
            " VALUES ('person', %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (best, key, origin or None, domicile, method, confidence, now(), now()),
        ).fetchone()
        stats["parties"] += 1
        if len(companies) > 1:
            stats["merged"] += 1
        if confidence < 0.9:
            stats["uncertain"] += 1

        for row in members:
            written = conn.execute(
                "INSERT INTO core.party_role (party_id, company_id, person_id,"
                " role, signature, valid_from, valid_to, source_url, method,"
                " created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (party_id, company_id, role) DO NOTHING"
                " RETURNING id",
                (party["id"], row["company_id"], row["id"], row["role"],
                 row["signature"], row["first_seen_date"], row["left_date"],
                 row["source_url"], row["method"] or "shab", now()),
            ).fetchone()
            if written:
                stats["roles"] += 1
    return stats


def party(conn: Connection, party_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM core.party WHERE id = %s", (party_id,)
    ).fetchone()


def parties(
    conn: Connection, *, q: str = "", min_boards: int = 1, limit: int = 200
) -> list[Row]:
    """People, with how many boards each sits on. Busiest first.

    The order is the useful one: a name on nine boards is a route into nine
    companies, and a name on one is a contact.
    """
    where = ["TRUE"]
    params: list[Any] = []
    if q:
        where.append("p.full_name %% %s")
        params.append(q)
    order = "boards DESC, p.full_name"
    if q:
        order = "similarity(p.full_name, %s) DESC, boards DESC"
        params_tail = [q]
    else:
        params_tail = []
    return conn.execute(
        f"""SELECT p.*, COUNT(DISTINCT r.company_id) AS boards
              FROM core.party p
              LEFT JOIN core.party_role r ON r.party_id = p.id
             WHERE {' AND '.join(where)}
             GROUP BY p.id
            HAVING COUNT(DISTINCT r.company_id) >= %s
             ORDER BY {order} LIMIT %s""",
        params + [min_boards] + params_tail + [limit],
    ).fetchall()


def roles_for_party(conn: Connection, party_id: int) -> list[Row]:
    return conn.execute(
        """SELECT r.*, c.name, c.uid, c.canton, c.status,
                  f.score, f.band, f.source, pr.state
             FROM core.party_role r
             JOIN core.company c ON c.id = r.company_id
             LEFT JOIN intel.fit f ON f.company_id = c.id
             LEFT JOIN intel.prospect pr ON pr.company_id = c.id
            WHERE r.party_id = %s
            ORDER BY r.valid_to NULLS FIRST, c.name""",
        (party_id,),
    ).fetchall()


def parties_for_company(conn: Connection, company_id: int) -> list[Row]:
    """The people named at this company, and how many boards each also sits on."""
    return conn.execute(
        """SELECT p.*, r.role, r.signature, r.valid_from, r.valid_to,
                  (SELECT COUNT(DISTINCT o.company_id) FROM core.party_role o
                    WHERE o.party_id = p.id) AS boards
             FROM core.party_role r
             JOIN core.party p ON p.id = r.party_id
            WHERE r.company_id = %s
            ORDER BY r.valid_to NULLS FIRST, boards DESC, p.full_name""",
        (company_id,),
    ).fetchall()


def board_overlap(conn: Connection, company_id: int, *, limit: int = 40) -> list[Row]:
    """Companies sharing an officer with this one, and who the officer is.

    The question the party table exists for. Asked of `core.person` it cannot
    be answered without matching names in the query, which is how two different
    people with one surname become a business relationship.
    """
    return conn.execute(
        """SELECT other.company_id, c.name, c.uid, c.canton,
                  f.score, f.band, f.source, pr.state,
                  array_agg(DISTINCT p.full_name ORDER BY p.full_name) AS people,
                  COUNT(DISTINCT p.id) AS shared,
                  -- Whether anyone shared is still in post at both ends. A
                  -- link through two people who have each since left is real
                  -- history and a poor introduction, and the two must not
                  -- look the same in a list.
                  bool_or(mine.valid_to IS NULL AND other.valid_to IS NULL) AS current
             FROM core.party_role mine
             JOIN core.party_role other
               ON other.party_id = mine.party_id AND other.company_id <> mine.company_id
             JOIN core.party p ON p.id = mine.party_id
             JOIN core.company c ON c.id = other.company_id
             LEFT JOIN intel.fit f ON f.company_id = c.id
             LEFT JOIN intel.prospect pr ON pr.company_id = c.id
            WHERE mine.company_id = %s
            GROUP BY other.company_id, c.name, c.uid, c.canton,
                     f.score, f.band, f.source, pr.state
            ORDER BY current DESC, shared DESC, f.score DESC NULLS LAST, c.name
            LIMIT %s""",
        (company_id, limit),
    ).fetchall()


def path_between(
    conn: Connection, start_id: int, target_id: int, *, max_hops: int = 4
) -> list[Row]:
    """The shortest chain of shared officers from one company to another.

    A recursive walk over company -> party -> company, stopping at the first
    depth that reaches the target. Four hops by default: beyond that a "warm
    introduction" is a chain of strangers, and the query grows as fast as the
    claim weakens.
    """
    return conn.execute(
        """
        WITH RECURSIVE walk(company_id, path, people, depth) AS (
            SELECT %s::bigint, ARRAY[%s::bigint], ARRAY[]::text[], 0
          -- UNION rather than UNION ALL. One person can hold two roles at the
          -- same company, and the join then produces the identical route
          -- twice; deduplicating in the walk keeps it out of the frontier as
          -- well as out of the answer.
          UNION
            SELECT other.company_id,
                   walk.path || other.company_id,
                   walk.people || p.full_name,
                   walk.depth + 1
              FROM walk
              JOIN core.party_role mine ON mine.company_id = walk.company_id
              JOIN core.party_role other
                ON other.party_id = mine.party_id
               AND other.company_id <> walk.company_id
              JOIN core.party p ON p.id = mine.party_id
             WHERE walk.depth < %s
               AND NOT other.company_id = ANY(walk.path)
               -- Stop expanding a branch that has already arrived. Without it
               -- the walk carries on past the answer and returns longer routes
               -- to the same place.
               AND walk.company_id <> %s
        )
        SELECT * FROM (
            SELECT DISTINCT ON (path) path, people, depth
              FROM walk WHERE company_id = %s ORDER BY path, depth
        ) routes ORDER BY depth, path LIMIT 5
        """,
        (start_id, start_id, max_hops, target_id, target_id),
    ).fetchall()


def same_name_parties(conn: Connection, party_id: int) -> list[Row]:
    """Other parties whose name folds the same, held apart by their origin.

    Offered rather than merged. Two of these turned up in the first live
    resolution and neither is a fault in the rule: `Macédoine` and `Macédoine
    du Nord` are one country renamed in 2019, and the commune of `Rossens` was
    absorbed into `Gibloux` in 2016. Both move a person's stated origin without
    moving the person. A rule loose enough to join them would also join two
    genuinely different people, so the split stands and a human is shown the
    candidates.
    """
    return conn.execute(
        """SELECT p.*, (SELECT COUNT(DISTINCT r.company_id) FROM core.party_role r
                         WHERE r.party_id = p.id) AS boards
             FROM core.party p
            WHERE p.sort_name = (SELECT sort_name FROM core.party WHERE id = %s)
              AND p.id <> %s
            ORDER BY p.full_name""",
        (party_id, party_id),
    ).fetchall()


def companies_named(conn: Connection, ids: list[int]) -> list[Row]:
    """Names for a set of company ids, for rendering a path."""
    if not ids:
        return []
    return conn.execute(
        "SELECT id, name FROM core.company WHERE id = ANY(%s)", (list(ids),)
    ).fetchall()


def party_counts(conn: Connection) -> dict[str, int]:
    row = conn.execute(
        """SELECT (SELECT COUNT(*) FROM core.party) AS parties,
                  (SELECT COUNT(*) FROM core.party_role) AS roles,
                  (SELECT COUNT(*) FROM (
                       SELECT party_id FROM core.party_role
                        GROUP BY party_id HAVING COUNT(DISTINCT company_id) > 1
                   ) x) AS connectors,
                  (SELECT COUNT(*) FROM core.person) AS people"""
    ).fetchone()
    return {k: int(v) for k, v in row.items()}


# --- notices and officers -----------------------------------------------------


def save_publications(
    conn: Connection, company_id: int, publications: list[dict[str, Any]]
) -> int:
    """Store the register's own notices, keyed on the SOGC id.

    Immutable once filed, so a conflict means we have seen it before and there
    is nothing to update.
    """
    written = 0
    stamp = now()
    for item in publications:
        row = conn.execute(
            """
            INSERT INTO core.publication (company_id, sogc_id, sogc_date, journal_id,
                                          journal_date, canton, message, source_url,
                                          method, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_id, sogc_id) DO NOTHING
            RETURNING id
            """,
            (
                company_id, item.get("sogc_id"), item.get("sogc_date"),
                item.get("journal_id"), item.get("journal_date"),
                item.get("canton"), item.get("message") or "",
                item.get("source_url"), item.get("method", "zefix-sogc"), stamp,
            ),
        ).fetchone()
        if row:
            written += 1
    return written


def publications_for(conn: Connection, company_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM core.publication WHERE company_id = %s"
        " ORDER BY sogc_date DESC",
        (company_id,),
    ).fetchall()


def prune_people(
    conn: Connection, company_id: int, keep: list[tuple[str, str | None]]
) -> int:
    """Drop parsed officers this company's notices no longer yield.

    The people stage was written on the principle that the parser will improve
    and stored notices can be reparsed for free. That only holds if a reparse
    can also take a row *away*, and an upsert cannot: it refreshes what it
    finds and leaves everything else untouched, so a name the parser should
    never have produced survives every correction.

    It cost something to discover. A regex was reading the domicile in "Aslan
    Arzu, de Turquie, à Carouge GE" as a second officer, and `Carouge GE`
    accumulated across the survey onto twenty-three boards: the exact shape of
    a well-connected person, and the most valuable node in any relationship
    graph built on it. Fixing the regex left every one of those rows in place.

    Only rows the parser owns. `method` says where a row came from, and a
    correction made by a person is not the parser's to remove.
    """
    # Two parallel arrays rather than a list of tuples. A list of tuples is the
    # obvious way to write it and Postgres refuses it: "input of anonymous
    # composite types is not implemented". Unnested side by side they are the
    # same comparison in one statement, instead of one DELETE per officer.
    cur = conn.execute(
        """
        DELETE FROM core.person
         WHERE company_id = %s AND method = 'shab'
           AND NOT EXISTS (
               SELECT 1 FROM unnest(%s::text[], %s::text[]) AS kept(name, role)
                WHERE kept.name = person.full_name
                  AND kept.role = COALESCE(person.role, ''))
        """,
        (
            company_id,
            [name for name, _role in keep],
            [role or "" for _name, role in keep],
        ),
    )
    return cur.rowcount


def save_people(
    conn: Connection, company_id: int, people: list[dict[str, Any]],
    *, authoritative: bool = False,
) -> int:
    """Store officers parsed from notices.

    Keyed on (company_id, full_name, role), so re-running the parser refreshes
    attributes rather than duplicating a person. Attributes are overwritten only
    when the incoming row carries them, so a notice naming someone without
    repeating their origin cannot blank it.

    `authoritative` turns that off, and a reparse sets it. The merge rule is
    right for one notice arriving and wrong for a corrected parser: a regex was
    giving one officer the *next* officer's origin, and because the fixed
    parser answers NULL where it used to answer wrongly, COALESCE kept the
    wrong value for ever. A reparse reads every notice a company has and merges
    them itself, so its answer is the whole answer and may overwrite.
    """
    merge = (
        """
            origin = excluded.origin,
            domicile = excluded.domicile,
            signature = excluded.signature,
        """
        if authoritative
        else """
            origin = COALESCE(excluded.origin, person.origin),
            domicile = COALESCE(excluded.domicile, person.domicile),
            signature = COALESCE(excluded.signature, person.signature),
        """
    )
    stamp = now()
    written = 0
    for item in people:
        conn.execute(
            f"""
            INSERT INTO core.person (company_id, full_name, role, origin, domicile,
                                     signature, first_seen_date, last_seen_date,
                                     left_date, source_url, method, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_id, full_name, role) DO UPDATE SET
                {merge}
                -- The oldest notice naming them wins, and the newest says how
                -- current it is. LEAST and GREATEST rather than COALESCE:
                -- these are the ends of a span and a later parse of an older
                -- notice must be able to push the start back.
                first_seen_date = LEAST(excluded.first_seen_date,
                                        person.first_seen_date),
                last_seen_date = GREATEST(excluded.last_seen_date,
                                          person.last_seen_date),
                left_date = excluded.left_date,
                source_url = COALESCE(excluded.source_url, person.source_url),
                fetched_at = excluded.fetched_at
            """,
            (
                company_id, item["full_name"], item.get("role"), item.get("origin"),
                item.get("domicile"), item.get("signature"),
                item.get("first_seen_date"), item.get("last_seen_date"),
                item.get("left_date"), item.get("source_url"),
                item.get("method", "shab"), stamp,
            ),
        )
        written += 1
    return written


def people_for(
    conn: Connection, company_id: int, *, include_past: bool = False
) -> list[Row]:
    """Officers at this company. Current ones unless asked otherwise.

    The default is what every caller that addresses a human wants. Recording
    someone who left is worse than missing them: a draft would be addressed to
    the wrong person with the federal register cited as the authority, so the
    departed have to be asked for by name.
    """
    where = "" if include_past else " AND left_date IS NULL"
    return conn.execute(
        f"SELECT * FROM core.person WHERE company_id = %s{where}"
        " ORDER BY left_date NULLS FIRST, role",
        (company_id,),
    ).fetchall()


# --- websites and addresses ---------------------------------------------------


def save_site(conn: Connection, company_id: int, row: dict[str, Any]) -> None:
    """Store what the web said about a company. One row per company.

    Re-enriching replaces the row wholesale rather than merging: unlike a
    registry fact, a stale offering list is not worth preserving next to a
    fresh one, and a company that has redesigned its site should not carry both
    readings.
    """
    def blank_to_null(value: Any) -> Any:
        return None if value in (None, [], "") else json_param(value)

    conn.execute(
        """
        INSERT INTO intel.site (
            company_id, status, url, confidence, language, summary, offerings,
            signals, partnerships, tech_stack, size_hint, restricted,
            source_url, method, fetched_at, model, searches, fetches
        ) VALUES (
            %(company_id)s, %(status)s, %(url)s, %(confidence)s, %(language)s,
            %(summary)s, %(offerings)s, %(signals)s, %(partnerships)s,
            %(tech_stack)s, %(size_hint)s, %(restricted)s, %(source_url)s,
            %(method)s, %(fetched_at)s, %(model)s, %(searches)s, %(fetches)s
        )
        ON CONFLICT (company_id) DO UPDATE SET
            status = excluded.status, url = excluded.url,
            confidence = excluded.confidence, language = excluded.language,
            summary = excluded.summary, offerings = excluded.offerings,
            signals = excluded.signals, partnerships = excluded.partnerships,
            tech_stack = excluded.tech_stack, size_hint = excluded.size_hint,
            restricted = excluded.restricted, source_url = excluded.source_url,
            method = excluded.method, fetched_at = excluded.fetched_at,
            model = excluded.model, searches = excluded.searches,
            fetches = excluded.fetches,
            -- Having looked for an address survives a re-reading of the same
            -- site and does not survive a different one. Clearing it
            -- unconditionally would make every enrich --refresh re-fetch pages
            -- whose answer had not changed; never clearing it would leave a
            -- company pointing at a new domain nobody had opened.
            reached_at = CASE WHEN site.url IS NOT DISTINCT FROM excluded.url
                              THEN site.reached_at ELSE NULL END,
            reach_pages = CASE WHEN site.url IS NOT DISTINCT FROM excluded.url
                               THEN site.reach_pages ELSE NULL END
        """,
        {
            "company_id": company_id,
            "status": row["status"],
            "url": row.get("url"),
            "confidence": row.get("confidence"),
            "language": row.get("language"),
            "summary": row.get("summary"),
            "offerings": blank_to_null(row.get("offerings")),
            "signals": blank_to_null(row.get("signals")),
            "partnerships": blank_to_null(row.get("partnerships")),
            "tech_stack": blank_to_null(row.get("tech_stack")),
            "size_hint": row.get("size_hint"),
            "restricted": blank_to_null(row.get("restricted")),
            "source_url": row.get("source_url"),
            "method": row.get("method"),
            "fetched_at": now(),
            "model": row.get("model"),
            "searches": row.get("searches"),
            "fetches": row.get("fetches"),
        },
    )
    # Digested from the stored row, not the payload, for the same reason
    # upsert_company does: what a later reader sees must be what was hashed.
    stored = conn.execute(
        "SELECT * FROM intel.site WHERE company_id = %s", (company_id,)
    ).fetchone()
    conn.execute(
        "UPDATE intel.site SET content_hash = %s WHERE company_id = %s",
        (site_digest(stored), company_id),
    )


# Columns of `site` that may inform a judgement but must never be quoted back to
# the company.
RESTRICTED_SITE_COLUMNS = frozenset({"restricted"})


def site_for(conn: Connection, company_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM intel.site WHERE company_id = %s", (company_id,)
    ).fetchone()


def site_for_draft(conn: Connection, company_id: int) -> dict[str, Any] | None:
    """The web research, with everything unquotable removed.

    The single accessor any outward-facing path must use: drafting, exporting,
    anything that puts words in front of the company. Reading the table directly
    is the mistake this exists to make hard, so the filtering lives here rather
    than in each caller's SELECT, where the next caller would forget it.
    """
    row = site_for(conn, company_id)
    if row is None:
        return None
    return {k: v for k, v in row.items() if k not in RESTRICTED_SITE_COLUMNS}


def save_contact(
    conn: Connection, *, company_id: int, kind: str, value: str,
    person_id: int | None = None, source_url: str | None = None,
    method: str | None = None, status: str | None = None,
    suppressed: bool = False,
) -> int:
    """Store an address. Returns 1 when it is new, 0 when already held.

    Existence is checked before the write rather than inferred from the
    statement afterwards: an upsert that conflicts reports nothing reliable
    about whether the row was there, and the count is what a run reports.

    `suppressed` applies on insert only. It is the caller's judgement — this
    store has no opinion about which mailboxes are private, and acquiring one
    would mean importing the address vocabulary from `sources`, which is a
    dependency the module rules forbid. A row an operator later un-suppresses
    by hand stays that way: a sweep must not undo a decision it cannot see.
    """
    known = conn.execute(
        "SELECT 1 FROM core.contact WHERE company_id = %s AND kind = %s AND value = %s",
        (company_id, kind, value),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO core.contact (person_id, company_id, kind, value, status,
                                  source_url, method, fetched_at, suppressed)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (company_id, kind, value) DO UPDATE SET
            status = COALESCE(excluded.status, contact.status),
            source_url = COALESCE(excluded.source_url, contact.source_url),
            method = COALESCE(excluded.method, contact.method),
            fetched_at = excluded.fetched_at
        """,
        (person_id, company_id, kind, value, status, source_url, method, now(),
         suppressed),
    )
    return 0 if known else 1


def domains_to_check(
    conn: Connection, *, stale_after_days: int = 30, refresh: bool = False
) -> list[str]:
    """The distinct mail domains whose verdict is missing or old.

    One row per domain rather than per address, because the check is a DNS
    lookup and DNS answers about a domain: 12'111 addresses in this bank sit at
    5'139 domains, so asking per address would be two and a half times the
    queries for the same answers.
    """
    if refresh:
        return [
            r["domain"] for r in conn.execute(
                "SELECT DISTINCT lower(split_part(value, '@', 2)) AS domain"
                "  FROM core.contact WHERE kind = 'email' ORDER BY 1"
            )
        ]
    return [
        r["domain"] for r in conn.execute(
            """
            SELECT DISTINCT lower(split_part(value, '@', 2)) AS domain
              FROM core.contact
             WHERE kind = 'email'
               AND (status IS NULL OR status_at IS NULL
                    OR status_at < now() - make_interval(days => %s))
             ORDER BY 1
            """,
            (stale_after_days,),
        )
    ]


# A verdict reached by trying the mailbox, which a verdict about the domain
# must not overwrite. Held here as names rather than imported, because the
# vocabulary belongs to `sources` and `scout` may not depend on it; the store's
# only interest is that these outrank a DNS answer.
MAILBOX_VERDICTS = ("bounced",)


def save_domain_verdict(
    conn: Connection, domain: str, *, status: str, note: str,
    outranked_by: tuple[str, ...] = MAILBOX_VERDICTS,
) -> int:
    """Write one domain's verdict to every address at it. Returns the count.

    The verdict is about the domain, so it is stored on each address at that
    domain rather than in a table of its own. A domain table would be the
    normalised shape and would also be a second place for an address to exist,
    which is how a send list and a verdict list come to disagree.

    WHAT IT WILL NOT TOUCH. An address that bounced is skipped. A domain
    resolving says nothing about a mailbox at it, so a DNS sweep run after a
    send would otherwise mark `info@live-domain.ch` deliverable again on the
    strength of the MX record that was never in doubt, erasing the one answer
    that cost a delivery attempt to get.
    """
    done = conn.execute(
        """
        UPDATE core.contact
           SET status = %s, status_note = %s, status_at = %s
         WHERE kind = 'email' AND lower(split_part(value, '@', 2)) = %s
           AND (status IS NULL OR NOT (status = ANY(%s)))
        """,
        (status, note, now(), domain.strip().lower(), list(outranked_by)),
    )
    return int(getattr(done, "rowcount", 0) or 0)


def save_mailbox_verdict(
    conn: Connection, address: str, *, status: str, note: str
) -> int:
    """Write a verdict about one mailbox. Returns 1 when a row was held.

    Scoped to the address and not the domain, which is the whole difference
    from `save_domain_verdict`: `info@x.ch` failing says nothing about
    `location@x.ch`, and a régie commonly runs one and not the other.
    """
    done = conn.execute(
        """
        UPDATE core.contact
           SET status = %s, status_note = %s, status_at = %s
         WHERE kind = 'email' AND lower(value) = %s
        """,
        (status, note, now(), address.strip().lower()),
    )
    return int(getattr(done, "rowcount", 0) or 0)


def addresses_held(conn: Connection) -> frozenset[str]:
    """Every email address the survey holds, for matching a report against.

    A delivery report quotes the operator's own sending address and whatever
    list plumbing sits in the headers. Without this set those get recorded as
    bounced.
    """
    return frozenset(
        r["value"] for r in conn.execute(
            "SELECT lower(value) AS value FROM core.contact WHERE kind = 'email'"
        )
    )


def send_list(
    conn: Connection, *, desk_parts: tuple[str, ...],
    segments: list[str] | None = None, cantons: list[str] | None = None,
    statuses: tuple[str, ...] = ("deliverable",),
) -> list[Row]:
    """One address per firm, the desk address where there is one.

    Ordering decides which address a firm contributes, and it is the whole
    point of the query: a desk address is preferred over a named person's,
    because writing to `info@` about a product is a different act in law from
    writing to an employee by name. A firm with only a named mailbox still
    appears, so the caller can see the count and decide, rather than having it
    silently dropped.

    `desk_parts` is passed in for the reason `save_contact` gives for
    `suppressed`: knowing which local parts are desks would mean importing the
    address vocabulary from `sources`, and `scout` may not depend on it. The
    caller owns that list and this query only orders by it.

    `desk_local` reports whether the chosen address's local part was in that
    list, which is a fact about the ordering and not the verdict. Whether a
    mailbox reaches a desk or a person is `mailcheck.mailbox_kind`, which needs
    the company name as well: `actimmo@actimmo-ge.ch` is in no vocabulary and
    is still a desk.
    """
    where = ["k.kind = 'email'", "NOT k.suppressed", "k.status = ANY(%s)",
             "c.status = 'ACTIVE'"]
    params: list[object] = [list(statuses)]
    join = ""
    if segments:
        join = ("JOIN intel.segment g ON g.company_id = c.id"
                " AND g.segment = ANY(%s)")
        params.append(list(segments))
    if cantons:
        where.append("c.canton = ANY(%s)")
        params.append([x.strip().upper() for x in cantons])
    return conn.execute(
        f"""
        SELECT DISTINCT ON (c.id)
               c.id AS company_id, c.uid, c.name, c.canton, c.legal_seat,
               k.value AS address, k.method, k.status, k.source_url,
               split_part(k.value, '@', 1) = ANY(%s) AS desk_local
          FROM core.contact k
          JOIN core.company c ON c.id = k.company_id
          {join}
         WHERE {' AND '.join(where)}
         ORDER BY c.id,
                  (split_part(k.value, '@', 1) = ANY(%s)) DESC,
                  (k.method = 'published') DESC,
                  length(k.value), k.value
        """,
        (list(desk_parts), *params, list(desk_parts)),
    ).fetchall()


# --- drafts awaiting a person ------------------------------------------------
#
# `intel.draft` holds proposals. Three timestamps carry the whole state and no
# status column exists on purpose: `created_at` says it was written,
# `reviewed_at` that somebody read it, `sent_at` that it left. A draft with
# none of the last two is the queue.


def drafts_awaiting(
    conn: Connection, *, limit: int = 100, language: str = "",
    reviewed: bool | None = False,
) -> list[Row]:
    """The review queue, oldest first.

    Oldest first because a draft goes stale: the brief it was written against
    can be rewritten under it, and `brief_digest` is what says so. Reviewing
    newest-first leaves the stale ones at the bottom for ever.
    """
    where = ["d.sent_at IS NULL"]
    params: list[Any] = []
    if reviewed is True:
        where.append("d.reviewed_at IS NOT NULL")
    elif reviewed is False:
        where.append("d.reviewed_at IS NULL")
    if language:
        where.append("d.language = %s")
        params.append(language)
    params.append(int(limit))
    return conn.execute(
        f"""
        SELECT d.*, c.name AS company_name, c.uid, c.canton, c.legal_seat
          FROM intel.draft d
          JOIN core.company c ON c.id = d.company_id
         WHERE {' AND '.join(where)}
         ORDER BY d.created_at
         LIMIT %s
        """,
        tuple(params),
    ).fetchall()


def draft(conn: Connection, draft_id: int) -> Row | None:
    return conn.execute(
        "SELECT d.*, c.name AS company_name, c.uid, c.canton, c.legal_seat,"
        "       c.purpose"
        "  FROM intel.draft d JOIN core.company c ON c.id = d.company_id"
        " WHERE d.id = %s",
        (draft_id,),
    ).fetchone()


def draft_tally(conn: Connection) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT count(*) FILTER (WHERE sent_at IS NULL AND reviewed_at IS NULL)
                   AS awaiting,
               count(*) FILTER (WHERE sent_at IS NULL AND reviewed_at IS NOT NULL)
                   AS approved,
               count(*) FILTER (WHERE sent_at IS NOT NULL) AS sent,
               count(DISTINCT brief_digest) AS briefs
          FROM intel.draft
        """
    ).fetchone()
    return {k: int(row[k] or 0) for k in ("awaiting", "approved", "sent", "briefs")}


def stale_drafts(conn: Connection, digest: str) -> int:
    """How many unsent drafts were written against a different brief.

    The number `match` makes visible for scores and this makes visible for
    messages. A draft and the brief it quotes can disagree without either
    saying so, and the digest is the only thing that catches it.
    """
    row = conn.execute(
        "SELECT count(*) AS n FROM intel.draft"
        " WHERE sent_at IS NULL AND coalesce(brief_digest, '') <> %s",
        (digest,),
    ).fetchone()
    return int(row["n"] or 0)


def review_draft(conn: Connection, draft_id: int) -> Row | None:
    """Record that a person read it. Not that it was sent."""
    return conn.execute(
        "UPDATE intel.draft SET reviewed_at = %s WHERE id = %s RETURNING *",
        (now(), draft_id),
    ).fetchone()


def unreview_draft(conn: Connection, draft_id: int) -> Row | None:
    return conn.execute(
        "UPDATE intel.draft SET reviewed_at = NULL WHERE id = %s RETURNING *",
        (draft_id,),
    ).fetchone()


def mark_draft_sent(conn: Connection, draft_id: int) -> Row | None:
    """Record that it left, which only a person can know.

    Nothing in this program sends mail, so `sent_at` is set by the operator
    after their own client sent it. It is also what retires the row from the
    one-per-mailbox index, so a later approach to the same firm is a new
    proposal rather than an edit of this one.
    """
    return conn.execute(
        "UPDATE intel.draft SET sent_at = %s, reviewed_at ="
        " coalesce(reviewed_at, %s) WHERE id = %s RETURNING *",
        (now(), now(), draft_id),
    ).fetchone()


def drop_draft(conn: Connection, draft_id: int) -> int:
    done = conn.execute("DELETE FROM intel.draft WHERE id = %s", (draft_id,))
    return int(getattr(done, "rowcount", 0) or 0)


def contacts_for(conn: Connection, company_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM core.contact WHERE company_id = %s ORDER BY id",
        (company_id,),
    ).fetchall()


def contact(conn: Connection, contact_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM core.contact WHERE id = %s", (contact_id,)
    ).fetchone()


def toggle_suppressed(conn: Connection, contact_id: int) -> Row | None:
    """Turn "do not write here" on or off.

    Honoured by the draft stage and never cleared by a later run that finds the
    address published again: a person said no, and rediscovering the address is
    not consent.
    """
    return conn.execute(
        "UPDATE core.contact SET suppressed = NOT suppressed WHERE id = %s"
        " RETURNING *",
        (contact_id,),
    ).fetchone()


def mark_reached(conn: Connection, company_id: int, pages: int) -> None:
    """Record that a company's own site was read for an address."""
    conn.execute(
        "UPDATE intel.site SET reached_at = %s, reach_pages = %s WHERE company_id = %s",
        (now(), pages, company_id),
    )


# --- the address hunt -----------------------------------------------------------


def save_address_hunt(conn: Connection, company_id: int, hunt: Any) -> None:
    """Record how far the cascade got, including when it got nowhere.

    Upserted on the company, because a hunt is the current state of the
    looking rather than a log of every attempt: a later run that finds an
    address supersedes the one that did not, and keeping both would leave two
    rows disagreeing about whether the company is reachable.
    """
    conn.execute(
        "INSERT INTO intel.address_hunt (company_id, outcome, tier, domain,"
        " addresses, method, pages, searches, tried, note, hunted_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (company_id) DO UPDATE SET"
        " outcome = EXCLUDED.outcome, tier = EXCLUDED.tier,"
        " domain = EXCLUDED.domain, addresses = EXCLUDED.addresses,"
        " method = EXCLUDED.method, pages = EXCLUDED.pages,"
        " searches = EXCLUDED.searches, tried = EXCLUDED.tried,"
        " note = EXCLUDED.note, hunted_at = EXCLUDED.hunted_at",
        (company_id, hunt.outcome, hunt.tier, hunt.domain,
         list(hunt.addresses), hunt.method or None, hunt.pages, hunt.searches,
         json_param(hunt.tried or None), hunt.note or None, now()),
    )


def address_hunt(conn: Connection, company_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM intel.address_hunt WHERE company_id = %s", (company_id,)
    ).fetchone()


def hunt_counts(conn: Connection) -> dict[str, int]:
    return {
        str(row["outcome"]): int(row["n"])
        for row in conn.execute(
            "SELECT outcome, COUNT(*) AS n FROM intel.address_hunt GROUP BY outcome"
        )
    }


# --- runs ---------------------------------------------------------------------


def start_run(
    conn: Connection, stage: str, args: dict[str, Any], *, started_by: str = "cli"
) -> int:
    row = conn.execute(
        "INSERT INTO intel.run (stage, args_json, started_at, started_by)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (stage, json_param({k: v for k, v in args.items() if k != "func"}),
         now(), started_by),
    ).fetchone()
    conn.commit()
    return int(row["id"])


def append_line(conn: Connection, run_id: int, text: str) -> int:
    """One line of a run's log, numbered within the run.

    The sequence is computed from the table rather than held in memory because
    the writer is a worker thread and the reader is a request; a counter in the
    process would be right until the second console tab.
    """
    row = conn.execute(
        "INSERT INTO intel.run_line (run_id, seq, at, text)"
        " SELECT %s, COALESCE(MAX(seq), 0) + 1, %s, %s FROM intel.run_line"
        " WHERE run_id = %s RETURNING seq",
        (run_id, now(), text, run_id),
    ).fetchone()
    conn.commit()
    return int(row["seq"])


def lines_after(conn: Connection, run_id: int, after: int = 0) -> list[Row]:
    return conn.execute(
        "SELECT * FROM intel.run_line WHERE run_id = %s AND seq > %s ORDER BY seq",
        (run_id, after),
    ).fetchall()


def finish_run(
    conn: Connection, run_id: int, stats: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        "UPDATE intel.run SET finished_at = %s, stats_json = %s, error = %s"
        " WHERE id = %s",
        (now(), json_param(stats or {}), error, run_id),
    )
    conn.commit()


def runs(conn: Connection, limit: int = 40) -> list[Row]:
    return conn.execute(
        "SELECT * FROM intel.run ORDER BY id DESC LIMIT %s", (limit,)
    ).fetchall()


def run(conn: Connection, run_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM intel.run WHERE id = %s", (run_id,)
    ).fetchone()


def close_orphans(conn: Connection, started_by: str = "console") -> int:
    """Settle runs that a killed process left claiming to be running.

    Console runs only: a run started from a terminal may genuinely still be
    going in another window, and marking it dead would be a lie.
    """
    rows = conn.execute(
        "UPDATE intel.run SET finished_at = %s,"
        " error = COALESCE(error, 'interrupted: the console stopped')"
        " WHERE finished_at IS NULL AND started_by = %s RETURNING id",
        (now(), started_by),
    ).fetchall()
    conn.commit()
    return len(rows)


# --- operator settings --------------------------------------------------------

SETTABLE = ("cantons", "legal_forms", "statuses", "admit_new", "segments",
            "sectors", "signal_brief", "rate_card")


def settings_overlay(conn: Connection) -> dict[str, Any]:
    """Everything an operator has changed, ready to apply over the file."""
    return {
        row["key"]: row["value_json"]
        for row in conn.execute("SELECT key, value_json FROM intel.setting")
        if row["key"] in SETTABLE
    }


def save_setting(
    conn: Connection, key: str, value: Any, *, by: str = "console"
) -> None:
    if key not in SETTABLE:
        raise ValueError(f"{key!r} is not an operator setting")
    conn.execute(
        "INSERT INTO intel.setting (key, value_json, updated_at, updated_by)"
        " VALUES (%s, %s, %s, %s)"
        " ON CONFLICT (key) DO UPDATE SET value_json = excluded.value_json,"
        " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (key, json_param(value), now(), by),
    )


def setting_history(conn: Connection) -> list[Row]:
    return conn.execute(
        "SELECT key, updated_at, updated_by FROM intel.setting"
        " ORDER BY updated_at DESC"
    ).fetchall()


# The register's legal form catalogue, cached so a settings page can offer it
# without a network call on every render. Not a setting: it is a copy of
# something the register owns, written by a refresh rather than by a person,
# and deliberately outside SETTABLE.
CATALOGUE_KEY = "_legal_form_catalogue"


def save_catalogue(conn: Connection, forms: list[dict[str, Any]]) -> None:
    conn.execute(
        "INSERT INTO intel.setting (key, value_json, updated_at, updated_by)"
        " VALUES (%s, %s, %s, 'register')"
        " ON CONFLICT (key) DO UPDATE SET value_json = excluded.value_json,"
        " updated_at = excluded.updated_at",
        (CATALOGUE_KEY, json_param(forms), now()),
    )
    conn.commit()


def catalogue(conn: Connection) -> list[dict[str, Any]]:
    row = conn.execute(
        "SELECT value_json FROM intel.setting WHERE key = %s", (CATALOGUE_KEY,)
    ).fetchone()
    return row["value_json"] if row else []


# --- evidence that accumulates ---------------------------------------------------


def save_source_document(conn: Connection, row: dict[str, Any]) -> int:
    """Record where a claim came from. The same document twice is one row."""
    found = conn.execute(
        "SELECT id FROM intel.source_document"
        " WHERE company_id IS NOT DISTINCT FROM %s AND kind = %s"
        "   AND url IS NOT DISTINCT FROM %s"
        "   AND content_sha256 IS NOT DISTINCT FROM %s",
        (row.get("company_id"), row["kind"], row.get("url"),
         row.get("content_sha256")),
    ).fetchone()
    if found:
        return int(found["id"])
    made = conn.execute(
        "INSERT INTO intel.source_document (company_id, kind, url, title,"
        " content_sha256, licence, fetched_at, method, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (row.get("company_id"), row["kind"], row.get("url"), row.get("title"),
         row.get("content_sha256"), row.get("licence"), row.get("fetched_at"),
         row.get("method"), now()),
    ).fetchone()
    return int(made["id"])


def observe(conn: Connection, company_id: int, row: dict[str, Any]) -> int:
    """Record one claim. Never overwrites another.

    Two sources disagreeing is the case this exists for, so a second claim
    about the same attribute is a second row. Superseding is a separate,
    deliberate act: see `supersede`.
    """
    made = conn.execute(
        "INSERT INTO intel.observation (company_id, attribute, value, value_num,"
        " confidence, source_document_id, parser_version, method, observed_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (company_id, row["attribute"], row.get("value"), row.get("value_num"),
         row.get("confidence"), row.get("source_document_id"),
         row.get("parser_version"), row.get("method"), row.get("observed_at") or now()),
    ).fetchone()
    return int(made["id"])


def supersede(conn: Connection, observation_id: int, *, by: int | None = None) -> None:
    """Mark a claim as no longer believed, without destroying it.

    That it was once believed is itself a fact, and a deleted row is one a
    re-run writes again next week.
    """
    conn.execute(
        "UPDATE intel.observation SET superseded_at = %s, superseded_by = %s"
        " WHERE id = %s AND superseded_at IS NULL",
        (now(), by, observation_id),
    )


def observations_for(
    conn: Connection, company_id: int, *, attribute: str = "",
    include_superseded: bool = False,
) -> list[Row]:
    clauses = ["o.company_id = %s"]
    params: list[Any] = [company_id]
    if attribute:
        # A prefix, so `site` fetches `site.signal` and `site.summary` alike.
        clauses.append("(o.attribute = %s OR o.attribute LIKE %s)")
        params += [attribute, f"{attribute}.%"]
    if not include_superseded:
        clauses.append("o.superseded_at IS NULL")
    return conn.execute(
        "SELECT o.*, d.url, d.kind AS source_kind, d.licence"
        "  FROM intel.observation o"
        "  LEFT JOIN intel.source_document d ON d.id = o.source_document_id"
        f" WHERE {' AND '.join(clauses)}"
        " ORDER BY o.attribute, o.confidence DESC NULLS LAST, o.observed_at DESC",
        tuple(params),
    ).fetchall()


def disagreements(conn: Connection, company_id: int) -> list[Row]:
    """Attributes where two live observations say different things.

    The whole reason the table accumulates. A reader that only ever sees the
    winning claim cannot tell a settled fact from a contested one, and the
    contested ones are where the interesting questions are.
    """
    return conn.execute(
        "SELECT attribute, COUNT(DISTINCT value) AS answers, COUNT(*) AS claims"
        "  FROM intel.observation"
        " WHERE company_id = %s AND superseded_at IS NULL AND value IS NOT NULL"
        " GROUP BY attribute HAVING COUNT(DISTINCT value) > 1"
        " ORDER BY attribute",
        (company_id,),
    ).fetchall()


# --- weights and scores -----------------------------------------------------------


def save_weight_set(
    conn: Connection, name: str, weights: dict[str, Any], *, note: str = "",
    activate: bool = True,
) -> int:
    """A new version of a weight set, never an edit of one in use.

    Re-weighting a campaign must not rewrite what last month's ranking was, and
    a score run cites the set it used, so the set has to still exist and still
    say what it said.
    """
    from . import scoring

    highest = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM intel.weight_set WHERE name = %s",
        (name,),
    ).fetchone()
    made = conn.execute(
        "INSERT INTO intel.weight_set (name, version, note, active, created_at)"
        " VALUES (%s, %s, %s, FALSE, %s) RETURNING id",
        (name, int(highest["v"]) + 1, note or None, now()),
    ).fetchone()
    set_id = int(made["id"])
    for component, weight in scoring.normalise(weights).items():
        conn.execute(
            "INSERT INTO intel.weight (weight_set_id, component, weight)"
            " VALUES (%s, %s, %s)",
            (set_id, component, weight),
        )
    if activate:
        activate_weight_set(conn, set_id)
    return set_id


def activate_weight_set(conn: Connection, weight_set_id: int) -> None:
    conn.execute("UPDATE intel.weight_set SET active = FALSE WHERE active")
    conn.execute(
        "UPDATE intel.weight_set SET active = TRUE WHERE id = %s", (weight_set_id,)
    )


def active_weights(conn: Connection) -> tuple[int | None, dict[str, Any]]:
    """The weights in force, or the default with no set behind it.

    Falling back rather than refusing: a survey that has never opened a weight
    set should still be able to score, and the default says in its own name
    that it is a starting point.
    """
    from . import scoring

    found = conn.execute(
        "SELECT id FROM intel.weight_set WHERE active LIMIT 1"
    ).fetchone()
    if found is None:
        return None, dict(scoring.DEFAULT_WEIGHTS)
    rows = conn.execute(
        "SELECT component, weight FROM intel.weight WHERE weight_set_id = %s",
        (found["id"],),
    ).fetchall()
    return int(found["id"]), {r["component"]: r["weight"] for r in rows}


def weight_sets(conn: Connection) -> list[Row]:
    return conn.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM intel.weight w"
        "              WHERE w.weight_set_id = s.id) AS components"
        "  FROM intel.weight_set s ORDER BY s.name, s.version DESC"
    ).fetchall()


def save_score_run(conn: Connection, company_id: int, row: dict[str, Any]) -> int:
    """Record a scoring with its components and their evidence.

    Also projects the total onto `intel.fit`, which every list and filter in
    the console still ranks by. The projection honours the manual guard exactly
    as before: a score somebody set by hand is not replaced by a run.
    """
    made = conn.execute(
        "INSERT INTO intel.score_run (company_id, weight_set_id, total, band,"
        " source, model, brief_hash, site_hash, reasoning, advice, author,"
        " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING id",
        (company_id, row.get("weight_set_id"), row.get("total"), row.get("band"),
         row.get("source", "model"), row.get("model"), row.get("brief_hash"),
         row.get("site_hash"), row.get("reasoning"), row.get("advice"),
         row.get("author"), now()),
    ).fetchone()
    run_id = int(made["id"])

    for part in row.get("components") or []:
        component_id = conn.execute(
            "INSERT INTO intel.score_component (score_run_id, component, score,"
            " weight, origin, reasoning) VALUES (%s, %s, %s, %s, %s, %s)"
            " RETURNING id",
            (run_id, part["component"], part.get("score"), part.get("weight"),
             part.get("origin", "model"), part.get("reasoning")),
        ).fetchone()
        for item in part.get("evidence") or []:
            statement = item if isinstance(item, str) else item.get("statement")
            if not statement:
                continue
            direction = "for" if isinstance(item, str) else item.get("direction", "for")
            conn.execute(
                "INSERT INTO intel.score_evidence (score_component_id, direction,"
                " statement, observation_id, source_document_id)"
                " VALUES (%s, %s, %s, %s, %s)",
                (int(component_id["id"]), direction, statement,
                 None if isinstance(item, str) else item.get("observation_id"),
                 None if isinstance(item, str) else item.get("source_document_id")),
            )

    save_fit(conn, company_id, {
        "score": int(round(float(row.get("total") or 0))),
        "band": row.get("band") or "low",
        "reasoning": row.get("reasoning"),
        "advice": row.get("advice"),
        "against": row.get("against"),
        "matched": row.get("matched"),
        "model": row.get("model"),
        "brief_hash": row.get("brief_hash"),
        "site_hash": row.get("site_hash"),
    })
    return run_id


def score_run(conn: Connection, run_id: int) -> Row | None:
    return conn.execute(
        "SELECT r.*, s.name AS weight_set, s.version AS weight_version"
        "  FROM intel.score_run r"
        "  LEFT JOIN intel.weight_set s ON s.id = r.weight_set_id"
        " WHERE r.id = %s",
        (run_id,),
    ).fetchone()


def latest_score(conn: Connection, company_id: int) -> Row | None:
    return conn.execute(
        "SELECT r.*, s.name AS weight_set, s.version AS weight_version"
        "  FROM intel.score_run r"
        "  LEFT JOIN intel.weight_set s ON s.id = r.weight_set_id"
        " WHERE r.company_id = %s ORDER BY r.created_at DESC, r.id DESC LIMIT 1",
        (company_id,),
    ).fetchone()


def score_history(conn: Connection, company_id: int, *, limit: int = 20) -> list[Row]:
    return conn.execute(
        "SELECT * FROM intel.score_run WHERE company_id = %s"
        " ORDER BY created_at DESC, id DESC LIMIT %s",
        (company_id, limit),
    ).fetchall()


def components_of(conn: Connection, run_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM intel.score_component WHERE score_run_id = %s"
        " ORDER BY weight DESC NULLS LAST, component",
        (run_id,),
    ).fetchall()


def evidence_of(conn: Connection, component_id: int) -> list[Row]:
    return conn.execute(
        "SELECT e.*, d.url FROM intel.score_evidence e"
        "  LEFT JOIN intel.source_document d ON d.id = e.source_document_id"
        " WHERE e.score_component_id = %s ORDER BY e.direction DESC, e.id",
        (component_id,),
    ).fetchall()


def score_with_parts(conn: Connection, company_id: int) -> dict[str, Any] | None:
    """The newest run, its components, and each component's evidence.

    One call because every page that shows a score wants all three, and three
    round trips per company on a list of forty is how a page becomes slow for
    no reason a reader could name.
    """
    run = latest_score(conn, company_id)
    if run is None:
        return None
    parts = []
    for component in components_of(conn, int(run["id"])):
        parts.append({
            "row": component,
            "evidence": evidence_of(conn, int(component["id"])),
        })
    return {"run": run, "components": parts}


def record_selection(
    conn: Connection, company_id: int, *, by: str | None = None
) -> dict[str, Any] | None:
    """Write down where this company stood in the ranking when it was picked.

    Called when a prospect first moves out of 'new', which is the moment a
    person chose it out of a list. See the migration for why: outcomes are only
    ever observed for what was approached, and what was approached was chosen
    by score, so anything later learned from those outcomes is learned from the
    system's own choices unless the propensity at selection is kept.

    Written once and never updated. The point is what was true at the moment of
    the decision, and a later scoring is a different fact.
    """
    already = conn.execute(
        "SELECT selected_at FROM intel.prospect WHERE company_id = %s",
        (company_id,),
    ).fetchone()
    if already is not None and already["selected_at"] is not None:
        return None
    standing = conn.execute(
        "SELECT f.score, f.band,"
        "       (SELECT COUNT(*) FROM intel.fit) AS scored,"
        "       (SELECT COUNT(*) FROM intel.fit o WHERE o.score <= f.score) AS below"
        "  FROM intel.fit f WHERE f.company_id = %s",
        (company_id,),
    ).fetchone()
    if standing is None or not standing["scored"]:
        return None
    percentile = round(float(standing["below"]) / float(standing["scored"]), 4)
    conn.execute(
        "UPDATE intel.prospect SET selection_p = %s, selection_score = %s,"
        " selection_band = %s, selected_at = %s WHERE company_id = %s",
        (percentile, standing["score"], standing["band"], now(), company_id),
    )
    return {"selection_p": percentile, "score": standing["score"],
            "band": standing["band"]}
