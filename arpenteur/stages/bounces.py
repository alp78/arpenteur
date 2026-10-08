"""Read what inbound mail says about an address: it failed, or it refused.

Two facts arrive in the same mailbox and both belong to `core.contact`.

**A failure** is the half of verification a DNS sweep cannot reach. `verify`
establishes that a domain takes mail; this establishes whether the mailbox at
it exists, which is only ever answered by having tried.

**A refusal** is the promise the outreach makes in writing. Every message
`draft` composes ends with "répondez « STOP » à cette adresse et votre adresse
sera retirée de nos listes", and for a while nothing in this program kept it:
the sentence was composed deterministically, appended to every draft, and
honoured by no code at all. A written undertaking that no routine performs is
worse than no undertaking, so the word is read here and `suppressed` is set,
which is the flag `send_list` and every selection through it already obey.

**Nothing is sent from here.** The send happens through the operator's own
provider, from the CSV `sendlist` writes, for the reason `stages/mail.py` gives
for having no send path at all: a sender owns SPF, DKIM, DMARC alignment, rate
pacing and a suppression list, and each of those is a way to lose a domain's
reputation permanently. What this stage reads is ordinary inbound mail.

**Read-only, and unread stays unread**, following the mail stage exactly. The
folder is selected read-only and every fetch peeks, so a bounce a person has
not opened is still unopened after a run.

**Only addresses the survey holds.** A delivery report quotes the operator's
own sending address in the returned headers, and some quote a list manager's.
Matching against the bank is what keeps those out of the verdicts.
"""

from __future__ import annotations

from datetime import datetime
from email import message_from_bytes
from typing import Any, Callable

from ..config import Settings
from ..kernel.db import Connection
from ..modules.scout import store as scout_store
from ..sources import bounce
from ..sources.mail import Mailbox


def run(
    settings: Settings,
    conn: Connection,
    *,
    folder: str = "INBOX",
    limit: int = 500,
    since: datetime | None = None,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
    open_mailbox: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Scan the mailbox for delivery reports and write what they settle."""
    stats: dict[str, Any] = {
        "messages": 0, "reports": 0, "addresses": 0,
        bounce.BOUNCED: 0, bounce.DEFERRED: 0, "unmatched": 0,
        bounce.REFUSED: 0, "suppressed": 0,
    }
    if not settings.mail.configured:
        log("No mailbox is configured. Set MAIL_IMAP_SERVER, MAIL_IMAP_USER "
            "and MAIL_IMAP_PASSWORD in .env.")
        return stats

    known = scout_store.addresses_held(conn)
    if not known:
        log("The bank holds no addresses, so nothing could be matched.")
        return stats

    factory = open_mailbox or (
        lambda: Mailbox(
            settings.mail.host, settings.mail.port,
            user=settings.mail.user, password=settings.mail.password,
        )
    )

    found: list[bounce.Failure] = []
    refusals: dict[str, bounce.Refusal] = {}
    with factory() as box:
        uids = box.uids(folder, since=since)
        if limit:
            uids = uids[-limit:]
        stats["messages"] = len(uids)
        # Envelopes for everything, whole messages only for what the subject or
        # the sender already marks as a failure. The mail stage's rule, and the
        # reason it matters here is the same: this is somebody's own mailbox.
        for head in box.headers(folder, uids):
            sender = head.sender.email if head.sender else ""
            report = bounce.looks_like_report(head.subject, sender)
            # A refusal does not announce itself in the envelope the way a
            # failure notice does, so the screen cannot skip on the subject
            # alone: a reply from a real person with "STOP" in the body has an
            # ordinary subject. Anything from an address the survey holds is
            # therefore read, which is a narrow enough gate to keep the mail
            # stage's rule and wide enough to catch the promise.
            from_us = sender.strip().lower() in known
            if not (report or from_us):
                continue
            raw = box.raw(folder, head.uid)
            if not raw:
                continue
            message = message_from_bytes(raw)
            failures = bounce.failures_in(message, known=known)
            if failures:
                stats["reports"] += 1
                found.extend(failures)
                continue
            refused = bounce.refusal_in(message, known=known)
            if refused is not None:
                refusals.setdefault(refused.address, refused)

    # Latest wins per address, and a permanent failure is never replaced by a
    # transient one: a mailbox that answered 550 once and 450 later is gone.
    best: dict[str, bounce.Failure] = {}
    for failure in found:
        held = best.get(failure.address)
        if held is None or (failure.permanent and not held.permanent):
            best[failure.address] = failure

    for failure in sorted(best.values(), key=lambda f: f.address):
        stats[failure.status] = stats.get(failure.status, 0) + 1
        mark = "!" if failure.permanent else "~"
        log(f"  {mark} {failure.address:<42} {failure.code}  {failure.reason[:46]}")
        if dry_run:
            continue
        rows = scout_store.save_mailbox_verdict(
            conn, failure.address, status=failure.status,
            note=f"{failure.code}: {failure.reason}"[:300],
        )
        stats["addresses"] += rows
        if not rows:
            stats["unmatched"] += 1
    for refused in sorted(refusals.values(), key=lambda r: r.address):
        stats[bounce.REFUSED] += 1
        log(f"  stop {refused.address:<42} {refused.token} in {refused.where}")
        if dry_run:
            continue
        rows = scout_store.suppress_address(
            conn, refused.address,
            note=f"refused: {refused.token!r} in {refused.where}",
        )
        stats["suppressed"] += rows

    if not dry_run:
        conn.commit()

    log(f"  {stats['messages']} message(s) read, {stats['reports']} report(s), "
        f"{stats[bounce.BOUNCED]} permanent, {stats[bounce.DEFERRED]} transient, "
        f"{stats[bounce.REFUSED]} refusal(s)"
        + (" (dry run, nothing written)" if dry_run else ""))
    if stats[bounce.REFUSED] and not dry_run:
        log("A refusal is never undone by a later run finding the address "
            "published again: a person said no, and rediscovering it is not "
            "consent.")
    return stats
