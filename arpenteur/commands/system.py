"""The commands of the application itself: the database, the modules, the console, the doctor, the manual, the settings, the workspace.

Commands of the system module, registered when it is enabled. Moved out of
`cli.py` verbatim; what they share is in `common.py`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import tomllib
from pathlib import Path
from .. import db, workspace
from . import common
from .common import (
    _database_url,
    _masked,
)


def cmd_init_db(args: argparse.Namespace) -> int:
    settings = common.load_settings()
    url = _database_url(settings, args.fixture)
    conn = db.open_db(url, settings.modules)
    print(f"{url} ready: " + ", ".join(
        f"{module} v{version}" for module, version in db.versions(conn).items()
    ))
    conn.close()
    return 0


def cmd_workspace(args: argparse.Namespace) -> int:
    """Carry an installation between machines, or inspect a file that claims to.

    `export` writes one file. `inspect` reads its manifest without unpacking
    it. `import` replaces this installation's data with the file's, and
    refuses a database that already holds anything unless told plainly.
    """
    settings = common.load_settings()

    if args.action == "inspect":
        if not args.path:
            print("error: inspect wants a path", file=sys.stderr)
            return 1
        manifest = workspace.inspect(Path(args.path[0]))
        print(f"{Path(args.path[0]).name}\n")
        print(manifest.describe())
        problems = workspace.check_compatible(manifest)
        print()
        if problems:
            print("this build cannot import it:")
            for line in problems:
                print(f"  - {line}")
            return 1
        print("compatible with this build")
        for table, n in sorted(manifest.counts.items()):
            print(f"  {table:28} {n:>9,}")
        return 0

    if args.action == "export":
        out = Path(args.out) if args.out else Path(
            f"arpenteur-{dt.date.today().isoformat()}.arpw"
        )
        conn = db.open_db(settings.database_url, settings.modules)
        try:
            report = workspace.export(
                settings, conn, out,
                with_secrets=args.with_secrets,
                with_attachments=not args.no_attachments,
                note=args.note,
            )
        finally:
            conn.close()
        size = report.bytes_written / 1048576
        print(f"{report.path}  {size:.1f} MB")
        assert report.manifest is not None
        print(report.manifest.describe())
        if not args.with_secrets:
            print(
                "\n.env was left out, so the Zefix and Infomaniak passwords are "
                "not in this file. Pass --with-secrets to include it."
            )
        print(
            "\nThis file holds personal data and, where a client has sent "
            "documents, material under NDA. Encrypt it before it travels:\n"
            f"  gpg -c --cipher-algo AES256 {report.path.name}"
        )
        return 0

    if not args.path:
        print("error: import wants a path", file=sys.stderr)
        return 1
    report = workspace.import_(
        settings, Path(args.path[0]),
        force=args.force,
        with_attachments=not args.no_attachments,
    )
    print(f"imported {report.path.name}")
    for name in report.migrated:
        print(f"  migrated {name}")
    print(f"  {len(report.verified)} table(s) verified against the manifest")
    total = sum(found for _expected, found in report.verified.values())
    print(f"  {total:,} rows")
    return 0


def cmd_console(args: argparse.Namespace) -> int:
    """Serve the console.

    Bound to localhost by default. The database holds contact details for
    thousands of companies and, where sole proprietorships are included, for
    identifiable people; there is no authentication here because there is no
    intent that it be reachable. Putting it on 0.0.0.0 is a deliberate act and
    wants a reverse proxy with auth in front of it.
    """
    import uvicorn

    from ..console import create_app

    settings = common.load_settings()
    print(f"territory: {settings.territory.describe()}")
    print(f"console:   http://{args.host}:{args.port}")
    if args.host not in ("127.0.0.1", "localhost"):
        print(
            "warning: this binds beyond localhost and the console has no "
            "authentication. Put a proxy in front of it.",
            file=sys.stderr,
        )
    uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_regions(args: argparse.Namespace) -> int:
    settings = common.load_settings()
    print(f"active: {settings.territory.describe()}")
    print(f"forms:  {', '.join(settings.territory.legal_forms) or 'all'}")
    print(f"admit new companies: {'yes' if settings.admit_new else 'no'}")
    print("\ndefined in arpenteur.toml:")
    for name in sorted(settings.regions):
        members = settings.regions[name]
        print(f"  {name:14} {len(members):>2} cantons  {', '.join(members)}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Everything a first run needs, checked and printed; 1 when something is missing.

    Live by default: the mailbox is signed in to and a session is opened with
    the mail server, sending nothing. `--quick` reads settings and files only.
    """
    from .. import health

    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        found = health.checks(settings, conn, live=not args.quick)
    finally:
        conn.close()
    print(health.report(found))
    return 1 if any(check.state == "missing" for check in found) else 0


def cmd_help(args: argparse.Namespace) -> int:
    """A page of the documentation, printed as it is written.

    The same files the console serves under Help, so there is one manual.
    """
    from .. import manual

    if args.list:
        for name, title in manual.pages():
            print(f"{name:<22} {title}")
        return 0
    file = manual.path_of(args.topic)
    if file is None:
        print(f"error: no page called {args.topic!r}. `arpenteur help --list` names them.",
              file=sys.stderr)
        return 1
    print(file.read_text(encoding="utf-8"), end="")
    return 0


def cmd_settings(args: argparse.Namespace) -> int:
    """The operator settings held in the database: show them, or import them.

    `import-toml` moves each configuration table the Settings page edits out
    of `arpenteur.toml` and into the overlay, once: a section the database
    already holds is kept unless `--force` says otherwise, so the file can be
    cleaned of its personal values afterwards without a value being lost or
    a later run of the command undoing a change made on the page.
    """
    from ..config import OVERLAY_SECTIONS, SECTION_KEYS

    settings = common.load_settings()
    conn = db.open_db(settings.database_url, settings.modules)
    try:
        held = db.settings_overlay(conn)
        if args.action == "show":
            shown = 0
            for section in OVERLAY_SECTIONS:
                value = held.get(section)
                if not isinstance(value, dict):
                    continue
                shown += 1
                print(f"[{section}]")
                for key, item in value.items():
                    print(f"  {key} = {_masked(key, item)}")
            if not shown:
                print("No section is held in the database; the file is the "
                      "whole configuration. `arpenteur settings import-toml` "
                      "moves its tables here.")
            return 0

        with (settings.root / "arpenteur.toml").open("rb") as handle:
            raw = tomllib.load(handle)
        for section in OVERLAY_SECTIONS:
            table = raw.get(section)
            if not isinstance(table, dict):
                print(f"{section}: not in the file")
                continue
            picked = {key: table[key] for key in SECTION_KEYS[section] if key in table}
            if not picked:
                print(f"{section}: nothing to import")
                continue
            if section in held and not args.force:
                print(f"{section}: kept, the database already holds it "
                      "(--force to overwrite)")
                continue
            db.save_setting(conn, section, picked, by="import-toml")
            print(f"{section}: imported {len(picked)} key(s)")
        conn.commit()
        return 0
    finally:
        conn.close()


def cmd_modules(args: argparse.Namespace) -> int:
    """What is installed, what it owns, and how far its schema has got.

    `drop` is the only destructive command in this tool, and it says so twice
    before doing anything: modules are removable by design, but removing one
    deletes its data and nothing here can bring that back.
    """
    from ..kernel import db as kernel
    from ..kernel import modules as registry

    settings = common.load_settings()
    if args.action == "list":
        conn = db.open_db(settings.database_url, settings.modules)
        versions = kernel.versions(conn)
        enabled = set(settings.modules)
        print(f"{'module':<10} {'state':<10} {'schema':<18} version  what")
        for name in registry.AVAILABLE:
            module = getattr(
                __import__(f"arpenteur.modules.{name}", fromlist=["MODULE"]), "MODULE"
            )
            state = "enabled" if name in enabled else "off"
            schemas = ", ".join(module.schemas)
            version = versions.get(name, "-")
            print(f"{name:<10} {state:<10} {schemas:<18} {str(version):>7}  {module.what[:60]}")
        print("\nEdit [modules] enabled in arpenteur.toml to change what loads.")
        conn.close()
        return 0

    module = registry.load(registry.AVAILABLE).by_name.get(args.name)
    if module is None:
        print(f"error: unknown module {args.name!r}", file=sys.stderr)
        return 1
    if not module.removable:
        print(
            f"error: {module.name} cannot be dropped. It owns the company "
            "table and every other module is downstream of it.",
            file=sys.stderr,
        )
        return 1
    if module.name in settings.modules:
        print(
            f"error: {module.name} is still enabled in arpenteur.toml. Remove it "
            "from [modules] enabled first, so the next command does not create "
            "its schema again.",
            file=sys.stderr,
        )
        return 1
    everything = registry.load(registry.AVAILABLE).by_name
    dependants = [
        name for name in settings.modules
        if name in everything and module.name in everything[name].requires
    ]
    if dependants:
        print(
            f"error: {', '.join(dependants)} require{'s' if len(dependants) == 1 else ''} "
            f"{module.name} and {'is' if len(dependants) == 1 else 'are'} still enabled. "
            "Drop or disable them first.",
            file=sys.stderr,
        )
        return 1
    conn = db.open_db(settings.database_url, settings.modules)
    held = {
        schema: conn.execute(
            "SELECT COUNT(*) AS n FROM information_schema.tables"
            " WHERE table_schema = %s", (schema,)
        ).fetchone()["n"]
        for schema in module.schemas
    }
    print(f"This deletes {module.name}: schema " + ", ".join(
        f"{s} ({n} tables)" for s, n in held.items()
    ))
    print("Every row in them goes. There is no undo without a backup.")
    if not args.yes:
        print("Re-run with --yes to proceed.", file=sys.stderr)
        return 1
    kernel.drop_module(conn, module.name, module.schemas)
    print(f"dropped {module.name}.")
    conn.close()
    return 0


def add_parsers(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("init-db", help="create or migrate the database")
    p.add_argument(
        "--fixture",
        action="store_true",
        help="use the separate database reserved for recorded fixture data",
    )
    p.set_defaults(func=cmd_init_db)


    p = sub.add_parser("modules", help="what is installed, and what it owns")
    msub = p.add_subparsers(dest="action", required=True)
    msub.add_parser("list", help="every module, its schema and its version")
    drop = msub.add_parser(
        "drop", help="delete a module's schema and all of its data"
    )
    drop.add_argument("name", help="the module to drop, e.g. sales; its schema and every row in it go")
    drop.add_argument(
        "--yes", action="store_true", help="required: this cannot be undone"
    )
    p.set_defaults(func=cmd_modules)


    p = sub.add_parser("console", help="serve the browser console on localhost")
    p.add_argument("--host", default="127.0.0.1",
                   help="the address to listen on; anything but 127.0.0.1 exposes a console "
                        "with no sign-in, so put a proxy with one in front")
    p.add_argument("--port", type=int, default=8000, help="the port to listen on")
    p.set_defaults(func=cmd_console)


    sub.add_parser(
        "regions", help="territories defined in arpenteur.toml, and the active one"
    ).set_defaults(func=cmd_regions)


    p = sub.add_parser(
        "doctor", help="check everything a first run needs, and say what is missing"
    )
    p.add_argument(
        "--quick", action="store_true",
        help="read settings and files only; do not sign in to the mailbox or "
             "open a session with the mail server",
    )
    p.set_defaults(func=cmd_doctor)


    p = sub.add_parser("help", help="read a page of the manual in the terminal")
    p.add_argument("topic", nargs="?", default="index",
                   help="the page, e.g. guide/companies or install; the index when left out")
    p.add_argument("--list", action="store_true", help="name every page and stop")
    p.set_defaults(func=cmd_help)


    p = sub.add_parser(
        "settings", help="the operator settings held in the database"
    )
    p.add_argument(
        "action", choices=("import-toml", "show"),
        help="move the configuration tables from arpenteur.toml into the "
             "database, once, or print what it holds",
    )
    p.add_argument(
        "--force", action="store_true",
        help="overwrite a section the database already holds",
    )
    p.set_defaults(func=cmd_settings)


    p = sub.add_parser(
        "workspace",
        help="carry this installation's data to another machine, as one file",
    )
    p.add_argument(
        "action", choices=("export", "inspect", "import"),
        help="write a workspace file, read its manifest, or restore from it",
    )
    p.add_argument("path", nargs="*", default=[], help="the file, for inspect and import")
    p.add_argument("--out", default="", help="where export writes; defaults to the date")
    p.add_argument(
        "--with-secrets", action="store_true",
        help="include .env, which holds the Zefix and Infomaniak passwords",
    )
    p.add_argument(
        "--no-attachments", action="store_true",
        help="leave out the documents clients have sent",
    )
    p.add_argument("--note", default="", help="a line recorded in the manifest")
    p.add_argument(
        "--force", action="store_true",
        help="let import replace a database that already holds data",
    )
    p.set_defaults(func=cmd_workspace)

