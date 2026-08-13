"""The module registry: how Arpenteur is composed rather than written.

Arpenteur is four things that happen to ship together. A scout that surveys a
register and ranks companies. A map. A client record. A sales cycle with
quotes and bills. Someone licensing the scout may have no use for the sales
side, and someone using the sales side must not be able to break the scout by
removing it.

So they are modules, and three rules keep them separable.

**Dependencies point one way.** `sales` needs `clients` needs `scout`; nothing
points back. Each module owns exactly one Postgres schema and its own numbered
migrations, run in dependency order, and there is no foreign key from an
upstream schema into a downstream one. Dropping `commercial` cannot orphan a
company because nothing in `core` ever referred to it.

**Modules meet at named slots, never by import.** A page declares a slot; a
module contributes to it. The company page has a `company.panels` slot, and the
clients module puts "promote to client" in it. The company page does not know
who filled it and does not import anyone. An unfilled slot renders nothing,
which is exactly what "the module is not installed" should look like.

**The rules are enforced by a test.** `tests/test_modules.py` walks the import
graph and fails if a module imports one it has not declared. Architecture that
is only written down decays; architecture with a failing test does not.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

# Slots the kernel itself defines. A module may declare its own; these are the
# ones the shared console furniture offers.
#
# The name is `object.place` so a reader can tell from the string alone where a
# contribution will appear, which matters when a page has four of them and no
# single file lists what ends up on it.
SLOTS = (
    "nav.primary",       # top navigation entries
    "company.panels",    # cards on one company's page in the survey
    "company.columns",   # extra columns in the survey list
    "client.panels",     # cards on one client's page
    "client.strip",      # the links strip under a client's name
    "settings.blocks",   # sections on the settings page
    "home.tiles",        # counters on the landing page
)


@dataclass(frozen=True)
class Nav:
    label: str
    href: str
    group: str = "main"
    order: int = 100


@dataclass(frozen=True)
class Contribution:
    """One thing a module puts into a slot.

    `render` is called with whatever the slot passes and returns HTML, or an
    empty string to contribute nothing for this particular row. Returning
    nothing is normal: the sales module has no panel for a company that has
    never been quoted.
    """

    slot: str
    render: Callable[..., str]
    order: int = 100
    module: str = ""


@dataclass(frozen=True)
class Module:
    name: str
    title: str
    # One line, shown where modules are listed. Written for someone deciding
    # whether they need it, not for someone who already knows.
    what: str
    # Module names this one needs. The registry refuses to enable a module
    # whose requirements are absent rather than failing later at a missing
    # table, because "no such relation crm.client" is a much worse error than
    # "sales requires clients".
    requires: tuple[str, ...] = ()
    # The Postgres schemas it owns, and nobody else writes to. Usually one.
    # The scout has two because what a company *is* and what we concluded about
    # it change at different rates and only one of them is safe to truncate.
    # Declared so "what does this module store" has a checkable answer and
    # removing it is a known list of DROP SCHEMA statements.
    schemas: tuple[str, ...] = ()
    # Its own numbered SQL, relative to the module package.
    migrations: str = "migrations"
    # Pipeline stages it adds, by name. Read by the console runner and the CLI.
    stages: dict[str, Any] = field(default_factory=dict)
    # Routes, mounted by the console. A module with no web surface omits it.
    router: Any = None
    nav: tuple[Nav, ...] = ()
    contributions: tuple[Contribution, ...] = ()
    # Whether it may be turned off. The scout cannot: it owns `company`, and
    # every other module is downstream of it.
    removable: bool = True


class Registry:
    """The enabled modules, in dependency order."""

    def __init__(self, modules: list[Module]) -> None:
        self.modules = modules
        self.by_name = {m.name: m for m in modules}
        self._slots: dict[str, list[Contribution]] = {}
        for module in modules:
            for item in module.contributions:
                self._slots.setdefault(item.slot, []).append(
                    Contribution(item.slot, item.render, item.order, module.name)
                )
        for items in self._slots.values():
            items.sort(key=lambda c: (c.order, c.module))

    def has(self, name: str) -> bool:
        return name in self.by_name

    def fill(self, slot: str, *args: Any, **kwargs: Any) -> str:
        """Render every contribution to a slot, in order.

        The joined string is the whole point: a caller writes `fill("x", row)`
        and never learns whether nought or three modules answered.
        """
        out = []
        for item in self._slots.get(slot, ()):
            try:
                html = item.render(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                # One module's broken panel must not take down a page owned by
                # another. The failure is shown where it happened rather than
                # swallowed, because a silently missing panel is worse than a
                # visible complaint.
                html = (
                    f'<div class="warn">The {item.module} module failed to '
                    f"render here: {type(exc).__name__}: {exc}</div>"
                )
            if html:
                out.append(html)
        return "".join(out)

    def navigation(self) -> list[Nav]:
        items = [nav for module in self.modules for nav in module.nav]
        return sorted(items, key=lambda n: (n.group != "main", n.order, n.label))

    def stages(self) -> dict[str, Any]:
        found: dict[str, Any] = {}
        for module in self.modules:
            found.update(module.stages)
        return found

    def migration_dirs(self) -> list[tuple[str, Path]]:
        """Where each module's SQL lives, in the order it must be applied."""
        out = []
        for module in self.modules:
            package = importlib.import_module(f"arpenteur.modules.{module.name}")
            folder = Path(package.__file__).parent / module.migrations
            if folder.is_dir():
                out.append((module.name, folder))
        return out


# Everything that ships. Enabling is a list in arpenteur.toml; this is the
# catalogue of what may be listed.
AVAILABLE = ("scout", "geo", "clients", "sales")

DEFAULT = ("scout", "geo", "clients", "sales")


def load(names: Iterable[str]) -> Registry:
    """Resolve module names into an ordered registry.

    Topological by `requires`, so migrations run and routers mount in an order
    where a module's dependencies already exist. A cycle or a missing
    requirement raises here, at startup, rather than at the first query.
    """
    wanted = [str(n).strip() for n in names if str(n).strip()]
    unknown = [n for n in wanted if n not in AVAILABLE]
    if unknown:
        raise RuntimeError(
            f"Unknown module(s): {', '.join(unknown)}. "
            f"Available: {', '.join(AVAILABLE)}."
        )
    if "scout" not in wanted:
        raise RuntimeError(
            "The scout module cannot be disabled: it owns the company table "
            "and every other module is downstream of it."
        )

    loaded: dict[str, Module] = {}
    for name in wanted:
        package = importlib.import_module(f"arpenteur.modules.{name}")
        module = getattr(package, "MODULE", None)
        if not isinstance(module, Module):
            raise RuntimeError(
                f"arpenteur.modules.{name} does not define a MODULE."
            )
        loaded[name] = module

    ordered: list[Module] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(name: str, trail: tuple[str, ...]) -> None:
        if name in seen:
            return
        if name in visiting:
            raise RuntimeError(
                "Module dependency cycle: " + " -> ".join(trail + (name,))
            )
        module = loaded.get(name)
        if module is None:
            raise RuntimeError(
                f"Module {trail[-1] if trail else '?'} requires {name!r}, "
                "which is not enabled. Add it to [modules] enabled in "
                "arpenteur.toml, or disable the module that needs it."
            )
        visiting.add(name)
        for need in module.requires:
            visit(need, trail + (name,))
        visiting.discard(name)
        seen.add(name)
        ordered.append(module)

    for name in wanted:
        visit(name, ())
    return Registry(ordered)
