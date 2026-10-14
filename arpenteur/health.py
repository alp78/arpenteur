"""What the application needs before it is usable, as a list a page can draw.

Each check answers one question with one of three states: `ok`, `missing` or
`warn`, a sentence saying what was found, and the page where it is fixed. The
landing page draws the list; `arpenteur doctor` prints it. One function feeds
both, so the console and the terminal never disagree about what is missing.

This stage ships the checks that need no credential: the database, the
modules, the territory. The ones that reach out, to the register, the model,
the mailbox and the mail server, follow in the settings stage, where the
values they test move into the application.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .kernel import db as kernel_db
from .kernel import modules as module_registry
from .kernel.db import Connection

STATES = ("ok", "warn", "missing")


@dataclass(frozen=True)
class Check:
    name: str
    state: str
    detail: str
    href: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok"


def checks(settings: Any, conn: Connection) -> list[Check]:
    """The checklist, in the order a person sets things up."""
    out: list[Check] = []

    enabled = [
        str(name) for name in (getattr(settings, "modules", None) or module_registry.DEFAULT)
    ]
    versions = kernel_db.versions(conn)
    behind = [name for name in enabled if name not in versions]
    out.append(Check(
        "Database", "ok",
        f"Reached, with {len(versions)} module schema(s) in place.", "/runs",
    ))
    out.append(Check(
        "Modules",
        "missing" if behind else "ok",
        (f"{len(behind)} enabled module(s) have no schema yet: "
         f"{', '.join(behind)}. Open any page or run any command to migrate.")
        if behind else f"{len(enabled)} enabled: {', '.join(enabled)}.",
        "/settings",
    ))

    territory = getattr(settings, "territory", None)
    cantons = tuple(getattr(territory, "cantons", ()) or ())
    forms = tuple(getattr(territory, "legal_forms", ()) or ())
    if cantons and forms:
        out.append(Check(
            "Territory", "ok",
            f"{', '.join(cantons)}, {len(forms)} legal form(s).", "/settings",
        ))
    elif cantons:
        out.append(Check(
            "Territory", "missing",
            "Cantons are set but no legal form is: a sweep would find nothing.",
            "/settings",
        ))
    else:
        out.append(Check(
            "Territory", "missing",
            "No canton is set: a sweep would run over the whole country.",
            "/settings",
        ))
    return out


def problems(found: list[Check]) -> list[Check]:
    return [check for check in found if not check.ok]
