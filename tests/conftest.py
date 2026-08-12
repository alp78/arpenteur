from __future__ import annotations

import json
from pathlib import Path

import pytest

from arpenteur.config import LlmConfig, Settings, Territory, ZefixConfig

FIXTURES = Path(__file__).parent / "fixtures"


def _settings(root: Path, **overrides) -> Settings:
    fields = {
        "root": root,
        "territory": Territory(cantons=("GE",), legal_forms=("0106",), name="test"),
        "regions": {},
        "admit_new": True,
        "statuses": ("ACTIVE",),
        "segments": {},
        "signal_brief": "",
        "zefix": ZefixConfig(
            base_url="https://example.test/ZefixPublicREST",
            rate_limit=2.0,
            max_retries=0,
            contact="tests",
        ),
        "llm": LlmConfig(
            provider="anthropic",
            classify_model="test",
            draft_model="test",
            batch_size=20,
        ),
    }
    fields.update(overrides)
    return Settings(**fields)


@pytest.fixture
def build_settings() -> callable:
    """A real Settings for the tests that need one rather than a stand-in.

    Shared because two test modules were carrying identical copies, which is
    how a change to the constructor breaks a test that has nothing to do with
    it: adding `territory` broke both at once.
    """
    return _settings


@pytest.fixture
def fixture() -> callable:
    def load(name: str):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return load


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.sqlite3"
