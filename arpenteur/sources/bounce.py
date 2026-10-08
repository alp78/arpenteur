"""Read a delivery failure out of a message, where there is one.

The mailbox-level answer `verify` cannot give. DNS settles the domain; whether
`info@` reaches a desk or reaches nothing is settled by trying, and the report
that comes back is the only record of it.

**Two shapes, because servers send two shapes.** RFC 3464 defines a
`multipart/report` with a `message/delivery-status` part carrying
`Final-Recipient`, `Action` and `Status`, and that is what Infomaniak, Google
and Microsoft send. Plenty of smaller Swiss hosts send a plain-text note from
`MAILER-DAEMON` with the address and the SMTP reply in the body instead. A
parser that handled only the standard one would miss the hosts this survey
writes to most.

**The class of failure matters more than the text.** A 5.x.x is permanent and
settles the mailbox. A 4.x.x is the server saying "not now", which greylisting
produces by design on a first contact, and treating it as a refusal would
condemn a working address for being new. So the two are different verdicts and
only one of them is a conclusion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from email.message import Message

from . import mailcheck

# What a report can conclude about one mailbox.
#
# `bounced` outranks `deliverable`: a domain can resolve perfectly while the
# mailbox at it does not exist, which is the case this whole exercise was
# trying to reach.
BOUNCED = "bounced"
DEFERRED = "deferred"

# `Final-Recipient: rfc822; info@example.ch`, whose type label varies.
_FINAL = re.compile(
    r"^(?:final|original)-recipient\s*:\s*[^;]*;\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_STATUS = re.compile(r"^status\s*:\s*([245])\.(\d+)\.(\d+)", re.IGNORECASE | re.MULTILINE)
_ACTION = re.compile(r"^action\s*:\s*(\w+)", re.IGNORECASE | re.MULTILINE)
_DIAGNOSTIC = re.compile(
    r"^diagnostic-code\s*:\s*[^;]*;\s*(.+)$", re.IGNORECASE | re.MULTILINE
)
# A bare SMTP reply in prose, for the hosts that send no report at all. Anchored
# on a line start so a price or a year cannot pass for a reply code.
_REPLY = re.compile(r"(?:^|\s)([245])(\d\d)[\s-]", re.MULTILINE)

# Senders that only ever carry a failure. Matched on the local part so that
# `MAILER-DAEMON@anything` counts.
_DAEMONS = frozenset({
    "mailer-daemon", "postmaster", "mail-daemon", "maildaemon",
})


# A refusal, which is a different fact from a failure. The mailbox works; the
# person behind it has said not to write again.
REFUSED = "refused"

# What a refusal looks like in the four languages the survey writes in. The
# message itself asks for « STOP », so that word carries most of them, and the
# rest are what people send instead without reading the instruction.
#
# WHY THE LIST IS WIDE AND THE MATCH IS LENIENT. The two errors are not
# symmetric. Suppressing an address that did not ask costs one contact out of
# twelve thousand. Failing to suppress one that did ask breaks the promise the
# message makes in writing, which is the thing UWG art. 3 al. 1 let. o is
# about. So a plausible refusal is honoured and reported, never held back
# pending certainty.
REFUSALS = (
    "stop", "unsubscribe", "desinscription", "desinscrire", "desabonner",
    "desabonnement", "retirez-moi", "retirer mon adresse", "ne plus recevoir",
    "ne nous ecrivez plus", "ne m'ecrivez plus", "pas interesse",
    "abmelden", "abbestellen", "austragen", "keine weiteren",
    "nicht mehr schreiben", "kein interesse",
    "cancellami", "disiscrizione", "non scrivete piu", "non interessato",
)


@dataclass(frozen=True)
class Refusal:
    """One mailbox that asked not to be written to again."""

    address: str
    token: str
    where: str


def _folded(text: str) -> str:
    return mailcheck.deaccent(text or "").lower()


def refusal_in(
    message: Message, *, known: frozenset[str] | None = None
) -> Refusal | None:
    """Whether this message is somebody asking to be left alone.

    Read from the sender rather than from the body's own quoted addresses: a
    refusal is made by whoever sent it, and the quoted original carries the
    address we wrote to. Both are checked, because a reply from a colleague's
    mailbox refuses on the firm's behalf and is the one the operator means.
    """
    sender = (message.get("from") or "")
    at = sender.rpartition("<")[2].partition(">")[0].strip().lower() or (
        sender.strip().lower()
    )
    subject = _folded(message.get("subject") or "")

    hit, where = "", ""
    for token in REFUSALS:
        if re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", subject):
            hit, where = token, "the subject"
            break
    if not hit:
        # Only the visible text, and only its opening. A refusal is short and
        # sits at the top; the same word thirty lines down is in a quoted
        # thread or a signature block.
        for part in _text_parts(message)[:2]:
            opening = _folded(part)[:400]
            for token in REFUSALS:
                if re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", opening):
                    hit, where = token, "the opening of the message"
                    break
            if hit:
                break
    if not hit:
        return None

    if known is not None and at in known:
        return Refusal(at, hit, where)
    # The sender is not an address the survey holds, which happens whenever a
    # colleague answers. The address we wrote to is quoted in the reply, so it
    # is taken from there.
    for part in _text_parts(message):
        for raw in re.findall(r"[\w.+-]+@[\w.-]+\.\w+", part):
            candidate = raw.strip().lower()
            if known is None or candidate in known:
                return Refusal(candidate, hit, where)
    return None


@dataclass(frozen=True)
class Failure:
    """One mailbox that could not be reached, and how certainly."""

    address: str
    status: str
    code: str
    reason: str

    @property
    def permanent(self) -> bool:
        return self.status == BOUNCED


def _text_parts(message: Message) -> list[str]:
    """Every readable part, the delivery-status ones first.

    Ordered that way because a report repeats the failed address in the
    returned message's own headers, and reading those first would pick up the
    recipient of the original rather than the one that failed.

    WHY A DELIVERY-STATUS PART IS SERIALISED RATHER THAN DECODED. RFC 3464
    makes each per-recipient block a set of header fields with no body, and
    Python parses them as sub-messages accordingly: `get_payload()` returns a
    list and `get_payload(decode=True)` returns None, while `decode=True` on
    the sub-messages returns an empty string, because `Final-Recipient` and
    `Status` are headers and not content. Decoding alone produced nothing and
    every standard report fell through to the plain-text fallback, which read
    `550` off the prose instead of the `5.1.1` the report stated.
    """
    status: list[str] = []
    other: list[str] = []
    for part in message.walk():
        kind = (part.get_content_type() or "").lower()
        if kind == "message/delivery-status":
            payload = part.get_payload()
            if isinstance(payload, list):
                status.extend(
                    block.as_string() for block in payload
                    if isinstance(block, Message)
                )
            continue
        if part.get_content_maintype() == "multipart":
            continue
        try:
            raw = part.get_payload(decode=True)
        except Exception:  # noqa: BLE001
            continue
        if not raw:
            continue
        other.append(
            raw.decode(part.get_content_charset() or "utf-8", "replace")
        )
    return status + other


def _classify(digit: str) -> str:
    return BOUNCED if digit == "5" else DEFERRED


# Subjects a failure notice carries, in the four languages this survey writes
# in. Matched as substrings because every host words it differently and most
# prefix the original subject.
_SUBJECTS = (
    "undeliver", "delivery status", "delivery failure", "returned mail",
    "failure notice", "mail delivery", "delivery incomplete",
    "unzustellbar", "nicht zustellbar", "unbekannt",
    "non remis", "echec de distribution", "non livre",
    "mancata consegna", "non consegnato",
)


def looks_like_report(subject: str, sender: str) -> bool:
    """Whether an envelope is worth fetching the whole message for.

    The screen exists so the stage keeps the mail stage's discipline:
    envelopes for everything, bodies only for what matched. Deliberately
    generous, because a false positive costs one fetch and a false negative
    loses a verdict that cost a delivery attempt.
    """
    local = (sender or "").strip().lower().rpartition("@")[0].rpartition("<")[2]
    if local.strip() in _DAEMONS:
        return True
    flat = mailcheck.deaccent(subject or "").lower()
    return any(word in flat for word in _SUBJECTS)


def failures_in(
    message: Message, *, known: frozenset[str] | None = None
) -> list[Failure]:
    """Every mailbox this message reports as unreachable.

    `known` restricts the answer to addresses the survey holds. Without it a
    report quotes the operator's own sending address and any mailing-list
    plumbing in the headers, and those would be recorded as bounced.
    """
    sender = message.get("from") or ""
    subject = message.get("subject") or ""
    kind = (message.get_content_type() or "").lower()
    if not (looks_like_report(subject, sender) or "report" in kind):
        return []

    parts = _text_parts(message)
    out: dict[str, Failure] = {}
    for body in parts:
        # The standard shape: one block per recipient, each with its own status.
        for block in re.split(r"\n\s*\n", body):
            found = _FINAL.search(block)
            if not found:
                continue
            address = found.group(1).strip().strip("<>").lower()
            if not mailcheck.shape_ok(address):
                continue
            if known is not None and address not in known:
                continue
            code_match = _STATUS.search(block)
            if code_match:
                code = ".".join(code_match.groups())
                status = _classify(code_match.group(1))
            else:
                # No Status field. `Action: failed` is the other RFC 3464 way
                # of saying permanent, and some reports carry only that.
                action = _ACTION.search(block)
                if action and action.group(1).lower() == "failed":
                    code, status = "5.0.0", BOUNCED
                else:
                    continue
            diagnostic = _DIAGNOSTIC.search(block)
            reason = (diagnostic.group(1).strip() if diagnostic
                      else f"reported {code}")
            out[address] = Failure(address, status, code, reason[:300])

    if out:
        return sorted(out.values(), key=lambda f: f.address)

    # Nothing standard. The plain-text hosts: find an address the survey holds
    # and the SMTP reply nearest it in the same body.
    for body in parts:
        for raw in re.findall(r"[\w.+-]+@[\w.-]+\.\w+", body):
            address = raw.strip().lower()
            if not mailcheck.shape_ok(address):
                continue
            if known is not None and address not in known:
                continue
            reply = _REPLY.search(body)
            if not reply:
                continue
            code = f"{reply.group(1)}{reply.group(2)}"
            out[address] = Failure(
                address, _classify(reply.group(1)), code,
                f"no delivery report, SMTP {code} in the body",
            )
    return sorted(out.values(), key=lambda f: f.address)
