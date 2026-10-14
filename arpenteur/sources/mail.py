"""Reading a mailbox over IMAP, and nothing else.

There is no send path in this file and there is not meant to be one. Arpenteur
can post a message, and `sources/smtp.py` is the only module that may: keeping
the two halves apart is what lets this one guarantee that reading somebody's
mailbox cannot mark a message read, move it, or send anything. Importing
correspondence does not require the ability to send it, so the ability is
absent here.

Three habits are enforced here rather than left to the caller.

**The mailbox is opened read-only.** `SELECT` is issued with `readonly=True`
and every fetch uses `BODY.PEEK[...]` rather than `BODY[...]`, which is the
difference between reading a message and marking it read. A CRM that silently
clears the unread badge on somebody's inbox has damaged the thing it was asked
to observe, and the owner of the mailbox would be right to distrust it
afterwards. Belt and braces: the read-only SELECT already forbids the flag
change on a well-behaved server, and PEEK says so again in the fetch itself so
that the intent survives a later edit.

**Headers first, bodies only on demand.** Envelopes are cheap and a message
body is not, but the real reason is privacy: this is somebody's personal
mailbox, and most of what is in it has nothing to do with any client. The
caller matches on the envelope and asks for the body only for the messages
that matched, so the text of private correspondence is never copied into the
database at all. See `stages/mail.py` for the other half of that rule.

**What cannot be decoded is not guessed at.** A header in an unknown charset
comes back mangled rather than dropped, and a message with no Message-ID is
reported with an empty one so the caller can refuse it. Mail in the wild is
full of small violations of the standard and none of them are worth an
exception that stops an import halfway.
"""

from __future__ import annotations

import email
import imaplib
import re
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from typing import Iterator

# Providers whose domain says nothing about who somebody works for. A message
# from a gerant writing on his Bluewin address is still worth filing, but it
# must be matched on the address itself: matching on the domain would file
# every message from every Bluewin user in Switzerland against whichever
# client happened to have one. The list is deliberately short and covers what
# a Vaud contact actually uses; an unknown provider is treated as corporate,
# which errs towards proposing a match a human then rejects rather than
# towards silently filing nothing.
FREE_MAIL = frozenset({
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.ch", "hotmail.fr",
    "outlook.com", "outlook.fr", "live.com", "live.fr", "msn.com",
    "yahoo.com", "yahoo.fr", "ymail.com",
    "bluewin.ch", "sunrise.ch", "hispeed.ch", "vtxnet.ch", "netplus.ch",
    "romandie.com", "swissonline.ch", "citycable.ch",
    "gmx.ch", "gmx.net", "gmx.com", "web.de",
    "protonmail.com", "protonmail.ch", "proton.me", "pm.me",
    "icloud.com", "me.com", "mac.com", "aol.com", "orange.fr", "free.fr",
    "wanadoo.fr", "laposte.net", "bluemail.ch", "infomaniak.com",
})

# Folders never read. Spam is other people's marketing and Trash is what the
# owner has already decided against; importing either would put back exactly
# what a person took the trouble to discard. Drafts are unsent by definition.
SKIP_FOLDERS = frozenset({"Spam", "Junk", "Trash", "Drafts", "Promotions",
                          "SocialNetworks"})

# Folders whose messages were written by the mailbox's owner. Direction is
# read from the folder rather than guessed from the addresses, because a
# person mailing themselves in copy is common and would otherwise read as
# incoming.
SENT_FOLDERS = frozenset({"Sent", "Sent Messages", "Envoyés", "INBOX.Sent"})

_HEADER_FIELDS = ("MESSAGE-ID SUBJECT FROM TO CC DATE REFERENCES "
                  "IN-REPLY-TO REPLY-TO")

_LIST_LINE = re.compile(rb'\((?P<flags>[^)]*)\) "(?P<sep>[^"]*)" (?P<name>.+)')


@dataclass(frozen=True)
class Address:
    """One participant, as the message spells them."""

    name: str
    email: str

    @property
    def domain(self) -> str:
        _, _, domain = self.email.rpartition("@")
        return domain.lower()

    @property
    def is_free_mail(self) -> bool:
        return self.domain in FREE_MAIL


@dataclass(frozen=True)
class Header:
    """A message as its envelope describes it, with no body attached."""

    folder: str
    uid: int
    message_id: str
    subject: str
    sent_at: datetime | None
    sender: Address | None
    to: tuple[Address, ...] = ()
    cc: tuple[Address, ...] = ()
    # The Message-ID at the root of the References chain, which is as close to
    # a thread identifier as mail offers. It is not a database key: two clients
    # can start chains that a human would call one conversation, and no header
    # will ever say so.
    thread_key: str = ""
    references: tuple[str, ...] = field(default_factory=tuple)

    @property
    def outgoing(self) -> bool:
        return self.folder in SENT_FOLDERS

    @property
    def participants(self) -> tuple[Address, ...]:
        """Everyone on the message, sender first, in the order encountered."""
        seen: dict[str, Address] = {}
        for who in ((self.sender,) if self.sender else ()) + self.to + self.cc:
            seen.setdefault(who.email.lower(), who)
        return tuple(seen.values())

    @property
    def counterparties(self) -> tuple[Address, ...]:
        """Participants who are worth matching against a company.

        For an outgoing message that is the recipients, for an incoming one the
        sender and anyone else in copy. In both cases the mailbox's own address
        is left for the caller to filter, since only the caller knows it.
        """
        return tuple(self.to + self.cc) if self.outgoing else self.participants


@dataclass(frozen=True)
class Body:
    """The readable text of a message, and what it was taken from."""

    text: str
    kind: str = "text/plain"
    truncated: bool = False

def _decode(raw: str | None) -> str:
    """A header as text, whatever it was encoded in.

    RFC 2047 words, unknown charsets and outright broken encodings all end up
    here, and the useful behaviour for all three is the same: return the best
    reading available and never raise. A subject line that comes back with a
    replacement character is legible; an exception loses the message.
    """
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw))).strip()
    except Exception:
        return str(raw).strip()


def addresses(raw: str | None) -> tuple[Address, ...]:
    """Parse an address header into participants, dropping what has no address."""
    if not raw:
        return ()
    found = []
    for name, addr in getaddresses([raw]):
        addr = (addr or "").strip().lower()
        if "@" not in addr:
            continue
        found.append(Address(name=_decode(name), email=addr))
    return tuple(found)


def _sent_at(raw: str | None) -> datetime | None:
    """The Date header as an aware datetime, or None.

    Mail dates are wrong often enough that this cannot raise. A message with an
    unreadable date is still a message; the caller falls back to the time it
    was fetched and records that it did.
    """
    if not raw:
        return None
    try:
        when = parsedate_to_datetime(raw)
    except Exception:
        return None
    if when is None:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _references(message: Message) -> tuple[str, ...]:
    raw = " ".join(filter(None, (message.get("References"),
                                 message.get("In-Reply-To"))))
    return tuple(re.findall(r"<[^<>@\s]+@[^<>\s]+>", raw))


def header_from(folder: str, uid: int, raw: bytes) -> Header:
    """Build a Header from the bytes of a HEADER.FIELDS fetch."""
    message = email.message_from_bytes(raw)
    refs = _references(message)
    message_id = (message.get("Message-ID") or "").strip()
    return Header(
        folder=folder,
        uid=uid,
        message_id=message_id,
        subject=_decode(message.get("Subject")),
        sent_at=_sent_at(message.get("Date")),
        sender=(addresses(message.get("From")) or (None,))[0],
        to=addresses(message.get("To")),
        cc=addresses(message.get("Cc")),
        # The first reference is the root of the chain; a message that starts
        # one is its own root.
        thread_key=refs[0] if refs else message_id,
        references=refs,
    )


def _text_of(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    without = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    without = re.sub(r"(?i)<br\s*/?>|</p>", "\n", without)
    without = re.sub(r"<[^>]+>", " ", without)
    without = re.sub(r"&nbsp;?", " ", without)
    return re.sub(r"[ \t]{2,}", " ", without).strip()


def body_from(raw: bytes, *, limit: int = 200_000) -> Body:
    """The readable text of a fetched message.

    Prefers text/plain and falls back to the HTML part stripped of its tags,
    because a message that exists only as HTML is common and refusing to read
    it would lose real correspondence. Attachments are ignored: what this is
    for is the record of what was said, and a 4 MB PDF in the timeline is not
    that. Filing an attachment is a separate, deliberate act.
    """
    message = email.message_from_bytes(raw)
    plain, html = [], []
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        if "attachment" in disposition:
            continue
        if part.get_content_type() == "text/plain":
            plain.append(_text_of(part))
        elif part.get_content_type() == "text/html":
            html.append(_text_of(part))
    if plain:
        text, kind = "\n".join(plain), "text/plain"
    elif html:
        text, kind = _strip_html("\n".join(html)), "text/html"
    else:
        text, kind = "", "none"
    text = text.strip()
    return Body(text=text[:limit], kind=kind, truncated=len(text) > limit)


def folder_names(rows: list | None) -> list[str]:
    """The folder names in a LIST response.

    Public because the send half needs the same answer to find the Sent folder,
    and because hand-rolling it there got it wrong: a server may quote the name
    or leave it bare, and splitting on the last quote finds the separator
    instead of the name on every server that leaves it bare.
    """
    names = []
    for row in rows or []:
        found = _LIST_LINE.match(row if isinstance(row, bytes) else b"")
        if not found:
            continue
        name = found.group("name").decode("utf-8", errors="replace")
        names.append(name.strip('"'))
    return names


class Mailbox:
    """An open, read-only IMAP session.

    A context manager because a mail server holds the connection open and a
    stage that raises halfway through should not leave a session behind. The
    credential is passed in rather than read here: nothing in this module
    touches the environment, so a test can drive it without one.
    """

    def __init__(self, host: str, port: int = 993, *, user: str,
                 password: str, timeout: float = 30.0) -> None:
        self.host, self.port = host, port
        self.user, self.password, self.timeout = user, password, timeout
        self._imap: imaplib.IMAP4_SSL | None = None
        self._selected: str = ""

    def __enter__(self) -> "Mailbox":
        self._imap = imaplib.IMAP4_SSL(
            self.host, self.port, ssl_context=ssl.create_default_context(),
            timeout=self.timeout,
        )
        try:
            self._imap.login(self.user, self.password)
        except imaplib.IMAP4.error as exc:
            # The server's own words, which distinguish a wrong password from
            # a mailbox that forbids external clients. Infomaniak issues one
            # password per device and refuses the account password outright,
            # and the two failures are indistinguishable without this text.
            raise MailError(
                f"{self.host} refused the login for {self.user}: "
                f"{_reason(exc)}"
            ) from exc
        return self

    def __exit__(self, *exc: object) -> None:
        if self._imap is not None:
            try:
                self._imap.logout()
            except Exception:
                pass
            self._imap = None

    @property
    def imap(self) -> imaplib.IMAP4_SSL:
        if self._imap is None:
            raise MailError("The mailbox is not open. Use it as a context manager.")
        return self._imap

    def folders(self) -> list[str]:
        ok, rows = self.imap.list()
        if ok != "OK":
            raise MailError(f"The server refused LIST: {ok}")
        return folder_names(rows)

    def readable(self) -> list[str]:
        """Folders worth importing from, in a stable order."""
        return [name for name in self.folders() if name not in SKIP_FOLDERS]

    def _select(self, folder: str) -> None:
        if self._selected == folder:
            return
        ok, _ = self.imap.select(f'"{folder}"', readonly=True)
        if ok != "OK":
            raise MailError(f"Cannot open {folder} read-only")
        self._selected = folder

    def uids(self, folder: str, *, since: datetime | None = None) -> list[int]:
        """The UIDs in a folder, oldest first.

        `since` becomes an IMAP SINCE, which is a date and not a timestamp: the
        server compares whole days in its own timezone. It is a way of not
        asking for ten years of mail, not a precise incremental cursor, and the
        caller deduplicates on Message-ID regardless.
        """
        self._select(folder)
        criteria: list[str] = ["ALL"]
        if since is not None:
            criteria = ["SINCE", since.strftime("%d-%b-%Y")]
        ok, data = self.imap.uid("SEARCH", None, *criteria)
        if ok != "OK":
            raise MailError(f"The server refused SEARCH in {folder}: {ok}")
        return [int(raw) for raw in (data[0] or b"").split()]

    def headers(self, folder: str, uids: list[int]) -> Iterator[Header]:
        """Envelopes for the given UIDs, in batches, without marking anything."""
        if not uids:
            return
        self._select(folder)
        for batch in _batched(uids, 50):
            ok, data = self.imap.uid(
                "FETCH", ",".join(str(u) for u in batch),
                f"(BODY.PEEK[HEADER.FIELDS ({_HEADER_FIELDS})])",
            )
            if ok != "OK":
                raise MailError(f"The server refused FETCH in {folder}: {ok}")
            for uid, raw in _pairs(data):
                yield header_from(folder, uid, raw)

    def raw(self, folder: str, uid: int) -> bytes:
        """One message exactly as it arrived. PEEK, so it stays unread.

        `body` is the right reader for correspondence and the wrong one for a
        delivery report: it keeps `text/plain` and `text/html` and discards
        everything else, and a report's verdict lives in a
        `message/delivery-status` part that is neither. The MIME tree has to
        survive, so the bytes come back whole and the caller parses them.
        """
        self._select(folder)
        ok, data = self.imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if ok != "OK":
            raise MailError(f"The server refused FETCH of {uid} in {folder}")
        for _uid, blob in _pairs(data):
            return blob
        return b""

    def body(self, folder: str, uid: int) -> Body:
        """The full text of one message. PEEK, so it stays unread."""
        self._select(folder)
        ok, data = self.imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if ok != "OK":
            raise MailError(f"The server refused FETCH of {uid} in {folder}")
        for _uid, raw in _pairs(data):
            return body_from(raw)
        return Body(text="", kind="none")


class MailError(RuntimeError):
    """Something the mail server said no to, in words worth showing a person."""


def _reason(exc: Exception) -> str:
    """An IMAP error's text, without the b'' that imaplib wraps it in."""
    args = getattr(exc, "args", ())
    raw = args[0] if args else exc
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _pairs(data: list) -> Iterator[tuple[int, bytes]]:
    """UID and payload out of imaplib's alternating list.

    A FETCH response arrives as a list where a tuple carries the literal and
    the loose bytes that follow are the closing parenthesis. The UID is in the
    tuple's first half, which is why it is parsed back out of a string here
    rather than tracked alongside the request: the server may return the UIDs
    in any order it likes.
    """
    for item in data or []:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        prefix = item[0].decode("utf-8", errors="replace") if item[0] else ""
        found = re.search(r"UID (\d+)", prefix)
        if not found:
            continue
        yield int(found.group(1)), item[1] or b""


def _batched(items: list[int], size: int) -> Iterator[list[int]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]
