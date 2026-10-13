"""Building a curated list from the survey, and keeping its verdicts fresh.

The order of operations, and nothing else: the rows come from
`modules/clients/campaigns.py`, the decisions from `sources/audience.py`.

    build    reads every firm the rule could keep and every address they hold,
             judges each firm, deduplicates across firms, applies the
             operator's pins, and writes one row per firm with its reason.
             A dry run does all of it and writes nothing, so the funnel can be
             read before the list is kept.
    refresh  resolves again the domains whose DNS verdict this list relies on
             and that are missing or older than the rule allows. Nothing else
             in the bank is touched.

A list is rebuilt freely until a campaign sends from it. After that it is
frozen, because the waves already sent were cut from its rows.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

from ..config import Settings
from ..kernel.db import Connection, Row
from ..modules.clients import campaigns as campaign_store
from ..modules.clients import store as clients_store
from ..sources import audience as rules
from ..sources import mailcheck
from . import verify


def resolve(conn: Connection, ref: str | int) -> Row:
    """An audience by code or id, or a refusal naming what exists."""
    row = (campaign_store.audience(conn, int(ref)) if str(ref).isdigit()
           else campaign_store.audience_by_code(conn, str(ref)))
    if row is None:
        known = ", ".join(r["code"] for r in campaign_store.audiences(conn)) or "none yet"
        raise ValueError(f"No audience {ref!r}. Known: {known}.")
    return row


def parse_segments(text: str) -> dict[str, float]:
    """`tourism-office=1,mountain-transport=0.7`, a band alone weighing 1."""
    weights: dict[str, float] = {}
    for part in (text or "").split(","):
        name, _, weight = part.strip().partition("=")
        if not name.strip():
            continue
        value = float(weight) if weight.strip() else 1.0
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"A band's weight is between 0 and 1; {name} has {value}.")
        weights[name.strip()] = value
    if not weights:
        raise ValueError("A list needs at least one band, e.g. tourism-office=1.")
    return weights


def create(conn: Connection, *, code: str, name: str, rule: rules.Rule) -> int:
    if rule.programme and clients_store.programme_by_code(conn, rule.programme) is None:
        raise ValueError(f"No programme {rule.programme!r}.")
    audience_id = campaign_store.add_audience(conn, code=code, name=name,
                                              rule=rule.to_json())
    conn.commit()
    return audience_id


def _firms(firm_rows: list[Row], address_rows: list[Row]) -> list[rules.Firm]:
    held: dict[int, list[rules.Address]] = {}
    for a in address_rows:
        held.setdefault(int(a["company_id"]), []).append(rules.Address(
            contact_id=int(a["contact_id"]), value=a["value"], status=a["status"],
            status_at=a["status_at"], method=a["method"],
            suppressed=bool(a["suppressed"]), refused=bool(a["refused"]),
            written_at=a["written_at"],
        ))
    return [
        rules.Firm(
            company_id=int(f["company_id"]), name=f["name"], canton=f["canton"],
            legal_form_uid=f["legal_form_uid"], active=bool(f["active"]),
            segments=tuple(f["segments"] or ()),
            addresses=tuple(held.get(int(f["company_id"]), ())),
            capital=f["capital"], hunt_note=f["hunt_note"], written_at=f["written_at"],
            programme_stage=f["programme_stage"], first_stage=f["first_stage"],
        )
        for f in firm_rows
    ]


def _pins(stored: dict[int, dict]) -> dict[int, rules.Pin]:
    return {
        company_id: rules.Pin(
            verdict=pin.get("verdict"), language=pin.get("language"),
            contact_id=pin.get("contact_id"), why=str(pin.get("why") or ""),
        )
        for company_id, pin in stored.items()
    }


def funnel(conn: Connection, audience_id: int) -> list[tuple[str, int]]:
    """The stored list's reasons, in the order the checks run."""
    counts = campaign_store.funnel_counts(conn, audience_id)
    return [(code, counts[code]) for code in rules.REASONS if code in counts]


def build(
    settings: Settings, conn: Connection, *, audience: str | int,
    dry_run: bool = False, today: date | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Judge every firm the rule could keep, and write the list unless asked not to."""
    row = resolve(conn, audience)
    rule = rules.Rule.from_json(row["rule"])
    if row["frozen_at"] and not dry_run:
        raise ValueError(
            f"{row['code']} is frozen: a campaign has sent from it. Copy it to rebuild."
        )
    programme = (clients_store.programme_by_code(conn, rule.programme)
                 if rule.programme else None)
    firm_rows, address_rows, recent = campaign_store.candidates(
        conn, segments=list(rule.segments), cantons=list(rule.cantons),
        programme_id=int(programme["id"]) if programme else None,
        members_only=rule.members_only,
    )
    today = today or date.today()
    judged = [rules.judge(firm, rule, today=today, recent_domains=recent)
              for firm in _firms(firm_rows, address_rows)]
    outcomes = rules.assign(judged, rule,
                            _pins(campaign_store.pins(conn, int(row["id"]))))
    counted = rules.funnel(outcomes)
    log(f"{row['code']}: {len(outcomes)} firms within the rule")
    for code, n in counted:
        log(f"  {code:16} {n:>5}")
    kept = sum(1 for o in outcomes if o.verdict == rules.KEPT)
    stats: dict[str, Any] = {"firms": len(outcomes), "kept": kept,
                             "dropped": len(outcomes) - kept, "funnel": dict(counted)}
    if dry_run:
        log("(dry run, nothing written)")
        return stats
    stats.update(campaign_store.save_members(conn, int(row["id"]), outcomes,
                                             rule_digest=rule.digest()))
    conn.commit()
    return stats


def refresh(
    settings: Settings, conn: Connection, *, audience: str | int,
    dry_run: bool = False, log: Callable[[str], None] = print,
    resolve_domain: Callable[..., tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Resolve again only this list's domains whose verdict is missing or old."""
    row = resolve(conn, audience)
    rule = rules.Rule.from_json(row["rule"])
    domains = campaign_store.stale_domains(conn, int(row["id"]),
                                           max_age_days=rule.max_age_days)
    if not domains:
        log(f"{row['code']}: every domain carries a verdict under "
            f"{rule.max_age_days} days old.")
        return {"domains": 0}
    extra = {"resolve": resolve_domain} if resolve_domain else {}
    stats = verify.run(conn, domains=domains, dry_run=dry_run, log=log, **extra)
    if not dry_run:
        conn.commit()
    return stats


def free_domains() -> tuple[str, ...]:
    """The consumer providers, which the results query never matches by domain."""
    return tuple(sorted(mailcheck.FREE_MAIL))
