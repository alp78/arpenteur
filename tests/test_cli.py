from __future__ import annotations

from arpenteur import cli
from arpenteur.config import LlmConfig, Settings, ZefixConfig


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


def test_fixture_source_uses_its_own_database(tmp_path, monkeypatch) -> None:
    config = settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: config)
    args = cli.build_parser().parse_args(["source", "zefix", "--fixture"])

    assert cli.cmd_source(args) == 0
    assert not config.db_path.exists()
    assert (config.data_dir / "arpenteur.fixture.sqlite3").is_file()


def test_fixture_flags_are_available_to_follow_up_commands() -> None:
    parser = cli.build_parser()

    assert parser.parse_args(["init-db", "--fixture"]).fixture is True
    assert parser.parse_args(["hydrate", "--fixture"]).fixture is True
    assert parser.parse_args(["profile", "--fixture"]).fixture is True
    assert parser.parse_args(["stats", "--fixture"]).fixture is True
