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


# --- the transport, as it is run ---------------------------------------------------


def test_the_prompt_goes_over_stdin_not_the_argument_list(monkeypatch) -> None:
    """A dossier with a document attached is longer than an argument list may
    be, and E2BIG names nothing a reader can use."""
    import json
    import subprocess

    seen: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen.update(kwargs)
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"subtype": "success", "result": "ok",
                                        "usage": {}, "modelUsage": {}}), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = llm.ClaudeCodeProvider("/usr/bin/claude", timeout=5)
    provider._run(system="be brief", user="the whole dossier " * 10000,
                  model="claude-sonnet-5-5", schema=None, tools="")
    argv = seen["argv"]
    assert seen["input"] == "the whole dossier " * 10000
    assert argv[1] == "-p" and argv[2].startswith("--"), "no prompt in the argument list"
    assert "--system-prompt" in argv and "--model" in argv


def test_a_cli_that_cannot_be_started_is_one_sentence(monkeypatch) -> None:
    import subprocess

    def refuse(argv, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(subprocess, "run", refuse)
    provider = llm.ClaudeCodeProvider("/usr/bin/claude", timeout=5)
    with pytest.raises(RuntimeError, match="could not be started"):
        provider._run(system="", user="x", model="", schema=None, tools="")


def test_only_the_claude_code_sign_in_counts(tmp_path, monkeypatch) -> None:
    """A profile written by another tool used to pass the check, after which
    the first call failed."""
    from pathlib import Path

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(llm, "_on_mac", lambda: False)
    monkeypatch.setattr(llm, "_claude_cli", lambda: "/usr/bin/claude")
    other = tmp_path / ".config" / "anthropic" / "credentials"
    other.mkdir(parents=True)
    (other / "default.json").write_text("{}", encoding="utf-8")
    assert llm._has_local_profile() is False
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
    assert llm._has_local_profile() is True


def test_on_a_mac_the_installed_cli_is_trusted(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(llm, "_on_mac", lambda: True)
    monkeypatch.setattr(llm, "_claude_cli", lambda: "/opt/homebrew/bin/claude")
    assert llm._has_local_profile() is True
    monkeypatch.setattr(llm, "_claude_cli", lambda: None)
    assert llm._has_local_profile() is False


def test_the_unwired_provider_is_gone() -> None:
    assert not hasattr(llm, "InfomaniakProvider")
    with pytest.raises(RuntimeError, match="Unknown llm.provider"):
        llm.get_provider(SimpleNamespace(llm=SimpleNamespace(provider="infomaniak")))
