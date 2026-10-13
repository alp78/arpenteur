"""Sending one message over SMTP, and filing a copy in the Sent folder.

The only module in Arpenteur that may open an SMTP session or write to a
mailbox. `sources/mail.py` keeps its read-only guarantee intact, enforced by
its own test, and the write half lives here so that the separation is a fact
about the package rather than a habit.

**Nothing here decides to send.** This module posts the message it is handed
and returns what happened. Choosing the recipient, checking that the address
was never suppressed, and obtaining a person's confirmation all belong to
`stages/send.py`, which is where those rules can be read in one place.

**A sent message is filed in the Sent folder.** SMTP delivers to the recipient
and tells the sender's own mailbox nothing, so a message posted this way would
otherwise exist nowhere the operator can see. `file_copy` appends it over IMAP,
which is what puts it in their own mail client and what lets the `mail` stage
import it on its next run with no special case.

**Filing is best effort and never fails a send.** The message has already left
once SMTP returns; raising afterwards would report a failure that did not
happen and invite a second send to the same person.

**TLS is verified.** `ssl.create_default_context()` checks the certificate
chain and the hostname. A host that needs otherwise is a host whose mail should
be fixed rather than trusted.
"""

from __future__ import annotations

import imaplib
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from typing import Iterable

from . import mail

# How the session is protected. `STARTTLS` upgrades a plain connection and is
# what port 587 expects; `SSL` wraps the socket from the first byte, which is
# port 465. `NONE` exists because a local relay on a loopback interface is a
# real deployment, and it refuses to carry a password.
STARTTLS = "starttls"
SSL = "ssl"
NONE = "none"

SECURITIES = (STARTTLS, SSL, NONE)

# Where a copy goes when no folder is named. Tried in order against what the
# server actually lists, because the name differs by host and by language.
SENT_CANDIDATES = (
    "Sent", "INBOX.Sent", "Sent Items", "Sent Messages",
    "Envoyés", "INBOX.Envoyés", "Gesendet", "Posta inviata",
)


class SendError(RuntimeError):
    """A message that did not leave, with the reason a person can act on."""


@dataclass(frozen=True)
class Posted:
    """What one successful send produced."""

    message_id: str
    to: str
    subject: str
    at: datetime
    filed: str = ""

    @property
    def raw_hint(self) -> str:
        return f"{self.message_id} to {self.to}"


def compose(
    *,
    sender: str,
    to: str,
    subject: str,
    body: str,
    html: str = "",
    images: dict[str, bytes] | None = None,
    sender_name: str = "",
    reply_to: str = "",
    message_id: str = "",
) -> EmailMessage:
    """One message, text first and HTML second where one was rendered.

    `multipart/alternative` in that order is what a mail client expects, and
    it is the shape that survives a reader whose client refuses HTML. HTML
    alone is one of the few structural things a filter still counts against a
    cold message, so the text part is never omitted.

    `images` carries the signature's own logo and nothing else. It maps a
    content-id to the bytes, and each one is attached inside the HTML part as
    `multipart/related`, which is what lets the signature reference `cid:name`.

    **Attached rather than fetched, and that is the whole of the rule.** A
    remote image is a request to somebody's server carrying the reader's IP and
    the moment they opened the letter, which is tracking whether or not anyone
    reads the log; a tracking pixel is the same mechanism with the picture left
    out. An attachment asks for nothing and reports nothing. So a URL here is
    refused and the bytes travel with the message.
    """
    if "@" not in (sender or ""):
        raise SendError(f"The sender address {sender!r} is not an address.")
    if "@" not in (to or ""):
        raise SendError(f"The recipient address {to!r} is not an address.")
    if not (subject or "").strip():
        raise SendError("A message with no subject reads as spam. Give one.")
    if not (body or "").strip():
        raise SendError("Refusing to send an empty message.")

    message = EmailMessage()
    message["From"] = f"{sender_name} <{sender}>" if sender_name else sender
    message["To"] = to
    message["Subject"] = subject.strip()
    message["Date"] = formatdate(localtime=True)
    domain = sender.rpartition("@")[2]
    message["Message-ID"] = message_id or make_msgid(domain=domain)
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body, subtype="plain", charset="utf-8")
    if html.strip():
        message.add_alternative(html, subtype="html", charset="utf-8")
        for name, blob in (images or {}).items():
            if not blob:
                continue
            # The HTML alternative, which `add_related` turns into a
            # `multipart/related` holding the markup and its pictures. Attaching
            # to `message` instead would hang the image beside the whole letter,
            # where `cid:` does not resolve and a reader sees a paperclip.
            message.get_payload()[-1].add_related(
                blob, maintype="image", subtype=_image_subtype(blob),
                cid=f"<{name}>", filename=f"{name}.{_image_subtype(blob)}",
            )
    return message


# The first bytes of the file, because a signature image is whatever the
# operator put on disk and an extension is a claim rather than a fact.
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"GIF8", "gif"),
)


def _image_subtype(blob: bytes) -> str:
    for magic, subtype in _MAGIC:
        if blob.startswith(magic):
            return subtype
    raise SendError(
        "The signature image is not a PNG, JPEG or GIF. Mail clients render "
        "those three and little else; SVG in particular is not displayed."
    )


@dataclass
class Sender:
    """An SMTP session, opened for as long as a batch needs it.

    One connection per batch rather than one per message: a host that accepts
    twenty-five messages on one session will often rate-limit twenty-five
    separate logins as an attack.
    """

    host: str
    port: int = 587
    user: str = ""
    password: str = ""
    security: str = STARTTLS
    timeout: int = 30

    def __post_init__(self) -> None:
        if self.security not in SECURITIES:
            raise SendError(
                f"Unknown security {self.security!r}. One of: "
                + ", ".join(SECURITIES)
            )
        if self.security == NONE and self.password:
            raise SendError(
                "Refusing to send a password over an unprotected connection. "
                "Set smtp security to 'starttls' or 'ssl'."
            )
        self._client: smtplib.SMTP | None = None

    def __enter__(self) -> "Sender":
        context = ssl.create_default_context()
        try:
            if self.security == SSL:
                self._client = smtplib.SMTP_SSL(
                    self.host, self.port, timeout=self.timeout, context=context
                )
            else:
                self._client = smtplib.SMTP(
                    self.host, self.port, timeout=self.timeout
                )
                self._client.ehlo()
                if self.security == STARTTLS:
                    self._client.starttls(context=context)
                    self._client.ehlo()
            if self.user and self.password:
                self._client.login(self.user, self.password)
        except (OSError, smtplib.SMTPException, ssl.SSLError) as exc:
            self.close()
            raise SendError(_reason(exc, self.host, self.port)) from exc
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.quit()
        except Exception:
            try:
                client.close()
            except Exception:
                pass

    def post(self, message: EmailMessage) -> Posted:
        """Hand one message to the server, or say why it refused."""
        if self._client is None:
            raise SendError("The SMTP session is not open.")
        to = parseaddr(message["To"] or "")[1]
        try:
            refused = self._client.send_message(message)
        except smtplib.SMTPException as exc:
            raise SendError(_reason(exc, self.host, self.port)) from exc
        if refused:
            detail = "; ".join(f"{a}: {c} {m!r}" for a, (c, m) in refused.items())
            raise SendError(f"The server refused the recipient. {detail}")
        return Posted(
            message_id=str(message["Message-ID"] or ""),
            to=to,
            subject=str(message["Subject"] or ""),
            at=datetime.now(timezone.utc),
        )


def file_copy(
    message: EmailMessage,
    *,
    host: str,
    port: int = 993,
    user: str,
    password: str,
    folder: str = "",
) -> str:
    """Append a sent message to the mailbox's Sent folder, best effort.

    Returns the folder written to, or an empty string. Every failure is
    swallowed: the message has already been delivered by the time this runs,
    and an exception here would describe a send that did not fail.
    """
    try:
        with imaplib.IMAP4_SSL(host, port) as imap:
            imap.login(user, password)
            target = folder or _sent_folder(imap)
            if not target:
                return ""
            raw = message.as_bytes()
            when = imaplib.Time2Internaldate(datetime.now(timezone.utc).timestamp())
            status, _ = imap.append(target, r"(\Seen)", when, raw)
            return target if status == "OK" else ""
    except Exception:
        return ""


def _sent_folder(imap: imaplib.IMAP4_SSL) -> str:
    """The Sent folder this server actually lists.

    The names come from `mail.folder_names`, which is the parser the read half
    already has tests for. The first version parsed the LIST line here and got
    it wrong against a server that leaves folder names unquoted, so every copy
    silently went unfiled.
    """
    status, data = imap.list()
    if status != "OK":
        return ""
    return _first_match(SENT_CANDIDATES, mail.folder_names(data))


def _first_match(wanted: Iterable[str], listed: list[str]) -> str:
    lowered = {name.lower(): name for name in listed}
    for candidate in wanted:
        hit = lowered.get(candidate.lower())
        if hit:
            return hit
    for name in listed:
        if "sent" in name.lower() or "envoy" in name.lower():
            return name
    return ""


def _reason(exc: Exception, host: str, port: int) -> str:
    """What went wrong, said so the operator can fix it.

    The same shape as `mail._reason`: an SMTP failure is nearly always one of
    four things, and naming which one saves a round of guessing.
    """
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return (
            f"{host} rejected the credentials. Most Swiss hosts issue one "
            "password per application and refuse the account password over "
            "SMTP."
        )
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return (
            f"{host} refused the From address. A host usually requires it to "
            "be the authenticated mailbox or one of its aliases."
        )
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return f"{host} does not offer what the session asked for: {exc}"
    if isinstance(exc, ssl.SSLError):
        return (
            f"TLS failed against {host}:{port}. Port 587 wants 'starttls' and "
            f"port 465 wants 'ssl'; a mismatch fails exactly here. {exc}"
        )
    if isinstance(exc, (TimeoutError, OSError)) and not isinstance(
        exc, smtplib.SMTPException
    ):
        return (
            f"No SMTP session to {host}:{port}. Check the host, the port and "
            f"whether the network permits it. {exc}"
        )
    return f"{host}:{port} failed: {exc}"
