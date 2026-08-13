"""Clients: the work that follows a yes.

Owns `crm`: the client record, projects, one interaction log read as a
timeline and as a calendar, the sourced dossier, versioned AI approaches, the
interview glossary and the documents a client sends.

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
        Nav("Clients", "/clients", group="work", order=10),
        Nav("Mail", "/mail", group="work", order=20),
        Nav("Calendar", "/calendar", group="work", order=30),
    ),
)
