"""The module boundaries, enforced rather than documented.

Arpenteur is composed of modules so that the sales side can be removed without
breaking the scout, and so that both integrate without either importing the
other. Architecture that is only written down decays: someone adds one import
from the company page into the quote store, it works, and six months later the
sales module cannot be removed and nobody knows why.

So the rules are tests.

**The import graph.** No module may import a module it has not declared in
`requires`, and the kernel may import no module at all. Checked by walking the
AST rather than by importing, so a violation is caught even in a branch that
never runs.

**The foreign key graph.** Every cross-schema reference must point upstream:
`commercial` to `crm` to `core`, never the reverse. This is the property that
makes dropping a schema safe, and Postgres itself is the authority on it.

A note on how not to check the second one. `DROP SCHEMA x RESTRICT` looks like
the natural probe and is not: RESTRICT refuses whenever anything depends on the
schema's contents, including the schema's own indexes and constraints, so it
refuses for every non-empty schema and proves nothing either way. `CASCADE` is
the opposite failure: it means "drop the dependents too" and therefore always
succeeds. The catalogue is the only honest answer.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from arpenteur.kernel import modules

ROOT = Path(__file__).resolve().parent.parent / "arpenteur"
MODULES_DIR = ROOT / "modules"


def module_names() -> list[str]:
    return sorted(p.name for p in MODULES_DIR.iterdir() if (p / "__init__.py").is_file())


def imports_in(path: Path) -> set[str]:
    """Which sibling modules a file reaches for, however it spells it.

    Relative imports are resolved by counting dots against the file's own
    position, because `from ...modules.sales import x` and
    `from arpenteur.modules.sales import x` are the same coupling and only one
    of them is greppable.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    parts = path.relative_to(ROOT.parent).parts  # arpenteur/modules/<name>/...
    found: set[str] = set()

    def note(dotted: str) -> None:
        bits = dotted.split(".")
        for index, bit in enumerate(bits):
            if bit == "modules" and index + 1 < len(bits):
                found.add(bits[index + 1])

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                note(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # arpenteur/modules/sales/console.py at level 3 means arpenteur
                base = list(parts[: len(parts) - node.level])
                note(".".join(base + ([node.module] if node.module else [])))
            elif node.module:
                note(node.module)
    return found


# --- the import graph ---------------------------------------------------------


def declared(name: str) -> modules.Module:
    import importlib

    return getattr(importlib.import_module(f"arpenteur.modules.{name}"), "MODULE")


def _closure(name: str, seen: set[str] | None = None) -> set[str]:
    """Everything a module may reach: its requirements, and theirs."""
    seen = seen if seen is not None else set()
    for need in declared(name).requires:
        if need not in seen:
            seen.add(need)
            _closure(need, seen)
    return seen


@pytest.mark.parametrize("name", module_names())
def test_a_module_imports_only_what_it_declares(name: str) -> None:
    allowed = {name} | _closure(name)
    for path in sorted((MODULES_DIR / name).rglob("*.py")):
        for other in imports_in(path):
            assert other in allowed, (
                f"{path.relative_to(ROOT.parent)} imports the {other!r} module, "
                f"which {name!r} does not declare in "
                f"requires={declared(name).requires}. Either declare it, or "
                "meet through a slot instead."
            )


def test_the_kernel_imports_no_module() -> None:
    """The kernel is what modules are composed by. A kernel that knows the name
    of a module is a kernel that cannot run without it."""
    for path in sorted((ROOT / "kernel").rglob("*.py")):
        assert not imports_in(path), (
            f"{path.name} imports a module. The kernel composes modules and "
            "must not depend on any of them."
        )


def test_no_module_imports_the_console() -> None:
    """The seam that now carries the navigation, guarded from the other side.

    A module declares where it appears in the header, and the console reads
    that declaration. The temptation the moment a module wants a page is to
    import the console and render one, and the direction has to stay: the
    console knows the modules, no module knows the console. Otherwise a
    deployment that ships the sales module without the web front end will not
    import.

    Text rather than the AST walk above, because `imports_in` deliberately only
    reports sibling modules and would say nothing at all about this.
    """
    for path in sorted(MODULES_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for spelling in ("from ...console", "from arpenteur.console",
                         "import arpenteur.console"):
            assert spelling not in text, (
                f"{path.relative_to(ROOT.parent)} imports the console. A module "
                "contributes to a slot and declares its navigation; it does not "
                "render pages."
            )


def test_every_module_navigation_entry_is_complete() -> None:
    """A header entry with no label or no href is a gap nobody sees until the
    page is open."""
    for name in module_names():
        for item in declared(name).nav:
            assert item.label.strip(), f"{name} declares a nameless nav entry"
            assert item.href.startswith("/"), (
                f"{name} declares nav to {item.href!r}, which is not a path"
            )


def test_two_modules_cannot_claim_the_same_place_in_the_header() -> None:
    seen: dict[str, str] = {}
    for name in module_names():
        for item in declared(name).nav:
            assert item.href not in seen, (
                f"{name} and {seen[item.href]} both put {item.href} in the header"
            )
            seen[item.href] = name


def test_the_scout_imports_neither_clients_nor_sales() -> None:
    """The direction that matters commercially: someone licensing the survey
    alone must be able to take it."""
    for path in sorted((MODULES_DIR / "scout").rglob("*.py")):
        reached = imports_in(path)
        assert not (reached & {"clients", "sales"}), (
            f"{path.name} reaches into {reached & {'clients', 'sales'}}"
        )


# --- the registry -------------------------------------------------------------


def test_modules_resolve_in_dependency_order() -> None:
    order = [m.name for m in modules.load(("sales", "clients", "scout")).modules]
    assert order.index("scout") < order.index("clients") < order.index("sales")


def test_a_missing_requirement_is_refused_at_startup() -> None:
    """Better than "relation crm.client does not exist" at the first query."""
    with pytest.raises(RuntimeError, match="requires 'clients'"):
        modules.load(("scout", "sales"))


def test_an_unknown_module_names_what_is_available() -> None:
    with pytest.raises(RuntimeError, match="Unknown module"):
        modules.load(("scout", "invoicing"))


def test_the_scout_cannot_be_disabled() -> None:
    with pytest.raises(RuntimeError, match="cannot be disabled"):
        modules.load(("clients",))


def test_every_module_owns_at_least_one_schema() -> None:
    for module in modules.load(modules.AVAILABLE).modules:
        assert module.schemas, f"{module.name} declares no schema"
        assert module.what and len(module.what) > 40, (
            f"{module.name} needs a line explaining what it is for, written "
            "for someone deciding whether they need it"
        )


def test_no_two_modules_claim_the_same_schema() -> None:
    owner: dict[str, str] = {}
    for module in modules.load(modules.AVAILABLE).modules:
        for schema in module.schemas:
            assert schema not in owner, (
                f"{module.name} and {owner[schema]} both claim {schema!r}; "
                "removing either would take the other's data"
            )
            owner[schema] = module.name


# --- the slots ----------------------------------------------------------------


def test_an_unfilled_slot_renders_nothing() -> None:
    """Which is exactly what "that module is not installed" should look like."""
    assert modules.load(("scout",)).fill("client.panels", 1) == ""


def test_contributions_render_in_order() -> None:
    registry = modules.Registry([
        modules.Module(
            name="a", title="A", what="x" * 50, schemas=("a",),
            contributions=(
                modules.Contribution("s", lambda: "<second>", order=20),
                modules.Contribution("s", lambda: "<first>", order=10),
            ),
        )
    ])
    assert registry.fill("s") == "<first><second>"


def test_one_module_failing_does_not_take_down_the_page() -> None:
    """A page is owned by whoever declared the slot. Another module's broken
    panel must be visible where it broke, not fatal and not silent."""
    def boom() -> str:
        raise ValueError("no rate card")

    registry = modules.Registry([
        modules.Module(
            name="sales", title="S", what="x" * 50, schemas=("commercial",),
            contributions=(modules.Contribution("s", boom),),
        ),
        modules.Module(
            name="clients", title="C", what="x" * 50, schemas=("crm",),
            contributions=(modules.Contribution("s", lambda: "<intact>"),),
        ),
    ])
    out = registry.fill("s")
    assert "<intact>" in out
    assert "sales module failed" in out and "no rate card" in out


# --- the foreign key graph ----------------------------------------------------


@pytest.mark.pg
def test_every_cross_schema_reference_points_upstream(pg) -> None:
    """The property that makes a module removable, asked of the catalogue.

    `commercial` may point at `crm`, `crm` at `core`, `geo` at `core`. Nothing
    points the other way, so dropping a downstream schema cannot orphan an
    upstream row. One import from a quote table into `core.company` would be
    fine; one from `core` into `commercial` would end the guarantee, and this
    is where that gets caught.
    """
    rank = {"core": 0, "intel": 1, "geo": 1, "crm": 1, "commercial": 2}
    rows = pg.execute("""
        SELECT DISTINCT nc.nspname AS child, np.nspname AS parent
          FROM pg_constraint c
          JOIN pg_class tc ON tc.oid = c.conrelid
          JOIN pg_class tp ON tp.oid = c.confrelid
          JOIN pg_namespace nc ON nc.oid = tc.relnamespace
          JOIN pg_namespace np ON np.oid = tp.relnamespace
         WHERE c.contype = 'f' AND nc.nspname <> np.nspname
    """).fetchall()
    assert rows, "no cross-schema references found; is the database migrated?"
    for row in rows:
        child, parent = row["child"], row["parent"]
        assert rank[child] > rank[parent], (
            f"{child} references {parent}, which is downstream of it. That "
            f"makes {parent} unremovable and breaks the module boundary."
        )


def test_a_contribution_may_decline_by_returning_nothing() -> None:
    """The sales module has no panel for a client who has never been quoted,
    and that is not an error."""
    registry = modules.Registry([
        modules.Module(
            name="sales", title="S", what="x" * 50, schemas=("commercial",),
            contributions=(modules.Contribution("s", lambda: ""),),
        )
    ])
    assert registry.fill("s") == ""
