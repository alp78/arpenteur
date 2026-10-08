"""Posting a message, under the rules that make posting it safe.

Arpenteur now has a send half. `sources/smtp.py` opens the session; this stage
decides whether anything should be handed to it. The division matters because
every rule worth having is here, in one readable place, rather than spread
across the callers that happen to send.

**Nothing leaves without `confirm=True`.** A run with no confirmation reports
what it would post and stops. The default is the safe one because the mistake
this guards against costs 2'450 messages, and no flag that defaults to sending
survives a tired operator.

**A suppressed address is never written to, and the check happens last.**
`send_list` applies the rule in bulk, but a list built an hour ago does not
know about the reply that arrived since. `address_state` is read for every
recipient immediately before the session opens, so a refusal that landed in
between still holds.

**A permanent delivery failure stops a send.** Writing again to a mailbox that
answered 550 earns a sender reputation, which is the one asset a campaign
cannot buy back.

**A draft carrying a marker cannot be posted.** `[[ MANQUE : ... ]]` is what
the draft stage writes where a setting was not filled, and the console already
withholds approval from such a draft. Saying so again here means the rule holds
whichever surface asks.

**One session per batch, capped.** `smtp.per_run` bounds a run. The cap is a
review limit rather than a politeness one: a batch nobody read is a batch that
sends the same wrong sentence twenty-five times.

**A copy is filed in Sent.** SMTP tells the sender's own mailbox nothing, so
without this a sent message would exist nowhere the operator can see. The
`mail` stage then imports it on its next run and attributes it to the firm,
which is what fills the campaign ledger's outbound column.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import Settings
from ..kernel.db import Connection
from ..modules.clients import store as clients
from ..modules.scout import store as scout_store
from ..sources import letter, smtp

# What the draft stage writes where a setting was missing. A message carrying
# it is unsendable by construction.
MARKER = "[[ MANQUE"


@dataclass
class Outcome:
    """What one run did, and what it declined to do."""

    posted: list[smtp.Posted] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)
    filed: int = 0
    considered: int = 0

    @property
    def sent(self) -> int:
        return len(self.posted)

    def as_stats(self) -> dict[str, Any]:
        return {
            "considered": self.considered,
            "sent": self.sent,
            "refused": len(self.refused),
            "filed": self.filed,
        }


@dataclass(frozen=True)
class Letter:
    """One message waiting for a decision, from a draft or typed by hand."""

    to: str
    subject: str
    body: str
    draft_id: int | None = None
    company_id: int | None = None
    firm: str = ""


def _why_not(conn: Connection, one: Letter) -> str:
    """The reason this letter must not be posted, or an empty string.

    The parameter is not called `letter`: that is the module this file imports
    for rendering, and shadowing it here is a trap for the next edit.
    """
    if MARKER in one.subject or MARKER in one.body:
        return "carries a [[ MANQUE ]] marker where a setting was not filled"
    held = scout_store.address_state(conn, one.to)
    if held is not None and held["held"]:
        if held["suppressed"]:
            return "the address asked to be left alone"
        if held["failed"]:
            return "the address returned a permanent delivery failure"
    return ""


def letters_from_drafts(
    conn: Connection, draft_ids: list[int]
) -> tuple[list[Letter], list[tuple[str, str]]]:
    """Turn draft ids into letters, refusing the ones that cannot go."""
    letters: list[Letter] = []
    refused: list[tuple[str, str]] = []
    for draft_id in draft_ids:
        row = scout_store.draft(conn, draft_id)
        if row is None:
            refused.append((f"draft {draft_id}", "no such draft"))
            continue
        name = str(row["company_name"] or f"draft {draft_id}")
        if row["sent_at"]:
            refused.append((name, "already recorded as sent"))
            continue
        if row["refused_at"]:
            refused.append((name, "was refused by a reviewer"))
            continue
        if not row["reviewed_at"]:
            refused.append((name, "has not been approved by anybody"))
            continue
        if not (row["address"] or "").strip():
            refused.append((name, "carries no recipient address"))
            continue
        letters.append(
            Letter(
                to=str(row["address"]).strip(),
                subject=str(row["subject"] or ""),
                body=str(row["body"] or ""),
                draft_id=int(row["id"]),
                company_id=int(row["company_id"]),
                firm=name,
            )
        )
    return letters, refused


def signature_path(settings: Settings):
    """Where the signature fragment lives, under the survey root."""
    return settings.root / (settings.smtp.signature_file or "signature.html")


def signature_html(settings: Settings) -> str:
    """The signature, cleaned, or an empty string when none is written."""
    if not settings.smtp.signature_file:
        return ""
    return letter.load_signature(signature_path(settings))


def run(
    settings: Settings,
    conn: Connection,
    *,
    letters: list[Letter] | None = None,
    draft_ids: list[int] | None = None,
    confirm: bool = False,
    plain: bool = False,
    log: Callable[[str], None] = print,
    open_sender: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Post what is allowed to go, having said what is not."""
    out = Outcome()
    config = settings.smtp
    if not config.configured:
        log("No SMTP is configured. Missing:")
        for gap in config.missing():
            log(f"  {gap}")
        return out.as_stats()

    waiting = list(letters or [])
    if draft_ids:
        from_drafts, refused = letters_from_drafts(conn, draft_ids)
        waiting.extend(from_drafts)
        out.refused.extend(refused)
    out.considered = len(waiting) + len(out.refused)
    if not waiting and not out.refused:
        log("Nothing to post.")
        return out.as_stats()

    allowed: list[Letter] = []
    for one in waiting:
        reason = _why_not(conn, one)
        if reason:
            out.refused.append((one.firm or one.to, reason))
            continue
        allowed.append(one)

    for who, reason in out.refused:
        log(f"  refused  {who[:40]:<40} {reason}")

    if len(allowed) > config.per_run:
        log(f"  {len(allowed)} allowed, capped at {config.per_run} for this "
            "run. Raise [smtp] per_run to change it.")
        allowed = allowed[: config.per_run]

    if not allowed:
        log("Nothing left to post once the rules applied.")
        return out.as_stats()

    if not confirm:
        log(f"{len(allowed)} message(s) would be posted from "
            f"{config.sender} through {config.host}:{config.port}:")
        for one in allowed:
            log(f"  {one.to[:42]:<42} {one.subject[:46]}")
        log("")
        log("Nothing was sent. Pass --yes to post, having read the list.")
        return out.as_stats()

    factory = open_sender or (
        lambda: smtp.Sender(
            config.host, config.port, user=config.user,
            password=config.password, security=config.security,
        )
    )
    code = (settings.enrol.programme or "").strip().upper()
    programme = clients.programme_by_code(conn, code) if code else None
    signature = signature_html(settings)
    tail = letter.to_text(signature)

    with factory() as sender:
        for one in allowed:
            text = one.body if not tail else f"{one.body.rstrip()}\n\n{tail}\n"
            message = smtp.compose(
                sender=config.sender,
                sender_name=config.sender_name,
                to=one.to,
                subject=one.subject,
                body=text,
                html=(
                    ""
                    if plain
                    else letter.render(one.body, signature_html=signature)
                ),
                reply_to=config.reply_to,
            )
            try:
                posted = sender.post(message)
            except smtp.SendError as exc:
                out.refused.append((one.firm or one.to, str(exc)))
                log(f"  failed   {one.to[:40]:<40} {exc}")
                continue
            out.posted.append(posted)
            log(f"  sent     {one.to[:40]:<40} {posted.message_id}")

            folder = smtp.file_copy(
                message, host=settings.mail.host, port=settings.mail.port,
                user=settings.mail.user, password=settings.mail.password,
                folder=config.sent_folder,
            )
            if folder:
                out.filed += 1
            else:
                log("           no copy filed; the Sent folder was not "
                    "writable, so your own client will not show this one")

            if one.draft_id is not None:
                scout_store.mark_draft_sent(conn, one.draft_id)
            if programme is not None and one.company_id is not None:
                clients.note_contacted(
                    conn, int(programme["id"]), one.company_id,
                    address=one.to, by="stage:send",
                )
            conn.commit()

    log("")
    log(f"  {out.sent} sent, {out.filed} filed in Sent, "
        f"{len(out.refused)} refused")
    return out.as_stats()
