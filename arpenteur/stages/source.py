"""The source stage: fill the company table from public registers.

Two passes, deliberately separate. The sweep is cheap and wide (one search
per name prefix, many companies per response); hydration is one request per
company and only runs for rows that still lack a purpose. Splitting them
means an interrupted run never has to redo the wide pass to get the narrow
one, and a re-sweep months later only hydrates what is genuinely new.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Callable

from .. import db
from ..config import Settings
from ..http import PoliteClient
from ..sources.zefix import ZefixClient, walk_prefixes
from ..sources.zefix_fixture import (
    DEFAULT_CANTONS as FIXTURE_CANTONS,
    DEFAULT_LEGAL_FORMS as FIXTURE_LEGAL_FORMS,
    FixtureZefixClient,
)


# Zefix grants free REST access on conditions, and one of them is a request:
# renounce regular mass querying. The reason given is concrete. All three
# access routes (web app, REST, mobile) share one system, the web app alone
# serves over 400'000 requests a day, and a user who disrupts it degrades the
# service for everyone; the stated remedy is to block that user's access.
#
# Hydration is one request per company, so an uncapped pass over a completed
# sweep is a six-figure burst: exactly that shape. Past this many rows the
# caller has to ask for it deliberately, with --limit or --bulk. The sweep
# itself needs no such guard, being a few hundred requests for the whole of
# Romandie.
POLITE_HYDRATION_LIMIT = 2000


def _client(settings: Settings) -> PoliteClient:
    zefix = settings.zefix
    if not zefix.has_credentials:
        raise RuntimeError(
            "Zefix credentials missing. They are free: email zefix@bj.admin.ch, "
            "then set ZEFIX_USER and ZEFIX_PASSWORD in .env (see .env.example)."
        )
    return PoliteClient(
        contact=zefix.contact,
        rate_limit=zefix.rate_limit,
        max_retries=zefix.max_retries,
        cache_dir=settings.cache_dir,
        auth=(zefix.user or "", zefix.password or ""),
    )


def run_zefix(
    settings: Settings,
    conn: sqlite3.Connection,
    *,
    cantons: list[str] | None = None,
    legal_forms: list[str] | None = None,
    resume: bool = True,
    hydrate: bool = True,
    limit: int | None = None,
    bulk: bool = False,
    fixture: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Sweep a territory, then hydrate what it found.

    The client is chosen here and injected below, which is what lets the same
    sweep run against the live registry or against the recorded fixture set
    without a branch in the middle of the loop.
    """
    if fixture:
        return _run_zefix(
            settings,
            conn,
            zefix=FixtureZefixClient(),
            cantons=cantons or list(FIXTURE_CANTONS),
            wanted_forms=legal_forms or list(FIXTURE_LEGAL_FORMS),
            resume=resume,
            hydrate=hydrate,
            limit=limit,
            bulk=bulk,
            log=log,
        )

    with _client(settings) as http:
        return _run_zefix(
            settings,
            conn,
            zefix=ZefixClient(http, settings.zefix.base_url),
            cantons=cantons or settings.cantons,
            wanted_forms=legal_forms or settings.legal_forms,
            resume=resume,
            hydrate=hydrate,
            limit=limit,
            bulk=bulk,
            log=log,
        )


def _run_zefix(
    settings: Settings,
    conn: sqlite3.Connection,
    *,
    zefix: ZefixClient | FixtureZefixClient,
    cantons: list[str],
    wanted_forms: list[str],
    resume: bool,
    hydrate: bool,
    limit: int | None,
    bulk: bool,
    log: Callable[[str], None],
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "searched": 0,
        "seen": 0,
        "hydrated": 0,
        "refused": 0,
        "cantons": cantons,
    }

    index = zefix.legal_form_index()
    forms: list[dict[str, Any]] = []
    for name in wanted_forms:
        entry = index.get(name.strip().lower())
        if not entry:
            log(f"  ! unknown legal form {name!r}, skipped")
            continue
        forms.append(entry)
    if not forms:
        raise RuntimeError(
            "No legal form resolved. Check survey.legal_forms in "
            "arpenteur.toml against the registry catalogue."
        )

    for canton in cantons:
        for form in forms:
            form_uid = str(form["uid"])
            label = form["short"] or form["name"] or form_uid
            done = db.completed_prefixes(conn, canton, form_uid) if resume else set()
            if done:
                log(f"{canton}/{label}: resuming, {len(done)} prefixes already done")

            def search(pattern: str, _form: dict[str, Any] = form) -> list[Any]:
                stats["searched"] += 1
                return zefix.search(
                    name=pattern, canton=canton, legal_form_id=_form["id"]
                )

            for prefix, companies, refused in walk_prefixes(
                search, skip=done, log=log
            ):
                for company in companies:
                    if not company.uid:
                        continue
                    company.canton = company.canton or canton
                    company.legal_form_uid = company.legal_form_uid or form_uid
                    company.legal_form_label = company.legal_form_label or label
                    db.upsert_company(conn, company.as_row())
                    stats["seen"] += 1
                if refused:
                    stats["refused"] += 1
                db.record_sweep(
                    conn, canton, form_uid, prefix, len(companies), refused
                )
                conn.commit()
                log(
                    f"{canton}/{label} {prefix}*: {len(companies)}"
                    + (" (too large, narrowing)" if refused else "")
                )

    if hydrate:
        stats["hydrated"] = hydrate_pending(
            settings, conn, zefix=zefix, limit=limit, bulk=bulk, log=log
        )

    return stats


def hydrate_pending(
    settings: Settings,
    conn: sqlite3.Connection,
    *,
    zefix: ZefixClient | FixtureZefixClient | None = None,
    cantons: list[str] | None = None,
    legal_forms: list[str] | None = None,
    limit: int | None = None,
    bulk: bool = False,
    fixture: bool = False,
    log: Callable[[str], None] = print,
) -> int:
    """Fetch full detail for companies never hydrated, or gone stale since.

    Filtered rather than first-come, because the register is swept far wider
    than it is worked. `purpose` only arrives with this call, so a territory
    cannot be classified until it is hydrated, and hydrating by id order would
    spend the whole polite budget on whichever canton happens to sort first.

    Two kinds of work arrive here. A company the sweep found but never fetched
    in detail, and a company the sync stage marked stale because a FOSC notice
    said it changed. The second is served first: a company whose purpose was
    amended last week is more wrong than one that has never been read at all.
    """
    where = ["(hydrated_at IS NULL OR stale_since IS NOT NULL)"]
    params: list[Any] = []
    if cantons:
        where.append(f"canton IN ({','.join('?' * len(cantons))})")
        params.extend(cantons)
    if legal_forms:
        where.append(f"legal_form_uid IN ({','.join('?' * len(legal_forms))})")
        params.extend(legal_forms)
    sql = (
        f"SELECT uid, stale_since FROM company WHERE {' AND '.join(where)} "
        "ORDER BY stale_since IS NULL, last_mutation_date DESC, id"
    )
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return 0

    if limit is None and not bulk and len(rows) > POLITE_HYDRATION_LIMIT:
        raise RuntimeError(
            f"{len(rows)} companies await hydration, one request each. Zefix "
            f"asks users to avoid regular mass querying and reserves the right "
            f"to block accounts that disturb the service. Pass --limit to take "
            f"a slice, or --bulk to accept a run of this size deliberately."
        )

    owns_client = zefix is None and not fixture
    http = _client(settings) if owns_client else None
    client = zefix or (
        FixtureZefixClient()
        if fixture
        else ZefixClient(http, settings.zefix.base_url)  # type: ignore[arg-type]
    )
    count = 0
    try:
        for index, row in enumerate(rows, 1):
            # A stale company must actually re-ask. Its detail is already on
            # disk from the fetch that made it stale, and the cache would
            # hand back exactly the copy the notice told us was out of date.
            stale = row["stale_since"] is not None
            full = client.company(row["uid"], max_age=0 if stale else None)
            if full:
                company_id = db.upsert_company(conn, full.as_row())
                # The SHAB notices arrive on the same call. Storing them here
                # rather than in a later stage means the officer data costs no
                # extra request: re-fetching 183k companies to get prose we
                # already had would be a day of polite traffic for nothing.
                db.save_publications(conn, company_id, full.publications)
                # Cleared only after the row is written, so an interrupted run
                # leaves the work still queued rather than silently dropped.
                db.clear_stale(conn, company_id)
                count += 1
            if index % 50 == 0:
                conn.commit()
                log(f"  hydrated {index}/{len(rows)}")
        conn.commit()
    finally:
        if owns_client and http:
            http.close()
    return count
