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
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    cantons = cantons or settings.cantons
    wanted_forms = legal_forms or settings.legal_forms
    stats: dict[str, Any] = {
        "searched": 0,
        "seen": 0,
        "hydrated": 0,
        "refused": 0,
        "cantons": cantons,
    }

    with _client(settings) as http:
        zefix = ZefixClient(http, settings.zefix.base_url)

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
                settings, conn, zefix=zefix, limit=limit, log=log
            )

    return stats


def hydrate_pending(
    settings: Settings,
    conn: sqlite3.Connection,
    *,
    zefix: ZefixClient | None = None,
    limit: int | None = None,
    log: Callable[[str], None] = print,
) -> int:
    """Fetch full detail for companies the sweep has not hydrated yet."""
    rows = conn.execute(
        "SELECT uid FROM company WHERE hydrated_at IS NULL ORDER BY id"
        + (" LIMIT ?" if limit else ""),
        (limit,) if limit else (),
    ).fetchall()
    if not rows:
        return 0

    owns_client = zefix is None
    http = _client(settings) if owns_client else None
    client = zefix or ZefixClient(http, settings.zefix.base_url)  # type: ignore[arg-type]
    count = 0
    try:
        for index, row in enumerate(rows, 1):
            full = client.company(row["uid"])
            if full:
                db.upsert_company(conn, full.as_row())
                count += 1
            if index % 50 == 0:
                conn.commit()
                log(f"  hydrated {index}/{len(rows)}")
        conn.commit()
    finally:
        if owns_client and http:
            http.close()
    return count
