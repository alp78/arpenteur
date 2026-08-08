from __future__ import annotations

from arpenteur import db
from arpenteur.config import LlmConfig, Settings, ZefixConfig
from arpenteur.stages.source import hydrate_pending, run_zefix


def settings(tmp_path) -> Settings:
    return Settings(
        root=tmp_path,
        cantons=["GE"],
        legal_forms=["sa"],
        segments={},
        zefix=ZefixConfig(
            base_url="https://example.test/ZefixPublicREST",
            rate_limit=2.0,
            max_retries=0,
            page_cap=1000,
            contact="tests",
        ),
        llm=LlmConfig(
            provider="anthropic",
            classify_model="test",
            draft_model="test",
            batch_size=20,
        ),
    )


def test_fixture_source_runs_without_credentials(tmp_path) -> None:
    config = settings(tmp_path)
    conn = db.open_db(config.db_path)

    stats = run_zefix(config, conn, fixture=True, log=lambda _: None)

    assert stats == {"searched": 36, "seen": 2, "hydrated": 2, "cantons": ["VD"]}
    rows = conn.execute(
        "SELECT uid, purpose, source_url FROM company ORDER BY uid"
    ).fetchall()
    assert [row["uid"] for row in rows] == ["CHE-100.000.001", "CHE-100.000.002"]
    assert all(row["purpose"] for row in rows)
    assert all(row["source_url"].startswith("fixture://zefix/") for row in rows)
    assert conn.execute("SELECT COUNT(*) FROM sweep").fetchone()[0] == 36
    conn.close()


def test_fixture_hydrate_can_follow_a_separate_sweep(tmp_path) -> None:
    config = settings(tmp_path)
    conn = db.open_db(config.db_path)

    stats = run_zefix(config, conn, fixture=True, hydrate=False, log=lambda _: None)

    assert stats["hydrated"] == 0
    assert hydrate_pending(config, conn, fixture=True, log=lambda _: None) == 2
    assert conn.execute("SELECT COUNT(*) FROM company WHERE hydrated_at IS NOT NULL").fetchone()[0] == 2
    conn.close()
