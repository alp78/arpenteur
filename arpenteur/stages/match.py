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
from pathlib import Path
from typing import Any, Callable

from .. import db
from ..config import Settings
from ..kernel.db import Connection, Row
from ..modules.scout import scoring
from ..llm import Spend, get_provider

# Stand-in for a provider that does not account for itself, which is every
# fake in the test suite. Reporting zeros is right: nothing was billed.
_NO_SPEND = Spend()


SYSTEM = """You judge how well a company fits a campaign, for someone deciding \
whether to approach it and what to say.

You are given the campaign brief and everything known about one company: its \
register entry, its officers, and what its own website says. The brief is \
authoritative. It states who this campaign serves well, who it serves badly, \
what to look for and what warns off; apply it rather than your own notion of \
a good prospect.

You score four things separately, each 0 to 100. Score them separately: a \
company can be an excellent fit that is two years too early, and collapsing \
that into one number loses the only thing worth knowing about it.

  fit     How closely this company matches who the brief says the campaign \
serves well, and how far it matches who it says are served badly. Weigh a \
disqualifier as heavily as a signal. A company matching three signals and one \
disqualifier is usually worse than one matching two and none.
  need    Evidence that this company has the problem the campaign solves. Not \
whether it could benefit in principle: whether something in the material says \
it has the problem now. No evidence is a low score, not a middling one.
  timing  Signals that this is the moment rather than some moment: a move, a \
hire, a change of officers, a system visibly aging, a deadline in its sector. \
Absence of any timing signal is a low score and an honest one.
  value   The commercial size of the opportunity if it converts, judged from \
what the company is and does.

Two further components, reachability and relationship, are computed from data \
you cannot see. Do not score them and do not refer to them.

For each of the four, give the evidence you scored on: `for` items and \
`against` items, in the brief's own words where they are the brief's signals, \
quoting or naming what in the material supports each. Evidence that would fit \
any company in the segment is not evidence.

Thin material is a reason for a low score with a note saying the material is \
thin, never for inventing a reason to be confident. A company with no website \
found is not thereby a bad fit or a good one; say so in the reasoning and \
score what you can actually see.

Use the whole range. A campaign where everything scores 60 to 80 has told you \
nothing.

The advice:
- advice is what you would actually say in a first approach to this specific \
company, following the brief's own guidance on how it opens conversations.
- Write it in the language of the brief.
- Name something true and specific about this company. Advice that would fit \
any company in the segment is worth nothing, and the recipient can tell.
- Never mention anything from the restricted material. It is not shown to you \
for that reason, and if you infer something of that kind, leave it out."""

def _component_schema(name: str, asks: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": asks,
        "properties": {
            "score": {"type": "integer", "description": "0 to 100."},
            "reasoning": {"type": "string",
                          "description": "Two sentences at most."},
            "for": {"type": "array", "items": {"type": "string"},
                    "description": "What in the material argues for this score."},
            "against": {"type": "array", "items": {"type": "string"},
                        "description": "What argues the other way. An empty "
                                       "list is a real answer."},
        },
        "required": ["score", "reasoning", "for", "against"],
        "additionalProperties": False,
    }


SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fit": _component_schema("fit", scoring.BY_KEY["icp_fit"].asks),
        "need": _component_schema("need", scoring.BY_KEY["need"].asks),
        "timing": _component_schema("timing", scoring.BY_KEY["timing"].asks),
        "value": _component_schema("value", scoring.BY_KEY["value"].asks),
        "reasoning": {"type": "string",
                      "description": "The judgement overall, in three sentences."},
        "advice": {"type": ["string", "null"]},
    },
    "required": ["fit", "need", "timing", "value", "reasoning", "advice"],
    "additionalProperties": False,
}

# The model's key for each judged component, and the component it fills. The
# names differ because "icp_fit" is a word from a data model and "fit" is the
# word a person writing a prompt would use.
JUDGED_KEYS = {"fit": "icp_fit", "need": "need", "timing": "timing",
               "value": "value"}


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
    company: Row,
    segments: list[Row],
    officers: list[Row],
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
    conn: Connection,
    *,
    settings: Settings,
    uids: list[str] | None,
    segment: str | None,
    researched_only: bool,
    limit: int | None,
    rescore: bool,
    brief_hash: str | None,
) -> list[Row]:
    where = ["c.hydrated_at IS NOT NULL"]
    params: list[Any] = []
    if uids:
        where.append(f"c.uid IN ({','.join(['%s'] * len(uids))})")
        params.extend(uids)
    else:
        clause, values = settings.status_clause()
        if clause:
            where.append(clause)
            params.extend(values)
    if segment:
        where.append(
            "EXISTS (SELECT 1 FROM intel.segment s WHERE s.company_id = c.id AND s.segment = %s)"
        )
        params.append(segment)
    if researched_only:
        where.append("EXISTS (SELECT 1 FROM intel.site w WHERE w.company_id = c.id)")
    if not rescore:
        # A score is stale when either input has moved: the brief it was taken
        # against, or the web research it read. Both are compared, so
        # rewriting campaign.md or re-running enrich each cause exactly the
        # affected companies to be scored again and no others. A manual score
        # is never stale, because a person is not an input that expired.
        where.append(
            """c.id NOT IN (
                SELECT f.company_id FROM intel.fit f
                 WHERE f.source = 'manual'
                    OR (f.brief_hash IS NOT DISTINCT FROM %s
                        AND f.site_hash IS NOT DISTINCT FROM (
                             SELECT w.content_hash FROM intel.site w
                              WHERE w.company_id = f.company_id
                        ))
            )"""
        )
        params.append(brief_hash)
    sql = f"SELECT c.* FROM core.company c WHERE {' AND '.join(where)} ORDER BY c.id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def assemble(
    conn: Connection, company: Row, reply: dict[str, Any],
    weights: dict[str, Any],
) -> dict[str, Any]:
    """Turn one model reply plus what the database knows into a score run.

    The four judged components come from the reply; reachability and
    relationship are counted here from rows the model never saw. Everything
    keeps its own evidence, so the total can be taken apart again by anybody
    who disagrees with it.
    """
    components: list[dict[str, Any]] = []
    parts: dict[str, Any] = {}

    for key, component in JUDGED_KEYS.items():
        given = reply.get(key) or {}
        score = given.get("score")
        if score is None:
            continue
        parts[component] = score
        components.append({
            "component": component,
            "score": score,
            "weight": weights.get(component),
            "origin": "model",
            "reasoning": given.get("reasoning"),
            "evidence": (
                [{"direction": "for", "statement": s} for s in given.get("for") or []]
                + [{"direction": "against", "statement": s}
                   for s in given.get("against") or []]
            ),
        })

    contacts = db.contacts_for(conn, company["id"])
    score, reason, evidence = scoring.reach_score([dict(c) for c in contacts])
    parts["reach"] = score
    components.append({
        "component": "reach", "score": score, "weight": weights.get("reach"),
        "origin": "computed", "reasoning": reason,
        "evidence": [{"direction": "for", "statement": s} for s in evidence],
    })

    overlaps = db.board_overlap(conn, company["id"])
    score, reason, evidence = scoring.relationship_score([dict(o) for o in overlaps])
    parts["relationship"] = score
    components.append({
        "component": "relationship", "score": score,
        "weight": weights.get("relationship"), "origin": "computed",
        "reasoning": reason,
        "evidence": [{"direction": "for", "statement": s} for s in evidence],
    })

    total, band = scoring.combine(parts, weights)
    # `matched` and `against` are still written onto `intel.fit`, which the
    # company page and the board read. Gathered from every component's
    # evidence rather than asked for separately, so the two cannot disagree.
    matched = [e["statement"] for c in components for e in c["evidence"]
               if e["direction"] == "for"]
    against = [e["statement"] for c in components for e in c["evidence"]
               if e["direction"] == "against"]
    return {
        "total": total, "band": band, "components": components, "parts": parts,
        "reasoning": reply.get("reasoning"), "advice": reply.get("advice"),
        "matched": matched[:12], "against": against[:12],
    }


def run(
    settings: Settings,
    conn: Connection,
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

    weight_set_id, weights = db.active_weights(conn)

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
            "SELECT segment, source FROM intel.segment WHERE company_id = %s",
            (company["id"],),
        ).fetchall()
        officers = conn.execute(
            "SELECT full_name, role FROM core.person WHERE company_id = %s ORDER BY role",
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

        found = assemble(conn, company, reply, weights)
        db.save_score_run(conn, company["id"], {
            **found,
            "weight_set_id": weight_set_id,
            "model": model,
            "brief_hash": digest,
            "site_hash": db.site_digest(site),  # what this score read
        })
        conn.commit()
        stats["scored"] += 1
        band = found["band"]
        if band in stats:
            stats[band] += 1
        parts = " ".join(
            f"{key.split('_')[0]}:{int(value)}"
            for key, value in found["parts"].items() if value is not None
        )
        log(f"  {found['total']:>6} {band:9} {company['name'][:34]:36} {parts}")
        if index % 25 == 0:
            log(f"  {index}/{len(companies)}")

    # What the run cost, from what the API reported rather than estimated. A
    # stage that cannot be priced afterwards cannot be tuned, and four of the
    # five paid stages were dropping this on the floor.
    stats.update(getattr(provider, "spent", _NO_SPEND).as_stats())
    return stats
