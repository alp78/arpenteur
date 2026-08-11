"""Model access behind one small interface.

Anthropic is the only implementation today. The interface exists anyway,
because the sovereign story this tool is built for eventually wants open
models on Swiss infrastructure, and the stages must not need editing on the
day that switch is made. Providers take a system prompt and a user prompt and
return text; anything richer would leak one vendor's shape into the callers.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol


class Provider(Protocol):
    name: str

    def complete(self, *, system: str, user: str, model: str, max_tokens: int) -> str: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None = None) -> None:
        # Imported here rather than at module scope so the package still
        # imports (and the test suite still runs) with no key and no SDK.
        from anthropic import Anthropic

        if api_key:
            self.client = Anthropic(api_key=api_key)
            self.credential = "api key"
        else:
            # No api_key argument: the SDK resolves the credential itself,
            # finding the OAuth profile written by `ant auth login`. The
            # subscription token is short-lived and refreshed by the SDK, so
            # it must never be copied into this process or into .env.
            self.client = Anthropic()
            self.credential = "subscription"

    def complete(self, *, system: str, user: str, model: str, max_tokens: int) -> str:
        message = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )


class InfomaniakProvider:
    """Placeholder for the sovereign path: open models served from Switzerland.

    Left unimplemented on purpose rather than half-written. When it lands it
    implements the same two-prompt contract and nothing else changes.
    """

    name = "infomaniak"

    def __init__(self, api_key: str, product_id: str) -> None:
        self.api_key = api_key
        self.product_id = product_id

    def complete(self, *, system: str, user: str, model: str, max_tokens: int) -> str:
        raise NotImplementedError(
            "The Infomaniak provider is not wired yet. Set llm.provider = "
            '"anthropic" in arpenteur.toml.'
        )


def _has_local_profile() -> bool:
    """Is there an `ant auth login` profile on this machine?

    Checked before constructing a client so a missing sign-in reads as
    "run ant auth login" rather than as the SDK's generic auth error. The
    location follows the SDK's own convention, ANTHROPIC_CONFIG_DIR first.
    """
    import os
    from pathlib import Path

    configured = os.environ.get("ANTHROPIC_CONFIG_DIR")
    base = Path(configured) if configured else Path.home() / ".config" / "anthropic"
    credentials = base / "credentials"
    return credentials.is_dir() and any(credentials.glob("*.json"))


SETUP_HELP = """No Anthropic credential found.

Either set ANTHROPIC_API_KEY in .env (console.anthropic.com), or sign in
with a Claude subscription:

    ant auth login

Sourcing and hydration need neither; only profile and draft call a model."""


def get_provider(settings: Any) -> Provider:
    choice = settings.llm.provider
    if choice == "anthropic":
        llm = settings.llm
        # An api_key wins when the mode allows it, because it is the explicit
        # one: it was put in .env on purpose. Subscription is the fallback.
        if llm.uses_api_key and llm.api_key:
            return AnthropicProvider(llm.api_key)
        if llm.uses_subscription:
            if not _has_local_profile():
                raise RuntimeError(
                    "llm.auth_mode is 'subscription' but no Claude sign-in was "
                    "found. Run: ant auth login"
                    if llm.auth_mode == "subscription"
                    else SETUP_HELP
                )
            return AnthropicProvider()
        # auth_mode = "api_key" with nothing in .env.
        raise RuntimeError(
            "llm.auth_mode is 'api_key' but ANTHROPIC_API_KEY is not set; "
            "see .env.example"
        )
    if choice == "infomaniak":
        import os

        return InfomaniakProvider(
            os.environ.get("INFOMANIAK_API_KEY", ""),
            os.environ.get("INFOMANIAK_AI_PRODUCT_ID", ""),
        )
    raise RuntimeError(f"Unknown llm.provider: {choice!r}")


def extract_json(text: str) -> Any:
    """Pull a JSON value out of a model reply.

    Models wrap JSON in prose or fences often enough that every caller would
    otherwise repeat this. Parsed strictly first; the fence-stripping and
    brace-scanning are fallbacks, not the happy path.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError(f"No JSON found in model reply: {text[:200]!r}")
