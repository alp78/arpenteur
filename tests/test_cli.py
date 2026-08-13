from __future__ import annotations

from arpenteur import cli


def test_fixture_source_uses_its_own_database(tmp_path, monkeypatch, build_settings) -> None:
    config = build_settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: config)
    args = cli.build_parser().parse_args(["source", "zefix", "--fixture"])

    assert cli.cmd_source(args) == 0
    # A separate database on the same server, not the live one.
    assert "_fixture" in cli._database_url(config, True)
    assert cli._database_url(config, False) == config.database_url


def test_fixture_flags_are_available_to_follow_up_commands() -> None:
    parser = cli.build_parser()

    assert parser.parse_args(["init-db", "--fixture"]).fixture is True
    assert parser.parse_args(["hydrate", "--fixture"]).fixture is True
    assert parser.parse_args(["profile", "--fixture"]).fixture is True
    assert parser.parse_args(["stats", "--fixture"]).fixture is True
