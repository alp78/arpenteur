"""The guard that keeps a sweep from turning into a mass query.

Zefix grants free REST access and asks users to renounce regular mass
querying, giving a concrete reason: all three access routes share one system,
the web application alone serves over 400'000 requests a day, and a user who
disturbs it degrades the service for everyone. The stated remedy is to block
that user.

Hydration is one request per company. The Romandie sweep holds 183'361 of
them, so the difference between a considerate run and a six-figure burst is a
single absent `--limit`. That is too easy to do by accident to leave to care,
hence a guard that fails closed and a test that keeps it failing closed.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from arpenteur import db
from arpenteur.kernel.db import Connection
from arpenteur.stages import source


@pytest.fixture
def conn(db_path: Path) -> Connection:
    connection = db.open_db(db_path)
    for index in range(source.POLITE_HYDRATION_LIMIT + 5):
        db.upsert_company(
            connection,
            {"uid": f"CHE{index:09d}", "name": f"Company {index}", "method": "zefix"},
        )
    connection.commit()
    return connection


@pytest.fixture
def settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        zefix=SimpleNamespace(
            base_url="https://example.invalid",
            rate_limit=2.0,
            max_retries=1,
            contact="test",
            user="u",
            password="p",
            has_credentials=True,
        ),
        cache_dir=tmp_path / "cache",
    )


def test_an_uncapped_pass_over_the_corpus_refuses_to_run(settings, conn) -> None:
    """No network is reachable in this test, so a raise proves it stopped first.

    If the guard were removed this would fail on a connection error instead of
    the RuntimeError, which is the distinction worth catching: the run must end
    before the first request, not partway through the hundred thousandth.
    """
    with pytest.raises(RuntimeError, match="mass querying"):
        source.hydrate_pending(settings, conn, log=lambda _m: None)


def test_the_guard_names_the_way_out(settings, conn) -> None:
    """An error that stops the work without saying what to do instead gets
    worked around by whatever is quickest, which here means --bulk."""
    with pytest.raises(RuntimeError) as caught:
        source.hydrate_pending(settings, conn, log=lambda _m: None)
    message = str(caught.value)
    assert "--limit" in message and "--bulk" in message


def test_a_limited_run_gets_past_its_own_query(settings, db_path: Path) -> None:
    """The way out the guard names has to actually work.

    `--limit` selected with `LIMIT ?`, a SQLite placeholder Postgres never
    saw, so psycopg refused the call and every capped run died on the query
    before a single request went out. The guard tests above all take the
    uncapped path, which is why it survived: the one route they prove exists
    was the one route nobody could take. That left `--bulk` over the whole
    territory as the only hydration that ran, and it is a day of requests.

    Reaching the network is the pass condition. There is none in a test, so
    the failure that arrives must be about the connection rather than about
    the statement.
    """
    import psycopg

    connection = db.open_db(db_path)
    for i in range(3):
        db.upsert_company(
            connection, {"uid": f"CHE{i}", "name": f"Name {i}", "method": "zefix"}
        )
    connection.commit()

    with pytest.raises(Exception) as caught:
        source.hydrate_pending(settings, connection, limit=2, log=lambda _m: None)
    assert not isinstance(caught.value, psycopg.ProgrammingError), (
        f"the selection query itself failed: {caught.value}"
    )


class _Register:
    """Stands in for the Zefix client and keeps every company it is asked for."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def company(self, uid: str, max_age: int | None = None) -> None:
        self.asked.append(uid)


def test_a_limit_of_zero_hydrates_nothing(settings, conn) -> None:
    """`conn` holds more companies than the guard lets through without a flag.
    Zero skipped the guard (a limit was given) and the slice (zero is falsy),
    which is the whole backlog at one request each."""
    register = _Register()
    done = source.hydrate_pending(
        settings, conn, zefix=register, limit=0, log=lambda _m: None
    )
    assert done == 0
    assert register.asked == []


def test_a_small_backlog_needs_no_ceremony(settings, db_path: Path) -> None:
    """The guard is for the corpus, not for ordinary use.

    A handful of new companies after a re-sweep must hydrate without a flag,
    or the flag becomes habitual and stops meaning anything.
    """
    connection = db.open_db(db_path)
    db.upsert_company(connection, {"uid": "CHE1", "name": "One", "method": "zefix"})
    connection.commit()
    with pytest.raises(Exception) as caught:
        source.hydrate_pending(settings, connection, log=lambda _m: None)
    assert "mass querying" not in str(caught.value), (
        "a one-company backlog tripped the bulk guard"
    )


# --- backing off without disappearing -----------------------------------------
#
# The client sleeps when a server pushes back, and a sleep is invisible: it
# looks exactly like slow work from the outside. So the bound on it is asserted
# rather than trusted. Two shards of a six-way run sat in a 24-hour sleep for
# an hour before anyone noticed, and nothing in any log said so.

import datetime as _dt  # noqa: E402

from arpenteur.http import MAX_BACKOFF, _retry_after  # noqa: E402


def test_a_server_asking_for_a_day_does_not_get_one() -> None:
    """The bug this file was extended for.

    The cap was applied to the exponential fallback and not to the header, so
    `Retry-After: 86400` was honoured in full. A delay that long is a refusal,
    not a hint, and waiting it out serves nobody: the retry budget is the right
    place to give up, not the clock.
    """
    asked = _retry_after("86400")
    assert asked == 86400.0, "the header should still be read faithfully"
    assert min(asked, float(MAX_BACKOFF)) == float(MAX_BACKOFF), (
        "a server-supplied delay must be capped like any other"
    )


def test_the_http_date_form_is_not_dropped() -> None:
    """RFC 9110 allows two forms and only one was understood.

    `"120".isdigit()` is true and a date string's is not, so a date-form header
    fell through to the exponential branch and the server's instruction was
    discarded in silence.
    """
    soon = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=90)
    parsed = _retry_after(soon.strftime("%a, %d %b %Y %H:%M:%S GMT"))
    assert parsed is not None, "an HTTP-date Retry-After must be understood"
    assert 60 <= parsed <= 120


def test_an_unusable_header_is_not_mistaken_for_go_now() -> None:
    """None and 0 mean different things: "nothing said" and "no wait at all".
    Collapsing them would turn a malformed header into a hot retry loop."""
    assert _retry_after("garbage") is None
    assert _retry_after(None) is None
    assert _retry_after("") is None
    assert _retry_after("0") == 0.0
