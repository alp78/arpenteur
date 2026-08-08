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
                  under data/http-cache keyed by a hash of method+url+body.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


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
                "Accept": "application/json",
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
    ) -> Response:
        path = self._cache_path(method, url, json_body) if use_cache else None
        if path and path.is_file():
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
                # Respect Retry-After when the server sends one; otherwise
                # exponential backoff, capped so a bad afternoon does not
                # turn into a half-hour sleep.
                wait = reply.headers.get("Retry-After")
                delay = float(wait) if wait and wait.isdigit() else min(2**attempt, 30)
                time.sleep(delay)
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
