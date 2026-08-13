"""PostgreSQL: connections, and migrations owned by modules.

Two things moved here from the SQLite era and one is new.

**Rows come back as dictionaries.** `sqlite3.Row` supported both `row["name"]`
and `row[0]`; `psycopg`'s `dict_row` supports the first, which is what every
call site in this codebase actually used. The type alias `Row` exists so the
ninety-odd annotations that said `sqlite3.Row` say something true without
naming a driver.

**Migrations are per module, numbered inside each.** The version is a table
rather than `PRAGMA user_version`, because there are now several sequences: the
scout is at its version, sales at its own, and a deployment that never enabled
sales has no opinion about the latter. Each file runs once, inside a
transaction, and is recorded with the sha256 of what was applied so an edited
migration is caught rather than silently skipped.

**The schema is created by the module that owns it.** `kernel` creates nothing
except its own bookkeeping.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

# What a query gives back. Named here so nothing else imports the driver just
# to write a type.
Row = dict[str, Any]
Connection = psycopg.Connection

__all__ = [
    "Row", "Connection", "Jsonb", "now", "dsn", "connect", "json_param",
    "ensure_extensions", "migrate", "versions", "drop_module", "migration_files",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_param(value: Any) -> Any:
    """Wrap a value bound for a jsonb column.

    psycopg does not adapt a bare `dict` or `list` — it raises rather than
    guessing between json, hstore and a composite type, which is the right call
    and means every JSON parameter has to say so. `None` passes through so a
    nullable column stays nullable rather than becoming the JSON literal
    `null`, which is a different thing and compares differently.
    """
    return None if value is None else Jsonb(value)


# Timestamps come back as ISO strings, not datetime objects.
#
# The database stores TIMESTAMPTZ, which is what makes date arithmetic,
# range indexes and the analytics schema possible; this is only about what
# crosses back into Python. Forty-five places in the console format a stamp by
# slicing it — `row["occurred_at"][:16].replace("T", " ")` — and every one of
# them is a rendering concern rather than a calculation. Converting here keeps
# that code honest instead of scattering `.isoformat()` through the views, and
# where a real date computation is needed it belongs in SQL, where it already
# is.
_TEMPORAL = (datetime, date, time)


def _stringify(value: Any) -> Any:
    if isinstance(value, _TEMPORAL):
        return value.isoformat()
    return value


class Record(dict):
    """A row read by name, or by position where that is what was written.

    `sqlite3.Row` allowed both, and about a hundred and eighty places in this
    codebase take the first column of a count with `fetchone()[0]`. Supporting
    the integer index here rather than rewriting every one of them keeps the
    port to what it is — a change of database — instead of mixing it with a
    hundred and eighty edits that could each be wrong in their own way.

    Reading by name is the better habit and the one new code should keep: a
    positional read silently follows the SELECT list when someone adds a
    column. This exists so that habit can be adopted gradually rather than in
    one commit.
    """

    __slots__ = ()

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            try:
                return list(self.values())[key]
            except IndexError:
                raise IndexError(
                    f"row has {len(self)} columns; index {key} is past the end"
                ) from None
        return super().__getitem__(key)


def rows_as_dicts(cursor: Any) -> Any:
    make = dict_row(cursor)

    def row(values: Any) -> Record:
        return Record((k, _stringify(v)) for k, v in make(values).items())

    return row


def dsn(url: str | None = None) -> str:
    """The connection string, from the argument, the environment, or the
    local development container."""
    return (
        url
        or os.environ.get("DATABASE_URL")
        or "postgresql://arpenteur:arpenteur@127.0.0.1:5433/arpenteur"
    )


def sibling(url: str, suffix: str) -> str:
    """The same server, a differently named database.

    How the fixture set stays out of the database holding live registry data.
    Under SQLite that was a second file next to the first; here it is a second
    database on the same server, which is the same idea and the same guarantee.
    """
    base, _, name = url.rpartition("/")
    name, sep, params = name.partition("?")
    return f"{base}/{name}{suffix}{sep}{params}"


def ensure_database(url: str) -> str:
    """Create the database named in `url` if it is not there yet.

    CREATE DATABASE cannot run inside a transaction and cannot run against the
    database being created, so it connects to `postgres` on the same server.
    Idempotent: an existing database is left exactly as it is.
    """
    base, _, name = url.rpartition("/")
    name = name.partition("?")[0]
    try:
        psycopg.connect(url).close()
        return url
    except psycopg.OperationalError as exc:
        if "does not exist" not in str(exc):
            raise
    with psycopg.connect(f"{base}/postgres", autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    return url


def connect(url: str | None = None) -> Connection:
    """One connection, rows as dictionaries, autocommit off.

    Autocommit stays off because the stages rely on it: a sweep writes
    thousands of companies and commits in batches, and a crash halfway should
    leave the register half-updated rather than half-written.
    """
    return psycopg.connect(dsn(url), row_factory=rows_as_dicts)


# --- bookkeeping --------------------------------------------------------------

BOOKKEEPING = """
CREATE SCHEMA IF NOT EXISTS kernel;

CREATE TABLE IF NOT EXISTS kernel.migration (
    module     TEXT NOT NULL,
    version    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    -- The sha256 of the file as applied. A migration edited after the fact is
    -- the classic way two deployments end up with different schemas while both
    -- claim the same version; comparing the digest turns that into an error.
    digest     TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (module, version)
);
"""

EXTENSIONS = ("postgis", "pg_trgm", "unaccent", "btree_gin")

NUMBERED = re.compile(r"^(\d+)_([a-z0-9_]+)\.sql$")


def ensure_extensions(conn: Connection) -> list[str]:
    """Install what every module may rely on.

    Done once, by the kernel, rather than by whichever module happens to run
    first. `postgis` is here even though only the geo module uses geometry:
    creating an extension needs elevated rights and doing it at startup keeps
    that out of a module's migration, where it would fail confusingly for a
    deployment whose database user is not a superuser.
    """
    installed = []
    for name in EXTENSIONS:
        try:
            conn.execute(f'CREATE EXTENSION IF NOT EXISTS "{name}"')
            installed.append(name)
        except psycopg.errors.InsufficientPrivilege:
            conn.rollback()
            raise RuntimeError(
                f"The database user cannot create the {name} extension. Have a "
                f'superuser run: CREATE EXTENSION "{name}";'
            ) from None
    conn.commit()
    return installed


def migration_files(folder: Path) -> list[tuple[int, str, Path]]:
    found = []
    for path in sorted(folder.glob("*.sql")):
        match = NUMBERED.match(path.name)
        if not match:
            raise RuntimeError(
                f"{path} is not named NNNN_name.sql, so its order is undefined."
            )
        found.append((int(match.group(1)), match.group(2), path))
    found.sort(key=lambda item: item[0])
    return found


def applied(conn: Connection, module: str) -> dict[int, str]:
    rows = conn.execute(
        "SELECT version, digest FROM kernel.migration WHERE module = %s",
        (module,),
    ).fetchall()
    return {row["version"]: row["digest"] for row in rows}


def migrate(conn: Connection, dirs: Iterable[tuple[str, Path]]) -> list[str]:
    """Apply every module's outstanding migrations, in the order given.

    Each file runs in its own transaction with its bookkeeping row, so a
    failure halfway through a sequence leaves the ones before it applied and
    recorded rather than rolled back into an ambiguous state.
    """
    conn.execute(BOOKKEEPING)
    conn.commit()

    done = []
    for module, folder in dirs:
        seen = applied(conn, module)
        for version, name, path in migration_files(folder):
            body = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
            if version in seen:
                if seen[version] != digest:
                    raise RuntimeError(
                        f"{module} migration {version:04d}_{name} has changed "
                        f"since it was applied ({seen[version]} -> {digest}). "
                        "Migrations are immutable once run; add a new one."
                    )
                continue
            try:
                conn.execute(body)
                conn.execute(
                    "INSERT INTO kernel.migration (module, version, name, digest)"
                    " VALUES (%s, %s, %s, %s)",
                    (module, version, name, digest),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            done.append(f"{module} {version:04d}_{name}")
    return done


def versions(conn: Connection) -> dict[str, int]:
    """Where each module stands. Read by `arpenteur status` and the console."""
    rows = conn.execute(
        "SELECT module, MAX(version) AS version FROM kernel.migration"
        " GROUP BY module ORDER BY module"
    ).fetchall()
    return {row["module"]: row["version"] for row in rows}


def drop_module(conn: Connection, module: str, schemas: Iterable[str]) -> None:
    """Remove a module's data entirely.

    This is what makes `removable` mean something rather than being a promise
    in a docstring. It works precisely because no upstream schema holds a
    foreign key into a downstream one: dropping `commercial` cannot orphan a
    company, and Postgres would refuse the statement if it could.
    """
    for schema in schemas:
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    conn.execute("DELETE FROM kernel.migration WHERE module = %s", (module,))
    conn.commit()
