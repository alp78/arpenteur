"""The source stage: fill the company table from public registers.

Two passes, deliberately separate. The sweep is cheap and wide (one search
per name prefix, many companies per response); hydration is one request per
company and only runs for rows that still lack a purpose. Splitting them
means an interrupted run never has to redo the wide pass to get the narrow
one, and a re-sweep months later only hydrates what is genuinely new.
"""

from __future__ import annotations

from typing import Any, Callable

from .. import db
from ..config import Settings
from ..kernel.db import Connection
from ..http import PoliteClient
from ..sources.zefix import ResultListTooLarge, ZefixClient, walk_prefixes
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
    conn: Connection,
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
    conn: Connection,
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

            # Both loop variables are bound as defaults rather than captured.
            # `walk_prefixes` calls this within the iteration, so a captured
            # `canton` would read correctly at present; binding it is what keeps
            # that true if the walk is ever deferred or run in parallel, where
            # the closure would otherwise search the last canton of the loop.
            def search(
                pattern: str,
                _form: dict[str, Any] = form,
                _canton: str = canton,
            ) -> list[Any]:
                stats["searched"] += 1
                return zefix.search(
                    name=pattern, canton=_canton, legal_form_id=_form["id"]
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


def run_by_name(
    settings: Settings,
    conn: Connection,
    *,
    words: list[str],
    cantons: list[str] | None = None,
    hydrate: bool = True,
    limit: int | None = None,
    bulk: bool = False,
    fixture: bool = False,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Find the companies whose registered name carries a given word.

    The sweep walks every name prefix because it wants a whole territory. This
    wants a trade: the organisations whose name says `tourismus`, `bergbahn` or
    `naturpark`. Zefix answers a leading wildcard, measured on 08.10.2026 in
    Valais, where `*tourisme*` returns 19 companies and `Tourisme*` returns
    none. A trade costs one request per canton per word, against the several
    hundred a prefix walk spends on the same cantons.

    Three differences from the sweep, each deliberate.

    **Every legal form comes back**, because `legal_form_id` is left unset. The
    forms a trade uses are not the forms a survey of companies sweeps: 104 of
    the 798 tourism organisations found this way are associations, and
    `survey.legal_forms` has never collected one.

    **Nothing is written to `intel.sweep`.** A name search has not completed a
    prefix, and recording one would make a later full sweep skip territory it
    never read. The run is recorded in `intel.run` with its words, which is
    where the Limen name gate is auditable today.

    **Hydration is asked for by uid.** The found set is a few hundred companies
    inside a register of half a million, so `hydrate_pending` is handed the uids
    rather than a canton list that would pull in everything else awaiting
    detail.
    """
    if fixture:
        return _run_names(
            settings, conn, zefix=FixtureZefixClient(),
            cantons=cantons or list(FIXTURE_CANTONS), words=words,
            hydrate=hydrate, limit=limit, bulk=bulk, dry_run=dry_run, log=log,
        )

    with _client(settings) as http:
        return _run_names(
            settings, conn, zefix=ZefixClient(http, settings.zefix.base_url),
            cantons=cantons or settings.cantons, words=words,
            hydrate=hydrate, limit=limit, bulk=bulk, dry_run=dry_run, log=log,
        )


def _run_names(
    settings: Settings,
    conn: Connection,
    *,
    zefix: ZefixClient | FixtureZefixClient,
    cantons: list[str],
    words: list[str],
    hydrate: bool,
    limit: int | None,
    bulk: bool,
    dry_run: bool,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "searched": 0,
        "seen": 0,
        "found": 0,
        "refused": 0,
        "hydrated": 0,
        "words": words,
        "cantons": cantons,
    }
    # Distinct companies, because a tourism office whose name carries both
    # `tourismus` and `bergbahn` is one organisation answering twice, and a
    # count of rows seen would report the word list rather than the cohort.
    found: set[str] = set()

    for canton in cantons:
        for word in words:
            stats["searched"] += 1
            try:
                companies = zefix.search(name=word, canton=canton)
            except ResultListTooLarge:
                # The sweep narrows a refused prefix. A word cannot be narrowed
                # without changing what was asked for, so this is reported and
                # left to the operator.
                stats["refused"] += 1
                log(f"  ! {word} is too broad in {canton}, narrow the word")
                continue
            for company in companies:
                if not company.uid:
                    continue
                # The search answer carries no canton, exactly as in the sweep.
                company.canton = company.canton or canton
                found.add(company.uid)
                if not dry_run:
                    db.upsert_company(conn, company.as_row())
                stats["seen"] += 1
            if companies:
                log(f"{canton} {word}: {len(companies)}")
        if not dry_run:
            conn.commit()

    stats["found"] = len(found)
    if hydrate and found and not dry_run:
        stats["hydrated"] = hydrate_pending(
            settings, conn, zefix=zefix, uids=sorted(found),
            limit=limit, bulk=bulk, log=log,
        )
    return stats


def hydrate_pending(
    settings: Settings,
    conn: Connection,
    *,
    zefix: ZefixClient | FixtureZefixClient | None = None,
    cantons: list[str] | None = None,
    legal_forms: list[str] | None = None,
    name_like: str | None = None,
    uids: list[str] | None = None,
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

    `name_like` is the filter that makes a national run possible at all. The
    register cannot be searched by activity, so finding every rental agency in
    Switzerland would otherwise mean hydrating half a million companies, which
    is a fifty-five hour run and precisely the mass querying Zefix asks users
    to renounce. A name pattern narrows that to a few per cent, and the purpose
    text those calls return is what actually decides the activity.

    Two kinds of work arrive here. A company the sweep found but never fetched
    in detail, and a company the sync stage marked stale because a FOSC notice
    said it changed. The second is served first: a company whose purpose was
    amended last week is more wrong than one that has never been read at all.
    """
    where = ["(hydrated_at IS NULL OR stale_since IS NOT NULL)"]
    params: list[Any] = []
    if cantons:
        where.append(f"canton IN ({','.join(['%s'] * len(cantons))})")
        params.extend(cantons)
    if legal_forms:
        where.append(f"legal_form_uid IN ({','.join(['%s'] * len(legal_forms))})")
        params.extend(legal_forms)
    if name_like:
        where.append("name ~* %s")
        params.append(name_like)
    if uids:
        # Named companies rather than a territory, which is what an outside
        # source produces: a directory of agencies knows which firms are worth
        # the detail call, and the name gate that stands in for that knowledge
        # cannot see a régie called "Bory & Cie". Narrower than any filter
        # above it, so it is a smaller call on the register, not a larger one.
        where.append("uid = ANY(%s)")
        params.append(list(uids))
    sql = (
        f"SELECT uid, stale_since FROM core.company WHERE {' AND '.join(where)} "
        "ORDER BY stale_since IS NULL, last_mutation_date DESC, id"
    )
    # `is not None`, not truthiness: the guard on the size of the run reads a
    # limit of 0 as given, so the slice must too, or zero means "everything".
    if limit is not None:
        # `%s`, not the `?` this carried since the SQLite days. Postgres never
        # saw a placeholder there, so psycopg refused the call before a single
        # request went out and every `--limit` run failed. Which left `--bulk`
        # over the whole territory as the only hydration that worked, and that
        # is a twenty-five hour run: the reason this survey has one canton
        # hydrated and five untouched.
        sql += " LIMIT %s"
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


def import_company(
    settings: Settings,
    conn: Connection,
    uid: str,
    *,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Fetch one company by UID, whatever the territory says.

    The survey covers a territory; a client does not have to be inside it. The
    first real client of this tool was a foundation in Vaud, and the territory
    was five cantons of limited companies: a sweep would never have found it,
    and requiring one to would be the tool dictating who may be a client.

    So this is the deliberate single-company door. It hydrates in one request
    and stores the gazette notices alongside, exactly as the bulk path does.
    """
    cleaned = uid.strip().upper().replace("-", "").replace(".", "").replace(" ", "")
    if not cleaned.startswith("CHE") or not cleaned[3:].isdigit():
        raise RuntimeError(
            f"{uid!r} is not a Swiss UID. They look like CHE-100.000.011 or "
            "CHE100000011, and are on any company's page at zefix.ch."
        )
    with _client(settings) as http:
        zefix = ZefixClient(http, settings.zefix.base_url)
        full = zefix.company(cleaned, max_age=0)
    if full is None:
        raise RuntimeError(
            f"The register has no company with UID {cleaned}. Check it at "
            "zefix.ch; a struck-off company keeps its UID and would be found."
        )
    company_id = db.upsert_company(conn, full.as_row())
    db.save_publications(conn, company_id, full.publications)
    conn.commit()
    log(f"  {full.uid}  {full.name}  ({full.legal_form_label}, {full.canton})")
    return {
        "company_id": company_id, "uid": full.uid, "name": full.name,
        "canton": full.canton, "publications": len(full.publications),
    }
