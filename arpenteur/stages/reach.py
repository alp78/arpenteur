"""The reach stage: resolve a point of contact. Not built yet.

Order matters, and it is cheapest-and-most-consented first:

  published   an address the company itself put on its own site. Free, and
              the only tier where the company chose to be reachable.
  pattern     infer the house convention from addresses already published on
              the domain, then verify before use.
  provider    a paid lookup, last, for what the first two missed.

Whatever the tier, the result carries `method` and `source_url` so the
dataset can always answer where an address came from, and `suppressed` is
checked before anything is ever drafted against it.
"""

from __future__ import annotations

MESSAGE = (
    "The reach stage is not implemented yet. It will try published addresses "
    "first, then the domain's own naming pattern, then a paid lookup for the "
    "remainder. The `contact` table is already in the schema."
)


def run(*_args: object, **_kwargs: object) -> None:
    print(MESSAGE)
