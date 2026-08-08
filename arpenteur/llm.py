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

    def __init__(self, api_key: str) -> None:
        # Imported here rather than at module scope so the package still
        # imports (and the test suite still runs) with no key and no SDK.
        from anthropic import Anthropic

        self.client = Anthropic(api_key=api_key)

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


def get_provider(settings: Any) -> Provider:
    choice = settings.llm.provider
    if choice == "anthropic":
        if not settings.llm.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set; see .env.example")
        return AnthropicProvider(settings.llm.api_key)
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
