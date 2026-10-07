"""An installation's whole working set, as one file.

    arpenteur workspace export --out ~/arpenteur-2026-10-07.arpw
    arpenteur workspace inspect ~/arpenteur-2026-10-07.arpw
    arpenteur workspace import ~/arpenteur-2026-10-07.arpw

A dump restores data. It does not say which schema that data belongs to, which
modules were running when it was written, or whether the code about to read it
is the code that wrote it. Those three silences are how a restore succeeds and
the application is still wrong, so the file carries a manifest and the import
reads it before touching the database.

The archive is a zip rather than a tar because `inspect` has to read the
manifest without unpacking sixty-odd megabytes, and random access is what zip
has and tar does not. The dump inside is already compressed by `pg_dump`, so
it goes in stored rather than deflated a second time.

The dump is logical, not a copy of the data directory. A PostgreSQL data
directory is not portable across CPU architectures, and the first real use of
this was carrying a survey from Windows on x86 to a Mac on arm64.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import attachments as att
from .kernel import db as kernel_db
from .kernel import modules as module_registry

MANIFEST = "manifest.json"
DUMP = "database.dump"
CONFIG = "arpenteur.toml"
SECRETS = ".env"
ATTACHMENTS = "attachments/"

# Bumped only when the archive's own shape changes, which is a different
# question from which migrations the data inside it has seen.
FORMAT = 1

# What gets counted on the way out and checked on the way back in. Not every
# table: the ones whose loss would be noticed, named explicitly so that a new
# table does not silently join or leave the check.
COUNTED = (
    "core.company",
    "core.contact",
    "core.person",
    "core.publication",
    "intel.site",
    "intel.segment",
    "intel.address_hunt",
    "intel.note",
    "crm.client",
    "crm.project",
    "crm.attachment",
    "crm.note",
    "crm.email_message",
    "commercial.invoice",
    "commercial.quote",
    "commercial.time_entry",
    "geo.establishment",
)


class WorkspaceError(RuntimeError):
    """Something about the file or the target refuses the operation."""


@dataclass(frozen=True)
class Manifest:
    """What the archive says about itself."""

    format: int
    created_at: str
    app_version: str
    server_version: str
    database: str
    collate: str
    ctype: str
    modules: tuple[str, ...]
    migrations: dict[str, dict[str, str]]
    counts: dict[str, int]
    attachments: int
    attachment_bytes: int
    has_secrets: bool
    extensions: tuple[str, ...] = ()
    note: str = ""

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Manifest:
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(raw) - known
        if unknown:
            # A field this build does not know means the file was written by a
            # later one. Say which, rather than dropping it and carrying on.
            raise WorkspaceError(
                "this workspace was written by a newer Arpenteur: its manifest "
                f"carries {', '.join(sorted(unknown))}, which this build does "
                "not understand. Update Arpenteur, then import it."
            )
        return cls(
            format=int(raw["format"]),
            created_at=str(raw["created_at"]),
            app_version=str(raw.get("app_version", "")),
            server_version=str(raw.get("server_version", "")),
            database=str(raw.get("database", "")),
            collate=str(raw.get("collate", "")),
            ctype=str(raw.get("ctype", "")),
            modules=tuple(raw.get("modules") or ()),
            migrations={k: dict(v) for k, v in (raw.get("migrations") or {}).items()},
            counts={k: int(v) for k, v in (raw.get("counts") or {}).items()},
            attachments=int(raw.get("attachments", 0)),
            attachment_bytes=int(raw.get("attachment_bytes", 0)),
            has_secrets=bool(raw.get("has_secrets", False)),
            extensions=tuple(raw.get("extensions") or ()),
            note=str(raw.get("note", "")),
        )

    def describe(self) -> str:
        made = self.created_at[:19].replace("T", " ")
        rows = sum(self.counts.values())
        return (
            f"written {made} by Arpenteur {self.app_version or '?'} on "
            f"PostgreSQL {self.server_version or '?'}\n"
            f"modules: {', '.join(self.modules) or 'none recorded'}\n"
            f"{rows:,} rows in {len(self.counts)} counted tables, "
            f"{self.attachments:,} attachment(s) "
            f"({self.attachment_bytes / 1048576:.1f} MB)\n"
            f"secrets included: {'yes' if self.has_secrets else 'no'}"
        )


@dataclass
class Report:
    """What an export or an import actually did."""

    path: Path
    manifest: Manifest | None = None
    bytes_written: int = 0
    migrated: list[str] = field(default_factory=list)
    verified: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def mismatches(self) -> dict[str, tuple[int, int]]:
        return {k: v for k, v in self.verified.items() if v[0] != v[1]}


# --- finding the dump tools ---------------------------------------------------


@dataclass(frozen=True)
class Tool:
    """A dump binary and the address the database has from where it runs."""

    command: list[str]
    in_container: bool

    def dsn(self, url: str) -> str:
        """The connection string as the tool will see it.

        Borrowing the binary from the container moves the caller's idea of
        where the server is. The host reaches it on the published port, which
        for this project is 5433; from inside the container that port belongs
        to nothing and the server is on 5432. Passing the host's URL to a tool
        running in the container is a connection refused that reads as though
        the database were down.
        """
        if not self.in_container:
            return url
        head, _, tail = url.rpartition("@")
        if not head:
            return url
        _hostport, _, database = tail.partition("/")
        return f"{head}@127.0.0.1:5432/{database}"


def _tool(name: str, *, container: str = "arpenteur-db") -> Tool:
    """`pg_dump` or `pg_restore`, wherever this machine keeps it.

    Looked for on the PATH first and in the running container second. A
    desktop build bundles its own server and therefore its own binaries, so
    the PATH branch is the one with a future; the container branch is what
    makes this work today on a machine that only ever had Docker.
    """
    override = os.environ.get(f"ARPENTEUR_{name.upper()}")
    if override:
        return Tool([override], False)
    found = shutil.which(name)
    if found:
        return Tool([found], False)
    docker = shutil.which("docker")
    if docker:
        probe = subprocess.run(  # noqa: S603
            [docker, "exec", container, name, "--version"],
            capture_output=True, text=True, check=False,
        )
        if probe.returncode == 0:
            return Tool([docker, "exec", "-i", container, name], True)
    raise WorkspaceError(
        f"{name} was not found on the PATH, and no {container} container is "
        f"running to borrow it from. Install the PostgreSQL client tools, or "
        f"set ARPENTEUR_{name.upper()} to the binary."
    )


def _run(command: list[str], *, stdin: Any = None, stdout: Any = None) -> str:
    done = subprocess.run(  # noqa: S603
        command, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE,
        text=False, check=False,
    )
    detail = (done.stderr or b"").decode("utf-8", "replace").strip()
    if done.returncode != 0:
        raise WorkspaceError(f"{command[0]} exited {done.returncode}: {detail}")
    return detail


# --- reading the database -----------------------------------------------------


def counts(conn: kernel_db.Connection) -> dict[str, int]:
    """Rows per counted table, skipping the ones this deployment lacks.

    A module that is not enabled has no schema, and asking anyway would turn a
    legitimate configuration into an error. The same skip hides a name spelt
    wrongly, which is how `crm.document` sat in this list reassuringly for as
    long as it took to read the catalogue; `test_every_counted_table_exists`
    is what stops the next one.
    """
    out: dict[str, int] = {}
    for qualified in COUNTED:
        schema, _, table = qualified.partition(".")
        exists = conn.execute(
            "SELECT 1 FROM information_schema.tables"
            " WHERE table_schema = %s AND table_name = %s",
            (schema, table),
        ).fetchone()
        if not exists:
            continue
        out[qualified] = conn.execute(
            f'SELECT count(*) AS n FROM "{schema}"."{table}"'
        ).fetchone()["n"]
    return out


def migrations(conn: kernel_db.Connection) -> dict[str, dict[str, str]]:
    """Every applied migration, as module -> version -> digest.

    The version is a string because it becomes a JSON key, and JSON has no
    integer keys. Converting it back on the way in is one `int()`; pretending
    the round trip preserves the type is a bug that waits for a comparison.
    """
    rows = conn.execute(
        "SELECT module, version, digest FROM kernel.migration"
        " ORDER BY module, version"
    ).fetchall()
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        out.setdefault(row["module"], {})[str(row["version"])] = row["digest"]
    return out


def _database_facts(conn: kernel_db.Connection) -> dict[str, str]:
    row = conn.execute(
        "SELECT current_database() AS name,"
        "       current_setting('server_version') AS version,"
        "       (SELECT datcollate FROM pg_database"
        "         WHERE datname = current_database()) AS collate,"
        "       (SELECT datctype FROM pg_database"
        "         WHERE datname = current_database()) AS ctype"
    ).fetchone()
    return {
        "name": row["name"], "version": row["version"],
        "collate": row["collate"] or "", "ctype": row["ctype"] or "",
    }


def _extensions(conn: kernel_db.Connection) -> tuple[str, ...]:
    rows = conn.execute(
        "SELECT extname FROM pg_extension WHERE extname <> 'plpgsql'"
        " ORDER BY extname"
    ).fetchall()
    return tuple(row["extname"] for row in rows)


def _app_version() -> str:
    try:
        from importlib.metadata import version

        return version("arpenteur")
    except Exception:  # noqa: BLE001
        return ""


# --- export -------------------------------------------------------------------


def export(
    settings: Any,
    conn: kernel_db.Connection,
    out: Path,
    *,
    with_secrets: bool = False,
    with_attachments: bool = True,
    note: str = "",
) -> Report:
    """Write the installation to one file.

    The dump is taken by `pg_dump` rather than assembled here. It knows about
    sequence positions, materialized views, the order constraints want their
    tables in and the wire format of a geometry column, and each of those is a
    way a hand-rolled exporter is wrong for a month before anyone notices.
    """
    facts = _database_facts(conn)
    url = settings.database_url
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(
        (p for p in att.root_for(settings.data_dir).rglob("*") if p.is_file()),
        key=lambda p: p.as_posix(),
    ) if with_attachments else []

    manifest = Manifest(
        format=FORMAT,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        app_version=_app_version(),
        server_version=facts["version"],
        database=facts["name"],
        collate=facts["collate"],
        ctype=facts["ctype"],
        modules=tuple(settings.modules),
        migrations=migrations(conn),
        counts=counts(conn),
        attachments=len(files),
        attachment_bytes=sum(p.stat().st_size for p in files),
        has_secrets=with_secrets,
        extensions=_extensions(conn),
        note=note,
    )

    dump = out.with_suffix(out.suffix + ".tmp-dump")
    try:
        tool = _tool("pg_dump")
        with dump.open("wb") as sink:
            _run(
                [*tool.command, "--dbname", tool.dsn(url), "--format=custom",
                 "--compress=9", "--no-owner", "--no-privileges"],
                stdout=sink,
            )
        # Written beside the target and moved into place, so an interrupted
        # export leaves no half file that looks importable.
        staging = out.with_suffix(out.suffix + ".tmp")
        with zipfile.ZipFile(staging, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                MANIFEST,
                json.dumps(_as_json(manifest), indent=2, sort_keys=True) + "\n",
            )
            zf.write(dump, DUMP, compress_type=zipfile.ZIP_STORED)
            config = Path(settings.root) / CONFIG
            if config.exists():
                zf.write(config, CONFIG)
            if with_secrets:
                env = Path(settings.root) / SECRETS
                if env.exists():
                    zf.write(env, SECRETS)
            base = att.root_for(settings.data_dir)
            for path in files:
                zf.write(path, ATTACHMENTS + path.relative_to(base).as_posix())
        staging.replace(out)
    finally:
        dump.unlink(missing_ok=True)

    return Report(path=out, manifest=manifest, bytes_written=out.stat().st_size)


def _as_json(manifest: Manifest) -> dict[str, Any]:
    return {
        "format": manifest.format,
        "created_at": manifest.created_at,
        "app_version": manifest.app_version,
        "server_version": manifest.server_version,
        "database": manifest.database,
        "collate": manifest.collate,
        "ctype": manifest.ctype,
        "modules": list(manifest.modules),
        "migrations": manifest.migrations,
        "counts": manifest.counts,
        "attachments": manifest.attachments,
        "attachment_bytes": manifest.attachment_bytes,
        "has_secrets": manifest.has_secrets,
        "extensions": list(manifest.extensions),
        "note": manifest.note,
    }


# --- inspect ------------------------------------------------------------------


def inspect(path: Path) -> Manifest:
    """The manifest, without unpacking the archive."""
    path = Path(path)
    if not path.exists():
        raise WorkspaceError(f"no such workspace file: {path}")
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if MANIFEST not in names:
                raise WorkspaceError(
                    f"{path.name} has no {MANIFEST}, so it is not an Arpenteur "
                    "workspace. A plain pg_dump is restored with pg_restore."
                )
            raw = json.loads(zf.read(MANIFEST))
            if DUMP not in names:
                raise WorkspaceError(f"{path.name} has a manifest but no {DUMP}")
    except zipfile.BadZipFile as exc:
        raise WorkspaceError(f"{path.name} is not readable as a zip: {exc}") from exc
    manifest = Manifest.from_json(raw)
    if manifest.format > FORMAT:
        raise WorkspaceError(
            f"{path.name} is a format {manifest.format} workspace and this "
            f"build reads up to {FORMAT}. Update Arpenteur, then import it."
        )
    return manifest


# --- import -------------------------------------------------------------------


def _known_digests() -> dict[str, dict[str, str]]:
    """Every migration this build carries, hashed the way `migrate` hashes it."""
    import hashlib

    out: dict[str, dict[str, str]] = {}
    loaded = module_registry.load(module_registry.AVAILABLE)
    for module, folder in loaded.migration_dirs():
        for version, _name, path in kernel_db.migration_files(folder):
            body = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
            out.setdefault(module, {})[str(version)] = digest
    return out


def check_compatible(manifest: Manifest) -> list[str]:
    """Refuse a file this build cannot honour; report what it will do.

    Three cases, and only one of them is an error. A file holding a migration
    this build does not have cannot be restored safely, because the code would
    then be reading a schema it has never seen. A file missing migrations this
    build has is the ordinary case and they run after the restore. A file whose
    shared migration has a different digest is the dangerous one: same version,
    different schema, and nothing downstream would notice.
    """
    known = _known_digests()
    problems: list[str] = []
    for module, applied in sorted(manifest.migrations.items()):
        mine = known.get(module)
        if mine is None:
            problems.append(
                f"the workspace carries the {module} module, which this build "
                f"does not have"
            )
            continue
        for version, digest in sorted(applied.items(), key=lambda kv: int(kv[0])):
            if version not in mine:
                problems.append(
                    f"{module} migration {int(version):04d} is in the workspace "
                    f"but not in this build, so the workspace is newer"
                )
            elif mine[version] != digest:
                problems.append(
                    f"{module} migration {int(version):04d} differs: the "
                    f"workspace has {digest}, this build has {mine[version]}"
                )
    return problems


def _occupied(conn: kernel_db.Connection) -> dict[str, int]:
    """What a restore into this database would destroy."""
    present = conn.execute(
        "SELECT 1 FROM information_schema.tables"
        " WHERE table_schema = 'kernel' AND table_name = 'migration'"
    ).fetchone()
    if not present:
        return {}
    return {k: v for k, v in counts(conn).items() if v}


def _standing(url: str) -> dict[str, int]:
    """What the target already holds, where there is a target at all.

    A database that does not exist yet is the cleanest case an import can be
    given, and it is what the first import on a new machine always meets. It
    has to read as empty rather than as a connection error, which is what this
    exists to say.
    """
    import psycopg

    try:
        conn = kernel_db.connect(url)
    except psycopg.OperationalError as exc:
        if "does not exist" in str(exc):
            return {}
        raise WorkspaceError(
            f"the database at {_safe(url)} is not reachable: {exc}"
        ) from exc
    try:
        return _occupied(conn)
    finally:
        conn.close()


def import_(
    settings: Any,
    path: Path,
    *,
    force: bool = False,
    with_attachments: bool = True,
) -> Report:
    """Replace this installation's data with the archive's.

    Destructive by nature, so it refuses a database that holds anything unless
    told plainly to go ahead, and it says what it counted before refusing.
    """
    path = Path(path)
    manifest = inspect(path)
    problems = check_compatible(manifest)
    if problems:
        raise WorkspaceError(
            "this workspace does not match this build:\n  - "
            + "\n  - ".join(problems)
        )

    url = settings.database_url
    standing = _standing(url)
    if standing and not force:
        held = ", ".join(f"{k} {v:,}" for k, v in sorted(standing.items()))
        raise WorkspaceError(
            f"the database at {_safe(url)} already holds data ({held}). "
            "Importing replaces all of it. Pass --force to go ahead."
        )

    _recreate(url, collate=manifest.collate, ctype=manifest.ctype)

    with zipfile.ZipFile(path) as zf:
        # Unpacked to a real file first. `ZipFile.open` hands back a stream
        # with no file descriptor behind it, which `subprocess` cannot pass to
        # a child, and a custom-format archive wants to be seekable anyway.
        staged = Path(path).with_suffix(".tmp-restore")
        try:
            with zf.open(DUMP) as source, staged.open("wb") as sink:
                shutil.copyfileobj(source, sink)
            tool = _tool("pg_restore")
            with staged.open("rb") as source:
                _run(
                    [*tool.command, "--dbname", tool.dsn(url), "--no-owner",
                     "--no-privileges", "--single-transaction"],
                    stdin=source,
                )
        finally:
            staged.unlink(missing_ok=True)
        if with_attachments:
            base = att.root_for(settings.data_dir)
            for name in zf.namelist():
                if not name.startswith(ATTACHMENTS) or name.endswith("/"):
                    continue
                target = base / name[len(ATTACHMENTS):]
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink)

    conn = kernel_db.connect(url)
    try:
        kernel_db.ensure_extensions(conn)
        loaded = module_registry.load(settings.modules or module_registry.AVAILABLE)
        done = kernel_db.migrate(conn, loaded.migration_dirs())
        now = counts(conn)
    finally:
        conn.close()

    verified = {
        table: (expected, now.get(table, 0))
        for table, expected in sorted(manifest.counts.items())
    }
    report = Report(path=path, manifest=manifest, migrated=done, verified=verified)
    if report.mismatches:
        detail = ", ".join(
            f"{t}: expected {a:,}, found {b:,}"
            for t, (a, b) in report.mismatches.items()
        )
        raise WorkspaceError(
            f"the restore did not land what the manifest promised ({detail}). "
            "The database is in an unknown state; import again."
        )
    return report


def _safe(url: str) -> str:
    """A connection string without its password, for an error message."""
    if "@" not in url:
        return url
    head, _, tail = url.rpartition("@")
    scheme, _, _creds = head.partition("://")
    return f"{scheme}://...@{tail}"


def _recreate(url: str, *, collate: str = "", ctype: str = "") -> None:
    """An empty database with no extensions pre-installed.

    From `template0` on purpose. The PostGIS image installs its extensions
    into `template1`, so a database created the ordinary way already holds
    `postgis`, `topology` and `tiger`, and restoring an archive that also
    creates them produces a wall of errors that then have to be triaged by
    hand. Starting from `template0` makes the restore silent when it works,
    which is the only way its noise means anything.

    The collation is carried over from the archive rather than left to the
    server's default. Company names carry accents and the whole survey is
    ordered by name; a restore under a different collation sorts differently
    and nothing announces it.
    """
    import psycopg

    base, _, name = url.rpartition("/")
    name = name.split("?", 1)[0]
    admin = psycopg.connect(f"{base}/postgres", autocommit=True)
    try:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        clause = ""
        if collate and ctype:
            clause = f" LC_COLLATE '{collate}' LC_CTYPE '{ctype}'"
        try:
            admin.execute(
                f'CREATE DATABASE "{name}" TEMPLATE template0'
                f" ENCODING 'UTF8'{clause}"
            )
        except psycopg.errors.DatabaseError as exc:
            if not clause:
                raise
            # A machine that does not have the archive's locale installed is a
            # real situation and not a reason to refuse the import. Say so, and
            # take the server's default rather than failing.
            print(
                f"warning: this server cannot create a database with collation "
                f"{collate!r} ({exc}). Falling back to its default, which means "
                f"names may sort differently here than they did on the machine "
                f"the workspace came from.",
            )
            admin.execute(
                f'CREATE DATABASE "{name}" TEMPLATE template0 ENCODING \'UTF8\''
            )
    finally:
        admin.close()
