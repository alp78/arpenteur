"""The profile stage: sort companies into segments from their purpose text.

The registered purpose is a company's own description of what it does, filed
with the state. It is public, it is written by the company, and for a first
pass it beats anything a crawler would infer from a homepage. No site is
fetched at this stage.

A company can land in several segments: a regulated laboratory is both
compliance-sector and data-driven, and forcing one label would throw away the
more interesting half.
"""

from __future__ import annotations

from typing import Any, Callable

from .. import db
from ..config import Settings
from ..kernel.db import Connection, Row
from ..llm import extract_json, get_provider

SYSTEM = """You classify Swiss companies into market segments from their \
registered commercial-register purpose text.

Rules:
- Judge only from the purpose text. Do not guess from the company name alone.
- A company may match several segments, or none. Returning none is correct \
and expected for a plain trade or retail activity.
- confidence is 0.0 to 1.0 and reflects how clearly the text supports it.
- rationale is one short clause quoting or paraphrasing the trigger.

Reply with JSON only: a list with one object per input company,
[{"uid": "...", "segments": [{"segment": "...", "confidence": 0.0, \
"rationale": "..."}]}]"""


def build_prompt(segments: dict[str, str], companies: list[Row]) -> str:
    lines = ["Segments:"]
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


def parse_reply(text: str, valid: set[str]) -> dict[str, list[dict[str, Any]]]:
    """Turn a model reply into {uid: [segment dicts]}, dropping what is invalid.

    A hallucinated segment name is dropped rather than stored: the segment
    vocabulary is closed and lives in arpenteur.toml, and a stray label would
    silently pollute every later filter.
    """
    data = extract_json(text)
    if isinstance(data, dict):
        data = [data]
    result: dict[str, list[dict[str, Any]]] = {}
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
            try:
                confidence = float(seg.get("confidence", 0))
            except (TypeError, ValueError):
                confidence = 0.0
            kept.append(
                {
                    "segment": name,
                    "confidence": max(0.0, min(1.0, confidence)),
                    "rationale": (seg.get("rationale") or "").strip() or None,
                }
            )
        result[str(uid)] = kept
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
        # Left join rather than NOT IN: a company classified into zero
        # segments still has no segment rows, and must not be re-asked.
        where += " AND c.id NOT IN (SELECT DISTINCT company_id FROM intel.segment)"
    sql = f"SELECT c.* FROM core.company c {where} ORDER BY c.id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, params).fetchall()

    stats: dict[str, Any] = {"considered": len(rows), "classified": 0, "labels": 0}
    if not rows:
        log("Nothing to classify.")
        return stats

    valid = set(settings.segments)
    provider = get_provider(settings)
    model = settings.llm.classify_model
    size = max(1, settings.llm.batch_size)

    for start in range(0, len(rows), size):
        batch = rows[start : start + size]
        reply = provider.complete(
            system=SYSTEM,
            user=build_prompt(settings.segments, batch),
            model=model,
            max_tokens=4000,
        )
        try:
            parsed = parse_reply(reply, valid)
        except ValueError as exc:
            log(f"  ! batch at {start}: {exc}")
            continue

        for row in batch:
            segments = parsed.get(row["uid"], [])
            if dry_run:
                names = ", ".join(s["segment"] for s in segments) or "(none)"
                log(f"  {row['name'][:48]:50} {names}")
            else:
                db.save_segments(conn, row["id"], segments, model)
            stats["classified"] += 1
            stats["labels"] += len(segments)

        if not dry_run:
            conn.commit()
        log(f"  {min(start + size, len(rows))}/{len(rows)}")

    return stats
