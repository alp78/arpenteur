"""One commands file per module, registered when the module is enabled.

A file here is named after its module and exposes `add_parsers(sub)`, which
registers that module's subcommands and their handlers. `system.py` holds the
commands of the application itself and is always registered. `mount` finds
each enabled module's file by name, so a module package never imports the
command line: the command line looks for the module's commands.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
from collections.abc import Iterable

from ..kernel import modules


def mount(sub: argparse._SubParsersAction, enabled: Iterable[str] | None = None) -> list[str]:
    """Register the system commands, then each enabled module's; say which."""
    from . import system

    system.add_parsers(sub)
    found = ["system"]
    try:
        registry = modules.load(tuple(enabled or ()) or modules.DEFAULT)
    except RuntimeError:
        # A list that does not resolve, such as sales without clients. Every
        # command is offered, and the one that runs reports the fault in a
        # sentence; refusing to build the parser would hide `modules drop`,
        # which is the command that repairs it.
        registry = modules.load(modules.DEFAULT)
    for module in registry.modules:
        name = f"{__name__}.{module.name}"
        if importlib.util.find_spec(name) is None:
            continue
        importlib.import_module(name).add_parsers(sub)
        found.append(module.name)
    return found
