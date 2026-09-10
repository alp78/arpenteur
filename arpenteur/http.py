"""A polite HTTP client: rate limited, retrying, and cached on disk.

Every remote call in this project goes through here. Three reasons it is one
module rather than per-source code:

  rate limiting   Zefix is a public service run for everyone. A token bucket
                  at a couple of requests a second finishes a canton in
                  minutes and is invisible to them; a naive loop is not.
  retries         Transient 429/5xx are normal on any registry. Backing off
                  and retrying is the difference between a sweep that
                  finishes and one that dies at 80%.
  cache           A full sweep is tens of thousands of requests. Re-running
                  it while developing must not mean re-asking. Responses land
                  under data/http-cache keyed by a hash of method+url+body,
                  and answer for as long as the caller says they may: forever
                  for a closed day of the register's feed, not at all for a
                  company whose notice just told us it changed.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

# The longest this client will ever sleep between attempts, whoever asks for
# it. Thirty seconds is short enough that a stalled worker is visible in a log
# and long enough to clear an ordinary burst limit.
MAX_BACKOFF = 30


def _retry_after(value: str | None) -> float | None:
    """`Retry-After` in seconds, or None when the header says nothing usable.

    RFC 9110 allows two forms and the second was being dropped in silence: a
    delay in seconds, or an HTTP-date. `"120".isdigit()` is true and
    `"Fri, 10 Sep 2026 23:30:00 GMT".isdigit()` is false, so a date-form header
    fell through to the exponential branch and the server's actual instruction
    was discarded.

    Returns None rather than 0 for an unusable value, so the caller can tell
    "the server said nothing" from "the server said go now".
    """
    if not value:
        return None
    text = value.strip()
    if text.isdigit():
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    import datetime as _dt

    now = _dt.datetime.now(_dt.timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return max(0.0, (when - now).total_seconds())


def _older_than(path: Path, max_age: float | None) -> bool:
    """Has a cached response outlived its usefulness?

    None means never, which is the honest answer for an immutable resource
    and a trap for a mutable one, so the decision sits with the caller.
    """
    if max_age is None:
        return False
    try:
        return (time.time() - path.stat().st_mtime) > max_age
    except OSError:
        return True


class RateLimiter:
    """Token bucket. Simple, monotonic, and good enough for one process."""

    def __init__(self, rate: float, burst: int = 4) -> None:
        self.rate = max(rate, 0.1)
        self.burst = max(burst, 1)
        self._tokens = float(self.burst)
        self._last = time.monotonic()

    def take(self) -> None:
        while True:
            current = time.monotonic()
            self._tokens = min(
                self.burst, self._tokens + (current - self._last) * self.rate
            )
            self._last = current
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            time.sleep((1.0 - self._tokens) / self.rate)


@dataclass
class Response:
    status: int
    body: Any
    url: str
    from_cache: bool = False


class PoliteClient:
    """HTTP with the three behaviours above. JSON in, parsed JSON out."""

    def __init__(
        self,
        *,
        contact: str,
        rate_limit: float = 2.0,
        max_retries: int = 4,
        cache_dir: Path | None = None,
        auth: tuple[str, str] | None = None,
        timeout: float = 30.0,
        accept: str = "application/json",
    ) -> None:
        self.limiter = RateLimiter(rate_limit)
        self.max_retries = max_retries
        self.cache_dir = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = httpx.Client(
            timeout=timeout,
            auth=auth,
            headers={
                # Naming the tool and a way to reach its operator is what
                # separates a courteous client from an anonymous scraper.
                "User-Agent": contact,
                # A registry API and a company's own website want opposite
                # answers here, and a site with a strict content negotiator
                # replies 406 to a client that only ever asks for JSON.
                "Accept": accept,
            },
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _cache_path(self, method: str, url: str, body: Any) -> Path | None:
        if not self.cache_dir:
            return None
        material = json.dumps(
            [method, url, body], sort_keys=True, ensure_ascii=False, default=str
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        # Two-level fan-out: a flat directory with 40k entries is unpleasant
        # to list and slow on some filesystems.
        return self.cache_dir / digest[:2] / f"{digest}.json"

    def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        use_cache: bool = True,
        max_age: float | None = None,
    ) -> Response:
        """Fetch a URL, politely.

        max_age is in seconds and decides how long a cached copy may answer
        for. None, the default, means forever, which is right for a resource
        that cannot change: a past day of the FOSC feed is closed, and the
        sweep re-reads it endlessly while developing.

        It is wrong for anything the register can amend. A company detail
        cached forever makes a synchroniser blind: the notice says the company
        changed, we re-fetch, and the disk hands back the same bytes that were
        there before the change. Callers refreshing a known mutation pass
        max_age=0, which re-asks but still stores the answer.
        """
        path = self._cache_path(method, url, json_body) if use_cache else None
        if path and path.is_file() and not _older_than(path, max_age):
            cached = json.loads(path.read_text(encoding="utf-8"))
            return Response(cached["status"], cached["body"], url, from_cache=True)

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.limiter.take()
            try:
                reply = self.client.request(method, url, json=json_body)
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(min(2**attempt, 30))
                continue

            if reply.status_code in (429, 500, 502, 503, 504):
                # Respect Retry-After when the server sends one, but never
                # further than MAX_BACKOFF.
                #
                # THE CAP USED TO APPLY TO THE WRONG BRANCH. It bounded the
                # exponential fallback — the one that was already small — and
                # left a server-supplied delay unbounded, so the comment's
                # promise about a bad afternoon was exactly inverted. A
                # `Retry-After: 86400` from a Cloudflare or Squarespace edge
                # parked a worker for twenty-four hours, holding its socket in
                # CLOSE_WAIT and writing nothing. Two shards of a six-way run
                # sat like that for an hour before anyone noticed, because a
                # silent sleep looks identical to slow work.
                #
                # A delay longer than the cap is a refusal, not a hint: waiting
                # it out serves nobody, and the retry budget is a better place
                # to give up than the clock.
                delay = _retry_after(reply.headers.get("Retry-After"))
                if delay is None:
                    delay = float(min(2**attempt, MAX_BACKOFF))
                time.sleep(min(delay, float(MAX_BACKOFF)))
                last_error = httpx.HTTPStatusError(
                    f"{reply.status_code} from {url}", request=reply.request, response=reply
                )
                continue

            body: Any
            try:
                body = reply.json()
            except ValueError:
                body = reply.text

            result = Response(reply.status_code, body, url)
            if path and reply.status_code == 200:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps({"status": result.status, "body": body}, ensure_ascii=False),
                    encoding="utf-8",
                )
            return result

        raise RuntimeError(f"{method} {url} failed after {self.max_retries} retries") from last_error

    def get(self, url: str, **kwargs: Any) -> Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, json_body: Any = None, **kwargs: Any) -> Response:
        return self.request("POST", url, json_body=json_body, **kwargs)
