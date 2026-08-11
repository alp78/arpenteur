"""The match stage: how well a company fits the campaign, and how to open.

Everything before this stage describes a company. This one is the first that
takes a position on it, by reading the campaign brief and the company's file
together and answering three questions: how good a fit, why, and what would
you actually say.

Two decisions worth stating.

**A judge, not a similarity score.** The obvious alternative is to embed the
brief and the company profile and rank by cosine distance. That is cheaper and
it is the wrong instrument here. The funnel narrows hard before this point,
profile is cheap over everything, enrich is a shortlist, match is shorter
still, so the volume never justifies the machinery. And a distance cannot
explain itself or write the approach advice, which is the actual deliverable:
a ranked list without a reason is a spreadsheet.

**The brief is hashed into every row.** Rewriting campaign.md invalidates every
score taken against the old one. Storing the digest is what makes those
findable afterwards, instead of a table where old and new scores sit together
looking equally current.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from .. import db
from ..config import Settings
from ..llm import get_provider

SYSTEM = """You judge how well a company fits a campaign, for someone deciding \
whether to approach it and what to say.

You are given the campaign brief and everything known about one company: its \
register entry, its officers, and what its own website says. The brief is \
authoritative. It states who this campaign serves well, who it serves badly, \
what to look for and what warns off; apply it rather than your own notion of \
a good prospect.

Judgement:
- score is 0 to 100 and means how well this company matches the brief. Use the \
whole range. A campaign where everything scores 60 to 80 has told you nothing.
- Weigh what the brief says warns you off as heavily as what it says to look \
for. A company that matches three signals and one disqualifier is usually a \
worse prospect than one that matches two and none.
- matched lists the brief's own signals you actually found, in the brief's \
words, so the score can be checked against the evidence.
- against is what argues for not approaching. Say it plainly. An empty list \
is a real answer, but so is a high score with a serious reservation.
- Thin evidence is a reason for a middling score and a note saying so, not for \
inventing a reason to be confident. A company with no website found is not \
thereby a bad fit or a good one.

The advice:
- advice is what you would actually say in a first approach to this specific \
company, following the brief's own guidance on how it opens conversations.
- Write it in the language of the brief.
- Name something true and specific about this company. Advice that would fit \
any company in the segment is worth nothing, and the recipient can tell.
- Never mention anything from the restricted material. It is not shown to you \
for that reason, and if you infer something of that kind, leave it out."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "band": {"type": "string", "enum": ["high", "medium", "low", "excluded"]},
        "reasoning": {"type": "string"},
        "matched": {"type": "array", "items": {"type": "string"}},
        "against": {"type": "array", "items": {"type": "string"}},
        "advice": {"type": ["string", "null"]},
    },
    "required": ["score", "band", "reasoning", "matched", "against", "advice"],
    "additionalProperties": False,
}


def load_brief(settings: Settings) -> str:
    path = Path(getattr(settings, "brief_path", settings.root / "campaign.md"))
    if not path.is_file():
        raise RuntimeError(
            f"No campaign brief at {path}. This stage scores companies against "
            "it, so without one every score would be an invention. Copy the "
            "template and write it for the campaign at hand."
        )
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"{path} is empty.")
    return text


def brief_digest(brief: str) -> str:
    """Identifies the brief a score was taken against. See the module note."""
    return hashlib.sha256(brief.encode("utf-8")).hexdigest()[:16]


def build_prompt(
    company: sqlite3.Row,
    segments: list[sqlite3.Row],
    officers: list[sqlite3.Row],
    site: dict[str, Any] | None,
) -> str:
    """The company's whole file, minus anything unquotable.

    `site` arrives from db.site_for_draft, which is the accessor that strips
    the restricted column. The judge is not shown solvency or enforcement
    material at all: it writes the approach advice, and the surest way to keep
    that material out of an approach is for the writer never to have seen it.
    """
    lines = [
        f"Name: {company['name']}",
        f"UID: {company['uid']}",
        f"Legal form: {company['legal_form_label'] or '?'}",
        f"Seat: {company['legal_seat'] or '?'} ({company['canton'] or '?'})",
        f"Registry status: {company['status'] or '?'}",
    ]
    if company["capital"]:
        lines.append(f"Capital: {company['capital']:.0f} {company['capital_currency'] or ''}")
    if company["first_seen"]:
        lines.append(f"First seen in the register sweep: {company['first_seen'][:10]}")
    if company["purpose"]:
        lines.append(
            "Registered purpose: " + " ".join((company["purpose"] or "").split())[:1500]
        )
    if segments:
        lines.append(
            "Segments: "
            + ", ".join(f"{row['segment']} ({row['source']})" for row in segments)
        )
    if officers:
        lines.append(
            "Officers: "
            + ", ".join(f"{row['full_name']} ({row['role']})" for row in officers[:6])
        )

    if not site:
        lines.append("\nWeb research: not done. Judge on the register alone, and say so.")
        return "\n".join(lines)

    lines.append(f"\nWebsite: {site.get('url') or 'none found'}")
    lines.append(f"Website confidence: {site.get('status')} / {site.get('confidence')}")
    if site.get("summary"):
        lines.append(f"What the site says: {site['summary']}")
    for column, label in (
        ("offerings", "Offerings"),
        ("signals", "Signals found"),
        ("partnerships", "Partnerships"),
        ("tech_stack", "Technology named"),
    ):
        value = site.get(column)
        if not value:
            continue
        try:
            items = json.loads(value) if isinstance(value, str) else value
        except (TypeError, ValueError):
            continue
        if items:
            lines.append(f"{label}: " + "; ".join(str(i) for i in items))
    if site.get("size_hint"):
        lines.append(f"Size: {site['size_hint']}")
    return "\n".join(lines)


def _select(
    conn: sqlite3.Connection,
    *,
    settings: Settings,
    uids: list[str] | None,
    segment: str | None,
    researched_only: bool,
    limit: int | None,
    rescore: bool,
    brief_hash: str | None,
) -> list[sqlite3.Row]:
    where = ["c.hydrated_at IS NOT NULL"]
    params: list[Any] = []
    if uids:
        where.append(f"c.uid IN ({','.join('?' * len(uids))})")
        params.extend(uids)
    else:
        clause, values = settings.status_clause()
        if clause:
            where.append(clause)
            params.extend(values)
    if segment:
        where.append(
            "EXISTS (SELECT 1 FROM segment s WHERE s.company_id = c.id AND s.segment = ?)"
        )
        params.append(segment)
    if researched_only:
        where.append("EXISTS (SELECT 1 FROM site w WHERE w.company_id = c.id)")
    if not rescore:
        # A score taken against a different brief is stale, so it is selected
        # again rather than skipped. This is what makes rewriting campaign.md
        # safe: the next run re-scores what the rewrite invalidated.
        where.append(
            "c.id NOT IN (SELECT company_id FROM fit WHERE brief_hash IS ? "
            "AND source <> 'manual')"
        )
        params.append(brief_hash)
    sql = f"SELECT c.* FROM company c WHERE {' AND '.join(where)} ORDER BY c.id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def run(
    settings: Settings,
    conn: sqlite3.Connection,
    *,
    uids: list[str] | None = None,
    segment: str | None = None,
    researched_only: bool = True,
    limit: int | None = None,
    rescore: bool = False,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    brief = load_brief(settings)
    digest = brief_digest(brief)
    model = settings.llm.match_model or settings.llm.draft_model

    companies = _select(
        conn, settings=settings, uids=uids, segment=segment,
        researched_only=researched_only, limit=limit, rescore=rescore,
        brief_hash=digest,
    )
    stats: dict[str, Any] = {
        "considered": len(companies), "scored": 0, "failed": 0,
        "high": 0, "medium": 0, "low": 0, "excluded": 0,
        "model": model, "brief": digest,
    }
    if not companies:
        log("Nothing to score. Run enrich first, or pass --no-researched-only.")
        return stats

    if limit is None and not uids and len(companies) > 500:
        raise RuntimeError(
            f"{len(companies)} companies selected. This stage costs one model "
            "call each; pass --limit to confirm the size of the run."
        )

    if dry_run:
        log(f"{len(companies)} companies would be scored against brief {digest}:")
        for company in companies[:20]:
            log(f"  {company['uid']}  {company['name']}")
        if len(companies) > 20:
            log(f"  ... and {len(companies) - 20} more")
        return stats

    provider = get_provider(settings)

    for index, company in enumerate(companies, 1):
        segments = conn.execute(
            "SELECT segment, source FROM segment WHERE company_id = ?",
            (company["id"],),
        ).fetchall()
        officers = conn.execute(
            "SELECT full_name, role FROM person WHERE company_id = ? ORDER BY role",
            (company["id"],),
        ).fetchall()
        site = db.site_for_draft(conn, company["id"])

        try:
            reply = provider.complete_json(
                system=SYSTEM,
                user=(
                    f"CAMPAIGN BRIEF\n{'=' * 60}\n{brief}\n\n"
                    f"COMPANY\n{'=' * 60}\n"
                    f"{build_prompt(company, segments, officers, site)}"
                ),
                model=model,
                max_tokens=settings.llm.match_max_tokens,
                schema=SCHEMA,
            )
        except Exception as exc:
            stats["failed"] += 1
            log(f"  ! {company['name'][:40]}: {exc}")
            continue

        band = str(reply.get("band") or "low")
        db.save_fit(
            conn,
            company["id"],
            {
                "score": int(reply.get("score") or 0),
                "band": band,
                "reasoning": reply.get("reasoning"),
                "advice": reply.get("advice"),
                "against": reply.get("against"),
                "matched": reply.get("matched"),
                "model": model,
                "brief_hash": digest,
            },
        )
        conn.commit()
        stats["scored"] += 1
        if band in stats:
            stats[band] += 1
        log(f"  {reply.get('score'):>3} {band:9} {company['name'][:44]}")
        if index % 25 == 0:
            log(f"  {index}/{len(companies)}")

    return stats
