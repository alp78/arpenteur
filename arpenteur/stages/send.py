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

**Every attempt is logged before the server is asked.** `crm.outbound` gets its
row and the row is committed first, then the message is posted, then the
outcome is written. A crash in between leaves an attempt with no outcome, and
the next send for that draft is refused until a person settles it, because a
message the server may already have taken must not go twice.

**A daily quota and a pause.** The host counts what leaves a mailbox over a
sliding 24 hours and blocks an address whose traffic looks abusive, so a run
stops at `[smtp] daily_quota` and says when room returns, and waits
`pause_seconds` between messages.

**A campaign's draft leaves only with its wave.** A draft a campaign rendered
is refused when posted any other way, so `send --draft N` cannot skip the
wave's test and approval.

**The programme credited is the one named.** It used to be read from `[enrol]`
whatever the message belonged to, which would have moved Drymos firms on the
Limen board. The caller names it now; only a caller naming nothing falls back
to the configured one, and the log says so.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..config import Settings
from ..kernel.db import Connection
from ..modules.clients import campaigns as campaign_store
from ..modules.clients import store as clients
from ..modules.scout import store as scout_store
from ..sources import letter, mailcheck, smtp

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
    variant: str = ""


def _days_since(stamp: str | None) -> int | None:
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).days


def _why_not(conn: Connection, one: Letter, *, wave_id: int | None = None,
             cooldown_days: int = 0, kind: str = "message") -> str:
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
        if held["dead"]:
            return "the address's domain no longer resolves"
    if one.draft_id is not None:
        belongs = campaign_store.wave_of_draft(conn, one.draft_id)
        if belongs is not None and belongs["wave_id"] != wave_id:
            return (f"belongs to wave {belongs['number']} of {belongs['campaign']}; "
                    "it is sent from the campaign, after the wave's test")
        if campaign_store.unsettled_for_draft(conn, one.draft_id) is not None:
            return ("an earlier attempt has no recorded outcome; settle it before "
                    "sending again, since the server may already have it")
    if kind != "test":
        # A mailbox is written to once. The cooldown below rests the firm and the
        # domain; the mailbox itself is spent by its first accepted message.
        once = campaign_store.last_written(conn, address=one.to)
        if once is not None:
            return f"received a message on {once[:10]}; a mailbox is written to once"
    if cooldown_days and kind == "message":
        domain = one.to.partition("@")[2].lower()
        last = campaign_store.last_written(
            conn, address=one.to, company_id=one.company_id,
            domain="" if domain in mailcheck.FREE_MAIL else domain,
        )
        days = _days_since(last)
        if days is not None and days < cooldown_days:
            return f"written to {days} days ago, inside the {cooldown_days}-day cooldown"
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


def signature_images(settings: Settings) -> dict[str, bytes]:
    """The pictures the signature refers to, read from disk.

    One entry, keyed `signature`, because `cid:signature` is what the stored
    fragment names. A missing file is not an error: the signature still renders
    with the logo's alt text, and a letter that refuses to go out over a
    misplaced picture is worse than one that goes out plain.
    """
    name = (settings.smtp.signature_image or "").strip()
    if not name:
        return {}
    try:
        return {"signature": (settings.root / name).read_bytes()}
    except OSError:
        return {}


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
    programme: str | None = None,
    wave_id: int | None = None,
    kind: str | None = None,
    limit: int | None = None,
    cooldown_days: int = 0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Post what is allowed to go, having said what is not.

    `programme` names the programme a sent message moves a card on: a code,
    `""` for none, or None for the one `[enrol]` names. `kind` is what the
    outbound log records, by default `message` for a draft and `letter` for a
    message typed by hand; a wave's test passes `test`, which marks nothing
    sent and moves no card. `limit` can lower `per_run` and never raises it.
    """
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

    def kind_of(one: Letter) -> str:
        return kind or ("message" if one.draft_id is not None else "letter")

    allowed: list[Letter] = []
    for one in waiting:
        reason = _why_not(conn, one, wave_id=wave_id, cooldown_days=cooldown_days,
                          kind=kind_of(one))
        if reason:
            out.refused.append((one.firm or one.to, reason))
            continue
        allowed.append(one)

    for who, reason in out.refused:
        log(f"  refused  {who[:40]:<40} {reason}")

    cap = config.per_run if limit is None else max(0, min(config.per_run, limit))
    if len(allowed) > cap:
        log(f"  {len(allowed)} allowed, capped at {cap} for this run. Raise "
            "[smtp] per_run to change it.")
        allowed = allowed[:cap]

    room = config.daily_quota - campaign_store.attempts_since(conn, hours=24)
    if allowed and room <= 0:
        log(f"The daily quota of {config.daily_quota} is used up. "
            + _room_returns(conn))
        return out.as_stats()
    if len(allowed) > room:
        log(f"  {len(allowed)} allowed, {room} left in today's quota of "
            f"{config.daily_quota}; the rest waits. " + _room_returns(conn))
        allowed = allowed[:room]

    if not allowed:
        log("Nothing left to post once the rules applied.")
        return out.as_stats()

    if not confirm:
        minutes = max(0, len(allowed) - 1) * config.pause_seconds / 60
        log(f"{len(allowed)} message(s) would be posted from "
            f"{config.sender} through {config.host}:{config.port}, "
            f"{config.pause_seconds}s apart, about {minutes:.0f} min:")
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
    if programme is None:
        code = (settings.enrol.programme or "").strip().upper()
        if code:
            log(f"  crediting {code}, the programme [enrol] names")
    else:
        code = programme.strip().upper()
    credited = clients.programme_by_code(conn, code) if code else None
    signature = signature_html(settings)
    tail = letter.to_text(signature)
    pictures = signature_images(settings) if "cid:" in signature else {}

    with factory() as sender:
        for index, one in enumerate(allowed):
            if index:
                sleep(config.pause_seconds)
                if campaign_store.attempts_since(conn, hours=24) >= config.daily_quota:
                    log("  the daily quota filled during the run; stopping. "
                        + _room_returns(conn))
                    break
                if not sender.alive():
                    try:
                        sender.reopen()
                        log("  the session had closed; opened a new one")
                    except smtp.SendError as exc:
                        log(f"  the session closed and would not reopen: {exc}")
                        break
            this_kind = kind_of(one)
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
                images={} if plain else pictures,
                reply_to=config.reply_to,
            )
            attempt = campaign_store.record_attempt(
                conn, kind=this_kind, address=one.to, subject=one.subject,
                message_id=str(message["Message-ID"] or ""), draft_id=one.draft_id,
                wave_id=wave_id, variant=one.variant or None,
            )
            conn.commit()
            try:
                posted = sender.post(message)
            except smtp.SendError as exc:
                campaign_store.record_failed(conn, attempt, str(exc))
                conn.commit()
                out.refused.append((one.firm or one.to, str(exc)))
                log(f"  failed   {one.to[:40]:<40} {exc}")
                continue
            campaign_store.record_posted(conn, attempt)
            conn.commit()
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

            if this_kind == "message":
                if one.draft_id is not None:
                    scout_store.mark_draft_sent(conn, one.draft_id)
                if credited is not None and one.company_id is not None:
                    clients.note_contacted(
                        conn, int(credited["id"]), one.company_id,
                        address=one.to, by="stage:send",
                    )
            conn.commit()

    log("")
    log(f"  {out.sent} sent, {out.filed} filed in Sent, "
        f"{len(out.refused)} refused")
    return out.as_stats()


def _room_returns(conn: Connection) -> str:
    """When the oldest attempt in the window leaves it, freeing one place."""
    oldest = campaign_store.oldest_attempt_since(conn, hours=24)
    if not oldest:
        return ""
    try:
        when = datetime.fromisoformat(oldest.replace("Z", "+00:00")) + timedelta(hours=24)
    except ValueError:
        return ""
    return f"Room returns from {when.astimezone():%d.%m.%Y %H:%M}."
