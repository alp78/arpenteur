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
string that has to be json.loads-ed. Writers wrap with `db.json_param`, which
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
    "LEFT JOIN intel.prospect p ON p.company_id = c.id"
)


def search_companies(
    conn: Connection, *, q: str = "", canton: str = "", segment: str = "",
    band: str = "", state: str = "", stage: str = "", limit: int = 200,
) -> tuple[list[Row], int]:
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
    total = conn.execute(
        f"SELECT COUNT(*) AS n {_LIST_JOINS} WHERE {clause}", params
    ).fetchone()["n"]
    return rows, total


def cantons_present(conn: Connection) -> list[str]:
    return [
        row["canton"]
        for row in conn.execute(
            "SELECT DISTINCT canton FROM core.company"
            " WHERE canton IS NOT NULL ORDER BY canton"
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
    next_at: str | None = None,
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
    conn.execute(
        "INSERT INTO intel.prospect (company_id, state, created_at, updated_at)"
        " VALUES (%s, %s, %s, %s) ON CONFLICT (company_id) DO NOTHING",
        (company_id, state or "new", stamp, stamp),
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


def prospect_for(conn: Connection, company_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM intel.prospect WHERE company_id = %s", (company_id,)
    ).fetchone()


# --- the board ----------------------------------------------------------------
#
# The working set, as opposed to the survey. 184'939 companies is a register;
# the few dozen being worked are a pipeline, and they are exactly the ones with
# a row in `intel.prospect`. Nothing arrives on the board by being scored well:
# it arrives because somebody put it there, which is what makes the column
# counts mean anything.


def board(
    conn: Connection, *, canton: str = "", segment: str = "", band: str = "",
    owner: str = "", q: str = "", limit: int = 500,
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
    conn: Connection, *, band: str = "high", segment: str = "", canton: str = "",
    limit: int = 25,
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
                                     signature, last_seen_date, source_url, method,
                                     fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_id, full_name, role) DO UPDATE SET
                {merge}
                last_seen_date = GREATEST(excluded.last_seen_date,
                                          person.last_seen_date),
                source_url = COALESCE(excluded.source_url, person.source_url),
                fetched_at = excluded.fetched_at
            """,
            (
                company_id, item["full_name"], item.get("role"), item.get("origin"),
                item.get("domicile"), item.get("signature"),
                item.get("last_seen_date"), item.get("source_url"),
                item.get("method", "shab"), stamp,
            ),
        )
        written += 1
    return written


def people_for(conn: Connection, company_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM core.person WHERE company_id = %s ORDER BY role",
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
) -> int:
    """Store an address. Returns 1 when it is new, 0 when already held.

    Existence is checked before the write rather than inferred from the
    statement afterwards: an upsert that conflicts reports nothing reliable
    about whether the row was there, and the count is what a run reports.
    """
    known = conn.execute(
        "SELECT 1 FROM core.contact WHERE company_id = %s AND kind = %s AND value = %s",
        (company_id, kind, value),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO core.contact (person_id, company_id, kind, value, status,
                                  source_url, method, fetched_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (company_id, kind, value) DO UPDATE SET
            status = COALESCE(excluded.status, contact.status),
            source_url = COALESCE(excluded.source_url, contact.source_url),
            method = COALESCE(excluded.method, contact.method),
            fetched_at = excluded.fetched_at
        """,
        (person_id, company_id, kind, value, status, source_url, method, now()),
    )
    return 0 if known else 1


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
            "signal_brief", "rate_card")


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
