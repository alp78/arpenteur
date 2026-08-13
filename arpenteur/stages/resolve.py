"""The resolve stage: one human out of the many rows that name them.

`core.person` holds what a notice said, where it said it: one row per company,
name and role. `core.party` holds the human those rows are about, and
`core.party_role` is the edge between them. This stage builds the second from
the first.

It costs nothing. No request, no model, a few thousand local rows and about
three seconds over the whole survey, so it is run again whenever the officer
parser improves rather than being treated as a migration.

**It rebuilds rather than updates.** The resolution rule will change, and a
table that accumulated its own history would make every improvement to the rule
a data migration with nothing to migrate to. The source rows are untouched, so
rebuilding loses nothing the register published.

**It splits when unsure.** The rule and its reasoning are in
`store.resolve_parties`; the short version is that a person wrongly split is a
missing edge, and a person wrongly merged is a false statement about a named
individual in a tool that goes on to suggest writing to them. Every party
records which branch of the rule produced it and how much to believe it.
"""

from __future__ import annotations

from typing import Any, Callable

from ..config import Settings
from ..kernel.db import Connection
from ..modules.scout import store


def run(
    settings: Settings,
    conn: Connection,
    *,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    before = store.party_counts(conn)
    if dry_run:
        log(
            f"{before['people']} officer rows would be resolved into parties. "
            f"{before['parties']} parties are currently held; the table is "
            "rebuilt, so nothing accumulates."
        )
        return {**before, "dry_run": True}

    stats = store.resolve_parties(conn)
    conn.commit()
    log(
        f"{stats['parties']} people out of {before['people']} officer rows, "
        f"{stats['roles']} roles."
    )
    log(
        f"{stats['merged']} of them sit on more than one board, which is what "
        "the graph is for."
    )
    log(
        f"{stats['uncertain']} were resolved on weaker evidence than a name "
        "and a stated origin, and say so on their page."
    )
    return stats
