"""The people stage: who decides, from public sources. Not built yet.

Planned sources, in order of authority:

  cantonal excerpt   company.cantonal_excerpt_url points at the official
                     register extract, which names the officers of the
                     company. For an SME the decision maker is in there,
                     and it is the one source that is authoritative rather
                     than inferred.
  published pages    impressum, equipe, contact. Swiss SMEs publish named
                     roles far more often than the databases assume.

Rows land in `person` with the provenance columns already in the schema.
"""

from __future__ import annotations

MESSAGE = (
    "The people stage is not implemented yet. It will read officers from the "
    "cantonal register excerpt linked on each company row, and from published "
    "team pages. The `person` table is already in the schema."
)


def run(*_args: object, **_kwargs: object) -> None:
    print(MESSAGE)
