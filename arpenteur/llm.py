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
from dataclasses import dataclass, field
from typing import Any, Protocol


class Provider(Protocol):
    name: str

    def complete(self, *, system: str, user: str, model: str, max_tokens: int) -> str: ...

    def complete_json(
        self, *, system: str, user: str, model: str, max_tokens: int,
        schema: dict[str, Any], effort: str = "",
    ) -> dict[str, Any]: ...

    def research(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        schema: dict[str, Any],
        max_searches: int,
    ) -> "Research": ...


@dataclass
class Spend:
    """What one provider instance has cost since it was made.

    Tokens are the bill. `Research` already carried them because the enrich
    stage was the first to cost money per company, and every other model stage
    quietly dropped them: `complete_json` returned a dict and the usage went
    with the response object. The result was a system that could price one of
    its five paid stages.

    Accumulated on the provider rather than returned, because a run makes one
    provider and calls it once per company, and threading a second return
    value through five stages to add it up again at the end is the same
    arithmetic done in five places.
    """

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, message: Any) -> None:
        """Take what an API response reports. Never estimates.

        A message with no usage adds a call and no tokens, which is honest:
        the call happened and what it cost is unknown. Inventing a figure from
        the character count would produce a cost report that looks precise and
        is not, which is worse than one with a gap in it.
        """
        self.calls += 1
        usage = getattr(message, "usage", None)
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0

    def as_stats(self) -> dict[str, int]:
        return {
            "model_calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass
class Research:
    """The result of a search-and-read call.

    `data` is validated against the caller's schema by the provider, so a
    stage can index it without parsing. The counters are carried because this
    is the first stage that costs money per company, and a run that cannot be
    priced afterwards cannot be tuned.

    Tokens are carried for a reason worth stating: they are the dominant cost
    and were the one thing not recorded. A per-company estimate of about forty
    centimes was quoted from a single observation during development, and
    because nothing stored the figure it could not be checked afterwards
    against anything. Searches and fetches were counted while the larger
    number was not, which is precisely the wrong half.

    They accumulate across the pause_turn loop, like the tool counters: each
    resumed request reports only its own usage, and the company was billed for
    all of them.
    """

    data: dict[str, Any]
    searches: int = 0
    fetches: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    # How many requests the turn took. This is what explains the token count:
    # a continuation re-sends the whole conversation including every search
    # result already read, so a company needing three turns costs far more
    # than three times one turn. Measured 12.08.2026, one company reached
    # 465'812 input tokens on four searches.
    turns: int = 1
    pages: list[str] = field(default_factory=list)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None = None) -> None:
        # Imported here rather than at module scope so the package still
        # imports (and the test suite still runs) with no key and no SDK.
        from anthropic import Anthropic

        self.spent = Spend()
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
        self.spent.add(message)
        return "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        schema: dict[str, Any],
        effort: str = "",
    ) -> dict[str, Any]:
        """A structured answer, with no web access.

        Separate from `research` because there are no server tools, so no
        pause_turn and no spend to account for. Separate from `complete`
        because the caller wants fields rather than prose, and validating at
        the API rather than parsing afterwards is the difference between a
        retry and a crash.

        `effort` is how much thinking the model does before answering, and it
        belongs to the stage rather than to this function: extracting a
        glossary and arbitrating between three architecture proposals are not
        the same kind of work and should not cost the same. It rides in
        `output_config` alongside the schema, which is where the API puts it.
        Empty means the model's own default.

        Streamed, not created outright. This used to be a plain create(), which
        was right when the only caller asked for four thousand tokens. It
        stopped being right the moment a stage asked for twenty-four thousand:
        the SDK estimates how long a request of that size could take, sees it
        cross ten minutes, and refuses before sending anything. Streaming
        removes the ceiling and costs nothing here, since the final message is
        all this function ever wanted.
        """
        output_config: dict[str, Any] = {
            "format": {"type": "json_schema", "schema": schema}
        }
        if effort:
            output_config["effort"] = effort
        with self.client.beta.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
        ) as stream:
            message = stream.get_final_message()
        # Recorded before the refusals below: a request that hit max_tokens
        # was billed for every token it produced on the way there, and that is
        # exactly the run somebody wants the cost of.
        self.spent.add(message)
        if message.stop_reason == "refusal":
            raise RuntimeError("the model declined this request")
        if message.stop_reason == "max_tokens":
            raise RuntimeError(
                "reply hit max_tokens before returning JSON; raise the stage's "
                "max_tokens in arpenteur.toml"
                + (
                    f" (effort is {effort!r}, and thinking tokens count against "
                    "the same ceiling)"
                    if effort in ("xhigh", "max")
                    else ""
                )
            )
        # Last text block, for the reason set out in research(): a reply can
        # carry several, and joining them corrupts the JSON.
        texts = [
            block.text
            for block in message.content
            if getattr(block, "type", "") == "text"
        ]
        if not texts:
            raise RuntimeError(
                f"no text block in reply (stop_reason {message.stop_reason!r})"
            )
        data = extract_json(texts[-1])
        if not isinstance(data, dict):
            raise RuntimeError(f"expected an object, got {type(data).__name__}")
        return data

    def research(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        schema: dict[str, Any],
        max_searches: int = 4,
    ) -> Research:
        """Search the web, read what is found, and return it against a schema.

        Anthropic runs the searching and fetching, so nothing here touches the
        open web directly and the PoliteClient rate limit does not apply.

        max_searches is bounded by the caller rather than left to the model,
        and the reason is not the per-search charge. Every result read comes
        back as input on the following turn, and a turn the model pauses and
        resumes re-sends the whole conversation, so searches drive the token
        bill and the token bill is nearly all of the cost: one company
        measured on 12.08.2026 spent about a franc, of which the four searches
        were a few centimes and 465'812 input tokens were the rest.

        Citations are deliberately not enabled: they are incompatible with
        structured outputs and would turn this into prose parsing again.
        """
        tools = [
            {
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": max_searches,
            },
            {
                "type": "web_fetch_20260209",
                "name": "web_fetch",
                "max_uses": max_searches,
            },
        ]
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        searches = fetches = 0
        input_tokens = output_tokens = 0
        pages: list[str] = []

        # A server-tool turn can stop with pause_turn when the model's own
        # tool loop hits its iteration limit. Resuming is a plain re-send of
        # the conversation; the cap stops a pathological company from looping.
        for turn in range(1, 5):
            # Streamed rather than a plain create(). A server-tool turn holds
            # one HTTP request open for the whole search-and-read loop, and
            # measured on 11.08.2026 a single company took just over six
            # minutes against the SDK's ten minute non-streaming ceiling. A
            # company that searches harder than that one would have timed out
            # and been recorded as a failure rather than a result.
            with self.client.beta.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=tools,
                output_config={"format": {"type": "json_schema", "schema": schema}},
            ) as stream:
                message = stream.get_final_message()

            # Spend is read from usage, not counted from the content blocks.
            # Counting blocks looked right and was not: a single turn observed
            # on 11.08.2026 carried sixteen server_tool_use blocks, paired with
            # code_execution_tool_result, while usage reported four searches
            # and two fetches. The block stream is an implementation detail of
            # how the server runs the tools; usage is the billed figure, which
            # is the one a run has to be priced against.
            usage = getattr(message, "usage", None)
            server = getattr(usage, "server_tool_use", None)
            searches += getattr(server, "web_search_requests", 0) or 0
            fetches += getattr(server, "web_fetch_requests", 0) or 0
            # Every search result the model reads comes back as input on the
            # next turn, so this grows far past the prompt this stage wrote.
            # That is the whole reason enrich is expensive and the reason it
            # is worth recording rather than estimating.
            input_tokens += getattr(usage, "input_tokens", 0) or 0
            output_tokens += getattr(usage, "output_tokens", 0) or 0
            # Also onto the provider's running total, so one object can be
            # asked what a whole run cost regardless of which call shapes it
            # used. `Research` keeps its own copy because enrich reports per
            # company and this is per run.
            self.spent.add(message)
            for block in message.content:
                if getattr(block, "type", "") == "server_tool_use":
                    url = (getattr(block, "input", None) or {}).get("url")
                    if url:
                        pages.append(url)

            if message.stop_reason == "refusal":
                raise RuntimeError(
                    "the model declined this research request"
                    + (
                        f" ({message.stop_details.category})"
                        if getattr(message, "stop_details", None)
                        else ""
                    )
                )
            if message.stop_reason == "max_tokens":
                raise RuntimeError(
                    "research response hit max_tokens before returning JSON; "
                    "raise llm.enrich_max_tokens in arpenteur.toml"
                )
            if message.stop_reason == "pause_turn":
                messages = [
                    {"role": "user", "content": user},
                    {"role": "assistant", "content": message.content},
                ]
                continue

            # The last text block, not all of them joined. A server-tool turn
            # interleaves the model's narration with its tool calls, so a run
            # that thinks aloud before answering yields several text blocks;
            # concatenating them glues prose, or a discarded first attempt, on
            # to the JSON and the parse fails on the whole string. The
            # structured reply is always the closing block.
            texts = [
                block.text
                for block in message.content
                if getattr(block, "type", "") == "text"
            ]
            if not texts:
                raise RuntimeError(
                    f"research returned no text block (stop_reason "
                    f"{message.stop_reason!r})"
                )
            return Research(
                data=extract_json(texts[-1]),
                searches=searches,
                fetches=fetches,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                turns=turn,
                pages=pages,
            )

        raise RuntimeError("research did not finish after 4 continuations")


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

    def complete_json(self, **_kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError(
            "The Infomaniak provider is not wired yet. Set llm.provider = "
            '"anthropic" in arpenteur.toml.'
        )

    def research(self, **_kwargs: Any) -> Research:
        # Distinct from the message above: even once this provider is wired,
        # searching the web is a hosted capability rather than a model one, so
        # the enrich stage would still need its own answer here.
        raise NotImplementedError(
            "The enrich stage needs server-side web search, which the "
            "Infomaniak provider does not offer. Set llm.provider = "
            '"anthropic" to run it.'
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
