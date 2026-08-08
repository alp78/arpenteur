"""The draft stage: propose a message for a human to judge. Not built yet.

Design constraints fixed before a line of it is written:

  a draft is a proposal      rows land in `draft` with sent_at null. Nothing
                             in this pipeline sends anything by itself.
  written from evidence      the segment, the purpose text and the sector,
                             not a template with a name substituted in.
  language follows canton    French for the romand cantons, German elsewhere.
  low volume by design       Swiss law (LCD art. 3 al. 1 let. o) draws its
                             line at mass advertising email. Researched,
                             individual, human-reviewed messages sit on the
                             right side of it; a bulk sender does not.
"""

from __future__ import annotations

MESSAGE = (
    "The draft stage is not implemented yet. It will write one proposal per "
    "contact into the `draft` table for review; sending stays a separate, "
    "manual act."
)


def run(*_args: object, **_kwargs: object) -> None:
    print(MESSAGE)
