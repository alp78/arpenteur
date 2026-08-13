"""Analytics: reading the whole thing back, without writing to any of it.

Owns `analytics`, which holds views and materialised views and no tables that
the application writes to. That separation is the point. Mixing operational and
analytical work in one place is what makes people afraid to run a report, and
the fear is justified: a query that scans every company while a sweep is
writing them is how a survey run slows to a crawl for reasons nobody can see
from either side.

What keeps them apart here is a schema and a refresh discipline, not a second
server. The heavy aggregates are materialised and refreshed on demand, so a
report reads a small table rather than the survey; the cheap ones are plain
views. Splitting to a replica is a later decision that should be taken when
contention is measured, not before.

**It depends on the scout and nothing else.** A view is not a foreign key: it
cannot orphan a row and it cannot stop a schema being dropped, because dropping
one takes its dependent views with it, which is the correct outcome. So the
crm and commercial views are created conditionally on those schemas existing,
and a deployment running the survey alone gets the survey half and no errors.
That is the one place in this codebase where a module reaches at something it
does not require, and it is safe for a reason that a comment in the migration
sets out at length.

Nothing here is on the critical path of anything. If every view in this schema
were dropped, the tool would go on working and nobody would be unable to do
their job today.
"""

from __future__ import annotations

from ...kernel.modules import Module

MODULE = Module(
    name="analytics",
    title="Reports",
    what=(
        "The funnel with its cycle times, conversion by segment, canton and "
        "score band, what each stage of the campaign cost, the pipeline and "
        "what is outstanding. Views over everything else, written to by "
        "nothing."
    ),
    requires=("scout",),
    schemas=("analytics",),
)
