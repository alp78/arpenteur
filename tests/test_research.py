"""Reading a server-tool reply correctly.

Both cases here are bugs that ran in production against the live API and
looked fine from the outside, which is why they are pinned. A server-tool
turn does not have the shape of an ordinary completion: the model narrates
between its tool calls, and the server runs those tools through machinery
whose block stream is not a count of anything the caller is billed for.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from arpenteur.llm import AnthropicProvider


class Block(SimpleNamespace):
    pass


def message(
    blocks: list[Any], *, searches: int = 0, fetches: int = 0, stop: str = "end_turn"
) -> SimpleNamespace:
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop,
        stop_details=None,
        usage=SimpleNamespace(
            server_tool_use=SimpleNamespace(
                web_search_requests=searches, web_fetch_requests=fetches
            )
        ),
    )


class StubStream:
    def __init__(self, reply: SimpleNamespace) -> None:
        self.reply = reply

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def get_final_message(self) -> SimpleNamespace:
        return self.reply


def provider_returning(reply: SimpleNamespace) -> AnthropicProvider:
    p = AnthropicProvider(api_key="test-only-not-a-real-key")
    p.client = SimpleNamespace(
        beta=SimpleNamespace(
            messages=SimpleNamespace(stream=lambda **_kw: StubStream(reply))
        )
    )
    return p


def research(p: AnthropicProvider):
    return p.research(
        system="s", user="u", model="m", max_tokens=100, schema={}, max_searches=4
    )


JSON = '{"status": "found", "url": "https://example.ch"}'


def test_the_closing_text_block_is_the_answer() -> None:
    """A turn that narrates before answering must still parse.

    Joining every text block glues the narration on to the JSON and the whole
    string fails to parse, which surfaced as "No JSON found in model reply"
    against a reply whose JSON was perfectly well formed.
    """
    reply = message(
        [
            Block(type="thinking", thinking="considering"),
            Block(type="text", text="Let me search for this company first."),
            Block(type="server_tool_use", name="web_search", input={"query": "x"}),
            Block(type="text", text=JSON),
        ]
    )
    assert research(provider_returning(reply)).data["url"] == "https://example.ch"


def test_a_discarded_first_attempt_does_not_corrupt_the_parse() -> None:
    """The nastier version: two JSON blocks, where joining yields '}{' and the
    brace scan then spans both and parses as neither."""
    reply = message(
        [
            Block(type="text", text='{"status": "not_found", "url": null}'),
            Block(type="text", text=JSON),
        ]
    )
    assert research(provider_returning(reply)).data["status"] == "found"


def test_spend_comes_from_usage_not_from_counting_blocks() -> None:
    """Observed live: sixteen server_tool_use blocks, four searches billed.

    The server pairs each call with a code_execution_tool_result and may split
    one logical search across several blocks, so counting them overstates the
    bill. usage carries the figure the account is actually charged.
    """
    blocks = [Block(type="text", text=JSON)]
    for _ in range(8):
        blocks.insert(0, Block(type="server_tool_use", name="web_search", input={}))
        blocks.insert(0, Block(type="code_execution_tool_result", content=[]))
    result = research(provider_returning(message(blocks, searches=4, fetches=2)))
    assert (result.searches, result.fetches) == (4, 2)


def test_a_reply_with_no_text_block_says_so() -> None:
    """Better than IndexError, and better than a silent empty result."""
    reply = message([Block(type="thinking", thinking="...")])
    with pytest.raises(RuntimeError, match="no text block"):
        research(provider_returning(reply))


def test_usage_missing_entirely_is_not_fatal() -> None:
    """Spend accounting must never be the reason a good result is thrown away."""
    reply = SimpleNamespace(
        content=[Block(type="text", text=JSON)], stop_reason="end_turn",
        stop_details=None, usage=None,
    )
    result = research(provider_returning(reply))
    assert result.data["status"] == "found"
    assert (result.searches, result.fetches) == (0, 0)
