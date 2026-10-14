"""Clients: the work that follows a yes.

Owns `crm`: the client record, projects, one interaction log read as a
timeline and as a calendar, the sourced dossier, versioned AI approaches, the
interview glossary, the documents a client sends, and programmes.

A programme is the one thing here that is not about a single client: one
endeavour across many companies, each member at its own stage. It lives in this
module rather than in the scout because it has to reach `core.company` and
`crm.client` in the same query, and only this side of the boundary may.

Depends on the scout because a client is a company, not a copy of one.
Promoting a prospect keeps the register entry, the officers, the research and
the notes already gathered, which is exactly what is worth having on the first
day of an engagement.

Removable. Dropping `crm` leaves every company, segment, score and note in the
scout untouched, because nothing in `core` or `intel` refers to it.
"""

from __future__ import annotations

from ...kernel.modules import Module, Nav

MODULE = Module(
    name="clients",
    title="Clients",
    what=(
        "Client records, projects, one timeline for correspondence, calls and "
        "meetings, a calendar, the sourced dossier, AI-suggested approaches "
        "and interview preparation."
    ),
    requires=("scout",),
    schemas=("crm",),
    nav=(
        Nav("Lists", "/audiences", group="outreach", order=210),
        Nav("Campaigns", "/campaigns", group="outreach", order=220),
        Nav("Mail", "/mail", group="outreach", order=240),
        Nav("Clients", "/clients", group="clients", order=310),
        Nav("Programmes", "/programmes", group="clients", order=320),
        Nav("Calendar", "/calendar", group="clients", order=330),
    ),
)
