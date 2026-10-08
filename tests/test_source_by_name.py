"""Finding a trade by name, rather than sweeping a territory.

The promises this holds to, each of which was a decision:

  the canton comes from the loop, because a search answer has none
  every legal form comes back, because a trade is not a legal form
  `intel.sweep` is left alone, because no prefix was completed
  a dry run writes nothing at all
"""

from __future__ import annotations

from arpenteur import db
from arpenteur.stages.source import run_by_name


def test_a_name_pattern_finds_the_company_and_hydrates_it(
    tmp_path, build_settings, db_path
) -> None:
    config = build_settings(tmp_path)
    conn = db.open_db(config.database_url)

    stats = run_by_name(
        config, conn, words=["*fiduciaire*"], fixture=True, log=lambda _: None
    )

    assert stats["found"] == 1
    assert stats["searched"] == 1
    assert stats["refused"] == 0
    assert stats["hydrated"] == 1
    rows = conn.execute("SELECT uid, name, canton, purpose FROM core.company").fetchall()
    assert [row["name"] for row in rows] == ["Aubert Fiduciaire Sàrl"]
    # The canton is the one the loop was on. Nothing in the search answer
    # carries it, so a missing loop would leave this NULL and every later
    # selector that filters by canton would silently skip the company.
    assert rows[0]["canton"] == "VD"
    assert rows[0]["purpose"]
    conn.close()


def test_a_name_search_completes_no_prefix(tmp_path, build_settings, db_path) -> None:
    """`intel.sweep` is the sweep's resume log, and this is not a sweep.

    A row here would tell a later `arpenteur source` that the prefix was done,
    and that sweep would skip every company under it.
    """
    config = build_settings(tmp_path)
    conn = db.open_db(config.database_url)

    run_by_name(config, conn, words=["*fiduciaire*"], fixture=True, log=lambda _: None)

    assert conn.execute("SELECT COUNT(*) AS n FROM intel.sweep").fetchone()[0] == 0
    conn.close()


def test_a_dry_run_writes_nothing(tmp_path, build_settings, db_path) -> None:
    config = build_settings(tmp_path)
    conn = db.open_db(config.database_url)

    stats = run_by_name(
        config, conn, words=["*fiduciaire*"], fixture=True, dry_run=True,
        log=lambda _: None,
    )

    assert stats["found"] == 1
    assert stats["hydrated"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM core.company").fetchone()[0] == 0
    conn.close()


def test_two_words_matching_one_company_count_it_once(
    tmp_path, build_settings, db_path
) -> None:
    """A tourism office named both `Tourismus` and `Bergbahn` is one firm.

    `seen` counts answers and `found` counts companies, and the cohort is the
    second number.
    """
    config = build_settings(tmp_path)
    conn = db.open_db(config.database_url)

    stats = run_by_name(
        config, conn, words=["*fiduciaire*", "*aubert*"], fixture=True,
        log=lambda _: None,
    )

    assert stats["seen"] == 2
    assert stats["found"] == 1
    conn.close()


def test_a_word_nothing_matches_finds_nothing(tmp_path, build_settings, db_path) -> None:
    config = build_settings(tmp_path)
    conn = db.open_db(config.database_url)

    stats = run_by_name(
        config, conn, words=["*tourismus*"], fixture=True, log=lambda _: None
    )

    assert stats == {
        "searched": 1, "seen": 0, "found": 0, "refused": 0, "hydrated": 0,
        "words": ["*tourismus*"], "cantons": ["VD"],
    }
    conn.close()
