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
from arpenteur.kernel.db import Connection, Row
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
