"""What a verdict is allowed to claim, and what the send list refuses to mix.

The bank held 12'111 addresses with `status` NULL on every row, so these pin
the two things that made recording a verdict worth doing: that a dead domain is
the only certain negative, and that a timeout is not one.
"""

from __future__ import annotations

import datetime as dt

import pytest

from arpenteur import db
from arpenteur.kernel.db import Connection
from arpenteur.modules.scout import store as scout_store
from arpenteur.stages import verify

DESKS = ("info", "contact", "kontakt", "verwaltung")


@pytest.fixture
def conn(db_path) -> Connection:
    return db.open_db(db_path)


@pytest.fixture
def firms(conn: Connection) -> None:
    for n, (uid, name, canton) in enumerate((
        ("CHE1", "Naef Immobilier SA", "GE"),
        ("CHE2", "Burri Verwaltungen AG", "BE"),
        ("CHE3", "Solus Immo AG", "ZH"),
    ), 1):
        db.upsert_company(conn, {
            "uid": uid, "name": name, "canton": canton, "status": "ACTIVE",
            "hydrated": True, "method": "zefix",
        })
    conn.commit()


def _add(conn, company_id, value, **kw):
    scout_store.save_contact(
        conn, company_id=company_id, kind="email", value=value, **kw
    )
    conn.commit()


# --- the verdict --------------------------------------------------------------


def test_one_lookup_settles_every_address_at_that_domain(conn, firms) -> None:
    """The reason the sweep is per domain: this bank has 12'111 addresses at
    5'139 domains, so per-address lookups would be two and a half times the
    queries for identical answers."""
    _add(conn, 1, "info@naef.ch", method="published")
    _add(conn, 1, "location@naef.ch", method="published")
    _add(conn, 2, "contact@naef.ch", method="published")

    asked: list[str] = []

    def resolver(domain, **_kw):
        asked.append(domain)
        return verify.DELIVERABLE, "MX mail.naef.ch"

    stats = verify.run(conn, log=lambda _m: None, resolve=resolver)
    assert asked == ["naef.ch"], "one lookup, not three"
    assert stats["addresses"] == 3
    rows = conn.execute(
        "SELECT status, status_note, status_at FROM core.contact ORDER BY id"
    ).fetchall()
    assert [r["status"] for r in rows] == [verify.DELIVERABLE] * 3
    assert all(r["status_note"] == "MX mail.naef.ch" for r in rows)
    assert all(r["status_at"] is not None for r in rows)


def test_a_resolver_that_did_not_answer_is_not_a_dead_domain(conn, firms) -> None:
    """A timeout is a fact about the network. Recording it as `dead` would
    retire a reachable firm from the survey on the strength of one bad
    second."""
    _add(conn, 1, "info@slow.ch", method="published")
    verify.run(conn, log=lambda _m: None,
               resolve=lambda _d, **_k: (verify.UNKNOWN, "the resolver did not answer"))
    row = conn.execute("SELECT status FROM core.contact").fetchone()
    assert row["status"] == verify.UNKNOWN
    assert row["status"] != verify.DEAD


def test_a_dead_domain_is_recorded_as_the_one_certain_negative(conn, firms) -> None:
    _add(conn, 3, "info@solusimmo.com", method="published")
    stats = verify.run(conn, log=lambda _m: None,
                       resolve=lambda _d, **_k: (verify.DEAD, "the domain does not exist"))
    assert stats[verify.DEAD] == 1
    row = conn.execute("SELECT status FROM core.contact").fetchone()
    assert row["status"] == verify.DEAD


def test_a_current_verdict_is_not_asked_again(conn, firms) -> None:
    _add(conn, 1, "info@naef.ch", method="published")
    verify.run(conn, log=lambda _m: None,
               resolve=lambda _d, **_k: (verify.DELIVERABLE, "MX"))
    asked: list[str] = []
    stats = verify.run(conn, log=lambda _m: None,
                       resolve=lambda d, **_k: (asked.append(d), (verify.DELIVERABLE, "MX"))[1])
    assert asked == [] and stats["domains"] == 0


def test_a_stale_verdict_is_asked_again(conn, firms) -> None:
    """A domain lapses. `info@livy-immobilien.ch` resolved on the day it was
    found and does not resolve now, which is the whole reason the date is part
    of the verdict."""
    _add(conn, 1, "info@naef.ch", method="published")
    verify.run(conn, log=lambda _m: None,
               resolve=lambda _d, **_k: (verify.DELIVERABLE, "MX"))
    conn.execute(
        "UPDATE core.contact SET status_at = %s",
        (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=99),),
    )
    conn.commit()
    asked: list[str] = []
    verify.run(conn, log=lambda _m: None,
               resolve=lambda d, **_k: (asked.append(d), (verify.DEAD, "gone"))[1])
    assert asked == ["naef.ch"]
    assert conn.execute("SELECT status FROM core.contact").fetchone()["status"] == verify.DEAD


def test_refresh_asks_about_a_current_verdict_too(conn, firms) -> None:
    _add(conn, 1, "info@naef.ch", method="published")
    verify.run(conn, log=lambda _m: None,
               resolve=lambda _d, **_k: (verify.DELIVERABLE, "MX"))
    asked: list[str] = []
    verify.run(conn, refresh=True, log=lambda _m: None,
               resolve=lambda d, **_k: (asked.append(d), (verify.DELIVERABLE, "MX"))[1])
    assert asked == ["naef.ch"]


def test_a_dry_run_resolves_nothing(conn, firms) -> None:
    _add(conn, 1, "info@naef.ch", method="published")
    asked: list[str] = []
    verify.run(conn, dry_run=True, log=lambda _m: None,
               resolve=lambda d, **_k: (asked.append(d), (verify.DEAD, "x"))[1])
    assert asked == []
    assert conn.execute("SELECT status FROM core.contact").fetchone()["status"] is None


# --- the send list ------------------------------------------------------------


def test_the_send_list_gives_one_address_per_firm(conn, firms) -> None:
    _add(conn, 1, "info@naef.ch", method="published")
    _add(conn, 1, "jean.dupont@naef.ch", method="published")
    _add(conn, 1, "location@naef.ch", method="published")
    verify.run(conn, log=lambda _m: None,
               resolve=lambda _d, **_k: (verify.DELIVERABLE, "MX"))
    rows = scout_store.send_list(conn, desk_parts=DESKS)
    assert len(rows) == 1
    assert rows[0]["address"] == "info@naef.ch", "the desk wins over the person"
    assert rows[0]["desk_local"] is True


def test_a_firm_with_only_a_named_mailbox_is_reported_not_dropped(conn, firms) -> None:
    """Dropping it would hide the decision. Writing to a person by name is a
    different act under the nLPD, so the caller has to see the count and
    choose."""
    _add(conn, 2, "marc.burri@burri-verwaltungen.ch", method="published")
    verify.run(conn, log=lambda _m: None,
               resolve=lambda _d, **_k: (verify.DELIVERABLE, "MX"))
    rows = scout_store.send_list(conn, desk_parts=DESKS)
    assert len(rows) == 1 and rows[0]["desk_local"] is False


def test_the_send_list_refuses_a_dead_domain(conn, firms) -> None:
    _add(conn, 3, "info@solusimmo.com", method="published")
    verify.run(conn, log=lambda _m: None,
               resolve=lambda _d, **_k: (verify.DEAD, "the domain does not exist"))
    assert scout_store.send_list(conn, desk_parts=DESKS) == []


def test_the_send_list_refuses_an_unverified_address(conn, firms) -> None:
    """An address nothing has checked must not reach a send by default: the
    whole bank was in that state and looked ready."""
    _add(conn, 1, "info@naef.ch", method="published")
    assert scout_store.send_list(conn, desk_parts=DESKS) == []


def test_the_send_list_leaves_out_a_suppressed_mailbox(conn, firms) -> None:
    _add(conn, 1, "arben72@bluewin.ch", method="published", suppressed=True)
    verify.run(conn, log=lambda _m: None,
               resolve=lambda _d, **_k: (verify.DELIVERABLE, "MX"))
    assert scout_store.send_list(conn, desk_parts=DESKS) == []


def test_maybe_is_sendable_only_when_asked_for(conn, firms) -> None:
    """No MX but an A record is the fallback RFC 5321 §5.1 requires a sender to
    try, and small Swiss hosts rely on it. Sendable, and weaker, so it is a
    separate decision."""
    _add(conn, 1, "info@naef.ch", method="published")
    verify.run(conn, log=lambda _m: None,
               resolve=lambda _d, **_k: (verify.MAYBE, "no MX, an A record only"))
    assert scout_store.send_list(conn, desk_parts=DESKS) == []
    widened = scout_store.send_list(
        conn, desk_parts=DESKS, statuses=(verify.DELIVERABLE, verify.MAYBE)
    )
    assert len(widened) == 1


def test_the_send_list_narrows_by_canton(conn, firms) -> None:
    _add(conn, 1, "info@naef.ch", method="published")
    _add(conn, 2, "info@burri.ch", method="published")
    verify.run(conn, log=lambda _m: None,
               resolve=lambda _d, **_k: (verify.DELIVERABLE, "MX"))
    rows = scout_store.send_list(conn, desk_parts=DESKS, cantons=["ge"])
    assert [r["canton"] for r in rows] == ["GE"]
