"""Posting a message, and the rules that decide whether it may be posted.

Arpenteur gained a send half on the owner's instruction, which replaced a
documented guarantee that nothing could leave. What replaces a guarantee has to
be tested harder than what it replaced, so these are the promises:

that nothing leaves without an explicit confirmation, the default being a dry
run; that an address which asked to be left alone is never written to, checked
at the last moment rather than trusted from a list; that a mailbox which
returned a permanent failure is not tried again; that a draft carrying an
unfilled marker cannot be posted from any surface; that a draft nobody approved
cannot be posted; and that a run is capped, because a batch nobody read is a
batch that sends the same wrong sentence twenty-five times.

The SMTP session is a stand-in throughout. A test that posted real mail would
be a test nobody dares run.
"""

from __future__ import annotations

from dataclasses import replace
from email.message import EmailMessage
from pathlib import Path

import pytest

from arpenteur import db
from arpenteur.config import MailConfig, SmtpConfig
from arpenteur.kernel.db import Connection
from arpenteur.modules.clients import store as clients_store
from arpenteur.sources import smtp
from arpenteur.stages import send as send_stage

pytestmark = pytest.mark.pg


class FakeSender:
    """Records what it was asked to post, and refuses what it is told to."""

    def __init__(self, refuse: set[str] | None = None, *, drops_after: int = 0) -> None:
        self.posted: list[EmailMessage] = []
        self.refuse = refuse or set()
        self.opened = 0
        # How many posts the session survives before it reads as closed, so a
        # test can watch the run reopen it between messages. 0 means never.
        self.drops_after = drops_after
        self.reopened = 0

    def alive(self) -> bool:
        return not (self.drops_after and len(self.posted) >= self.drops_after
                    and self.reopened == 0)

    def reopen(self) -> None:
        self.reopened += 1

    def __call__(self) -> "FakeSender":
        return self

    def __enter__(self) -> "FakeSender":
        self.opened += 1
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def post(self, message: EmailMessage) -> smtp.Posted:
        to = str(message["To"])
        if to in self.refuse:
            raise smtp.SendError(f"the server refused {to}")
        self.posted.append(message)
        return smtp.Posted(
            message_id=str(message["Message-ID"]), to=to,
            subject=str(message["Subject"]),
            at=db.now_dt() if hasattr(db, "now_dt") else __import__(
                "datetime"
            ).datetime.now(__import__("datetime").timezone.utc),
        )


@pytest.fixture
def smtp_settings(build_settings, tmp_path: Path):
    """Settings with a configured send half and no real credentials."""
    return build_settings(
        tmp_path,
        mail=MailConfig(host="imap.test", user="me@peringer.ch", password="x"),
        smtp=SmtpConfig(
            host="smtp.test", port=587, user="me@peringer.ch", password="x",
            sender="me@peringer.ch", sender_name="Me", per_run=3,
            pause_seconds=0,
        ),
    )


@pytest.fixture
def conn(db_path: str) -> Connection:
    connection = db.open_db(db_path)
    row = connection.execute(
        "INSERT INTO core.company (uid, name, canton, legal_form_uid,"
        " legal_form_label, legal_seat, status, purpose, source_url, method,"
        " fetched_at, first_seen, hydrated_at)"
        " VALUES ('CHE300000001', 'Regie Test SA', 'VD', '0106', 'SA',"
        " 'Lausanne', 'ACTIVE', 'gerance', 'https://example.test', 'zefix',"
        " '2026-09-01', '2026-09-01', '2026-09-01') RETURNING id"
    ).fetchone()
    connection.execute(
        "INSERT INTO core.contact (company_id, kind, value, source_url,"
        " method, fetched_at) VALUES (%s, 'email', 'info@regie-test.ch',"
        " 'https://example.test', 'website', now())",
        (int(row["id"]),),
    )
    connection.commit()
    return connection


@pytest.fixture
def company_id(conn: Connection) -> int:
    return int(conn.execute("SELECT id FROM core.company").fetchone()["id"])


def _draft(
    conn: Connection, company_id: int, *, address: str = "info@regie-test.ch",
    body: str = "Bonjour, ceci est le message.", reviewed: bool = True,
    sent: bool = False, subject: str = "Limen",
) -> int:
    row = conn.execute(
        "INSERT INTO intel.draft (company_id, language, subject, body, model,"
        " address, brief_digest, reviewed_at, sent_at)"
        " VALUES (%s, 'fr', %s, %s, 'test', %s, 'abc',"
        f" {'now()' if reviewed else 'NULL'}, {'now()' if sent else 'NULL'})"
        " RETURNING id",
        (company_id, subject, body, address),
    ).fetchone()
    conn.commit()
    return int(row["id"])


# --- composing -----------------------------------------------------------------


def test_a_message_carries_the_headers_a_receiver_expects() -> None:
    message = smtp.compose(
        sender="me@peringer.ch", sender_name="Me", to="you@test.ch",
        subject="Limen", body="Bonjour",
    )
    assert message["To"] == "you@test.ch"
    assert "me@peringer.ch" in message["From"]
    assert message["Subject"] == "Limen"
    assert message["Date"]
    assert message["Message-ID"].endswith("@peringer.ch>")
    assert message.get_content_type() == "text/plain"


def test_an_empty_subject_or_body_is_refused() -> None:
    """A message with neither reads as spam, which is the one thing to avoid."""
    with pytest.raises(smtp.SendError, match="subject"):
        smtp.compose(sender="a@b.ch", to="c@d.ch", subject=" ", body="x")
    with pytest.raises(smtp.SendError, match="empty"):
        smtp.compose(sender="a@b.ch", to="c@d.ch", subject="x", body="  ")


def test_an_address_that_is_not_an_address_is_refused() -> None:
    with pytest.raises(smtp.SendError, match="recipient"):
        smtp.compose(sender="a@b.ch", to="not-an-address", subject="x", body="y")


def test_plain_text_only() -> None:
    """A tracked image in a cold message is what a filter looks for."""
    message = smtp.compose(
        sender="a@b.ch", to="c@d.ch", subject="x", body="é accentué"
    )
    assert not message.is_multipart()
    assert "é accentué" in message.get_content()


# --- the session ---------------------------------------------------------------


def test_an_unknown_security_is_refused_before_any_socket_opens() -> None:
    with pytest.raises(smtp.SendError, match="security"):
        smtp.Sender("h", 25, security="tls-maybe")


def test_a_password_is_never_offered_over_an_unprotected_session() -> None:
    with pytest.raises(smtp.SendError, match="unprotected"):
        smtp.Sender("h", 25, user="u", password="p", security=smtp.NONE)


# --- the confirmation ----------------------------------------------------------


def test_a_run_without_confirmation_posts_nothing(
    smtp_settings, conn, company_id
) -> None:
    """The default, because the mistake costs one message per firm."""
    fake = FakeSender()
    said: list[str] = []
    stats = send_stage.run(
        smtp_settings, conn, draft_ids=[_draft(conn, company_id)],
        open_sender=fake, log=said.append,
    )
    assert stats["sent"] == 0
    assert fake.opened == 0, "no session is opened for a dry run"
    assert any("Nothing was sent" in line for line in said)


def test_confirming_posts_and_records(smtp_settings, conn, company_id) -> None:
    draft_id = _draft(conn, company_id)
    fake = FakeSender()
    stats = send_stage.run(
        smtp_settings, conn, draft_ids=[draft_id], confirm=True,
        open_sender=fake, log=lambda _m: None,
    )
    assert stats["sent"] == 1
    assert len(fake.posted) == 1
    assert fake.posted[0]["To"] == "info@regie-test.ch"
    row = conn.execute(
        "SELECT sent_at FROM intel.draft WHERE id = %s", (draft_id,)
    ).fetchone()
    assert row["sent_at"] is not None, "the draft records that it left"


# --- who may be written to -----------------------------------------------------


def test_an_address_that_asked_to_be_left_alone_is_never_written_to(
    smtp_settings, conn, company_id
) -> None:
    """The promise the message itself makes, kept at the last moment.

    `send_list` applies the same rule in bulk, and a list built an hour ago
    does not know about the reply that arrived since.
    """
    draft_id = _draft(conn, company_id)
    conn.execute(
        "UPDATE core.contact SET suppressed = true WHERE kind = 'email'"
    )
    conn.commit()
    fake = FakeSender()
    stats = send_stage.run(
        smtp_settings, conn, draft_ids=[draft_id], confirm=True,
        open_sender=fake, log=lambda _m: None,
    )
    assert stats["sent"] == 0
    assert fake.posted == []
    assert conn.execute(
        "SELECT sent_at FROM intel.draft WHERE id = %s", (draft_id,)
    ).fetchone()["sent_at"] is None


def test_a_mailbox_that_failed_permanently_is_not_tried_again(
    smtp_settings, conn, company_id
) -> None:
    draft_id = _draft(conn, company_id)
    conn.execute(
        "UPDATE core.contact SET status = 'bounced' WHERE kind = 'email'"
    )
    conn.commit()
    fake = FakeSender()
    stats = send_stage.run(
        smtp_settings, conn, draft_ids=[draft_id], confirm=True,
        open_sender=fake, log=lambda _m: None,
    )
    assert stats["sent"] == 0 and fake.posted == []


def test_a_draft_carrying_a_marker_cannot_be_posted(
    smtp_settings, conn, company_id
) -> None:
    """The console withholds approval from one of these; so does this."""
    draft_id = _draft(
        conn, company_id,
        body="Bonjour, le lien est [[ MANQUE : lien TestFlight ]].",
    )
    fake = FakeSender()
    stats = send_stage.run(
        smtp_settings, conn, draft_ids=[draft_id], confirm=True,
        open_sender=fake, log=lambda _m: None,
    )
    assert stats["sent"] == 0 and fake.posted == []


def test_a_draft_nobody_approved_cannot_be_posted(
    smtp_settings, conn, company_id
) -> None:
    draft_id = _draft(conn, company_id, reviewed=False)
    fake = FakeSender()
    stats = send_stage.run(
        smtp_settings, conn, draft_ids=[draft_id], confirm=True,
        open_sender=fake, log=lambda _m: None,
    )
    assert stats["sent"] == 0 and fake.posted == []


def test_a_draft_already_sent_is_not_sent_twice(
    smtp_settings, conn, company_id
) -> None:
    draft_id = _draft(conn, company_id, sent=True)
    fake = FakeSender()
    stats = send_stage.run(
        smtp_settings, conn, draft_ids=[draft_id], confirm=True,
        open_sender=fake, log=lambda _m: None,
    )
    assert stats["sent"] == 0 and fake.posted == []


def test_every_refusal_names_the_firm_and_the_reason(
    smtp_settings, conn, company_id
) -> None:
    draft_id = _draft(conn, company_id, reviewed=False)
    said: list[str] = []
    send_stage.run(
        smtp_settings, conn, draft_ids=[draft_id], confirm=True,
        open_sender=FakeSender(), log=said.append,
    )
    joined = " ".join(said)
    assert "Regie Test SA" in joined
    assert "approved" in joined


# --- the cap -------------------------------------------------------------------


def test_a_run_is_capped_at_per_run(smtp_settings, conn, company_id) -> None:
    """A review limit before a politeness one."""
    letters = [
        send_stage.Letter(to=f"a{n}@elsewhere.test", subject="x", body="y")
        for n in range(6)
    ]
    fake = FakeSender()
    stats = send_stage.run(
        smtp_settings, conn, letters=letters, confirm=True,
        open_sender=fake, log=lambda _m: None,
    )
    assert stats["sent"] == 3, "per_run is 3 in these settings"
    assert len(fake.posted) == 3


# --- the programme ------------------------------------------------------------


def test_a_sent_draft_moves_the_programme_card(
    smtp_settings, conn, company_id
) -> None:
    """The same link the console's own Mark as sent makes."""
    programme_id = clients_store.add_programme(conn, "SENDTEST", "Outreach")
    conn.commit()
    settings = replace(
        smtp_settings, enrol=replace(smtp_settings.enrol, programme="SENDTEST")
    )
    send_stage.run(
        settings, conn, draft_ids=[_draft(conn, company_id)], confirm=True,
        open_sender=FakeSender(), log=lambda _m: None,
    )
    row = conn.execute(
        "SELECT stage, contact FROM crm.programme_member"
        " WHERE programme_id = %s AND company_id = %s",
        (programme_id, company_id),
    ).fetchone()
    assert row is not None, "the firm joined the programme on first contact"
    assert row["stage"] == "contacted"
    assert row["contact"] == "info@regie-test.ch"


# --- configuration -------------------------------------------------------------


def test_an_unconfigured_send_half_names_what_is_missing(
    build_settings, tmp_path, conn, company_id
) -> None:
    settings = build_settings(tmp_path, smtp=SmtpConfig())
    said: list[str] = []
    stats = send_stage.run(
        settings, conn, draft_ids=[_draft(conn, company_id)],
        confirm=True, open_sender=FakeSender(), log=said.append,
    )
    assert stats["sent"] == 0
    joined = " ".join(said)
    assert "SMTP_SERVER" in joined and "SMTP_SENDER" in joined


def test_a_server_refusal_is_reported_and_does_not_stop_the_batch(
    smtp_settings, conn, company_id
) -> None:
    letters = [
        send_stage.Letter(to="bad@elsewhere.test", subject="x", body="y"),
        send_stage.Letter(to="good@elsewhere.test", subject="x", body="y"),
    ]
    fake = FakeSender(refuse={"bad@elsewhere.test"})
    stats = send_stage.run(
        smtp_settings, conn, letters=letters, confirm=True,
        open_sender=fake, log=lambda _m: None,
    )
    assert stats["sent"] == 1
    assert stats["refused"] == 1
    assert [str(m["To"]) for m in fake.posted] == ["good@elsewhere.test"]


# --- the folder parser that got this wrong once -------------------------------


def test_the_sent_folder_is_found_whether_the_name_is_quoted_or_bare() -> None:
    """The bug that made every filed copy silently vanish.

    Infomaniak lists folder names unquoted, and splitting on the last quote
    found the separator rather than the name.
    """
    from arpenteur.sources import mail

    bare = [b'(\\HasNoChildren \\UnMarked) "/" Sent']
    quoted = [b'(\\HasNoChildren) "/" "Sent"']
    assert mail.folder_names(bare) == ["Sent"]
    assert mail.folder_names(quoted) == ["Sent"]
    assert smtp._first_match(smtp.SENT_CANDIDATES, ["INBOX", "Sent"]) == "Sent"
    assert smtp._first_match(smtp.SENT_CANDIDATES, ["INBOX", "Envoyés"]) == "Envoyés"
    assert smtp._first_match(smtp.SENT_CANDIDATES, ["INBOX"]) == ""


# --- the formatted part and the signature -------------------------------------


def test_a_message_carries_both_a_text_and_an_html_part() -> None:
    """What a mail client expects, and what survives a client refusing HTML."""
    from arpenteur.sources import letter

    message = smtp.compose(
        sender="a@b.ch", to="c@d.ch", subject="x",
        body="Bonjour,\n\nUn paragraphe.\n",
        html=letter.render("Bonjour,\n\nUn paragraphe.\n"),
    )
    assert message.is_multipart()
    kinds = [part.get_content_type() for part in message.walk()]
    assert "text/plain" in kinds and "text/html" in kinds
    assert kinds.index("text/plain") < kinds.index("text/html"), (
        "text first, which is the order a client reads as a preference"
    )


def test_an_ordered_list_renders_as_one() -> None:
    from arpenteur.sources import letter

    html = letter.to_html("Le déroulement:\n\n1. Premier.\n2. Second.\n")
    assert "<ol" in html and html.count("<li") == 2
    # The numbering comes from the list rather than the text. Asserted on the
    # item's own content, because "1." also matches the 1.1rem in the styles.
    assert ">Premier.</li>" in html
    assert ">1. Premier.</li>" not in html


def test_a_bare_url_becomes_a_link_and_cannot_reopen_a_tag() -> None:
    from arpenteur.sources import letter

    html = letter.to_html("Voir https://peringer.ch/x?a=1&b=2 pour la suite.")
    assert 'href="https://peringer.ch/x?a=1&amp;b=2"' in html
    danger = letter.to_html('<script>alert(1)</script> et <b>gras</b>')
    assert "<script>" not in danger
    assert "&lt;b&gt;gras&lt;/b&gt;" in danger, "the body is escaped, not parsed"


def test_the_font_stack_starts_with_helvetica() -> None:
    """A Swiss letter to a Swiss reader, and no web font is guaranteed."""
    from arpenteur.sources import letter

    document = letter.render("Bonjour.")
    assert "Helvetica" in document
    assert "<style" not in document, "Gmail strips a style block"


def test_a_pasted_signature_loses_its_script_and_its_handlers() -> None:
    from arpenteur.sources import letter

    pasted = (
        '<div onclick="steal()">Alexis <b>P</b>'
        '<script>x()</script>'
        '<a href="javascript:bad()">bad</a>'
        '<a href="https://peringer.ch">site</a>'
        '<img src="https://tracker.test/p.gif">'
        "</div>"
    )
    cleaned = letter.clean_signature(pasted)
    assert "onclick" not in cleaned
    assert "<script>" not in cleaned and "x()" not in cleaned
    assert "javascript:" not in cleaned
    assert 'href="https://peringer.ch"' in cleaned
    assert "<b>P</b>" in cleaned


def test_a_signature_round_trips_through_the_file(tmp_path: Path) -> None:
    from arpenteur.sources import letter

    path = tmp_path / "signature.html"
    letter.store_signature(path, '<div>Alexis <i>P</i><script>x()</script></div>')
    assert letter.load_signature(path) == "<div>Alexis <i>P</i></div>"
    assert letter.load_signature(tmp_path / "absent.html") == ""


def test_the_signature_reaches_both_parts(
    smtp_settings, conn, company_id, tmp_path: Path
) -> None:
    """A reader whose client refuses HTML must not receive an unsigned letter."""
    from arpenteur.sources import letter

    letter.store_signature(
        tmp_path / "signature.html", "<div>Alexis Peringer</div>"
    )
    settings = replace(
        smtp_settings,
        smtp=replace(smtp_settings.smtp, signature_file="signature.html"),
    )
    fake = FakeSender()
    send_stage.run(
        settings, conn, draft_ids=[_draft(conn, company_id)], confirm=True,
        open_sender=fake, log=lambda _m: None,
    )
    message = fake.posted[0]
    parts = {
        part.get_content_type(): part.get_content()
        for part in message.walk() if not part.is_multipart()
    }
    assert "Alexis Peringer" in parts["text/plain"]
    assert "Alexis Peringer" in parts["text/html"]


def test_plain_sends_one_part_only(
    smtp_settings, conn, company_id
) -> None:
    fake = FakeSender()
    send_stage.run(
        smtp_settings, conn, draft_ids=[_draft(conn, company_id)], confirm=True,
        plain=True, open_sender=fake, log=lambda _m: None,
    )
    assert not fake.posted[0].is_multipart()


# --- the signature's own picture ---------------------------------------------

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_a_signature_picture_rides_inside_the_html_part() -> None:
    """`cid:` resolves within `multipart/related` and nowhere else.

    Attached to the message instead, the picture hangs beside the whole letter,
    the reference does not resolve, and the reader sees a paperclip and a
    broken image where the logo should be.
    """
    message = smtp.compose(
        sender="a@b.ch", to="c@d.ch", subject="s", body="text",
        html="<p>text</p><img src='cid:signature'>", images={"signature": PNG},
    )
    kinds = [part.get_content_type() for part in message.walk()]
    assert kinds == [
        "multipart/alternative", "text/plain", "multipart/related",
        "text/html", "image/png",
    ]
    picture = [p for p in message.walk() if p.get_content_type() == "image/png"][0]
    assert picture["Content-ID"] == "<signature>"


def test_a_picture_that_no_html_could_reference_is_not_attached() -> None:
    message = smtp.compose(
        sender="a@b.ch", to="c@d.ch", subject="s", body="text", images={"x": PNG},
    )
    assert "image/png" not in [p.get_content_type() for p in message.walk()]


def test_an_svg_logo_is_refused_with_the_reason() -> None:
    """What a webmail signature editor exports, and what no client renders."""
    with pytest.raises(smtp.SendError, match="SVG"):
        smtp.compose(
            sender="a@b.ch", to="c@d.ch", subject="s", body="t",
            html="<p>t</p>", images={"signature": b"<?xml version='1.0'?><svg/>"},
        )


def test_a_missing_picture_does_not_stop_the_letter(tmp_path, smtp_settings) -> None:
    settings = replace(
        smtp_settings,
        smtp=replace(smtp_settings.smtp, signature_image="nowhere/absent.png"),
    )
    assert send_stage.signature_images(settings) == {}


def test_no_picture_is_configured_means_no_picture(smtp_settings) -> None:
    settings = replace(
        smtp_settings, smtp=replace(smtp_settings.smtp, signature_image=""),
    )
    assert send_stage.signature_images(settings) == {}


# --- what a kept `style` may and may not carry --------------------------------

def test_a_pasted_style_keeps_its_typography() -> None:
    """The reason the attribute is kept at all.

    A signature copied out of a mail client is nothing but inline style, so
    dropping the attribute left the right words in none of the right shapes.
    """
    from arpenteur.sources import letter

    cleaned = letter.clean_signature(
        '<td style="color:rgb(18, 100, 138);font-size:11px;'
        'letter-spacing:0.08em;text-transform:uppercase">Role</td>'
    )
    assert "color:rgb(18, 100, 138)" in cleaned
    assert "text-transform:uppercase" in cleaned
    assert "letter-spacing:0.08em" in cleaned


def test_a_pasted_style_cannot_fetch_anything() -> None:
    """`url(` is the tracking pixel the tag allow-list was written to refuse,
    arriving through the attribute instead of through `<img>`."""
    from arpenteur.sources import letter

    cleaned = letter.clean_signature(
        '<div style="color:red;background:url(https://tracker.test/p.gif)">x</div>'
    )
    assert "color:red" in cleaned
    assert "url(" not in cleaned and "tracker.test" not in cleaned


def test_a_pasted_style_cannot_cover_the_message() -> None:
    from arpenteur.sources import letter

    cleaned = letter.clean_signature(
        '<div style="position:absolute;top:0;left:0;z-index:99;color:red">x</div>'
    )
    assert "position" not in cleaned and "z-index" not in cleaned
    assert "color:red" in cleaned


def test_a_pasted_style_cannot_run_anything() -> None:
    from arpenteur.sources import letter

    for hostile in (
        '<div style="width:1px;behavior:url(evil.htc)">x</div>',
        '<div style="width:1px;-moz-binding:url(evil.xml)">x</div>',
        '<div style="color:expression(alert(1))">x</div>',
    ):
        cleaned = letter.clean_signature(hostile)
        assert "behavior" not in cleaned and "binding" not in cleaned
        assert "expression" not in cleaned


def test_a_signature_may_point_at_the_picture_the_message_carries() -> None:
    from arpenteur.sources import letter

    cleaned = letter.clean_signature('<img src="cid:signature" width="218">')
    assert 'src="cid:signature"' in cleaned


def test_the_stored_signature_is_what_the_mail_client_wrote() -> None:
    """The committed file, read as a recipient's client would read it."""
    from arpenteur.sources import letter

    stored = Path(__file__).resolve().parents[1] / "signature.html"
    fragment = stored.read_text(encoding="utf-8")
    assert letter.clean_signature(fragment) == fragment.strip(), (
        "signature.html holds something the sanitiser would change, so what is "
        "sent is not what is in the file"
    )
    assert "cid:signature" in fragment
    assert letter.to_text(fragment).splitlines()[0] == "Alexis Peringer"


# --- campaigns, the quota and the outbound log --------------------------------

from dataclasses import replace as _replace  # noqa: E402

from arpenteur.config import EnrolConfig  # noqa: E402
from arpenteur.modules.clients import campaigns as _campaigns  # noqa: E402


def _letters(n: int) -> list[send_stage.Letter]:
    return [send_stage.Letter(to=f"person{i}@other-domain-{i}.ch", subject="s", body="b")
            for i in range(n)]


def _programme(conn: Connection, code: str) -> int:
    pid = clients_store.add_programme(conn, code, code)
    conn.commit()
    return pid


def test_the_named_programme_is_credited_not_the_configured_one(
    smtp_settings, conn, company_id
) -> None:
    _programme(conn, "limen")
    drymos = _programme(conn, "drymos")
    settings = _replace(smtp_settings, enrol=EnrolConfig(programme="LIMEN"))
    send_stage.run(settings, conn, draft_ids=[_draft(conn, company_id)], confirm=True,
                   programme="DRYMOS", open_sender=FakeSender(), log=lambda _m: None)
    members = conn.execute("SELECT programme_id FROM crm.programme_member").fetchall()
    assert [m["programme_id"] for m in members] == [drymos]


def test_an_empty_programme_credits_nobody(smtp_settings, conn, company_id) -> None:
    _programme(conn, "limen")
    settings = _replace(smtp_settings, enrol=EnrolConfig(programme="LIMEN"))
    send_stage.run(settings, conn, draft_ids=[_draft(conn, company_id)], confirm=True,
                   programme="", open_sender=FakeSender(), log=lambda _m: None)
    assert conn.execute("SELECT count(*) AS n FROM crm.programme_member").fetchone()["n"] == 0


def test_a_campaign_draft_is_refused_outside_its_wave(smtp_settings, conn, company_id) -> None:
    draft_id = _draft(conn, company_id)
    audience = _campaigns.add_audience(conn, code="a", name="a", rule={"segments": {}})
    member = conn.execute(
        "INSERT INTO crm.audience_member (audience_id, company_id, address, verdict,"
        " reason_code, reason) VALUES (%s, %s, 'info@regie-test.ch', 'kept', 'kept', 'x')"
        " RETURNING id", (audience, company_id)).fetchone()["id"]
    campaign = _campaigns.add_campaign(conn, code="c", name="c", audience_id=audience,
                                       templates="templates/x", strategy="score-overall")
    wave = conn.execute("INSERT INTO crm.campaign_wave (campaign_id, number) VALUES (%s, 1)"
                        " RETURNING id", (campaign,)).fetchone()["id"]
    conn.execute("INSERT INTO crm.campaign_message (campaign_id, wave_id, member_id, draft_id)"
                 " VALUES (%s, %s, %s, %s)", (campaign, wave, member, draft_id))
    conn.commit()
    fake = FakeSender()
    stats = send_stage.run(smtp_settings, conn, draft_ids=[draft_id], confirm=True,
                           open_sender=fake, log=lambda _m: None)
    assert stats["sent"] == 0 and fake.posted == []
    stats = send_stage.run(smtp_settings, conn, draft_ids=[draft_id], confirm=True,
                           wave_id=wave, open_sender=fake, log=lambda _m: None)
    assert stats["sent"] == 1


def test_the_quota_counts_tests_and_letters_and_says_when_room_returns(
    smtp_settings, conn
) -> None:
    settings = _replace(smtp_settings, smtp=_replace(smtp_settings.smtp, daily_quota=2,
                                                     per_run=10))
    _campaigns.record_attempt(conn, kind="test", address="me@peringer.ch", subject="t",
                              message_id="<t@test>")
    conn.commit()
    lines: list[str] = []
    stats = send_stage.run(settings, conn, letters=_letters(3), confirm=True,
                           open_sender=FakeSender(), log=lines.append)
    assert stats["sent"] == 1
    assert any("Room returns" in line for line in lines)


def test_the_pause_falls_between_messages_only(smtp_settings, conn) -> None:
    settings = _replace(smtp_settings, smtp=_replace(smtp_settings.smtp, pause_seconds=30))
    naps: list[float] = []
    send_stage.run(settings, conn, letters=_letters(3), confirm=True,
                   open_sender=FakeSender(), log=lambda _m: None, sleep=naps.append)
    assert naps == [30, 30]


def test_limit_lowers_per_run_and_never_raises_it(smtp_settings, conn) -> None:
    fake = FakeSender()
    send_stage.run(smtp_settings, conn, letters=_letters(5), confirm=True, limit=2,
                   open_sender=fake, log=lambda _m: None)
    assert len(fake.posted) == 2
    fake = FakeSender()
    send_stage.run(smtp_settings, conn, letters=_letters(5), confirm=True, limit=50,
                   open_sender=fake, log=lambda _m: None)
    assert len(fake.posted) == 3, "per_run is 3 in these settings"


def test_the_attempt_is_recorded_before_the_server_is_asked(
    smtp_settings, conn, company_id
) -> None:
    seen: list[int] = []

    class Witness(FakeSender):
        def post(self, message):
            seen.append(conn.execute(
                "SELECT count(*) AS n FROM crm.outbound WHERE message_id = %s"
                " AND posted_at IS NULL", (str(message["Message-ID"]),)).fetchone()["n"])
            return super().post(message)

    send_stage.run(smtp_settings, conn, draft_ids=[_draft(conn, company_id)], confirm=True,
                   open_sender=Witness(), log=lambda _m: None)
    assert seen == [1]
    row = conn.execute("SELECT kind, posted_at FROM crm.outbound").fetchone()
    assert row["kind"] == "message" and row["posted_at"] is not None


def test_an_attempt_without_an_outcome_blocks_a_resend(smtp_settings, conn, company_id) -> None:
    draft_id = _draft(conn, company_id)
    _campaigns.record_attempt(conn, kind="message", address="info@regie-test.ch",
                              subject="s", message_id="<lost@test>", draft_id=draft_id)
    conn.commit()
    lines: list[str] = []
    stats = send_stage.run(smtp_settings, conn, draft_ids=[draft_id], confirm=True,
                           open_sender=FakeSender(), log=lines.append)
    assert stats["sent"] == 0
    assert any("no recorded outcome" in line for line in lines)


def test_a_dead_domain_is_refused_at_the_last_moment(smtp_settings, conn, company_id) -> None:
    draft_id = _draft(conn, company_id)
    conn.execute("UPDATE core.contact SET status = 'dead'")
    conn.commit()
    stats = send_stage.run(smtp_settings, conn, draft_ids=[draft_id], confirm=True,
                           open_sender=FakeSender(), log=lambda _m: None)
    assert stats["sent"] == 0


def test_the_cooldown_is_rechecked_at_send_time(smtp_settings, conn, company_id) -> None:
    _draft(conn, company_id, sent=True)
    fresh = _draft(conn, company_id, address="contact@regie-test.ch")
    stats = send_stage.run(smtp_settings, conn, draft_ids=[fresh], confirm=True,
                           cooldown_days=180, open_sender=FakeSender(), log=lambda _m: None)
    assert stats["sent"] == 0


def test_a_test_send_marks_nothing_sent_and_moves_no_card(
    smtp_settings, conn, company_id
) -> None:
    _programme(conn, "drymos")
    one = send_stage.Letter(to="me@peringer.ch", subject="[TEST] s", body="b",
                            company_id=company_id)
    stats = send_stage.run(smtp_settings, conn, letters=[one], confirm=True, kind="test",
                           programme="DRYMOS", open_sender=FakeSender(), log=lambda _m: None)
    assert stats["sent"] == 1
    assert conn.execute("SELECT count(*) AS n FROM crm.programme_member").fetchone()["n"] == 0
    assert conn.execute("SELECT kind FROM crm.outbound").fetchone()["kind"] == "test"


def test_a_lost_session_is_reopened_between_messages_only(smtp_settings, conn) -> None:
    fake = FakeSender(drops_after=1)
    send_stage.run(smtp_settings, conn, letters=_letters(3), confirm=True,
                   open_sender=fake, log=lambda _m: None)
    assert fake.reopened == 1 and len(fake.posted) == 3


def test_the_signature_image_is_read_from_the_file(tmp_path: Path) -> None:
    from arpenteur.config import load_settings

    (tmp_path / "arpenteur.toml").write_text(
        '[smtp]\nsignature_image = "elsewhere/logo.png"\ndaily_quota = 40\n',
        encoding="utf-8")
    smtp_config = load_settings(tmp_path).smtp
    assert smtp_config.signature_image == "elsewhere/logo.png"
    assert smtp_config.daily_quota == 40
