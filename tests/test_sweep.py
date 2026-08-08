"""The sweep is the part that can silently lose data, so it gets the most tests.

Zefix caps a search response. If a prefix hits the cap and nothing deepens it,
every company past the cap is invisible and no error is ever raised, which is
exactly the failure a survey must not have.
"""

from __future__ import annotations

from arpenteur.sources.zefix import ALPHABET, Company, walk_prefixes

CAP = 5


def make_search(counts: dict[str, int]):
    """Search stub: `counts` maps a prefix to how many rows it returns."""
    seen: list[str] = []

    def search(pattern: str) -> list[Company]:
        prefix = pattern.rstrip("*")
        seen.append(prefix)
        n = counts.get(prefix, 1)
        return [Company(uid=f"CHE-{prefix}-{i}", name=f"{prefix}{i}") for i in range(n)]

    search.seen = seen  # type: ignore[attr-defined]
    return search


def test_covers_the_whole_alphabet() -> None:
    search = make_search({})
    results = list(walk_prefixes(search, page_cap=CAP))
    assert len(results) == len(ALPHABET)
    assert {p for p, _, _ in results} == set(ALPHABET)


def test_capped_prefix_is_deepened_and_marked() -> None:
    # "a" overflows; its children do not.
    search = make_search({"a": CAP})
    results = list(walk_prefixes(search, page_cap=CAP))
    by_prefix = {p: (rows, deep) for p, rows, deep in results}

    assert by_prefix["a"][1] is True, "a capped prefix must be marked deepened"
    # Every child of "a" was visited.
    for ch in ALPHABET:
        assert f"a{ch}" in by_prefix
    assert by_prefix["aa"][1] is False


def test_deepened_rows_are_not_trusted() -> None:
    """A truncated response must not be the only record of its companies.

    The caller skips writing rows for deepened prefixes; this asserts the
    flag it keys on is correct, since the children re-fetch the same ground.
    """
    search = make_search({"a": CAP})
    deepened = {p for p, _, deep in walk_prefixes(search, page_cap=CAP) if deep}
    assert deepened == {"a"}


def test_skip_resumes_without_refetching() -> None:
    done = {"a", "b", "c"}
    search = make_search({})
    results = list(walk_prefixes(search, page_cap=CAP, skip=done))

    assert not (done & {p for p, _, _ in results})
    assert len(results) == len(ALPHABET) - len(done)
    assert not (done & set(search.seen))  # type: ignore[attr-defined]


def test_max_depth_stops_recursion() -> None:
    # Everything overflows: without a depth limit this never terminates.
    search = make_search({p: CAP for p in ("a", "aa", "aaa", "aaaa", "aaaaa")})
    results = list(walk_prefixes(search, page_cap=CAP, max_depth=3))
    assert all(len(p) <= 3 for p, _, _ in results)
    # At the limit the prefix is still yielded, but not deepened further.
    deepest = [p for p, _, deep in results if len(p) == 3 and deep]
    assert deepest == []
