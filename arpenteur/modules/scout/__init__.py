"""The scout: survey a register, and decide what is worth approaching.

This is the module everything else is downstream of. It owns `core`, which is
what a company is according to the register, and `intel`, which is what we have
observed and concluded about it.

It cannot be disabled. Every other module holds a foreign key into `core` and
none of them holds one the other way, which is the arrangement that lets the
sales module be dropped without orphaning anything here.
"""

from __future__ import annotations

from ...kernel.modules import Module, Nav

MODULE = Module(
    name="scout",
    title="Scout",
    what=(
        "Sweeps the commercial register, fetches company detail, mines the "
        "official notices for officers, places each company in a sector and "
        "against your segments, "
        "researches their websites and scores them against a campaign brief."
    ),
    requires=(),
    schemas=("core", "intel", "audit"),
    nav=(
        Nav("Survey", "/", group="survey", order=110),
        Nav("Board", "/board", group="survey", order=120),
        Nav("People", "/people", group="survey", order=130),
    ),
    removable=False,
)
