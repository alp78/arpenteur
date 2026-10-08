"""The mail stage: read the mailbox, match what can be matched, file nothing.

This stage ends with a queue, never with a timeline entry. Everything it
imports sits in `crm.email_message` until a person accepts it, and the accept
is the only thing that writes to `crm.interaction`. That is a deliberate cost:
an integration that filed automatically would be finished in one run and would
also, on the evidence of every tool surveyed, put a supplier's invoice and a
family message in a client's project history within a week.

Three rules, all of them about restraint.

**Read-only, and unread stays unread.** See `sources/mail.py`: the folder is
selected read-only and every fetch peeks. Sending lives in `stages/send.py` and
reaches SMTP through `sources/smtp.py` alone, so nothing on this path can post
or alter a message.

**Envelopes for everything, bodies only for what matched.** This is somebody's
personal mailbox. A subject line is what makes the queue reviewable; the text
of a private message is content, and it is not copied into the database on the
strength of having been in the same folder. When a person files an unmatched
message by hand the body is fetched then, because their decision is what makes
it correspondence.

**A decision, once taken, is final against re-import.** Deduplication is on
Message-ID and a message already held is left untouched, state and all. A
second run must not resurrect what somebody has already declined, which is the
failure that teaches people to stop looking at the queue.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..config import Settings
from ..kernel.db import Connection
from ..modules.clients import store as clients
from ..sources import mail

# How far back a run looks when nothing is said. Long enough to pick up a
# conversation that started before the tool was pointed at the mailbox, short
# enough that the first run on a ten-year mailbox does not fetch ten years of
# envelopes to queue for review.
DEFAULT_DAYS = 90

# Envelopes per run without an explicit limit. Not a politeness limit — the
# server is the operator's own — but a review limit: a queue of two thousand
# messages is not reviewed, it is abandoned.
QUEUE_LIMIT = 500


def mailbox_for(settings: Settings) -> mail.Mailbox:
    if not settings.mail.configured:
        raise mail.MailError(
            "No mailbox configured. Set MAIL_IMAP_SERVER, MAIL_USERNAME and "
            "MAIL_PASSWORD in .env. Most Swiss hosts issue one password per "
            "application rather than accepting the account password over IMAP."
        )
    return mail.Mailbox(
        settings.mail.host, settings.mail.port,
        user=settings.mail.user, password=settings.mail.password,
    )


def consider(
    conn: Connection, header: mail.Header, *, own_domains: frozenset[str]
) -> tuple[int, str, str] | None:
    """Whose message this is, judged on the envelope alone."""
    addresses = [who.email for who in header.counterparties]
    return clients.match_company(
        conn, addresses, own_domains=own_domains, free_domains=mail.FREE_MAIL,
    )


def run(
    settings: Settings,
    conn: Connection,
    *,
    folders: list[str] | None = None,
    days: int = DEFAULT_DAYS,
    limit: int | None = None,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    own = settings.mail.domains
    since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    stats: dict[str, Any] = {
        "seen": 0, "queued": 0, "matched": 0, "known": 0, "unusable": 0,
        "folders": 0,
    }

    if not dry_run:
        # Contacts, websites and filed messages may all have taught the matcher
        # something since the last run, and what is already in the queue was
        # judged against what was known then.
        moved = clients.rematch_pending(
            conn, own_domains=own, free_domains=mail.FREE_MAIL,
        )
        conn.commit()
        if moved:
            log(f"{moved} message(s) already waiting now match a company.")
            stats["rematched"] = moved

    with mailbox_for(settings) as box:
        wanted = folders or box.readable()
        stats["folders"] = len(wanted)
        log(f"Reading {', '.join(wanted)} since {since:%d.%m.%Y}, read-only.")
        for folder in wanted:
            uids = box.uids(folder, since=since)
            if not uids:
                log(f"  {folder}: nothing since {since:%d.%m.%Y}")
                continue
            headers = list(box.headers(folder, uids))
            stats["seen"] += len(headers)
            # One query for the whole folder rather than one per message: on a
            # re-run almost everything is already held, and the point of
            # asking first is to not fetch a single body to learn that.
            held = clients.known_messages(
                conn, [h.message_id for h in headers if h.message_id]
            )
            fresh = [h for h in headers
                     if h.message_id and h.message_id not in held]
            stats["known"] += len(headers) - len(fresh)
            stats["unusable"] += sum(1 for h in headers if not h.message_id)
            log(f"  {folder}: {len(headers)} messages, {len(fresh)} new")

            for header in fresh:
                if stats["queued"] >= (limit or QUEUE_LIMIT):
                    log(f"  stopping at {stats['queued']}, the review limit. "
                        "Raise the limit or narrow the days.")
                    return stats
                found = consider(conn, header, own_domains=own)
                who = header.sender.email if header.sender else "?"
                if dry_run:
                    mark = "+" if found else "."
                    log(f"  {mark} {str(header.sent_at)[:10]} {who[:34]:36} "
                        f"{(header.subject or '')[:40]}")
                    stats["queued"] += 1
                    stats["matched"] += 1 if found else 0
                    continue
                body = None
                if found is not None:
                    # Matched, so the body is client correspondence and worth
                    # holding. Unmatched messages keep their envelope only.
                    body = box.body(folder, header.uid)
                _id, made = clients.save_email(conn, {
                    "message_id": header.message_id,
                    "thread_key": header.thread_key,
                    "folder": folder,
                    "uid": header.uid,
                    "direction": "out" if header.outgoing else "in",
                    "sent_at": header.sent_at,
                    "subject": header.subject,
                    "from_name": header.sender.name if header.sender else None,
                    "from_email": header.sender.email if header.sender else None,
                    "to_emails": [a.email for a in header.to],
                    "cc_emails": [a.email for a in header.cc],
                    "body": body.text if body else None,
                    "body_kind": body.kind if body else None,
                    "company_id": found[0] if found else None,
                    "match_method": found[1] if found else None,
                    "match_value": found[2] if found else None,
                })
                conn.commit()
                if made != "created":
                    continue
                stats["queued"] += 1
                if found:
                    stats["matched"] += 1
                    log(f"  + {who[:34]:36} {(header.subject or '')[:34]:36} "
                        f"matched on {found[1]} {found[2]}")

    log(
        f"{stats['queued']} queued for review, {stats['matched']} with a "
        "company proposed. Nothing has been filed."
    )
    return stats


def fetch_body(settings: Settings, conn: Connection, email_id: int) -> bool:
    """Fetch the text of a held message, for filing one the matcher missed.

    Returns False when the message is gone from the server, which is a normal
    outcome rather than an error: a message can be deleted between the import
    and the review, and the envelope is still worth filing without its text.
    """
    row = clients.email(conn, email_id)
    if row is None or row["body"] is not None:
        return row is not None
    try:
        with mailbox_for(settings) as box:
            body = box.body(row["folder"], int(row["uid"]))
    except mail.MailError:
        return False
    if not body.text:
        return False
    clients.set_email_body(conn, email_id, text=body.text, kind=body.kind)
    return True
