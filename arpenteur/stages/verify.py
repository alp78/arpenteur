"""Check the address bank as far as it can honestly be checked.

The bank held 12'111 addresses and not one recorded verdict: `contact.status`
was NULL on every row, while `mailcheck.verify` and its `Verdict` sat unused.
This stage is the join between them.

**What a DNS answer can and cannot settle.** It settles the domain. A domain
that does not resolve cannot take mail, so every address at it is dead, and 16
in this bank were: `info@livy-immobilien.ch` and `contact@blackrent.ch` among
them, all of which resolved on the day they were found. It does not settle the
mailbox. `info@` at a domain with an MX record may reach a desk, may reach a
catch-all that discards it, and DNS cannot tell those apart.

**Why no SMTP probe.** The next step up is to open a session with the exchange
and offer `RCPT TO` without sending. Three things spoil it. A catch-all accepts
every local part, so the answer is not about the mailbox; a greylisting server
defers a first contact by design, so one pass produces unknowns that need
retries; and twelve thousand probes from one address is what a blocklist is for,
which costs the survey its own deliverability to learn something a send would
have told it. The definitive existence test is a staged send that captures
bounces, and `store.send_list` is what feeds it.

**One lookup per domain.** 12'111 addresses sit at 5'139 domains, so resolving
per address would be two and a half times the queries for identical answers.
The verdict is written to every address at the domain.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Sequence


from ..kernel.db import Connection
from ..modules.scout import store as scout_store
from ..sources import mailcheck

# What a verdict can say, and what each one means for a send.
#
# `deliverable` is the domain taking mail, not the mailbox existing: it is the
# strongest thing DNS offers and it is still not proof. `maybe` is an address
# record with no MX, which RFC 5321 §5.1 requires a sender to try and which
# small Swiss hosts rely on, so it is sendable and worth separating. `dead` is
# the domain not existing, which is the one verdict here that is certain.
# `unknown` is a resolver that did not answer, and it is deliberately not
# `dead`: a timeout is a fact about the network, not about the domain.
DELIVERABLE, MAYBE, DEAD, UNKNOWN = (
    mailcheck.DELIVERABLE, mailcheck.MAYBE, mailcheck.DEAD, mailcheck.UNKNOWN)


def resolve_domain(
    domain: str, *, timeout: float = 6.0
) -> tuple[str, str]:
    """One domain's verdict, as a status and the note behind it.

    The same verdict the hunt reads, so a list never keeps an address at a
    domain whose named mail server does not exist.
    """
    return mailcheck.mail_verdict(domain, timeout=timeout)


def run(
    conn: Connection,
    *,
    refresh: bool = False,
    stale_after_days: int = 30,
    workers: int = 48,
    limit: int | None = None,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
    resolve: Callable[..., tuple[str, str]] = resolve_domain,
    domains: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Resolve every stale domain in the bank and record what came back.

    `domains` narrows the run to a named set, which is how one curated list is
    refreshed before its waves are planned without re-resolving the whole bank.
    """
    if domains is None:
        domains = scout_store.domains_to_check(
            conn, stale_after_days=stale_after_days, refresh=refresh
        )
    domains = list(domains)
    if limit:
        domains = domains[:limit]

    stats: dict[str, Any] = {
        "domains": len(domains), "addresses": 0,
        DELIVERABLE: 0, MAYBE: 0, DEAD: 0, UNKNOWN: 0,
    }
    if not domains:
        log("Every domain in the bank carries a current verdict.")
        return stats

    if dry_run:
        log(f"{len(domains)} domain(s) would be resolved:")
        for domain in domains[:20]:
            log(f"  {domain}")
        if len(domains) > 20:
            log(f"  ... and {len(domains) - 20} more")
        return stats

    with ThreadPoolExecutor(max_workers=workers) as pool:
        verdicts = list(pool.map(lambda d: (d, *resolve(d)), domains))

    for domain, status, note in verdicts:
        rows = scout_store.save_domain_verdict(
            conn, domain, status=status, note=note
        )
        stats[status] = stats.get(status, 0) + 1
        stats["addresses"] += rows
        if status == DEAD:
            log(f"  dead  {domain:<38} {rows} address(es)")
    conn.commit()

    log(f"  {stats['domains']:,} domains, {stats['addresses']:,} addresses: "
        f"{stats[DELIVERABLE]:,} deliverable, {stats[MAYBE]:,} maybe, "
        f"{stats[DEAD]:,} dead, {stats[UNKNOWN]:,} unknown")
    return stats
