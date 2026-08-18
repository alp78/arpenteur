"""The profile stage: read a purpose text and say what the company is.

The registered purpose is a company's own description of what it does, filed
with the state. It is public, it is written by the company, and for a first
pass it beats anything a crawler would infer from a homepage. No site is
fetched at this stage.

It answers two questions at once, because they take the same text and the same
call and asking them separately would mean paying to read 180'000 purposes
twice.

**Which segments does it fit?** Several at once, or none: a regulated
laboratory is both compliance-sector and data-driven, and forcing one label
would throw away the more interesting half. Segments are about a campaign.

**What trade is it in?** Exactly one, from a closed list. A company is in
catering or it is in construction; the ones that look like both are describing
a group, and the vocabulary carries an `other` for the purposes too general to
place. Sectors are about the company, and are what makes "restaurants in Vaud"
a filter rather than a search for a word.

A survey whose configuration names no sectors gets the segments alone and the
stage behaves exactly as it did before the vocabulary existed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .. import db
from ..config import Settings
from ..kernel.db import Connection, Row
from ..llm import Spend, extract_json, get_provider

# Stand-in for a provider that does not account for itself, which is every
# fake in the test suite. Reporting zeros is right: nothing was billed.
_NO_SPEND = Spend()


@dataclass(frozen=True)
class Verdict:
    """What the model said about one company.

    `sector` is None when the survey has no sector vocabulary, and also when
    the model named one that is not in it. Nothing is substituted in either
    case: writing `other` for a failed answer would make "the model could not
    place this" indistinguishable from "the model placed it as unplaceable",
    and the second is a fact worth keeping while the first is a retry.
    """

    segments: list[dict[str, Any]] = field(default_factory=list)
    sector: dict[str, Any] | None = None


_SEGMENT_RULES = """- A company may match several segments, or none. Returning \
none is correct and expected for a plain trade or retail activity.
- segment confidence is 0.0 to 1.0 and reflects how clearly the text supports it."""

_SECTOR_RULES = """- Give exactly one sector per company, and only a name from \
the list. Pick the activity the purpose leads with; where a purpose lists many \
trades, pick the one it spends the most words on.
- Use `other` when the purpose is too general to place, not when you are \
merely unsure between two sectors.
- sector_confidence is 0.0 to 1.0. Low confidence with a named sector is more \
useful than `other`."""

_COMMON = """You classify Swiss companies from their registered \
commercial-register purpose text.

Rules:
- Judge only from the purpose text. Do not guess from the company name alone.
%s
- Every rationale is one short clause quoting or paraphrasing the trigger.

Reply with JSON only: a list with one object per input company,
%s"""

_SHAPE_SEGMENTS = """[{"uid": "...", "segments": [{"segment": "...", \
"confidence": 0.0, "rationale": "..."}]}]"""

_SHAPE_BOTH = """[{"uid": "...", "sector": "...", "sector_confidence": 0.0, \
"sector_rationale": "...", "segments": [{"segment": "...", "confidence": 0.0, \
"rationale": "..."}]}]"""

# Kept as a module constant because it is what the stage's prompt was before
# sectors existed, and a survey without them must still get exactly it.
SYSTEM = _COMMON % (_SEGMENT_RULES, _SHAPE_SEGMENTS)


def system_prompt(with_sectors: bool) -> str:
    if not with_sectors:
        return SYSTEM
    return _COMMON % (f"{_SECTOR_RULES}\n{_SEGMENT_RULES}", _SHAPE_BOTH)


def build_prompt(
    segments: dict[str, str], companies: list[Row],
    sectors: dict[str, str] | None = None,
) -> str:
    lines: list[str] = []
    if sectors:
        # The sectors first, because the one-of choice is the harder of the two
        # and a list read last is a list read least.
        lines.append("Sectors (choose exactly one per company):")
        for name, definition in sectors.items():
            lines.append(f"- {name}: {definition}")
        lines.append("")
    lines.append("Segments:")
    for name, definition in segments.items():
        lines.append(f"- {name}: {definition}")
    lines.append("")
    lines.append("Companies:")
    for row in companies:
        purpose = (row["purpose"] or "").strip().replace("\n", " ")
        if len(purpose) > 900:
            purpose = purpose[:900] + "…"
        lines.append(f'- uid: {row["uid"]}')
        lines.append(f'  name: {row["name"]}')
        lines.append(f'  legal_form: {row["legal_form_label"] or "?"}')
        lines.append(f"  purpose: {purpose}")
    return "\n".join(lines)


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def parse_reply(
    text: str, valid: set[str], sectors: set[str] | None = None
) -> dict[str, Verdict]:
    """Turn a model reply into {uid: Verdict}, dropping what is invalid.

    A hallucinated label is dropped rather than stored: both vocabularies are
    closed and live in arpenteur.toml, and a stray name would silently pollute
    every later filter — and, for the sector, would put a dropdown entry on the
    settings page that nobody had chosen and nothing could explain.
    """
    data = extract_json(text)
    if isinstance(data, dict):
        data = [data]
    known_sectors = sectors or set()
    result: dict[str, Verdict] = {}
    for item in data or []:
        if not isinstance(item, dict):
            continue
        uid = item.get("uid")
        if not uid:
            continue
        kept: list[dict[str, Any]] = []
        for seg in item.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            name = str(seg.get("segment", "")).strip()
            if name not in valid:
                continue
            kept.append(
                {
                    "segment": name,
                    "confidence": _confidence(seg.get("confidence", 0)),
                    "rationale": (seg.get("rationale") or "").strip() or None,
                }
            )
        chosen = str(item.get("sector") or "").strip()
        sector = (
            {
                "sector": chosen,
                "confidence": _confidence(item.get("sector_confidence", 0)),
                "rationale": (item.get("sector_rationale") or "").strip() or None,
            }
            if chosen in known_sectors
            else None
        )
        result[str(uid)] = Verdict(segments=kept, sector=sector)
    return result


def run(
    settings: Settings,
    conn: Connection,
    *,
    limit: int | None = None,
    reclassify: bool = False,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    where = "WHERE c.purpose IS NOT NULL AND TRIM(c.purpose) <> ''"
    params: list[Any] = []
    # A company in liquidation is not a prospect for any campaign. Measured on
    # 11.08.2026, 6% of the survey was BEING_CANCELLED or CANCELLED, and
    # classifying them is the cheap end of the waste: the same rows would
    # otherwise reach `enrich`, which bills per company.
    clause, values = settings.status_clause()
    if clause:
        where += f" AND {clause}"
        params.extend(values)
    if not reclassify:
        if settings.sectors:
            # A sector row is the one mark every classified company carries,
            # because the vocabulary has an `other` and the model is told to
            # use it. That makes "nothing to label" and "not yet asked" stop
            # looking alike — which they did while the only evidence was a
            # segment row, so a company with no segments was re-asked and
            # re-billed on every run for as long as the survey existed.
            where += (
                " AND NOT EXISTS"
                " (SELECT 1 FROM intel.sector x WHERE x.company_id = c.id)"
            )
        else:
            where += " AND c.id NOT IN (SELECT DISTINCT company_id FROM intel.segment)"
    sql = f"SELECT c.* FROM core.company c {where} ORDER BY c.id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, params).fetchall()

    stats: dict[str, Any] = {
        "considered": len(rows), "classified": 0, "labels": 0, "sectors": 0,
    }
    if not rows:
        log("Nothing to classify.")
        return stats

    valid = set(settings.segments)
    sectors = set(settings.sectors)
    provider = get_provider(settings)
    model = settings.llm.classify_model
    size = max(1, settings.llm.batch_size)
    system = system_prompt(bool(sectors))

    for start in range(0, len(rows), size):
        batch = rows[start : start + size]
        reply = provider.complete(
            system=system,
            user=build_prompt(settings.segments, batch, settings.sectors),
            model=model,
            # A sector, its confidence and its rationale is another forty
            # tokens per company, and a batch of twenty that runs out of them
            # comes back as truncated JSON and is dropped whole.
            max_tokens=6000 if sectors else 4000,
        )
        try:
            parsed = parse_reply(reply, valid, sectors)
        except ValueError as exc:
            log(f"  ! batch at {start}: {exc}")
            continue

        for row in batch:
            verdict = parsed.get(row["uid"]) or Verdict()
            if dry_run:
                names = ", ".join(s["segment"] for s in verdict.segments) or "(none)"
                trade = (verdict.sector or {}).get("sector", "?")
                log(f"  {row['name'][:44]:46} {trade:20} {names}")
            else:
                db.save_segments(conn, row["id"], verdict.segments, model)
                if verdict.sector:
                    db.save_sector(
                        conn, row["id"], verdict.sector["sector"],
                        confidence=verdict.sector["confidence"],
                        rationale=verdict.sector["rationale"], model=model,
                    )
            stats["classified"] += 1
            stats["labels"] += len(verdict.segments)
            stats["sectors"] += 1 if verdict.sector else 0

        if not dry_run:
            conn.commit()
        log(f"  {min(start + size, len(rows))}/{len(rows)}")

    # What the run cost, from what the API reported rather than estimated. A
    # stage that cannot be priced afterwards cannot be tuned, and four of the
    # five paid stages were dropping this on the floor.
    stats.update(getattr(provider, "spent", _NO_SPEND).as_stats())
    return stats
