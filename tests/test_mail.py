"""Importing correspondence: what is read, what is matched, what is filed.

The import has one job that is easy and one that is hard. Fetching messages is
easy. Deciding whose they are is not, and the failure mode is not "no match" —
it is a confident wrong match that files a supplier's invoice into a client's
project history, where somebody reads it a month later as fact.

So most of what is tested here is refusal. The operator's own domain matches
nothing, because a website contact form sends from it and would otherwise claim
every enquiry. A free-mail domain matches nobody, because one client's gerant
using Bluewin must not make every Bluewin user in Switzerland that client. A
message already decided is never reconsidered by a later run. And nothing at
all reaches `crm.interaction` without somebody having accepted it.

The mailbox itself is not mocked at the socket level: `Mailbox` is a thin skin
over imaplib and the parts worth testing are the parsing and the matching,
which take bytes and rows.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.config import MailConfig, Territory
from arpenteur.console.app import create_app
from arpenteur.kernel.db import Connection
from arpenteur.modules.clients import store as clients
from arpenteur.sources import mail

pytestmark = pytest.mark.pg


RAW = b"""Message-ID: <abc123@fondation-exemple.ch>
Date: Wed, 12 Aug 2026 11:35:00 +0200
From: "Nora Fellmann" <n.fellmann@fondation-exemple.ch>
To: me@arpenteur-demo.ch
Cc: service.informatique@fondation-exemple.ch
Subject: RE: Contact arpenteur-demo.ch
References: <root@smtp.example.ch> <second@arpenteur-demo.ch>
"""

ENCODED = (
    b"Message-ID: <enc@example.ch>\r\n"
    b"From: =?utf-8?B?SsOpcsO0bWUgTcO8bGxlcg==?= <j.muller@example.ch>\r\n"
    b"Subject: =?iso-8859-1?Q?Mise_=E0_jour?=\r\n"
    b"Date: Mon, 3 Aug 2026 09:00:00 +0200\r\n"
)

MULTIPART = (
    b"Message-ID: <mp@example.ch>\r\n"
    b"From: a@example.ch\r\n"
    b'Content-Type: multipart/mixed; boundary="B"\r\n'
    b"\r\n"
    b"--B\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"Bonjour, voici le p\xc3\xa9rim\xc3\xa8tre.\r\n"
    b"--B\r\n"
    b"Content-Type: text/html; charset=utf-8\r\n\r\n"
    b"<p>Bonjour</p>\r\n"
    b"--B\r\n"
    b"Content-Type: application/pdf\r\n"
    b'Content-Disposition: attachment; filename="offre.pdf"\r\n\r\n'
    b"%PDF-1.4 not text at all\r\n"
    b"--B--\r\n"
)


def _company(conn: Connection, name: str, uid: str) -> int:
    return db.upsert_company(conn, {"uid": uid, "name": name, "canton": "VD"})


def _queue(conn: Connection, **fields) -> int:
    row = {
        "message_id": "<m1@example.ch>",
        "thread_key": "<m1@example.ch>",
        "folder": "INBOX",
        "uid": 1,
        "direction": "in",
        "sent_at": datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc),
        "subject": "Bonjour",
        "from_name": "Nils",
        "from_email": "n.fellmann@fondation-exemple.ch",
        "to_emails": ["me@arpenteur-demo.ch"],
        "cc_emails": [],
    }
    row.update(fields)
    email_id, _made = clients.save_email(conn, row)
    return email_id


OWN = frozenset({"arpenteur-demo.ch"})


# --- reading what a message says ----------------------------------------------


def test_a_header_yields_its_participants_and_its_chain():
    header = mail.header_from("INBOX", 7, RAW)
    assert header.message_id == "<abc123@fondation-exemple.ch>"
    assert header.sender == mail.Address("Nora Fellmann", "n.fellmann@fondation-exemple.ch")
    assert [a.email for a in header.cc] == ["service.informatique@fondation-exemple.ch"]
    # The root of the chain, not the immediate parent: a thread is identified
    # by where it started, so every reply in it agrees on the same key.
    assert header.thread_key == "<root@smtp.example.ch>"
    assert header.sent_at.year == 2026


def test_a_message_that_starts_a_chain_is_its_own_root():
    raw = b"Message-ID: <first@example.ch>\r\nFrom: a@example.ch\r\n"
    assert mail.header_from("INBOX", 1, raw).thread_key == "<first@example.ch>"


def test_encoded_headers_are_read_rather_than_shown_raw():
    header = mail.header_from("INBOX", 2, ENCODED)
    assert header.sender.name == "Jérôme Müller"
    assert header.subject == "Mise à jour"


def test_a_message_with_no_message_id_is_reported_not_invented():
    """The importer refuses it. A synthesised id would defeat deduplication,
    which is the one thing the whole import rests on."""
    header = mail.header_from("INBOX", 3, b"From: a@example.ch\r\n")
    assert header.message_id == ""


def test_direction_comes_from_the_folder_not_from_the_addresses():
    incoming = mail.header_from("INBOX", 1, RAW)
    outgoing = mail.header_from("Sent", 1, RAW)
    assert not incoming.outgoing and outgoing.outgoing
    # A person who copies themselves would otherwise have their own outgoing
    # message filed as something they received.
    assert incoming.counterparties[0].email == "n.fellmann@fondation-exemple.ch"
    assert [a.email for a in outgoing.counterparties] == [
        "me@arpenteur-demo.ch", "service.informatique@fondation-exemple.ch"
    ]


def test_the_plain_part_is_preferred_and_the_attachment_ignored():
    body = mail.body_from(MULTIPART)
    assert body.kind == "text/plain"
    assert "périmètre" in body.text
    assert "PDF" not in body.text


def test_an_html_only_message_is_still_readable():
    raw = (b"Message-ID: <h@example.ch>\r\nContent-Type: text/html\r\n\r\n"
           b"<p>Bonjour<br>Nils</p><script>alert(1)</script>")
    body = mail.body_from(raw)
    assert body.kind == "text/html"
    assert "Bonjour" in body.text and "alert" not in body.text


def test_free_mail_is_recognised_and_a_company_domain_is_not():
    assert mail.Address("", "x@bluewin.ch").is_free_mail
    assert not mail.Address("", "x@fondation-exemple.ch").is_free_mail


def test_the_folders_a_person_has_already_decided_against_are_skipped():
    """Spam is other people's marketing and Trash is what somebody threw away.
    Importing either puts back exactly what was discarded."""
    assert "Trash" in mail.SKIP_FOLDERS and "Spam" in mail.SKIP_FOLDERS


# --- deciding whose message it is ---------------------------------------------


def test_an_address_published_by_the_company_matches_it(db_path):
    conn = db.open_db(db_path)
    company_id = _company(conn, "Fondation Bois-Gentil", "CHE103895771")
    db.save_contact(conn, company_id=company_id, kind="email",
                    value="N.Fellmann@fondation-exemple.ch", method="published")
    found = clients.match_company(
        conn, ["n.fellmann@fondation-exemple.ch"], own_domains=OWN,
        free_domains=mail.FREE_MAIL,
    )
    assert found == (company_id, "address", "n.fellmann@fondation-exemple.ch")
    conn.close()


def test_the_operators_own_domain_matches_nothing(db_path):
    """The finding that made this rule. A website contact form sends from the
    site's own address, so every enquiry arrives from the operator; without
    this the queue proposes the same wrong company for all of them."""
    conn = db.open_db(db_path)
    company_id = _company(conn, "Voirol", "CHE000000001")
    db.save_contact(conn, company_id=company_id, kind="email",
                    value="me@arpenteur-demo.ch", method="published")
    assert clients.match_company(
        conn, ["me@arpenteur-demo.ch"], own_domains=OWN, free_domains=mail.FREE_MAIL
    ) is None
    conn.close()


def test_a_free_mail_domain_never_matches_by_domain(db_path):
    conn = db.open_db(db_path)
    company_id = _company(conn, "Bois-Gentil", "CHE103895771")
    db.save_contact(conn, company_id=company_id, kind="email",
                    value="gerant@bluewin.ch", method="published")
    # The exact address still matches: it is that person.
    assert clients.match_company(
        conn, ["gerant@bluewin.ch"], own_domains=OWN, free_domains=mail.FREE_MAIL
    )[1] == "address"
    # A different Bluewin user is not.
    assert clients.match_company(
        conn, ["someone.else@bluewin.ch"], own_domains=OWN,
        free_domains=mail.FREE_MAIL,
    ) is None
    conn.close()


def test_a_corporate_domain_matches_a_colleague_nobody_has_met(db_path):
    conn = db.open_db(db_path)
    company_id = _company(conn, "Bois-Gentil", "CHE103895771")
    db.save_contact(conn, company_id=company_id, kind="email",
                    value="n.fellmann@fondation-exemple.ch", method="published")
    found = clients.match_company(
        conn, ["compta@fondation-exemple.ch"], own_domains=OWN, free_domains=mail.FREE_MAIL
    )
    assert found == (company_id, "domain", "fondation-exemple.ch")
    conn.close()


def test_the_website_the_survey_read_is_a_match_of_last_resort(db_path):
    conn = db.open_db(db_path)
    company_id = _company(conn, "Bois-Gentil", "CHE103895771")
    db.save_site(conn, company_id, {"url": "https://www.fondation-exemple.ch/accueil",
                                    "status": "ok"})
    found = clients.match_company(
        conn, ["direction@fondation-exemple.ch"], own_domains=OWN, free_domains=mail.FREE_MAIL
    )
    assert found == (company_id, "site", "fondation-exemple.ch")
    conn.close()


def test_nothing_known_means_no_match_rather_than_a_guess(db_path):
    conn = db.open_db(db_path)
    _company(conn, "Bois-Gentil", "CHE103895771")
    assert clients.match_company(
        conn, ["inconnu@ailleurs.ch"], own_domains=OWN, free_domains=mail.FREE_MAIL
    ) is None
    conn.close()


# --- the queue -----------------------------------------------------------------


def test_the_same_message_twice_is_one_row(db_path):
    conn = db.open_db(db_path)
    first, made = clients.save_email(conn, {
        "message_id": "<dup@example.ch>", "folder": "INBOX", "direction": "in",
    })
    second, again = clients.save_email(conn, {
        "message_id": "<dup@example.ch>", "folder": "Archives", "direction": "in",
    })
    assert (made, again) == ("created", "known") and first == second
    conn.close()


def test_a_re_import_does_not_resurrect_what_was_ignored(db_path):
    """The failure that teaches people to stop looking at the queue."""
    conn = db.open_db(db_path)
    email_id = _queue(conn)
    clients.ignore_email(conn, email_id, by="test")
    clients.save_email(conn, {"message_id": "<m1@example.ch>", "folder": "INBOX",
                              "direction": "in"})
    assert clients.email(conn, email_id)["state"] == "ignored"
    assert clients.email_queue(conn, state="pending") == []
    conn.close()


def test_matched_messages_come_first_in_the_queue(db_path):
    conn = db.open_db(db_path)
    company_id = _company(conn, "Bois-Gentil", "CHE103895771")
    _queue(conn, message_id="<a@x.ch>", thread_key="<a@x.ch>",
           sent_at=datetime(2026, 8, 12, tzinfo=timezone.utc))
    _queue(conn, message_id="<b@x.ch>", thread_key="<b@x.ch>",
           sent_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
           company_id=company_id, match_method="domain", match_value="fondation-exemple.ch")
    # Older, but matched, so it leads: a queue sorted purely by date buries
    # the two messages that matter under a month of newsletters.
    assert [r["message_id"] for r in clients.email_queue(conn)] == [
        "<b@x.ch>", "<a@x.ch>"
    ]
    conn.close()


# --- filing, and what it teaches ------------------------------------------------


def test_filing_is_the_only_thing_that_writes_to_the_timeline(db_path):
    conn = db.open_db(db_path)
    company_id = _company(conn, "Bois-Gentil", "CHE103895771")
    client_id = db.promote_to_client(conn, company_id)
    email_id = _queue(conn, body="Bonjour, voici le périmètre.",
                      body_kind="text/plain")
    assert db.timeline(conn, client_id) == []

    interaction_id, learned = clients.file_email(
        conn, email_id, client_id=client_id, by="test",
        own_domains=OWN, free_domains=mail.FREE_MAIL,
    )
    entries = db.timeline(conn, client_id)
    assert len(entries) == 1
    assert entries[0]["kind"] == "email"
    assert entries[0]["external_id"] == "<m1@example.ch>"
    assert "périmètre" in entries[0]["body"]
    assert clients.email(conn, email_id)["state"] == "filed"
    assert clients.email(conn, email_id)["interaction_id"] == interaction_id
    conn.close()


def test_a_message_filed_and_re_imported_is_not_two_entries(db_path):
    conn = db.open_db(db_path)
    company_id = _company(conn, "Bois-Gentil", "CHE103895771")
    client_id = db.promote_to_client(conn, company_id)
    email_id = _queue(conn)
    clients.file_email(conn, email_id, client_id=client_id, own_domains=OWN)
    clients.reopen_email(conn, email_id)
    clients.file_email(conn, email_id, client_id=client_id, own_domains=OWN)
    assert len(db.timeline(conn, client_id)) == 1
    conn.close()


def test_filing_teaches_the_address_and_the_domain(db_path):
    conn = db.open_db(db_path)
    company_id = _company(conn, "Bois-Gentil", "CHE103895771")
    client_id = db.promote_to_client(conn, company_id)
    email_id = _queue(conn)
    _id, learned = clients.file_email(
        conn, email_id, client_id=client_id, own_domains=OWN,
        free_domains=mail.FREE_MAIL,
    )
    assert "n.fellmann@fondation-exemple.ch" in learned and "fondation-exemple.ch" in learned
    held = {(k["kind"], k["value"]) for k in clients.links_for(conn, company_id)}
    assert ("domain", "fondation-exemple.ch") in held
    # And the operator's own address is not learned as the client's.
    assert ("address", "me@arpenteur-demo.ch") not in held
    conn.close()


def test_filing_a_free_mail_message_learns_the_person_not_the_provider(db_path):
    conn = db.open_db(db_path)
    company_id = _company(conn, "Bois-Gentil", "CHE103895771")
    client_id = db.promote_to_client(conn, company_id)
    email_id = _queue(conn, from_email="gerant@bluewin.ch")
    clients.file_email(conn, email_id, client_id=client_id, own_domains=OWN,
                       free_domains=mail.FREE_MAIL)
    held = {(k["kind"], k["value"]) for k in clients.links_for(conn, company_id)}
    assert ("address", "gerant@bluewin.ch") in held
    assert ("domain", "bluewin.ch") not in held
    conn.close()


def test_a_domain_claimed_by_two_companies_is_a_conflict_not_a_race(db_path):
    conn = db.open_db(db_path)
    first = _company(conn, "Bois-Gentil", "CHE103895771")
    second = _company(conn, "Bois-Gentil Immobilier", "CHE103895772")
    assert clients.save_email_link(conn, first, kind="domain", value="fondation-exemple.ch") == "saved"
    assert clients.save_email_link(conn, first, kind="domain", value="fondation-exemple.ch") == "known"
    # Two entities sharing a mail domain is real. Picking a winner silently
    # would file one group company's correspondence against another.
    assert clients.save_email_link(conn, second, kind="domain", value="fondation-exemple.ch") == "conflict"
    conn.close()


# --- what a decision reaches backwards to ---------------------------------------


def test_filing_one_message_matches_the_rest_of_the_thread(db_path):
    """The whole point of the learning loop. Without this, the six earlier
    messages of a conversation sit unmatched for ever: they are already held,
    and an import never looks at what it already has."""
    conn = db.open_db(db_path)
    company_id = _company(conn, "Bois-Gentil", "CHE103895771")
    client_id = db.promote_to_client(conn, company_id)
    chain = "<root@smtp.example.ch>"
    # The enquiry as the website's contact form sent it: from the operator's
    # own address, with the enquirer's name only in the text.
    form = _queue(conn, message_id="<form@arpenteur-demo.ch>", thread_key=chain,
                  from_email="me@arpenteur-demo.ch", direction="in")
    reply = _queue(conn, message_id="<reply@fondation-exemple.ch>", thread_key=chain,
                   from_email="n.fellmann@fondation-exemple.ch")
    assert clients.match_company(conn, ["me@arpenteur-demo.ch"], own_domains=OWN) is None

    clients.file_email(conn, reply, client_id=client_id, own_domains=OWN,
                       free_domains=mail.FREE_MAIL)
    moved = clients.rematch_pending(conn, own_domains=OWN,
                                    free_domains=mail.FREE_MAIL)
    assert moved == 1
    carried = clients.email(conn, form)
    assert carried["company_id"] == company_id
    assert carried["match_method"] == "thread"
    conn.close()


def test_rematching_leaves_a_decision_alone(db_path):
    conn = db.open_db(db_path)
    company_id = _company(conn, "Bois-Gentil", "CHE103895771")
    db.save_contact(conn, company_id=company_id, kind="email",
                    value="n.fellmann@fondation-exemple.ch", method="published")
    ignored = _queue(conn)
    clients.ignore_email(conn, ignored, by="test")
    assert clients.rematch_pending(conn, own_domains=OWN,
                                   free_domains=mail.FREE_MAIL) == 0
    assert clients.email(conn, ignored)["company_id"] is None
    conn.close()


def test_rematching_does_not_overwrite_a_proposal_already_shown(db_path):
    conn = db.open_db(db_path)
    first = _company(conn, "Bois-Gentil", "CHE103895771")
    second = _company(conn, "Autre", "CHE103895779")
    db.save_contact(conn, company_id=second, kind="email",
                    value="n.fellmann@fondation-exemple.ch", method="published")
    email_id = _queue(conn, company_id=first, match_method="link",
                      match_value="fondation-exemple.ch")
    assert clients.rematch_pending(conn, own_domains=OWN,
                                   free_domains=mail.FREE_MAIL) == 0
    assert clients.email(conn, email_id)["company_id"] == first
    conn.close()


# --- the page ------------------------------------------------------------------


def _app(db_path, build_settings, tmp_path):
    settings = build_settings(
        tmp_path,
        territory=Territory(cantons=("VD",), legal_forms=("0106",), name="test"),
        mail=MailConfig(host="mail.example.ch", user="me@arpenteur-demo.ch",
                        password="x"),
    )
    return TestClient(create_app(settings))


def test_the_queue_page_says_why_a_company_is_proposed(db_path, build_settings, tmp_path):
    conn = db.open_db(db_path)
    company_id = _company(conn, "Fondation Bois-Gentil", "CHE103895771")
    db.promote_to_client(conn, company_id)
    _queue(conn, company_id=company_id, match_method="domain", match_value="fondation-exemple.ch")
    conn.commit()
    conn.close()

    page = _app(db_path, build_settings, tmp_path).get("/mail")
    assert page.status_code == 200
    assert "Fondation Bois-Gentil" in page.text
    # A bare client name asks the reader to trust a join they cannot see.
    assert "fondation-exemple.ch" in page.text and "published by the company" in page.text


def test_the_page_says_that_an_unmatched_body_was_not_read(db_path, build_settings, tmp_path):
    conn = db.open_db(db_path)
    _queue(conn)
    conn.commit()
    conn.close()
    page = _app(db_path, build_settings, tmp_path).get("/mail")
    assert "has not been read" in page.text


def test_filing_from_the_page_reaches_the_timeline(db_path, build_settings, tmp_path):
    conn = db.open_db(db_path)
    company_id = _company(conn, "Bois-Gentil", "CHE103895771")
    client_id = db.promote_to_client(conn, company_id)
    email_id = _queue(conn, body="Bonjour", body_kind="text/plain")
    conn.commit()
    conn.close()

    client = _app(db_path, build_settings, tmp_path)
    reply = client.post(f"/mail/{email_id}/file", data={"client_id": client_id},
                        follow_redirects=False)
    assert reply.status_code == 303

    conn = db.open_db(db_path)
    assert len(db.timeline(conn, client_id)) == 1
    assert clients.email(conn, email_id)["state"] == "filed"
    conn.close()


def test_ignoring_from_the_page_files_nothing(db_path, build_settings, tmp_path):
    conn = db.open_db(db_path)
    company_id = _company(conn, "Bois-Gentil", "CHE103895771")
    client_id = db.promote_to_client(conn, company_id)
    email_id = _queue(conn)
    conn.commit()
    conn.close()

    client = _app(db_path, build_settings, tmp_path)
    client.post(f"/mail/{email_id}/ignore", follow_redirects=False)

    conn = db.open_db(db_path)
    assert db.timeline(conn, client_id) == []
    assert clients.email(conn, email_id)["state"] == "ignored"
    conn.close()


def test_the_page_explains_itself_when_no_mailbox_is_configured(db_path, build_settings, tmp_path):
    settings = build_settings(
        tmp_path,
        territory=Territory(cantons=("VD",), legal_forms=("0106",), name="test"),
    )
    page = TestClient(create_app(settings)).get("/mail")
    assert page.status_code == 200
    assert "No mailbox is configured" in page.text


def test_the_read_half_still_cannot_write(db_path):
    """The guarantee that survived Arpenteur gaining a send half.

    Sending now exists, in `sources/smtp.py` and nowhere else. What this
    asserts is the separation: the module that reads somebody's mailbox holds
    no SMTP client and issues no command that changes a mailbox, so importing
    correspondence still cannot mark a message read, move it or post anything.
    """
    import inspect

    from arpenteur.sources import mail as source
    from arpenteur.stages import mail as stage

    for module in (source, stage):
        text = inspect.getsource(module)
        assert "smtplib" not in text
        # The IMAP commands that change a mailbox. Their absence is what makes
        # "read-only" a property of the code rather than of the connection
        # flag, which a later edit could pass differently without noticing.
        for command in ('"APPEND"', '"STORE"', '"COPY"', '"EXPUNGE"', "Deleted"):
            assert command not in text
    assert not hasattr(mail.Mailbox, "send")
    # Read-only is stated at the SELECT and again at every fetch.
    assert "readonly=True" in inspect.getsource(mail.Mailbox._select)
    assert "BODY.PEEK" in inspect.getsource(mail.Mailbox.headers)
    assert "BODY.PEEK" in inspect.getsource(mail.Mailbox.body)
