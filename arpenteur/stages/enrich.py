"""The enrich stage: what the open web says, for companies worth the money.

The registry gives identity and officers. It cannot say what a company sells,
what it runs on, or where to write. That is on the company's own site, so this
stage asks a model to search for it, read it, and answer a fixed set of
questions about it.

This is the first stage that costs real money per company: about a franc,
measured. The bill is tokens rather than searches, which took a measurement to
establish and is the opposite of what was assumed. Two consequences shape the
design:

  it never runs on the corpus   the caller passes uids, or a filter that
                               resolves to a shortlist. There is deliberately
                               no "enrich everything" path: 183k companies at
                               four searches each is not a run anyone means
                               to start by accident.
  it records what it spent     the run stats carry searches, fetches, tokens
                               and continuations, so a run is priced from its
                               own record. Tokens were the omission that
                               mattered: they are the bill, and an estimate of
                               forty centimes a company survived unchecked
                               until one was measured at 465'812 input tokens,
                               about a franc.

Every field here is an inference from a page, not a registry fact. `status`
and `confidence` are stored beside them because a guessed website silently
treated as certain is worse than no website at all: Swiss SME names collide,
and the wrong site would poison the fit score and the draft that follows it.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Callable

from .. import db
from ..config import Settings
from ..kernel.db import Connection, Row
from ..llm import get_provider

SYSTEM = """You research a Swiss company on the open web and report what you \
find, for someone deciding whether and how to approach it.

Method:
- Search for the company's own website. The registered name, seat and purpose \
are given; use them to tell the company apart from namesakes.
- Open the site itself. Do not report a site as found from search results \
alone: search snippets are enough to spot a candidate and never enough to \
confirm it or to read what the company says about itself.
- Look for the page carrying the contact details, whatever it is called \
(contact, nous contacter, impressum, à propos, mentions légales), and take \
the published address from there.
- Prefer the company's own words over a directory listing about it.

Judgement:
- status is "found" only when the site is that company beyond reasonable \
doubt. A same-name company in another canton or country is "not_found".
- When two candidate sites are plausible and nothing separates them, say \
"ambiguous" and put both in notes rather than guessing.
- confidence is your own 0.0 to 1.0 estimate that the url belongs to this \
exact legal entity.
- Report only what the pages support. An empty list is a valid answer and is \
much better than a plausible invention.
- Every list entry must be a fact about this company, taken from its own \
pages. Never enter what a directory says about similar or competing firms, \
and never enter the absence of something as if it were a finding: if the \
site names no partners, partnerships is empty. Anything you would have to \
hedge belongs in notes, not in a list.
- Anything touching solvency, debt enforcement (poursuite, commandement de \
payer, faillite), bankruptcy, litigation, criminal matters or a named \
individual's personal circumstances goes in restricted, and nowhere else. \
Not in signals, not in the summary, not in notes. These read as ordinary business intelligence and are not: for a \
one-person company they are an identifiable person's private affairs, and \
they are collected to inform a judgement that is never repeated back."""

# JSON Schema for the reply. Kept to the subset the API enforces: no string
# length or numeric bounds, additionalProperties false on every object.
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["found", "not_found", "ambiguous"]},
        "url": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "language": {"type": ["string", "null"]},
        "summary": {
            "type": ["string", "null"],
            "description": "Two or three sentences on what the company does.",
        },
        "offerings": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concrete products or services named on the site.",
        },
        # The description is filled in from enrich.signal_brief at call time.
        # What counts as a signal is the operator's question, not the tool's:
        # a recruiter, a distributor and a consultancy are reading the same
        # page for entirely different things.
        "signals": {
            "type": "array",
            "items": {"type": "string"},
            "description": "",
        },
        "partnerships": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Named partners, resellers, certifications, memberships.",
        },
        "tech_stack": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Software, platforms or vendors the site shows or names.",
        },
        "size_hint": {"type": ["string", "null"]},
        "restricted": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Findings about solvency, debt enforcement (poursuites), "
                "bankruptcy, litigation, criminal matters, or the personal "
                "circumstances of a named individual. These are collected for "
                "judgement only and are never repeated to the company, so "
                "they belong here and nowhere else."
            ),
        },
        "emails": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Contact addresses published on the site itself.",
        },
        "notes": {"type": ["string", "null"]},
    },
    "required": [
        "status", "url", "confidence", "language", "summary", "offerings",
        "signals", "partnerships", "tech_stack", "size_hint", "restricted",
        "emails", "notes",
    ],
    "additionalProperties": False,
}


def schema_for(signal_brief: str) -> dict[str, Any]:
    """SCHEMA with the operator's own definition of a signal written in.

    Copied rather than mutated so the module constant stays the shape the
    tests assert against, and so two surveys in one process cannot overwrite
    each other's brief.
    """
    schema = copy.deepcopy(SCHEMA)
    schema["properties"]["signals"]["description"] = (
        signal_brief or "Anything bearing on whether this company is worth approaching."
    )
    return schema


def build_prompt(company: Row, officers: list[Row]) -> str:
    """Everything known about a company, as the brief for the search."""
    address = {}
    if company["address_json"]:
        try:
            address = company["address_json"] or {}
        except (TypeError, ValueError):
            address = {}

    lines = [
        f"Name: {company['name']}",
        f"UID: {company['uid']}",
        f"Legal form: {company['legal_form_label'] or '?'}",
        f"Seat: {company['legal_seat'] or '?'} ({company['canton'] or '?'})",
    ]
    where = " ".join(
        str(part)
        for part in (
            address.get("street"),
            address.get("houseNumber"),
            address.get("swissZipCode"),
            address.get("city"),
        )
        if part
    )
    if where:
        lines.append(f"Address: {where}")
    if company["status"]:
        lines.append(f"Registry status: {company['status']}")
    if company["purpose"]:
        purpose = " ".join((company["purpose"] or "").split())
        lines.append(f"Registered purpose: {purpose[:1200]}")
    if officers:
        named = ", ".join(
            f"{row['full_name']} ({row['role']})" for row in officers[:6]
        )
        # The officers are given because a director's name is often the
        # fastest way to confirm a site belongs to this company rather than a
        # namesake, which is the judgement the model most often gets wrong.
        lines.append(f"Registered officers: {named}")
    return "\n".join(lines)


def _select(
    conn: Connection,
    *,
    settings: Settings,
    uids: list[str] | None,
    canton: str | None,
    segment: str | None,
    sector: str | None,
    limit: int | None,
    refresh: bool,
) -> list[Row]:
    where = ["c.hydrated_at IS NOT NULL"]
    params: list[Any] = []
    # Not for a company in liquidation. This stage bills per company, so the
    # 6% of the register that is winding up is real money as well as a draft
    # nobody wants sent. An explicit uid list overrides it: asking for one
    # company by name is a deliberate act.
    if not uids:
        clause, values = settings.status_clause()
        if clause:
            where.append(clause)
            params.extend(values)
    if uids:
        where.append(f"c.uid IN ({','.join(['%s'] * len(uids))})")
        params.extend(uids)
    if canton:
        where.append("c.canton = %s")
        params.append(canton)
    if segment:
        where.append("EXISTS (SELECT 1 FROM intel.segment s WHERE s.company_id = c.id AND s.segment = %s)")
        params.append(segment)
    if sector:
        where.append("EXISTS (SELECT 1 FROM intel.sector x WHERE x.company_id = c.id AND x.sector = %s)")
        params.append(sector)
    if not refresh:
        where.append("c.id NOT IN (SELECT company_id FROM intel.site)")
    sql = f"SELECT c.* FROM core.company c WHERE {' AND '.join(where)} ORDER BY c.id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def run(
    settings: Settings,
    conn: Connection,
    *,
    uids: list[str] | None = None,
    canton: str | None = None,
    segment: str | None = None,
    sector: str | None = None,
    limit: int | None = None,
    refresh: bool = False,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    model = settings.llm.enrich_model or settings.llm.draft_model
    if not model:
        raise RuntimeError(
            "No enrich model configured. Set llm.enrich_model in arpenteur.toml "
            "to a model with web search (Opus 4.6+ or Sonnet 4.6+)."
        )

    companies = _select(
        conn, settings=settings, uids=uids, canton=canton, segment=segment,
        sector=sector, limit=limit, refresh=refresh,
    )
    stats: dict[str, Any] = {
        "considered": len(companies),
        "found": 0,
        "not_found": 0,
        "ambiguous": 0,
        "failed": 0,
        "emails": 0,
        "searches": 0,
        "fetches": 0,
        # The dominant cost, and the one that was not being recorded. Searches
        # and fetches were counted while the larger number was estimated once
        # in development and never checked, which is the wrong half to count.
        "input_tokens": 0,
        "output_tokens": 0,
        # Continuations, which is what explains the token count.
        "turns": 0,
        "model": model,
    }
    if not companies:
        log("Nothing to enrich. Hydrate first, or widen the filter.")
        return stats

    # A guard rather than a limit: this stage bills per search, and a
    # mistyped filter that selects thousands should stop rather than spend.
    if limit is None and not uids and len(companies) > 200:
        raise RuntimeError(
            f"{len(companies)} companies selected. This stage costs money per "
            "company; pass --limit to confirm the size of the run."
        )

    # Resolved after the dry-run branch on purpose: listing the shortlist is
    # how you check a filter before paying for it, and it must work on a
    # machine that has no credential configured at all.
    if dry_run:
        log(f"{len(companies)} companies would be researched with {model}:")
        for company in companies[:20]:
            log(f"  {company['uid']}  {company['name']}")
        if len(companies) > 20:
            log(f"  ... and {len(companies) - 20} more")
        return stats

    provider = get_provider(settings)
    schema = schema_for(getattr(settings, "signal_brief", ""))

    for index, company in enumerate(companies, 1):
        officers = conn.execute(
            "SELECT full_name, role FROM core.person WHERE company_id = %s ORDER BY role",
            (company["id"],),
        ).fetchall()

        try:
            result = provider.research(
                system=SYSTEM,
                user=build_prompt(company, officers),
                model=model,
                max_tokens=settings.llm.enrich_max_tokens,
                schema=schema,
                max_searches=settings.llm.enrich_max_searches,
            )
        except Exception as exc:
            # One company failing must not end the run: the shortlist was
            # chosen deliberately and the rest of it is still worth having.
            stats["failed"] += 1
            log(f"  ! {company['name'][:40]}: {exc}")
            continue

        data = result.data
        stats["searches"] += result.searches
        stats["fetches"] += result.fetches
        stats["input_tokens"] += result.input_tokens
        stats["output_tokens"] += result.output_tokens
        stats["turns"] += result.turns
        status = str(data.get("status") or "not_found")
        stats[status if status in ("found", "not_found", "ambiguous") else "failed"] += 1

        db.save_site(
            conn,
            company["id"],
            {
                "status": status,
                "url": data.get("url"),
                "confidence": data.get("confidence"),
                "language": data.get("language"),
                "summary": data.get("summary"),
                "offerings": data.get("offerings"),
                "signals": data.get("signals"),
                "partnerships": data.get("partnerships"),
                "tech_stack": data.get("tech_stack"),
                "size_hint": data.get("size_hint"),
                "restricted": data.get("restricted"),
                "source_url": data.get("url"),
                "method": "web-search",
                "model": model,
                "searches": result.searches,
                "fetches": result.fetches,
            },
        )

        # Emails go to `contact`, where the reach stage already looks and
        # where suppression is honoured. Only when the site is certain: an
        # address from a namesake's site is worse than no address.
        if status == "found":
            for address in data.get("emails") or []:
                if "@" not in str(address):
                    continue
                stats["emails"] += db.save_contact(
                    conn,
                    company_id=company["id"],
                    kind="email",
                    value=str(address).strip().lower(),
                    source_url=data.get("url"),
                    method="site",
                )

        conn.commit()
        # A site reported as found without ever being opened is the shape of a
        # confident guess, and it is also why no email came back: the contact
        # page was never read. Worth saying out loud rather than leaving to
        # whoever notices `fetches` is zero.
        if status == "found" and result.fetches == 0:
            log(f"  ? {company['name'][:38]:40} found from search alone, site not read")
        mark = {"found": "+", "ambiguous": "?", "not_found": "-"}.get(status, "!")
        log(
            f"  {mark} {company['name'][:38]:40} {str(data.get('url') or '')[:44]}"
        )
        if index % 10 == 0:
            log(f"  {index}/{len(companies)}")

    return stats
