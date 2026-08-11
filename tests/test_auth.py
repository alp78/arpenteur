"""Which account pays for a model call, and how that is decided.

The two credentials draw on different accounts: an API key bills the console
account per token, a subscription sign-in draws on the owner's Claude plan.
Picking the wrong one is not a crash, it is a charge on the wrong account, so
the choice is asserted rather than left to whatever the SDK finds.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from arpenteur import llm
from arpenteur.config import LlmConfig


def settings(mode: str, api_key: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        llm=LlmConfig(
            provider="anthropic",
            classify_model="claude-haiku-4-5",
            draft_model="claude-sonnet-5",
            batch_size=20,
            auth_mode=mode,
            api_key=api_key,
        )
    )


@pytest.fixture
def no_profile(monkeypatch, tmp_path):
    """Point the profile lookup at an empty directory."""
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path / "empty"))


@pytest.fixture
def with_profile(monkeypatch, tmp_path):
    credentials = tmp_path / "anthropic" / "credentials"
    credentials.mkdir(parents=True)
    (credentials / "default.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path / "anthropic"))


@pytest.fixture
def built(monkeypatch):
    """Record how AnthropicProvider was constructed, without an SDK or network."""
    seen: dict[str, object] = {}

    def fake_init(self, api_key: str | None = None) -> None:
        seen["api_key"] = api_key
        self.credential = "api key" if api_key else "subscription"

    monkeypatch.setattr(llm.AnthropicProvider, "__init__", fake_init)
    return seen


def test_subscription_mode_ignores_a_present_api_key(with_profile, built) -> None:
    """The whole point of declaring the mode.

    A key left in .env, or exported in the shell, must not divert billing to
    the console account when the operator asked for their subscription.
    """
    provider = llm.get_provider(settings("subscription", "sk-ant-should-be-ignored"))
    assert built["api_key"] is None
    assert provider.credential == "subscription"


def test_api_key_mode_ignores_a_local_sign_in(with_profile, built) -> None:
    """The mirror case: a server must not fall back to the deploy user's plan."""
    llm.get_provider(settings("api_key", "sk-ant-test"))
    assert built["api_key"] == "sk-ant-test"


def test_api_key_mode_without_a_key_fails(with_profile) -> None:
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        llm.get_provider(settings("api_key", None))


def test_subscription_mode_without_a_sign_in_names_the_command(no_profile) -> None:
    with pytest.raises(RuntimeError, match="ant auth login"):
        llm.get_provider(settings("subscription", None))


def test_auto_prefers_the_explicit_api_key(with_profile, built) -> None:
    """A key in .env was put there deliberately; a profile may just exist."""
    llm.get_provider(settings("auto", "sk-ant-test"))
    assert built["api_key"] == "sk-ant-test"


def test_auto_falls_back_to_the_sign_in(with_profile, built) -> None:
    llm.get_provider(settings("auto", None))
    assert built["api_key"] is None


def test_auto_with_no_credential_explains_both_options(no_profile) -> None:
    with pytest.raises(RuntimeError) as exc:
        llm.get_provider(settings("auto", None))
    assert "ANTHROPIC_API_KEY" in str(exc.value)
    assert "ant auth login" in str(exc.value)
