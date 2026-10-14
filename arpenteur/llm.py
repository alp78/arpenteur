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
    """The metered Messages API. Billed per token against API credit.

    THIS IS NOT A SUBSCRIPTION PATH, AND IT USED TO CLAIM TO BE. Constructing
    the client with no key was labelled "subscription" here on the theory that
    the SDK would find the OAuth profile and the calls would draw on a Claude
    plan. They do not. `api.anthropic.com/v1/messages` bills the API account
    whatever credential opens it, and a Max plan does not fund it: the address
    cascade's search tier failed with "credit balance is too low" on a machine
    whose subscription was working perfectly, and the message was read as a
    broken key rather than as the two ledgers they are.

    So this class no longer pretends. A subscription is a different transport,
    not a different credential on this one — see `ClaudeCodeProvider`.
    """

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
            # Still allowed: the SDK reads ANTHROPIC_API_KEY from the
            # environment. Named for what it is rather than for what it was
            # hoped to be.
            self.client = Anthropic()
            self.credential = "api key from the environment"

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


class ClaudeCodeProvider:
    """A Claude subscription, reached through the Claude Code CLI.

    **Why a subprocess and not an SDK call.** A Claude plan and API credit are
    separate accounts, and the Messages API can only be paid for by the second.
    The only transport that spends a subscription is the one the subscription
    was sold with: `claude -p`, which authenticates with the OAuth login and
    draws on the plan's quota. There is no header that turns an API call into a
    subscription call, which is exactly the thing the old code assumed.

    **What that buys and what it costs.** Search and page-reading come with the
    harness, so this needs no server-tool wiring. In exchange a call is a
    process, it takes tens of seconds, and it is metered in plan quota rather
    than francs, so the honest unit of cost is time and rate limit rather than
    a bill.

    **The flags are not decoration; each removes weight from every call.**

      --restricted            drops the code-running tools and, more to the
                              point, ignores user and project settings, so a
                              CLAUDE.md and a memory directory do not ride
                              along on six thousand lookups
      --tools                 the built-in set is replaced, not merely
                              auto-approved, so unused tool schemas leave the
                              context
      --system-prompt         replaces the harness prompt with the caller's
      --strict-mcp-config     no MCP server is consulted
      --disable-slash-commands, --no-session-persistence
                              no skill discovery, no transcript per company

    Measured on one company, the difference between a bare `claude -p` and this
    set: 18 turns to 6, 270'000 cached tokens to 5'700, and the same answer.
    Left as it was, a national run would have paid the whole harness context
    once per company.

    **Not `--bare`.** It is the obvious flag to reach for and it is the one
    that breaks this: bare mode never reads OAuth credentials, so it falls back
    to an API key and quietly bills the account this class exists to avoid.
    """

    name = "claude-code"
    credential = "Claude subscription (claude -p)"

    def __init__(self, binary: str = "claude", timeout: int = 600) -> None:
        self.spent = Spend()
        self.binary = binary
        self.timeout = timeout

    # --- the subprocess -------------------------------------------------------

    def _run(
        self,
        *,
        system: str,
        user: str,
        model: str,
        schema: dict[str, Any] | None,
        tools: str,
    ) -> dict[str, Any]:
        import subprocess
        import tempfile

        # The prompt travels on stdin, never as an argument: a dossier with
        # one document attached is longer than an argument list may be, and
        # the failure that produces (E2BIG) names nothing a reader can use.
        argv = [
            self.binary, "-p",
            "--output-format", "json",
            "--tools", tools,
            "--restricted", "--strict-mcp-config",
            "--disable-slash-commands", "--no-session-persistence",
            "--permission-mode", "auto", "--permission-prompts", "none",
        ]
        if system:
            argv += ["--system-prompt", system]
        if model:
            argv += ["--model", model]
        if schema is not None:
            argv += ["--json-schema", json.dumps(schema)]

        # Run somewhere with no project of its own. `--restricted` already
        # ignores project settings, and a neutral directory means the file
        # tools are confined to a scratch it cannot damage.
        with tempfile.TemporaryDirectory(prefix="arpenteur-llm-") as cwd:
            try:
                done = subprocess.run(
                    argv, cwd=cwd, capture_output=True, text=True,
                    timeout=self.timeout, input=user,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    f"{self.binary!r} is not installed, and llm.auth_mode is "
                    "'subscription'. Install it with:\n"
                    "    npm install -g @anthropic-ai/claude-code\n"
                    "then sign in with:  claude   (once, interactively)"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"the subscription call did not finish in {self.timeout}s"
                ) from exc
            except OSError as exc:
                raise RuntimeError(
                    f"{self.binary!r} could not be started: {exc.strerror or exc}"
                ) from exc

        if done.returncode != 0:
            detail = (done.stderr or done.stdout or "").strip()[:400]
            raise RuntimeError(f"claude -p exited {done.returncode}: {detail}")
        try:
            payload = json.loads(done.stdout)
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"claude -p returned no JSON: {done.stdout[:300]!r}"
            ) from exc
        # A failed run still exits 0 and reports itself in the payload, which
        # is documented behaviour and easy to read straight past.
        if payload.get("is_error") or payload.get("subtype") != "success":
            raise RuntimeError(
                f"claude -p failed ({payload.get('subtype')}): "
                f"{str(payload.get('result'))[:300]}"
            )
        self._record(payload)
        return payload

    def _record(self, payload: dict[str, Any]) -> None:
        usage = payload.get("usage") or {}
        self.spent.calls += 1
        # Cache reads and writes are input tokens that were paid for; leaving
        # them out would under-report a run by an order of magnitude, which is
        # the whole failure `Spend` was written to stop.
        self.spent.input_tokens += (
            int(usage.get("input_tokens") or 0)
            + int(usage.get("cache_read_input_tokens") or 0)
            + int(usage.get("cache_creation_input_tokens") or 0)
        )
        self.spent.output_tokens += int(usage.get("output_tokens") or 0)

    @staticmethod
    def _tool_counts(payload: dict[str, Any]) -> tuple[int, int]:
        """Searches and fetches, from wherever this transport reports them.

        `usage.server_tool_use` is the API's counter and reads zero here: the
        harness searches with its own client-side tool, so nothing goes through
        the server tool that counter describes. The real figure is per model in
        `modelUsage`, and it is summed rather than taken from one entry because
        a single run uses more than one model — a small one does the harness's
        own housekeeping while the named model does the work.

        Fetches have no equivalent field, so they are reported as zero rather
        than estimated from the turn count. A made-up number here would end up
        in a cost report looking like a measurement.
        """
        server = (payload.get("usage") or {}).get("server_tool_use") or {}
        searches = int(server.get("web_search_requests") or 0)
        for entry in (payload.get("modelUsage") or {}).values():
            if isinstance(entry, dict):
                searches += int(entry.get("webSearchRequests") or 0)
        return searches, int(server.get("web_fetch_requests") or 0)

    # --- the Provider protocol ------------------------------------------------

    def complete(self, *, system: str, user: str, model: str, max_tokens: int) -> str:
        payload = self._run(
            system=system, user=user, model=model, schema=None, tools="",
        )
        return str(payload.get("result") or "")

    def complete_json(
        self, *, system: str, user: str, model: str, max_tokens: int,
        schema: dict[str, Any], effort: str = "",
    ) -> dict[str, Any]:
        payload = self._run(
            system=system, user=user, model=model, schema=schema, tools="",
        )
        data = payload.get("structured_output")
        if isinstance(data, dict):
            return data
        # The harness can answer in prose when a schema is awkward; the same
        # salvage the API path uses rather than losing the call.
        return extract_json(str(payload.get("result") or ""))

    def research(
        self, *, system: str, user: str, model: str, max_tokens: int,
        schema: dict[str, Any], max_searches: int,
    ) -> Research:
        """Search the web and read pages, on the subscription's quota.

        `max_searches` is a request rather than a cap: the harness owns its own
        loop and takes no budget argument, so the number is put to the model in
        words. That is weaker than the API path, where the tool definition
        enforces it, and it is the honest limitation of this transport.
        """
        asked = system
        if max_searches:
            asked = (
                f"{system}\n\nWork within about {max_searches} searches and as "
                "few page reads as will answer the question."
            ).strip()
        payload = self._run(
            system=asked, user=user, model=model, schema=schema,
            tools="WebSearch,WebFetch",
        )
        data = payload.get("structured_output")
        if not isinstance(data, dict):
            data = extract_json(str(payload.get("result") or "")) or {}
        searches, fetches = self._tool_counts(payload)
        usage = payload.get("usage") or {}
        return Research(
            data=data,
            searches=searches,
            fetches=fetches,
            input_tokens=(
                int(usage.get("input_tokens") or 0)
                + int(usage.get("cache_read_input_tokens") or 0)
                + int(usage.get("cache_creation_input_tokens") or 0)
            ),
            output_tokens=int(usage.get("output_tokens") or 0),
            turns=int(payload.get("num_turns") or 1),
        )


def _on_mac() -> bool:
    import platform

    return platform.system() == "Darwin"


def _has_local_profile() -> bool:
    """Is there a Claude Code sign-in on this machine?

    Checked before constructing a client so a missing sign-in reads as "sign
    in" rather than as a generic auth error. The CLI keeps its sign-in in one
    credentials file under `~/.claude/` on Linux and Windows; on macOS it
    keeps it in the Keychain, which nothing here can read, so there the
    installed CLI is trusted and a missing sign-in is reported by the first
    call. A profile written by any other tool is not a Claude Code sign-in
    and used to pass this check, after which the first call failed.
    """
    from pathlib import Path

    if (Path.home() / ".claude" / ".credentials.json").is_file():
        return True
    return _on_mac() and _claude_cli() is not None


def _claude_cli() -> str | None:
    """The Claude Code binary, or None when it is not installed."""
    import shutil

    return shutil.which("claude")


SETUP_HELP = """No Claude credential found.

The two ways to pay draw on different accounts and are not interchangeable.

  A Claude subscription, which is the default and costs plan quota:

      npm install -g @anthropic-ai/claude-code
      claude            # once, to sign in

  Or API credit, which costs money per token:

      ANTHROPIC_API_KEY=... in .env   (console.anthropic.com)

Sourcing and hydration need neither; only profile, draft and the address
search call a model."""


def get_provider(settings: Any) -> Provider:
    choice = settings.llm.provider
    if choice == "anthropic":
        llm = settings.llm
        # Subscription first, and that ordering is the point of this change.
        # The two modes are separate accounts, not two keys for one: a plan
        # cannot pay for the Messages API and API credit cannot be spent from a
        # plan. Preferring the subscription means the default run costs quota
        # and time rather than money, and the API stays available for whoever
        # wants to buy speed with it.
        if llm.uses_subscription:
            binary = _claude_cli()
            if binary and _has_local_profile():
                return ClaudeCodeProvider(binary, llm.subscription_timeout)
            if llm.auth_mode == "subscription":
                raise RuntimeError(
                    "llm.auth_mode is 'subscription' but the Claude Code CLI "
                    "is not installed. Install it with:\n"
                    "    npm install -g @anthropic-ai/claude-code\n"
                    "then sign in once by running: claude"
                    if not binary else
                    "llm.auth_mode is 'subscription' but no Claude sign-in was "
                    "found. Run `claude` once and sign in."
                )
        if llm.uses_api_key and llm.api_key:
            return AnthropicProvider(llm.api_key)
        if llm.auth_mode == "auto":
            raise RuntimeError(SETUP_HELP)
        raise RuntimeError(
            "llm.auth_mode is 'api_key' but ANTHROPIC_API_KEY is not set; "
            "see .env.example"
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

    # Whichever bracket opens first, because the outermost value is the reply.
    # Trying "[" before "{" regardless of position read the first array inside
    # an object: `Réponse : {"signals":["a","b"],"score":7}` came back as
    # `["a","b"]`, which the caller then treated as the whole answer.
    openers = sorted(
        (text.find(o), o, c)
        for o, c in (("[", "]"), ("{", "}"))
        if text.find(o) != -1
    )
    for start, _opener, closer in openers:
        end = text.rfind(closer)
        if end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError(f"No JSON found in model reply: {text[:200]!r}")
