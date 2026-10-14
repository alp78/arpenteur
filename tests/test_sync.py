"""The sync engine, and the one property it exists to have.

Running the same range twice must insert nothing, change nothing and report
the same numbers. A cron job that fires twice, a retry after a crash and an
operator who runs it again because the first run scrolled past all have to
converge on the same database, or the local copy is not a copy of anything.

The other half is that a change is actually seen. An engine that never
duplicates is trivial to write by never writing; these tests pin both edges.
"""

from __future__ import annotations

from datetime import date as Date
from pathlib import Path
from types import SimpleNamespace

import pytest

from arpenteur import db
from arpenteur.kernel.db import Connection
from arpenteur.config import Territory
from arpenteur.sources.zefix import Company, Mutation
from arpenteur.stages import sync

TODAY = Date(2026, 8, 11)


def mutation(uid: str, name: str, *, day: str, sogc_id: int,
             status: str = "ACTIVE", kinds: list[str] | None = None,
             canton: str = "GE", form: str = "0107") -> Mutation:
    return Mutation(
        company=Company(uid=uid, name=name, canton=canton, legal_seat="Genève",
                        legal_form_uid=form, status=status, source_url="feed"),
        message=f"Notice for {name}.",
        sogc_id=sogc_id,
        sogc_date=day,
        canton=canton,
        kinds=kinds or ["aenderungorgane"],
        source_url="feed",
    )


class FakeZefix:
    """Records what was asked for, and with what cache policy."""

    def __init__(self, feed: dict[str, list[Mutation]]) -> None:
        self.feed = feed
        self.calls: list[tuple[str, float | None]] = []

    def sogc_by_date(self, date: str, *, max_age: float | None = None):
        self.calls.append((date, max_age))
        return self.feed.get(date, [])


@pytest.fixture
def settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        territory=Territory(cantons=("GE",), legal_forms=("0107",), name="geneve"),
        admit_new=True,
        zefix=SimpleNamespace(base_url="https://example.invalid", rate_limit=2.0,
                              max_retries=1, contact="test", user="u", password="p",
                              has_credentials=True),
        cache_dir=tmp_path / "cache",
    )


@pytest.fixture
def conn(db_path: Path) -> Connection:
    connection = db.open_db(db_path)
    db.upsert_company(
        connection,
        # Seeded to match exactly what the feed would report for this company,
        # so "unchanged" means unchanged rather than "the fixture was thinner
        # than the notice".
        {"uid": "CHE111", "name": "Known Sàrl", "canton": "GE",
         "legal_seat": "Genève", "legal_form_uid": "0107", "status": "ACTIVE",
         "method": "zefix"},
    )
    connection.commit()
    return connection


def sync_with(monkeypatch, fake: FakeZefix, settings, conn, **kwargs):
    monkeypatch.setattr(sync, "_client", lambda _s: _NullClient())
    monkeypatch.setattr(sync, "ZefixClient", lambda _http, _base: fake)
    kwargs.setdefault("today", TODAY)
    kwargs.setdefault("log", lambda _m: None)
    return sync.run(settings, conn, **kwargs)


class _NullClient:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def counts(conn) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()[0]
        for table in ("core.company", "core.publication", "intel.sync_day")
    }


# --- the property ------------------------------------------------------------


def test_running_the_same_range_twice_changes_nothing(monkeypatch, settings, conn) -> None:
    """The whole point. Second run: no new rows, nothing reported as changed."""
    feed = {"2026-08-05": [mutation("CHE111", "Known Sàrl", day="2026-08-05", sogc_id=1)]}
    fake = FakeZefix(feed)

    first = sync_with(monkeypatch, fake, settings, conn,
                      since="2026-08-05", until="2026-08-05")
    after_first = counts(conn)

    second = sync_with(monkeypatch, fake, settings, conn,
                       since="2026-08-05", until="2026-08-05")

    assert counts(conn) == after_first, "a second pass wrote rows"
    assert second["days"] == 0, "a consumed day was fetched again"
    assert first["tracked"] == 1


def test_re_reading_a_day_never_duplicates_its_notices(monkeypatch, settings, conn) -> None:
    """--no-resume forces the fetch; the notice must still land once.

    Keyed on (company, sogc_id), so the guard is in the schema rather than in
    whoever remembers to check first.
    """
    feed = {"2026-08-05": [mutation("CHE111", "Known Sàrl", day="2026-08-05", sogc_id=1)]}
    fake = FakeZefix(feed)
    for _ in range(3):
        sync_with(monkeypatch, fake, settings, conn, since="2026-08-05",
                  until="2026-08-05", resume=False)
    assert conn.execute("SELECT COUNT(*) AS n FROM core.publication").fetchone()[0] == 1
    assert len(fake.calls) == 3, "resume=False should really re-fetch"


def test_an_unchanged_company_reports_unchanged(monkeypatch, settings, conn) -> None:
    """Without a content digest every upsert looks like an amendment, and a
    quiet day is indistinguishable from a broken sync."""
    feed = {"2026-08-05": [mutation("CHE111", "Known Sàrl", day="2026-08-05", sogc_id=1)]}
    stats = sync_with(monkeypatch, FakeZefix(feed), settings, conn,
                      since="2026-08-05", until="2026-08-05")
    assert (stats["unchanged"], stats["updated"]) == (1, 0)


def test_two_notices_in_one_day_are_one_company_not_two(
    monkeypatch, settings, conn
) -> None:
    """The verdict is per company per day, not per notice.

    Found live: three companies named in two notices each on the same day made
    a re-read of an unchanged day report six amendments. The database
    converged, but a report that invents movement is as misleading as one that
    hides it. Both notices land; the company is counted once.
    """
    feed = {"2026-08-05": [
        mutation("CHE111", "Known Sàrl", day="2026-08-05", sogc_id=1),
        mutation("CHE111", "Known Sàrl", day="2026-08-05", sogc_id=2),
    ]}
    stats = sync_with(monkeypatch, FakeZefix(feed), settings, conn,
                      since="2026-08-05", until="2026-08-05")
    assert stats["notices"] == 2
    assert stats["tracked"] == 1
    assert (stats["unchanged"], stats["updated"]) == (1, 0)
    assert conn.execute("SELECT COUNT(*) AS n FROM core.publication").fetchone()[0] == 2


def test_notices_that_disagree_within_a_day_settle_once(
    monkeypatch, settings, conn
) -> None:
    """The case that produced the false count: the same day renames a company
    twice. One change, reported once, and the later notice wins."""
    feed = {"2026-08-05": [
        mutation("CHE111", "First Name Sàrl", day="2026-08-05", sogc_id=1),
        mutation("CHE111", "Second Name Sàrl", day="2026-08-05", sogc_id=2),
    ]}
    stats = sync_with(monkeypatch, FakeZefix(feed), settings, conn,
                      since="2026-08-05", until="2026-08-05")
    assert (stats["updated"], stats["unchanged"]) == (1, 0)
    assert conn.execute("SELECT name FROM core.company").fetchone()[0] == "Second Name Sàrl"


# --- and that changes are actually seen ---------------------------------------


def test_a_rename_is_applied_from_the_feed_alone(monkeypatch, settings, conn) -> None:
    """The short record carries name, seat, form and status, so an identity
    change costs no second request."""
    feed = {"2026-08-05": [
        mutation("CHE111", "Renamed Sàrl", day="2026-08-05", sogc_id=1,
                 kinds=["firmenaenderung"])
    ]}
    stats = sync_with(monkeypatch, FakeZefix(feed), settings, conn,
                      since="2026-08-05", until="2026-08-05")
    assert stats["updated"] == 1
    assert conn.execute("SELECT name FROM core.company").fetchone()[0] == "Renamed Sàrl"


def test_a_striking_off_is_a_status_not_a_deletion(monkeypatch, settings, conn) -> None:
    """A prospect that has been struck off is a fact worth keeping. Deleting
    the row would also delete its notices, its officers and any notes on it."""
    feed = {"2026-08-05": [
        mutation("CHE111", "Known Sàrl", day="2026-08-05", sogc_id=1,
                 status="CANCELLED", kinds=["loeschung"])
    ]}
    stats = sync_with(monkeypatch, FakeZefix(feed), settings, conn,
                      since="2026-08-05", until="2026-08-05")
    assert stats["deletions"] == 1
    row = conn.execute("SELECT status FROM core.company WHERE uid = 'CHE111'").fetchone()
    assert row["status"] == "CANCELLED"


def test_a_touched_company_is_queued_for_re_hydration(monkeypatch, settings, conn) -> None:
    """Purpose and address are not in the short record, and a notice is the
    only evidence they may have moved."""
    feed = {"2026-08-05": [mutation("CHE111", "Known Sàrl", day="2026-08-05", sogc_id=1)]}
    sync_with(monkeypatch, FakeZefix(feed), settings, conn,
              since="2026-08-05", until="2026-08-05")
    row = conn.execute("SELECT stale_since, last_mutation_date FROM core.company").fetchone()
    assert row["stale_since"] is not None
    assert row["last_mutation_date"] == "2026-08-05"


def test_several_notices_for_one_company_are_one_piece_of_work(
    monkeypatch, settings, conn
) -> None:
    """stale_since keeps the moment we first fell behind, not the latest one."""
    feed = {
        "2026-08-05": [mutation("CHE111", "Known Sàrl", day="2026-08-05", sogc_id=1)],
        "2026-08-06": [mutation("CHE111", "Known Sàrl", day="2026-08-06", sogc_id=2)],
    }
    sync_with(monkeypatch, FakeZefix(feed), settings, conn,
              since="2026-08-05", until="2026-08-06")
    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM core.company WHERE stale_since IS NOT NULL"
    ).fetchone()[0]
    assert rows == 1
    assert conn.execute("SELECT last_mutation_date FROM core.company").fetchone()[0] == "2026-08-06"


# --- territory ----------------------------------------------------------------


def test_a_company_outside_the_territory_is_counted_not_stored(
    monkeypatch, settings, conn
) -> None:
    """The feed is national and cannot be filtered server side, so the
    territory does it here. Counting the remainder keeps a quiet day
    distinguishable from a filter that is excluding everything."""
    feed = {"2026-08-05": [
        mutation("CHE999", "Zurich AG", day="2026-08-05", sogc_id=7, canton="ZH")
    ]}
    stats = sync_with(monkeypatch, FakeZefix(feed), settings, conn,
                      since="2026-08-05", until="2026-08-05")
    assert stats["notices"] == 1 and stats["tracked"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM core.company").fetchone()[0] == 1


def test_a_legal_form_outside_the_territory_is_excluded_too(
    monkeypatch, settings, conn
) -> None:
    """Territory is two axes. A survey of limited companies must not silently
    acquire associations because they happen to be in the right canton."""
    feed = {"2026-08-05": [
        mutation("CHE999", "Association X", day="2026-08-05", sogc_id=7, form="0109")
    ]}
    stats = sync_with(monkeypatch, FakeZefix(feed), settings, conn,
                      since="2026-08-05", until="2026-08-05")
    assert stats["tracked"] == 0


def test_a_new_company_inside_the_territory_is_admitted(
    monkeypatch, settings, conn
) -> None:
    """How the map keeps up with new incorporations without a re-sweep."""
    feed = {"2026-08-05": [mutation("CHE999", "New Sàrl", day="2026-08-05", sogc_id=7)]}
    stats = sync_with(monkeypatch, FakeZefix(feed), settings, conn,
                      since="2026-08-05", until="2026-08-05")
    assert stats["inserted"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM core.company").fetchone()[0] == 2


def test_a_survey_can_refuse_to_grow(monkeypatch, settings, conn) -> None:
    """admit_new off freezes the population at what the sweep found, while
    still keeping those companies current."""
    settings.admit_new = False
    feed = {"2026-08-05": [
        mutation("CHE999", "New Sàrl", day="2026-08-05", sogc_id=7),
        mutation("CHE111", "Known Sàrl", day="2026-08-05", sogc_id=8),
    ]}
    stats = sync_with(monkeypatch, FakeZefix(feed), settings, conn,
                      since="2026-08-05", until="2026-08-05")
    assert stats["inserted"] == 0
    assert stats["tracked"] == 1, "known companies must still be updated"
    assert conn.execute("SELECT COUNT(*) AS n FROM core.company").fetchone()[0] == 1


def test_a_known_company_is_updated_even_from_outside_the_territory(
    monkeypatch, settings, conn
) -> None:
    """A company already held stays current whatever the territory says now.

    Narrowing the territory must not silently stop maintaining rows that are
    already in the database, which would leave them frozen and wrong rather
    than absent.
    """
    feed = {"2026-08-05": [
        mutation("CHE111", "Moved Sàrl", day="2026-08-05", sogc_id=9, canton="ZH")
    ]}
    stats = sync_with(monkeypatch, FakeZefix(feed), settings, conn,
                      since="2026-08-05", until="2026-08-05")
    assert stats["tracked"] == 1
    assert conn.execute("SELECT name FROM core.company").fetchone()[0] == "Moved Sàrl"


# --- caching ------------------------------------------------------------------


def test_a_closed_day_may_be_cached_and_today_may_not(monkeypatch, settings, conn) -> None:
    """The cantonal registers file through the working day, so a copy taken
    this morning is not the day's news."""
    fake = FakeZefix({})
    sync_with(monkeypatch, fake, settings, conn, since="2026-08-10", until="2026-08-11")
    policy = dict(fake.calls)
    assert policy["2026-08-10"] is None, "a closed day should be cacheable for ever"
    assert policy["2026-08-11"] == 0, "today must be re-asked"


def test_resuming_starts_the_day_after_the_last_one_consumed(
    monkeypatch, settings, conn
) -> None:
    fake = FakeZefix({})
    sync_with(monkeypatch, fake, settings, conn, since="2026-08-05", until="2026-08-07")
    fake.calls.clear()
    sync_with(monkeypatch, fake, settings, conn)
    assert [day for day, _ in fake.calls][0] == "2026-08-08"


def test_an_enormous_backfill_has_to_be_asked_for(monkeypatch, settings, conn) -> None:
    """One request per day is cheap; --since 2010 is six thousand of them."""
    with pytest.raises(RuntimeError, match="days to sync"):
        sync_with(monkeypatch, FakeZefix({}), settings, conn, since="2010-01-01")


def test_a_day_read_while_it_was_filling_is_read_again_the_next_day(
    monkeypatch, settings, conn
) -> None:
    """The cantonal registers file through the working day. A run at noon
    records the day, and the evening's notices arrive after it; the next
    run must read that day again, and from the register, not from the copy
    the noon run cached."""
    fake = FakeZefix({})
    sync_with(monkeypatch, fake, settings, conn, since="2026-08-10", until="2026-08-11",
              today=Date(2026, 8, 11))
    fake.calls.clear()
    sync_with(monkeypatch, fake, settings, conn, today=Date(2026, 8, 12))
    policy = dict(fake.calls)
    assert "2026-08-10" not in policy, "a day read after it ended is done"
    assert policy["2026-08-11"] == 0, "the day read while filling is read again, uncached"
    assert policy["2026-08-12"] == 0, "today is read uncached as before"


def test_resuming_on_the_same_day_reads_today_again(monkeypatch, settings, conn) -> None:
    """Resuming used to start the day after the last one consumed, which was
    tomorrow when that day was today, and reported "already up to date"."""
    fake = FakeZefix({})
    sync_with(monkeypatch, fake, settings, conn, since="2026-08-11")
    fake.calls.clear()
    stats = sync_with(monkeypatch, fake, settings, conn)
    assert [day for day, _ in fake.calls] == ["2026-08-11"]
    assert stats["days"] == 1


def test_a_day_re_read_on_purpose_is_fetched_rather_than_served_from_disk(
    monkeypatch, settings, conn
) -> None:
    fake = FakeZefix({})
    sync_with(monkeypatch, fake, settings, conn, since="2026-08-05", until="2026-08-06")
    fake.calls.clear()
    sync_with(monkeypatch, fake, settings, conn, since="2026-08-05", until="2026-08-06",
              resume=False)
    assert dict(fake.calls) == {"2026-08-05": 0, "2026-08-06": 0}
