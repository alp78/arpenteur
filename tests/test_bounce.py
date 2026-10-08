"""What a delivery report is allowed to conclude about a mailbox.

The fixtures are the shapes real servers send, not invented ones: an RFC 3464
`multipart/report` as Infomaniak, Google and Microsoft write it, and the
plain-text note from `MAILER-DAEMON` that smaller Swiss hosts still send. A
parser tested only against the standard shape would miss the second kind, which
is the kind the hosts in this survey most often run.
"""

from __future__ import annotations

from email import message_from_string

import pytest

from arpenteur import db
from arpenteur.kernel.db import Connection
from arpenteur.modules.scout import store as scout_store
from arpenteur.sources import bounce
from arpenteur.stages import verify

HELD = frozenset({"info@regie-x.ch", "location@regie-x.ch", "info@regie-y.ch"})

DSN_HARD = """From: MAILER-DAEMON@mail.infomaniak.com
To: alexis@peringer.ch
Subject: Undelivered Mail Returned to Sender
Content-Type: multipart/report; report-type=delivery-status; boundary="B1"

--B1
Content-Type: text/plain; charset=utf-8

This is the mail system at host mail.infomaniak.com.

<info@regie-x.ch>: host mx.regie-x.ch said: 550 5.1.1 <info@regie-x.ch>:
    Recipient address rejected: User unknown in virtual mailbox table

--B1
Content-Type: message/delivery-status

Reporting-MTA: dns; mail.infomaniak.com

Final-Recipient: rfc822; info@regie-x.ch
Original-Recipient: rfc822;info@regie-x.ch
Action: failed
Status: 5.1.1
Diagnostic-Code: smtp; 550 5.1.1 Recipient address rejected: User unknown

--B1--
"""

DSN_SOFT = """From: MAILER-DAEMON@mail.infomaniak.com
To: alexis@peringer.ch
Subject: Delivery Status Notification (Delay)
Content-Type: multipart/report; report-type=delivery-status; boundary="B2"

--B2
Content-Type: text/plain

Your message has not yet been delivered.

--B2
Content-Type: message/delivery-status

Final-Recipient: rfc822; info@regie-y.ch
Action: delayed
Status: 4.2.2
Diagnostic-Code: smtp; 452 4.2.2 Mailbox full

--B2--
"""

PLAIN = """From: Mail Delivery Subsystem <MAILER-DAEMON@hoster.ch>
To: alexis@peringer.ch
Subject: Returned mail: see transcript for details
Content-Type: text/plain; charset=utf-8

   ----- The following addresses had permanent fatal errors -----
<location@regie-x.ch>

   ----- Transcript of session follows -----
550 5.1.1 <location@regie-x.ch>... User unknown
"""

ORDINARY = """From: Jean Dupont <jean@regie-x.ch>
To: alexis@peringer.ch
Subject: Re: votre message
Content-Type: text/plain

Bonjour, merci de votre message. Ecrivez a info@regie-x.ch pour la suite.
"""


# --- what the parser concludes ------------------------------------------------


def test_a_standard_report_settles_the_mailbox() -> None:
    found = bounce.failures_in(message_from_string(DSN_HARD), known=HELD)
    assert len(found) == 1
    failure = found[0]
    assert failure.address == "info@regie-x.ch"
    assert failure.status == bounce.BOUNCED
    assert failure.code == "5.1.1"
    assert failure.permanent is True
    assert "User unknown" in failure.reason


def test_a_transient_failure_is_not_a_conclusion() -> None:
    """Greylisting defers a first contact by design. Recording a 4.x.x as a
    refusal would condemn a working address for being written to for the first
    time, which is every address in this survey."""
    found = bounce.failures_in(message_from_string(DSN_SOFT), known=HELD)
    assert len(found) == 1
    assert found[0].status == bounce.DEFERRED
    assert found[0].permanent is False


def test_a_plain_text_bounce_is_read_too() -> None:
    """No `message/delivery-status` part anywhere. Smaller Swiss hosts send
    this and a standards-only parser loses the verdict."""
    found = bounce.failures_in(message_from_string(PLAIN), known=HELD)
    assert len(found) == 1
    assert found[0].address == "location@regie-x.ch"
    assert found[0].status == bounce.BOUNCED


def test_ordinary_mail_is_not_a_bounce() -> None:
    """A reply that quotes an address the survey holds, from a person. Reading
    this as a failure would mark a mailbox dead for having answered."""
    assert bounce.failures_in(message_from_string(ORDINARY), known=HELD) == []


def test_an_address_the_survey_does_not_hold_is_ignored() -> None:
    """A report quotes the operator's own sending address in the returned
    headers, and some quote a list manager's."""
    assert bounce.failures_in(message_from_string(DSN_HARD), known=frozenset()) == []


@pytest.mark.parametrize("subject,sender,expected", [
    ("Undelivered Mail Returned to Sender", "x@y.ch", True),
    ("Mail delivery failed", "x@y.ch", True),
    ("Unzustellbar: votre message", "x@y.ch", True),
    ("Mancata consegna", "x@y.ch", True),
    ("Échec de distribution", "x@y.ch", True),
    ("Re: votre message", "MAILER-DAEMON@hoster.ch", True),
    ("Re: votre message", "jean@regie-x.ch", False),
    ("Facture 2026", "compta@regie-x.ch", False),
])
def test_the_envelope_screen(subject, sender, expected) -> None:
    """The screen decides which messages get fetched whole, so it keeps the
    mail stage's rule: envelopes for everything, bodies only for what matched."""
    assert bounce.looks_like_report(subject, sender) is expected


# --- what reaches the database ------------------------------------------------


@pytest.fixture
def conn(db_path) -> Connection:
    connection = db.open_db(db_path)
    db.upsert_company(connection, {
        "uid": "CHE1", "name": "Régie X SA", "canton": "GE",
        "status": "ACTIVE", "hydrated": True, "method": "zefix",
    })
    connection.commit()
    for value in ("info@regie-x.ch", "location@regie-x.ch"):
        scout_store.save_contact(
            connection, company_id=1, kind="email", value=value,
            method="published",
        )
    connection.commit()
    return connection


def test_a_bounce_is_written_to_the_one_mailbox(conn) -> None:
    """`info@` failing says nothing about `location@`, and a régie commonly
    runs one and not the other."""
    rows = scout_store.save_mailbox_verdict(
        conn, "info@regie-x.ch", status=bounce.BOUNCED, note="5.1.1: unknown"
    )
    conn.commit()
    assert rows == 1
    held = {
        r["value"]: r["status"] for r in conn.execute(
            "SELECT value, status FROM core.contact"
        )
    }
    assert held["info@regie-x.ch"] == bounce.BOUNCED
    assert held["location@regie-x.ch"] is None


def test_a_dns_sweep_does_not_erase_a_bounce(conn) -> None:
    """The trap this guard exists for. `regie-x.ch` resolves perfectly and its
    MX was never in doubt, so a `verify` run after a send would otherwise mark
    a mailbox that answered 550 deliverable again, discarding the one verdict
    that cost a delivery attempt to get."""
    scout_store.save_mailbox_verdict(
        conn, "info@regie-x.ch", status=bounce.BOUNCED, note="5.1.1: unknown"
    )
    conn.commit()

    verify.run(conn, refresh=True, log=lambda _m: None,
               resolve=lambda _d, **_k: (verify.DELIVERABLE, "MX mx.regie-x.ch"))

    held = {
        r["value"]: r["status"] for r in conn.execute(
            "SELECT value, status FROM core.contact"
        )
    }
    assert held["info@regie-x.ch"] == bounce.BOUNCED, "the bounce outranks DNS"
    assert held["location@regie-x.ch"] == verify.DELIVERABLE


def test_a_bounced_address_leaves_the_send_list(conn) -> None:
    verify.run(conn, log=lambda _m: None,
               resolve=lambda _d, **_k: (verify.DELIVERABLE, "MX"))
    assert len(scout_store.send_list(conn, desk_parts=("info", "location"))) == 1
    scout_store.save_mailbox_verdict(
        conn, "info@regie-x.ch", status=bounce.BOUNCED, note="5.1.1"
    )
    scout_store.save_mailbox_verdict(
        conn, "location@regie-x.ch", status=bounce.BOUNCED, note="5.1.1"
    )
    conn.commit()
    assert scout_store.send_list(conn, desk_parts=("info", "location")) == []


def test_addresses_held_is_what_a_report_is_matched_against(conn) -> None:
    held = scout_store.addresses_held(conn)
    assert held == frozenset({"info@regie-x.ch", "location@regie-x.ch"})


# --- the stage, end to end ----------------------------------------------------


class FakeMailbox:
    """A mailbox holding raw messages, with the real `Mailbox` surface.

    Written against the three methods the stage uses, so a change to any of
    their signatures breaks here rather than only against a live server.
    """

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        self.fetched: list[int] = []
        self.readonly_selects = 0

    def __enter__(self) -> "FakeMailbox":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def uids(self, folder: str, *, since=None) -> list[int]:
        self.readonly_selects += 1
        return list(range(1, len(self.messages) + 1))

    def headers(self, folder: str, uids: list[int]):
        from arpenteur.sources.mail import header_from

        for uid in uids:
            raw = self.messages[uid - 1].encode()
            yield header_from(folder, uid, raw.split(b"\n\n", 1)[0] + b"\n\n")

    def raw(self, folder: str, uid: int) -> bytes:
        self.fetched.append(uid)
        return self.messages[uid - 1].encode()


def _settings(tmp_path):
    from types import SimpleNamespace

    return SimpleNamespace(
        mail=SimpleNamespace(
            configured=True, host="mail.test", port=993,
            user="alexis@peringer.ch", password="x",
        ),
    )


def test_the_stage_turns_a_report_into_a_verdict(conn, tmp_path) -> None:
    from arpenteur.stages import bounces

    verify.run(conn, log=lambda _m: None,
               resolve=lambda _d, **_k: (verify.DELIVERABLE, "MX"))
    box = FakeMailbox([ORDINARY, DSN_HARD, PLAIN])
    stats = bounces.run(
        _settings(tmp_path), conn, log=lambda _m: None,
        open_mailbox=lambda: box,
    )
    assert stats["messages"] == 3
    assert stats["reports"] == 2
    assert stats[bounce.BOUNCED] == 2
    assert stats["addresses"] == 2
    held = {
        r["value"]: r["status"] for r in conn.execute(
            "SELECT value, status FROM core.contact"
        )
    }
    assert held["info@regie-x.ch"] == bounce.BOUNCED
    assert held["location@regie-x.ch"] == bounce.BOUNCED


def test_the_stage_fetches_only_what_the_envelope_marked(conn, tmp_path) -> None:
    """Three messages, one of them ordinary correspondence. Its body is never
    fetched, which is the mail stage's rule and matters because this is
    somebody's own mailbox."""
    from arpenteur.stages import bounces

    box = FakeMailbox([ORDINARY, DSN_HARD])
    bounces.run(_settings(tmp_path), conn, log=lambda _m: None,
                open_mailbox=lambda: box)
    assert box.fetched == [2], "only the report was read in full"


def test_the_stage_writes_nothing_on_a_dry_run(conn, tmp_path) -> None:
    from arpenteur.stages import bounces

    box = FakeMailbox([DSN_HARD])
    stats = bounces.run(_settings(tmp_path), conn, dry_run=True,
                        log=lambda _m: None, open_mailbox=lambda: box)
    assert stats["reports"] == 1 and stats["addresses"] == 0
    row = conn.execute(
        "SELECT status FROM core.contact WHERE value = 'info@regie-x.ch'"
    ).fetchone()
    assert row["status"] is None


def test_a_permanent_failure_outranks_a_later_delay(conn, tmp_path) -> None:
    """A mailbox that answered 550 once and 450 later is gone. Taking the
    latest report would reopen it."""
    from arpenteur.stages import bounces

    delayed = DSN_SOFT.replace("info@regie-y.ch", "info@regie-x.ch")
    box = FakeMailbox([DSN_HARD, delayed])
    bounces.run(_settings(tmp_path), conn, log=lambda _m: None,
                open_mailbox=lambda: box)
    row = conn.execute(
        "SELECT status FROM core.contact WHERE value = 'info@regie-x.ch'"
    ).fetchone()
    assert row["status"] == bounce.BOUNCED


def test_an_unconfigured_mailbox_says_so_rather_than_failing(conn, tmp_path) -> None:
    from types import SimpleNamespace

    from arpenteur.stages import bounces

    said: list[str] = []
    settings = SimpleNamespace(mail=SimpleNamespace(configured=False))
    stats = bounces.run(settings, conn, log=said.append)
    assert stats["messages"] == 0
    assert any("No mailbox is configured" in line for line in said)
