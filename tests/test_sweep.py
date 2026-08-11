"""The sweep is the part that can silently lose data, so it gets the most tests.

Zefix answers a search completely or refuses it outright with
RESULTLIST_TO_LARGE. There is no truncation and no paging, so a refusal that
is mistaken for an empty result loses every company under that prefix without
raising anything, which is exactly the failure a survey must not have.
"""

from __future__ import annotations

import pytest

from arpenteur.sources.zefix import (
    ALPHABET,
    Company,
    ResultListTooLarge,
    walk_prefixes,
)


def make_search(refuse: set[str] | None = None, counts: dict[str, int] | None = None):
    """Search stub: prefixes in `refuse` raise, the rest return `counts` rows."""
    refuse = refuse or set()
    counts = counts or {}
    seen: list[str] = []

    def search(pattern: str) -> list[Company]:
        prefix = pattern.rstrip("*")
        seen.append(prefix)
        if prefix in refuse:
            raise ResultListTooLarge(prefix)
        n = counts.get(prefix, 1)
        return [Company(uid=f"CHE-{prefix}-{i}", name=f"{prefix}{i}") for i in range(n)]

    search.seen = seen  # type: ignore[attr-defined]
    return search


def test_covers_the_whole_alphabet() -> None:
    search = make_search()
    results = list(walk_prefixes(search))
    assert len(results) == len(ALPHABET)
    assert {p for p, _, _ in results} == set(ALPHABET)


def test_refused_prefix_is_narrowed_and_marked() -> None:
    # "a" is refused; its children are not.
    search = make_search(refuse={"a"})
    results = list(walk_prefixes(search))
    by_prefix = {p: (rows, refused) for p, rows, refused in results}

    assert by_prefix["a"][1] is True, "a refused prefix must be marked"
    for ch in ALPHABET:
        assert f"a{ch}" in by_prefix, "every child of a refused prefix must be visited"
    assert by_prefix["aa"][1] is False


def test_refused_prefix_yields_no_rows() -> None:
    """A refusal carries no data, so it must not contribute companies.

    The registry returns nothing at all for a refused query. Yielding rows here
    would mean inventing them; the children are what actually hold the ground.
    """
    search = make_search(refuse={"a"})
    rows = {p: companies for p, companies, refused in walk_prefixes(search) if refused}
    assert rows == {"a": []}


def test_accepted_response_is_complete() -> None:
    """A 200 is the whole set, so every row must be handed to the caller.

    This is what makes the sweep exact rather than best-effort: there is no
    cap to compare against and no tail left behind.
    """
    search = make_search(counts={"a": 4874})
    results = {p: companies for p, companies, _ in walk_prefixes(search)}
    assert len(results["a"]) == 4874


def test_skip_resumes_without_refetching() -> None:
    done = {"a", "b", "c"}
    search = make_search()
    results = list(walk_prefixes(search, skip=done))

    assert not (done & {p for p, _, _ in results})
    assert len(results) == len(ALPHABET) - len(done)
    assert not (done & set(search.seen))  # type: ignore[attr-defined]


def test_max_depth_stops_recursion_and_reports_loss() -> None:
    # Everything refuses: without a depth limit this never terminates.
    search = make_search(refuse={"a", "aa", "aaa", "aaaa", "aaaaa"})
    warnings: list[str] = []
    results = list(walk_prefixes(search, max_depth=3, log=warnings.append))

    assert all(len(p) <= 3 for p, _, _ in results)
    # Hitting the limit means companies are unreachable. Silence would let a
    # sweep report success while missing a branch, so it must be surfaced.
    assert any("companies lost" in w for w in warnings)


def test_search_raises_rather_than_returning_empty() -> None:
    """The distinction the whole sweep rests on: refused is not empty."""
    search = make_search(refuse={"a"})
    with pytest.raises(ResultListTooLarge):
        search("a*")
