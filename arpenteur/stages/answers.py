"""Reading the replies to the enrolment message, and proposing what they say.

The mail stage brings every message in from `alexis@peringer.ch` and attributes
it to a firm. This stage reads the ones that arrived after a firm was written
to, says what each carries, and leaves the record where it was.

**It proposes an enrolment and records none.** The platform and the account
come back as a proposal against a member id, and a person accepts it from the
programme page or from `arpenteur programme answer`. That is the schema's
standing rule, the one `mail.py` states and `members_who_replied` keeps: a
stage that advanced because a message arrived would be the first thing here to
file itself, and the first to put a firm in a cohort of twelve on the strength
of a token match.

**One exception, and it is an instruction rather than a claim.** A reply asking
to be left alone suppresses the address on sight. Honouring that is not filing
a conclusion about the firm, it is doing what the firm asked, and the message
this campaign sends promises it in writing. `bounces` already does this from
the mailbox; a refusal reaching here means its envelope screen missed one, so
the promise is kept twice rather than once.

**A message nobody accepted has no body.** The mail stage fetches the text only
for a message a person filed, so an unaccepted reply arrives here with an empty
body and is reported as waiting on the mail queue. Reading its text anyway
would break that rule from a second place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..config import Settings
from ..kernel.db import Connection, Row
from ..modules.clients import store as clients
from ..modules.scout import store as scout_store
from ..sources import enrol


@dataclass(frozen=True)
class Proposal:
    """One message, what it was read as, and the member it concerns."""

    row: Row
    reading: enrol.Reading

    @property
    def member_id(self) -> int:
        return int(self.row["member_id"])

    @property
    def firm(self) -> str:
        return str(self.row["company_name"])


def propose(
    conn: Connection,
    programme_id: int,
    *,
    from_stage: str = "contacted",
    limit: int = 200,
    own_domains: frozenset[str] = frozenset(),
) -> list[Proposal]:
    """Read every reply since contact, newest last, and say what each holds."""
    rows = clients.unread_answers(
        conn, programme_id, from_stage=from_stage, limit=limit
    )
    held = clients.addresses_of(
        conn, sorted({int(row["company_id"]) for row in rows})
    )
    out: list[Proposal] = []
    for row in rows:
        reading = enrol.read(
            row["subject"] or "",
            row["body"] or "",
            sender=row["from_email"] or "",
            ours=own_domains,
            theirs=held.get(int(row["company_id"]), frozenset()),
        )
        out.append(Proposal(row, reading))
    return out


def run(
    settings: Settings,
    conn: Connection,
    *,
    programme: str = "",
    from_stage: str = "contacted",
    limit: int = 200,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Report every reply as a proposal, and honour any refusal in one."""
    stats: dict[str, Any] = {
        "messages": 0, enrol.ANSWER: 0, enrol.REFUSAL: 0, enrol.OTHER: 0,
        "enrollable": 0, "unaccepted": 0, "suppressed": 0,
    }
    code = (programme or settings.enrol.programme or "").strip().upper()
    if not code:
        log("No programme named. Set [enrol] programme in arpenteur.toml, or "
            "pass --programme.")
        return stats
    found = clients.programme_by_code(conn, code)
    if found is None:
        log(f"No programme with code {code}. `arpenteur programme list` "
            "names the ones there are.")
        return stats

    proposals = propose(
        conn, int(found["id"]), from_stage=from_stage, limit=limit,
        own_domains=settings.mail.domains,
    )
    stats["messages"] = len(proposals)
    if not proposals:
        log(f"{code}: no reply has arrived since a firm at '{from_stage}' was "
            "written to.")
        return stats

    for item in proposals:
        reading = item.reading
        stats[reading.verdict] = stats.get(reading.verdict, 0) + 1
        mark = {enrol.ANSWER: "+", enrol.REFUSAL: "stop",
                enrol.OTHER: "?"}.get(reading.verdict, "?")
        log(f"  {mark:<4} {item.firm[:38]:<38} member {item.member_id}")
        if not (item.row["body"] or "").strip():
            stats["unaccepted"] += 1
            log("       the mail queue holds this message unaccepted, so its "
                "text was never fetched")
        for why in reading.why:
            log(f"       {why}")
        if reading.verdict == enrol.REFUSAL:
            at = (item.row["from_email"] or "").strip().lower()
            if at and not dry_run:
                rows = scout_store.suppress_address(
                    conn, at, note=f"asked to stop: {reading.refusal}"
                )
                stats["suppressed"] += rows
            continue
        if reading.enrollable:
            stats["enrollable"] += 1
            tail = f" {reading.account}" if reading.account else ""
            log(f"       ready to enrol on {reading.platform}{tail}")
        elif reading.verdict == enrol.ANSWER:
            log("       not enough to enrol on yet")

    log("")
    log(f"  {stats[enrol.ANSWER]} answer(s), {stats['enrollable']} ready to "
        f"enrol, {stats[enrol.REFUSAL]} refusal(s), {stats[enrol.OTHER]} "
        "other reply(ies)")
    log("  Nothing was recorded against a member. Accept a reading from the "
        "programme page, or with `arpenteur programme answer`.")
    return stats
