"""Which account pays for a model call, and how that is decided.

The two credentials draw on different accounts, and — the part this file used
to get wrong — they are reached by different transports. An API key bills the
console account per token through the Messages API. A subscription draws on the
owner's Claude plan and can only be spent through the Claude Code CLI. There is
no header that turns one into the other, so "use my subscription" is a decision
about which program runs, not about which credential is passed.

These assert the choice rather than leaving it to whatever a client finds,
because picking the wrong one is not a crash. It is a charge on the wrong
account, or a run that stops on "credit balance is too low" while the plan it
was supposed to use sits there working.
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
def cli(monkeypatch):
    """The Claude Code CLI is installed and signed in."""
    monkeypatch.setattr(llm, "_claude_cli", lambda: "/usr/bin/claude")
    monkeypatch.setattr(llm, "_has_local_profile", lambda: True)


@pytest.fixture
def no_cli(monkeypatch):
    """No CLI, so no subscription transport exists on this machine."""
    monkeypatch.setattr(llm, "_claude_cli", lambda: None)
    monkeypatch.setattr(llm, "_has_local_profile", lambda: False)


@pytest.fixture
def cli_but_signed_out(monkeypatch):
    monkeypatch.setattr(llm, "_claude_cli", lambda: "/usr/bin/claude")
    monkeypatch.setattr(llm, "_has_local_profile", lambda: False)


@pytest.fixture
def built(monkeypatch):
    """Record how AnthropicProvider was constructed, without an SDK or network."""
    seen: dict[str, object] = {}

    def fake_init(self, api_key: str | None = None) -> None:
        seen["api_key"] = api_key
        self.credential = "api key" if api_key else "api key from the environment"

    monkeypatch.setattr(llm.AnthropicProvider, "__init__", fake_init)
    return seen


def test_subscription_mode_ignores_a_present_api_key(cli) -> None:
    """The whole point of declaring the mode.

    A key left in .env, or exported in the shell, must not divert billing to
    the console account when the operator asked for their subscription.
    """
    provider = llm.get_provider(settings("subscription", "sk-ant-should-be-ignored"))
    assert isinstance(provider, llm.ClaudeCodeProvider)


def test_subscription_mode_is_not_the_messages_api(cli) -> None:
    """The correction this file exists to hold.

    Subscription mode used to build an API client with no key, on the theory
    that a keyless client would find the plan's OAuth token and spend it. It
    does not: that client bills API credit whatever opens it. Anything that
    returns an AnthropicProvider here has reintroduced the bug, and the symptom
    is a billing error on a machine whose subscription works.
    """
    provider = llm.get_provider(settings("subscription", None))
    assert not isinstance(provider, llm.AnthropicProvider)
    assert provider.name == "claude-code"


def test_api_key_mode_ignores_a_local_sign_in(cli, built) -> None:
    """The mirror case: a server must not fall back to the deploy user's plan."""
    llm.get_provider(settings("api_key", "sk-ant-test"))
    assert built["api_key"] == "sk-ant-test"


def test_api_key_mode_without_a_key_fails(cli) -> None:
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        llm.get_provider(settings("api_key", None))


def test_subscription_without_the_cli_names_the_install(no_cli) -> None:
    """A missing CLI is the likely first failure, so it must not read as an
    auth problem: nothing is wrong with the account."""
    with pytest.raises(RuntimeError, match="npm install -g @anthropic-ai/claude-code"):
        llm.get_provider(settings("subscription", None))


def test_subscription_with_the_cli_but_no_sign_in_says_so(cli_but_signed_out) -> None:
    with pytest.raises(RuntimeError, match="sign in"):
        llm.get_provider(settings("subscription", None))


def test_auto_prefers_the_subscription_over_a_key(cli, built) -> None:
    """Reversed deliberately, and this is the assertion that records it.

    It used to prefer the API key on the grounds that a key in .env was put
    there on purpose. That reasoning holds for which credential is explicit and
    misses what it costs: the address search runs once per company over
    thousands of companies, so preferring the key turns a quota-and-time run
    into a bill nobody asked for. The plan is free at the point of use; the key
    is there for whoever wants to buy speed with it.
    """
    provider = llm.get_provider(settings("auto", "sk-ant-test"))
    assert isinstance(provider, llm.ClaudeCodeProvider)
    assert "api_key" not in built


def test_auto_falls_back_to_the_key_when_there_is_no_cli(no_cli, built) -> None:
    llm.get_provider(settings("auto", "sk-ant-test"))
    assert built["api_key"] == "sk-ant-test"


def test_auto_with_no_credential_explains_both_options(no_cli) -> None:
    with pytest.raises(RuntimeError) as exc:
        llm.get_provider(settings("auto", None))
    assert "ANTHROPIC_API_KEY" in str(exc.value)
    assert "claude-code" in str(exc.value)
