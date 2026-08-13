"""Sales: what is on offer, what was quoted, what was billed.

Owns `commercial`: the service catalogue, versioned rate cards, quotes with
immutable sent versions, invoices with Swiss TVA and QR-bill fields, and time
entries.

This is the module the modular design exists for. Every foreign key here points
at `crm` or `core` and none points back, so dropping `commercial` leaves the
survey, the clients, the dossiers and the timelines exactly as they were. That
is not a promise in a docstring: `tests/test_modules.py` reads the constraint
catalogue and fails if any reference ever runs the other way.

The seam in the other direction is the slot registry. The client page has a
`client.panels` slot; this module fills it with quotes and invoices. The client
page does not import this module and does not know whether anyone answered.
"""

from __future__ import annotations

from ...kernel.modules import Module

MODULE = Module(
    name="sales",
    title="Sales",
    what=(
        "A service catalogue and versioned rate card, quotes whose sent "
        "versions are never edited, invoices with Swiss TVA and QR-bill "
        "references, and time entries that feed day-rate lines."
    ),
    requires=("clients",),
    schemas=("commercial",),
)
