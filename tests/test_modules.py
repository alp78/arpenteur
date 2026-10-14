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


# The scout's own layering, which removability says nothing about because its
# schemas are dropped together: the register's facts in `core` never point at
# what was judged about them in `intel`, nor at the audit trail.
SCOUT_LAYER = {"core": 0, "intel": 1, "audit": 1}


def points_upstream(child: str, parent: str) -> bool:
    """Whether a foreign key from `child` to `parent` keeps every module removable.

    Derived from module ownership rather than from a ranking written by hand.
    The hand ranking put `crm` and `intel` side by side, so the campaign tables
    could not reference `intel.draft` although `clients` requires `scout`:
    dropping `crm` orphans nothing in `intel`, which is the whole guarantee.
    """
    owner = {
        schema: module.name
        for module in modules.load(modules.AVAILABLE).modules
        for schema in module.schemas
    }
    if owner[child] == owner[parent]:
        return SCOUT_LAYER.get(child, 1) > SCOUT_LAYER.get(parent, 0)
    return owner[parent] in _closure(owner[child])


def test_the_reference_rule_follows_module_ownership() -> None:
    assert points_upstream("crm", "core")
    assert points_upstream("crm", "intel"), "clients requires scout"
    assert points_upstream("commercial", "crm")
    assert points_upstream("commercial", "core"), "through clients"
    assert points_upstream("intel", "core")
    assert not points_upstream("intel", "crm"), "scout may never reach clients"
    assert not points_upstream("core", "crm")
    assert not points_upstream("crm", "commercial")
    assert not points_upstream("geo", "crm"), "geo does not require clients"
    assert not points_upstream("core", "intel"), "facts never point at judgements"


@pytest.mark.pg
def test_every_cross_schema_reference_points_upstream(pg) -> None:
    """The property that makes a module removable, asked of the catalogue.

    `commercial` may point at `crm`, `crm` at `core` and `intel`, `geo` at
    `core`. Nothing points the other way, so dropping a downstream schema
    cannot orphan an upstream row. One reference from a quote table into
    `core.company` is fine; one from `core` into `commercial` would end the
    guarantee, and this is where that gets caught.
    """
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
        assert points_upstream(child, parent), (
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


# --- the console and the database follow the enabled list ----------------------
#
# A module is off when it is not in `[modules] enabled`: its routes are not
# mounted, its header entries are gone, its panels are not drawn on other
# modules' pages, and its schema is not created. Each of those used to be a
# promise in prose; these make them tests.

import re  # noqa: E402

from arpenteur import db  # noqa: E402
from arpenteur.config import Territory  # noqa: E402

CONSOLE_ROUTES = ROOT / "console" / "routes"


def _store_imports(path: Path) -> set[str]:
    """The module packages a route file reaches into, by name."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and "modules." in (node.module or ""):
            found.add(node.module.split("modules.")[1].split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level and node.module \
                and node.module.startswith("modules"):
            found.add(node.module.split(".")[1])
    return found


@pytest.mark.parametrize("name", modules.AVAILABLE)
def test_a_route_file_reaches_only_its_own_module_and_what_it_requires(name: str) -> None:
    """The console's route file for a module may import that module's store
    and the stores of the modules it requires; anything else is a panel for a
    slot, rendered by the module that owns the data."""
    path = CONSOLE_ROUTES / f"{name}.py"
    if not path.is_file():
        pytest.skip(f"{name} has no routes")
    allowed = {name}
    pending = list(modules.load(modules.AVAILABLE).by_name[name].requires)
    while pending:
        req = pending.pop()
        if req not in allowed:
            allowed.add(req)
            pending.extend(modules.load(modules.AVAILABLE).by_name[req].requires)
    reached = _store_imports(path)
    assert reached <= allowed, f"{name} routes reach into {sorted(reached - allowed)}"


def _console(build_settings, tmp_path: Path, enabled: tuple[str, ...]):
    from fastapi.testclient import TestClient

    from arpenteur.console import create_app

    settings = build_settings(
        tmp_path, modules=enabled,
        territory=Territory(cantons=("VD",), legal_forms=("0107",), name="t"),
    )
    return TestClient(create_app(settings))


def test_only_the_enabled_modules_mount_their_routes(db_path, build_settings, tmp_path) -> None:
    from arpenteur.console.routes import every_route

    client = _console(build_settings, tmp_path, ("scout", "geo", "clients"))
    paths = {getattr(r, "path", "") for r in every_route(client.app)}
    assert not [p for p in paths if p.startswith(("/quote", "/invoice", "/reports"))], paths
    assert "/clients" in paths and "/map" in paths and "/companies" in paths
    assert client.get("/quotes").status_code == 404
    header = client.get("/").text.split("<header>")[1].split("</header>")[0]
    assert "Quotes" not in header and "Reports" not in header


def test_a_company_page_without_the_clients_module_offers_no_promotion(
    db_path, build_settings, tmp_path,
) -> None:
    conn = db.open_db(db_path)
    db.upsert_company(conn, {"uid": "CHE1", "name": "Atelier Sàrl", "canton": "VD",
                             "method": "zefix"})
    conn.commit()
    conn.close()
    alone = _console(build_settings, tmp_path, ("scout",))
    page = alone.get("/company/1").text
    assert "Make this a client" not in page and "Mail recognised by" not in page
    together = _console(build_settings, tmp_path / "b", ("scout", "clients"))
    page = together.get("/company/1").text
    assert "Make this a client" in page


def test_the_client_page_draws_the_panels_of_the_modules_that_are_on(
    db_path, build_settings, tmp_path,
) -> None:
    conn = db.open_db(db_path)
    company_id = db.upsert_company(conn, {"uid": "CHE1", "name": "Atelier Sàrl",
                                          "canton": "VD", "method": "zefix"})
    client_id = db.promote_to_client(conn, company_id)
    conn.commit()
    conn.close()
    without = _console(build_settings, tmp_path, ("scout", "clients"))
    page = without.get(f"/client/{client_id}").text
    assert "Start a quote" not in page and "Add a site" not in page
    with_all = _console(build_settings, tmp_path / "b", ("scout", "geo", "clients", "sales"))
    page = with_all.get(f"/client/{client_id}").text
    assert "Add a site" in page and "Start a quote" in page
    assert page.index("Add a site") < page.index("Start a quote")


def test_open_db_migrates_the_enabled_modules_only(pg_url: str) -> None:
    from arpenteur.kernel import db as kernel

    url = kernel.ensure_database(kernel.sibling(pg_url, "scout_only"))
    conn = db.open_db(url, ("scout",))
    try:
        held = {row["module"] for row in conn.execute(
            "SELECT DISTINCT module FROM kernel.migration"
        ).fetchall()}
        schemas = {row["schema_name"] for row in conn.execute(
            "SELECT schema_name FROM information_schema.schemata"
        ).fetchall()}
    finally:
        conn.close()
    assert held == {"scout"}
    assert "crm" not in schemas and "commercial" not in schemas


def test_a_module_that_requires_a_missing_one_is_refused_at_open(pg_url: str) -> None:
    from arpenteur.kernel import db as kernel

    url = kernel.ensure_database(kernel.sibling(pg_url, "scout_only"))
    with pytest.raises(RuntimeError, match="requires"):
        db.open_db(url, ("scout", "sales"))


def _toml(tmp_path: Path, enabled: str) -> Path:
    (tmp_path / "arpenteur.toml").write_text(
        f'[modules]\nenabled = [{enabled}]\n\n[survey]\ncantons = ["VD"]\n'
        'legal_forms = ["0107"]\n\n[zefix]\ncontact = "tests"\n',
        encoding="utf-8",
    )
    return tmp_path


def test_drop_refuses_a_module_that_is_still_enabled(db_path, tmp_path, monkeypatch, capsys) -> None:
    from arpenteur.cli import main

    monkeypatch.setenv("ARPENTEUR_HOME", str(_toml(tmp_path, '"scout", "geo", "clients", "sales"')))
    assert main(["modules", "drop", "sales", "--yes"]) == 1
    err = capsys.readouterr().err
    assert "still enabled" in err and "[modules] enabled" in err


def test_drop_refuses_a_module_another_enabled_one_requires(db_path, tmp_path, monkeypatch, capsys) -> None:
    from arpenteur.cli import main

    monkeypatch.setenv("ARPENTEUR_HOME", str(_toml(tmp_path, '"scout", "geo", "sales"')))
    assert main(["modules", "drop", "clients", "--yes"]) == 1
    assert re.search(r"sales requires clients", capsys.readouterr().err)
