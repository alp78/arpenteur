"""The review surface, and the reply proposal.

Two things the stages left to SQL. A queue of twenty-five messages reviewed
through psql is a queue that goes out unread, and a card that advanced because
mail arrived would be the first thing in this schema to file itself.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.config import Territory
from arpenteur.console import create_app
from arpenteur.console import draft_page
from arpenteur.kernel.db import Connection
from arpenteur.modules.clients import store as clients_store
from arpenteur.modules.scout import store as scout_store

GOOD = "Madame, Monsieur,\n\nUn texte.\n\nComment participer\n\nhttps://x/y"
FLAWED = "Madame, Monsieur,\n\nUn texte.\n\nOuvrez [[ MANQUE : lien TestFlight ]]"


@pytest.fixture
def settings(db_path) -> SimpleNamespace:
    return SimpleNamespace(
        database_url=db_path,
        territory=Territory(cantons=("GE",), legal_forms=("0106",), name="ge"),
        segments={}, sectors={},
        mail=SimpleNamespace(configured=False),
        root=None,
    )


@pytest.fixture
def client(settings, conn) -> TestClient:
    return TestClient(create_app(settings))


@pytest.fixture
def conn(db_path) -> Connection:
    connection = db.open_db(db_path)
    for uid, name in (("CHE1", "Régie Alpha SA"), ("CHE2", "Régie Beta SA")):
        db.upsert_company(connection, {
            "uid": uid, "name": name, "canton": "GE", "legal_seat": "Genève",
            "purpose": "gérance d'immeubles", "status": "ACTIVE",
            "hydrated": True, "method": "zefix",
        })
    connection.commit()
    return connection


def _draft(conn, company_id: int, body: str, *, address: str) -> int:
    row = conn.execute(
        "INSERT INTO intel.draft (company_id, address, language, subject,"
        " body, model, brief_digest) VALUES (%s,%s,'fr','Limen',%s,'m','d1')"
        " RETURNING id",
        (company_id, address, body),
    ).fetchone()
    conn.commit()
    return int(row["id"])


# --- the queue ----------------------------------------------------------------


def test_the_queue_holds_what_nobody_has_read(conn) -> None:
    _draft(conn, 1, GOOD, address="info@alpha.ch")
    second = _draft(conn, 2, GOOD, address="info@beta.ch")
    assert len(scout_store.drafts_awaiting(conn)) == 2
    scout_store.review_draft(conn, second)
    conn.commit()
    awaiting = scout_store.drafts_awaiting(conn)
    assert [r["id"] for r in awaiting] != [second]
    assert len(awaiting) == 1


def test_a_sent_draft_leaves_the_queue_entirely(conn) -> None:
    one = _draft(conn, 1, GOOD, address="info@alpha.ch")
    scout_store.mark_draft_sent(conn, one)
    conn.commit()
    assert scout_store.drafts_awaiting(conn) == []
    assert scout_store.drafts_awaiting(conn, reviewed=True) == []


def test_marking_sent_also_records_it_was_read(conn) -> None:
    """A draft cannot have been sent without somebody handling it, so `sent`
    implies `reviewed` rather than leaving a row that was sent unread."""
    one = _draft(conn, 1, GOOD, address="info@alpha.ch")
    row = scout_store.mark_draft_sent(conn, one)
    conn.commit()
    assert row["sent_at"] is not None and row["reviewed_at"] is not None


def test_the_tally_separates_approved_from_sent(conn) -> None:
    a = _draft(conn, 1, GOOD, address="info@alpha.ch")
    b = _draft(conn, 2, GOOD, address="info@beta.ch")
    scout_store.review_draft(conn, a)
    scout_store.mark_draft_sent(conn, b)
    conn.commit()
    tally = scout_store.draft_tally(conn)
    assert tally == {"awaiting": 0, "approved": 1, "sent": 1, "refused": 0,
                     "briefs": 1}


def test_a_draft_written_against_an_older_brief_is_counted(conn) -> None:
    """Rewriting the brief does not rewrite the drafts, and only the digest
    says which ones disagree with it."""
    _draft(conn, 1, GOOD, address="info@alpha.ch")
    assert scout_store.stale_drafts(conn, "d1") == 0
    assert scout_store.stale_drafts(conn, "d2") == 1


# --- what cannot be approved --------------------------------------------------


def test_a_placeholder_makes_a_draft_unapprovable(conn) -> None:
    one = _draft(conn, 1, FLAWED, address="info@alpha.ch")
    row = scout_store.draft(conn, one)
    assert draft_page.flawed(row) is True
    good = scout_store.draft(conn, _draft(conn, 2, GOOD, address="info@beta.ch"))
    assert draft_page.flawed(good) is False


def test_the_page_withholds_approve_and_the_route_refuses_it(conn, client) -> None:
    """Belt as well as braces. The page not drawing the button is not a
    guarantee, since a form can be posted without the page that drew it."""
    one = _draft(conn, 1, FLAWED, address="info@alpha.ch")
    page = client.get(f"/draft/{one}").text
    assert "Not approvable" in page
    assert "Approve</button>" not in page

    client.post(f"/draft/{one}/review", follow_redirects=False)
    row = scout_store.draft(conn, one)
    assert row["reviewed_at"] is None, "the route refused it too"


def test_the_marker_is_shown_as_a_fault_not_as_text(conn) -> None:
    row = scout_store.draft(conn, _draft(conn, 1, FLAWED, address="a@b.ch"))
    html = draft_page.one_page(row)
    assert 'class="marker"' in html
    assert "[[ MANQUE : lien TestFlight ]]" in html


def test_the_queue_banners_what_it_cannot_send(conn) -> None:
    rows = [scout_store.draft(conn, _draft(conn, 1, FLAWED, address="a@b.ch"))]
    html = draft_page.queue_page(rows, {"awaiting": 1}, stale=3)
    assert "MANQUE" in html
    assert "older version of" in html


# --- approving is not sending -------------------------------------------------


def test_approving_does_not_claim_it_was_sent(conn) -> None:
    one = _draft(conn, 1, GOOD, address="info@alpha.ch")
    row = scout_store.review_draft(conn, one)
    conn.commit()
    assert row["reviewed_at"] is not None
    assert row["sent_at"] is None, "nothing in this program sends mail"


# --- the reply proposal -------------------------------------------------------


def _member_at(conn, programme_id: int, company_id: int, stage: str):
    member_id, _ = clients_store.add_member(conn, programme_id, company_id)
    clients_store.move_member(conn, member_id, stage)
    conn.commit()
    return member_id


def _inbound(conn, company_id: int, *, when: dt.datetime, frm: str):
    conn.execute(
        "INSERT INTO crm.email_message (message_id, folder, direction,"
        " sent_at, subject, from_email, company_id, state)"
        " VALUES (%s, 'INBOX', 'in', %s, 'Re: Limen', %s, %s, 'pending')",
        (f"<{when.isoformat()}-{company_id}@x>", when, frm, company_id),
    )
    conn.commit()


@pytest.fixture
def programme(conn):
    pid = clients_store.add_programme(
        conn, code="LIMEN", name="Limen",
        stages=["selected", "contacted", "replied", "testing"],
    )
    conn.commit()
    return pid if isinstance(pid, int) else int(pid)


def test_a_reply_after_contact_is_proposed(conn, programme) -> None:
    _member_at(conn, programme, 1, "contacted")
    _inbound(conn, 1, when=dt.datetime.now(dt.timezone.utc), frm="jean@alpha.ch")
    found = clients_store.members_who_replied(conn, programme)
    assert len(found) == 1
    assert found[0]["company_name"] == "Régie Alpha SA"
    assert found[0]["from_email"] == "jean@alpha.ch"


def test_an_old_thread_is_not_a_reply(conn, programme) -> None:
    """A firm that wrote last year and was contacted yesterday has not
    replied, and counting it would move a card nobody answered."""
    _inbound(
        conn, 1,
        when=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=400),
        frm="jean@alpha.ch",
    )
    _member_at(conn, programme, 1, "contacted")
    assert clients_store.members_who_replied(conn, programme) == []


def test_the_proposal_does_not_move_the_card(conn, programme) -> None:
    """The rule `members_for` states: nothing in this schema files itself."""
    member_id = _member_at(conn, programme, 1, "contacted")
    _inbound(conn, 1, when=dt.datetime.now(dt.timezone.utc), frm="j@alpha.ch")
    clients_store.members_who_replied(conn, programme)
    row = clients_store.member(conn, member_id)
    assert row["stage"] == "contacted"


def test_a_member_at_another_stage_is_left_alone(conn, programme) -> None:
    _member_at(conn, programme, 1, "testing")
    _inbound(conn, 1, when=dt.datetime.now(dt.timezone.utc), frm="j@alpha.ch")
    assert clients_store.members_who_replied(conn, programme) == []
    assert len(clients_store.members_who_replied(
        conn, programme, from_stage="testing"
    )) == 1


def test_a_move_dates_the_card(conn, programme) -> None:
    """`move_member` writes audit.state_change, which is what lets the board
    say how long a card has sat where it is."""
    member_id = _member_at(conn, programme, 1, "contacted")
    clients_store.move_member(conn, member_id, "replied", by="cli:replies")
    conn.commit()
    rows = conn.execute(
        "SELECT from_state, to_state, by FROM audit.state_change"
        " WHERE entity = 'programme_member' AND entity_id = %s"
        " ORDER BY id DESC LIMIT 1", (member_id,)
    ).fetchall()
    assert rows and rows[0]["to_state"] == "replied"
    assert rows[0]["from_state"] == "contacted"
    assert rows[0]["by"] == "cli:replies"


# --- a refusal is kept, not deleted -------------------------------------------
#
# Discarding deleted the row, which lost the reason and, worse, the fact. The
# stage selects from the send list, so a deleted proposal is indistinguishable
# from one never written: the next run proposed the same message to the same
# mailbox and the reviewer refused it again.


def test_a_refused_draft_leaves_the_queue_but_stays_on_file(conn) -> None:
    one = _draft(conn, 1, GOOD, address="info@alpha.ch")
    scout_store.refuse_draft(conn, one, reason="ton trop commercial")
    conn.commit()
    assert scout_store.drafts_awaiting(conn) == []
    row = scout_store.draft(conn, one)
    assert row is not None
    assert row["refused_at"] is not None
    assert row["refused_reason"] == "ton trop commercial"


def test_a_refusal_counts_as_read(conn) -> None:
    one = _draft(conn, 1, GOOD, address="info@alpha.ch")
    row = scout_store.refuse_draft(conn, one)
    conn.commit()
    assert row["reviewed_at"] is not None, "somebody read it to refuse it"


def test_the_tally_separates_refused_from_sent(conn) -> None:
    a = _draft(conn, 1, GOOD, address="info@alpha.ch")
    b = _draft(conn, 2, GOOD, address="info@beta.ch")
    scout_store.refuse_draft(conn, a, reason="x")
    scout_store.mark_draft_sent(conn, b)
    conn.commit()
    tally = scout_store.draft_tally(conn)
    assert tally["refused"] == 1 and tally["sent"] == 1
    assert tally["awaiting"] == 0 and tally["approved"] == 0


def test_a_refused_mailbox_is_named_so_a_new_run_skips_it(conn) -> None:
    one = _draft(conn, 1, GOOD, address="info@alpha.ch")
    assert scout_store.refused_addresses(conn) == frozenset()
    scout_store.refuse_draft(conn, one, reason="x")
    conn.commit()
    assert scout_store.refused_addresses(conn) == frozenset({"info@alpha.ch"})


def test_the_page_says_why_it_was_refused(conn) -> None:
    one = _draft(conn, 1, GOOD, address="info@alpha.ch")
    scout_store.refuse_draft(conn, one, reason="ton trop commercial")
    conn.commit()
    html = draft_page.one_page(scout_store.draft(conn, one))
    assert "ton trop commercial" in html
    assert "will not propose this mailbox again" in html


def test_a_refused_draft_does_not_block_a_second_proposal_row(conn) -> None:
    """The unique index covers unsent, unrefused drafts. A refusal must block a
    new identical proposal, which the stage does by address; the index must not
    also refuse the insert outright, or a later approach could never be
    recorded at all."""
    one = _draft(conn, 1, GOOD, address="info@alpha.ch")
    scout_store.refuse_draft(conn, one, reason="x")
    conn.commit()
    again = _draft(conn, 1, GOOD, address="info@alpha.ch")
    assert again != one


# --- the prose the brief proscribes -------------------------------------------


def test_a_proscribed_turn_is_named_on_the_page(conn) -> None:
    """The first live draft wrote « Nous restons à votre disposition », where
    `rester` stands in for the verb of the act."""
    body = "Madame, Monsieur,\n\nNous restons à votre disposition."
    one = _draft(conn, 1, body, address="info@alpha.ch")
    html = draft_page.one_page(scout_store.draft(conn, one))
    assert "The brief proscribes" in html
    assert "rester" in html


def test_a_clean_body_draws_no_prose_banner(conn) -> None:
    one = _draft(conn, 1, GOOD, address="info@alpha.ch")
    html = draft_page.one_page(scout_store.draft(conn, one))
    assert "The brief proscribes" not in html
