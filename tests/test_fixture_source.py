from __future__ import annotations

from arpenteur import db
from arpenteur.config import Settings
from arpenteur.stages.source import hydrate_pending, run_zefix


def test_fixture_source_runs_without_credentials(tmp_path, build_settings) -> None:
    config = build_settings(tmp_path)
    conn = db.open_db(config.db_path)

    stats = run_zefix(config, conn, fixture=True, log=lambda _: None)

    # `refused` counts prefixes the registry declined as too large, which a
    # recorded set never does. Zero is the whole point of asserting it here.
    assert stats == {
        "searched": 36, "seen": 2, "hydrated": 2, "refused": 0, "cantons": ["VD"],
    }
    rows = conn.execute(
        "SELECT uid, purpose, source_url FROM company ORDER BY uid"
    ).fetchall()
    assert [row["uid"] for row in rows] == ["CHE-100.000.001", "CHE-100.000.002"]
    assert all(row["purpose"] for row in rows)
    assert all(row["source_url"].startswith("fixture://zefix/") for row in rows)
    assert conn.execute("SELECT COUNT(*) FROM sweep").fetchone()[0] == 36
    conn.close()


def test_fixture_rows_survive_the_filters_the_later_stages_apply(
    tmp_path, build_settings
) -> None:
    """A recorded row is only useful if the rest of the pipeline will select it.

    It said `EXISTIEREND` and legal form `0108`. The register answers `ACTIVE`
    and `0107` for a Sàrl, and every stage after hydrate filters on exactly
    those, so the fixture swept, hydrated, and was then invisible to
    `profile --fixture` without anything reporting a problem.
    """
    config = build_settings(tmp_path)
    conn = db.open_db(config.db_path)
    run_zefix(config, conn, fixture=True, log=lambda _: None)

    clause, params = Settings.status_clause(config, "status")
    selectable = conn.execute(
        f"SELECT COUNT(*) FROM company WHERE {clause}", params
    ).fetchone()[0]
    assert selectable == 2

    forms = {row[0] for row in conn.execute("SELECT DISTINCT legal_form_uid FROM company")}
    assert forms == {"0107"}, "0108 is a société coopérative, not a Sàrl"
    conn.close()


def test_fixture_hydrate_can_follow_a_separate_sweep(tmp_path, build_settings) -> None:
    config = build_settings(tmp_path)
    conn = db.open_db(config.db_path)

    stats = run_zefix(config, conn, fixture=True, hydrate=False, log=lambda _: None)

    assert stats["hydrated"] == 0
    assert hydrate_pending(config, conn, fixture=True, log=lambda _: None) == 2
    assert conn.execute("SELECT COUNT(*) FROM company WHERE hydrated_at IS NOT NULL").fetchone()[0] == 2
    conn.close()
