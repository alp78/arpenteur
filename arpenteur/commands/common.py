"""What every command shares: the run scope, the lookups, the flags.

The handlers live in one file per module beside this one; this is what they
have in common, and nothing here knows which module is which.
"""

from __future__ import annotations

import contextlib

import argparse
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import psycopg
from .. import db
from ..config import load_settings
from ..kernel.db import Connection


def _split(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


def _database_url(settings: Any, fixture: bool) -> str:
    """Keep recorded rows out of the database that holds live registry data.

    A separate database on the same server, created on demand. Under SQLite
    this was a second file beside the first; the guarantee is unchanged.
    """
    if not fixture:
        return settings.database_url
    from ..kernel.db import ensure_database, sibling

    return ensure_database(sibling(settings.database_url, "_fixture"))


@dataclass
class OpenRun:
    """The run a command holds open: its row, and what the row will say at the end."""

    id: int
    stats: dict[str, Any] = field(default_factory=dict)


def _settle_failed(conn: Connection, run_id: int, error: str) -> None:
    try:
        conn.rollback()
        db.finish_run(conn, run_id, error=error)
    except psycopg.Error:
        # The connection went with the stage: the server stopped, or the
        # session was cut. Nothing can be written, and the failure about to be
        # raised again is the one worth reporting, not this one.
        pass


@contextlib.contextmanager
def run_scope(
    conn: Connection, stage: str, args: argparse.Namespace | dict[str, Any],
    *, close: bool = True,
) -> Iterator[OpenRun]:
    """Open a run row, and finish it however the command ends.

    The command sets `run.stats` to what the row should record and lets the
    block end. A failure is written to the row and raised again, for the
    command to report in its own words. `close` is for the commands that
    opened the connection themselves: it is closed on every path out.

    Three things this gets right that each handler once got wrong alone.

    The rollback comes first. A statement that failed leaves the transaction
    aborted, and the UPDATE that finishes the run then fails in turn, so the
    row stayed "running" for ever with the reason lost.

    Ctrl-C is a `KeyboardInterrupt`, which is not an `Exception`, so a handler
    that caught `Exception` never saw it and left the row open.

    An error with no message is recorded under its class name. An empty error
    reads as none, and the run as done.
    """
    logged = args if isinstance(args, dict) else vars(args)
    try:
        run = OpenRun(db.start_run(conn, stage, logged))
        try:
            yield run
        except KeyboardInterrupt:
            _settle_failed(conn, run.id, "interrupted")
            raise
        except Exception as exc:
            _settle_failed(conn, run.id, str(exc) or type(exc).__name__)
            raise
        else:
            db.finish_run(conn, run.id, run.stats)
    finally:
        if close:
            conn.close()


def _scoped(args: argparse.Namespace):
    """Settings narrowed by whatever territory flags were given.

    Resolved once, at the edge, so every stage below sees the same territory
    instead of each deciding for itself what --region meant.
    """
    return load_settings().scoped(
        region=getattr(args, "region", None),
        cantons=_split(getattr(args, "canton", None)),
        legal_forms=_split(getattr(args, "legal_form", None)),
    )


def _search_words(args: argparse.Namespace) -> list[str]:
    """The words a name search asks for, from the flag or from a file.

    A file because a campaign's word list runs to a couple of dozen entries in
    three languages and belongs under version control, where a diff says what
    changed between two runs. Blank lines and `#` comments are skipped so the
    list can be annotated with what each word was meant to catch.
    """
    words: list[str] = []
    for raw in (getattr(args, "name", "") or "").split(","):
        if raw.strip():
            words.append(raw.strip())
    path = getattr(args, "name_file", "") or ""
    if path:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                words.append(line)
    # Order preserved, duplicates dropped: the log reads in the order the file
    # is written, and a word listed twice is one request wasted per canton.
    return list(dict.fromkeys(words))


def _masked(key: str, value: Any) -> str:
    """A setting's value for a terminal: an IBAN keeps its last four characters."""
    if key == "iban" and value:
        text = str(value).replace(" ", "")
        return "*" * max(0, len(text) - 4) + text[-4:]
    if isinstance(value, list):
        return f"{len(value)} entr{'y' if len(value) == 1 else 'ies'}"
    if isinstance(value, dict):
        return ", ".join(f"{k}={v}" for k, v in value.items())
    return str(value)


def _local_path(given: str) -> Path:
    """A path as typed, including a Windows one under WSL.

    `C:\\Users\\you\\notes\\note.md` is where the file is as far as its author is
    concerned, and it is `/mnt/c/Users/you/notes/note.md` as far as this
    process is concerned. Translating it here costs four lines and removes a
    step that would otherwise be got wrong at the end of every day.
    """
    text = given.strip().strip('"')
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", text)
    if match:
        drive, rest = match.groups()
        return Path(f"/mnt/{drive.lower()}/" + rest.replace("\\", "/"))
    return Path(text)


def _find_client(conn: Any, reference: str) -> Any:
    """A client by its code, its UID or its name, through the store's one lookup."""
    from ..modules.clients import store as clients_store

    try:
        return clients_store.find_client(conn, reference)
    except ValueError as exc:
        raise RuntimeError(f"{exc} Try: arpenteur clients") from None


def _find_company(conn: Any, reference: str) -> Any:
    """A company by its UID or part of its name, through the store's one lookup.

    Separate from `_find_client` because a programme's members are companies
    and most of them will never be clients.
    """
    from ..modules.scout import store as scout_store

    try:
        return scout_store.find_company(conn, reference)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from None


def positive_int(text: str) -> int:
    """An argparse type for a count of one or more, which is what `--limit` is.

    Zero is refused rather than read as "no limit". The stages that cap their
    spend with it disagreed about what zero meant: the guard took it for a
    limit given and the slice took it for none, so `--limit 0` ran everything
    and skipped the check that stops a run from doing so.
    """
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"{value} is not a positive number (1 or more)")
    return value


def territory_flags(parser: argparse.ArgumentParser) -> None:
    """The same three flags on every command that acts on a territory.

    One vocabulary rather than three: --region on `source` and --canton on
    `hydrate` would make the two disagree about what a run covered.
    """
    group = parser.add_argument_group("territory")
    group.add_argument(
        "--region", help="a name from [regions] in arpenteur.toml, e.g. romandie"
    )
    group.add_argument(
        "--canton", help="comma separated, e.g. VD,GE (overrides --region)"
    )
    group.add_argument(
        "--legal-form", help="comma separated eCH-0097 uids, e.g. 0107,0106"
    )
