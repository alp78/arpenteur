"""Command line: one subcommand per pipeline stage.

argparse rather than a framework. The surface is small, the dependency list
stays short, and `arpenteur --help` prints the whole pipeline in one screen.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path
from typing import Any
import psycopg
from psycopg.conninfo import conninfo_to_dict
from .kernel.db import dsn

from . import commands
from .kernel import modules as module_registry
from .commands import common
from .commands.common import (  # noqa: F401 - the names the tests and scripts reach for
    _database_url, _find_client, _find_company, positive_int, run_scope,
)


def _enabled() -> tuple[str, ...]:
    """The modules whose commands are offered, from the configuration.

    A configuration that cannot be read offers every module's commands, and
    the command that runs then reports the fault itself, in a sentence.
    """
    try:
        return tuple(common.load_settings().modules) or module_registry.DEFAULT
    except (FileNotFoundError, ValueError, RuntimeError, OSError):
        return module_registry.DEFAULT


def build_parser(enabled: tuple[str, ...] | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="arpenteur",
        description="Survey a market from the Swiss public register.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    commands.mount(sub, enabled if enabled is not None else _enabled())
    return parser


def _unreachable(exc: psycopg.OperationalError) -> str:
    """The sentence for a database that cannot be reached: where, why, what to run."""
    where = conninfo_to_dict(dsn())
    host = where.get("host") or "localhost"
    port = where.get("port") or 5432
    # libpq words the cause after its last "failed: ", and the rest is advice
    # on further lines that this sentence gives in its own words.
    cause = str(exc).rsplit(" failed: ", 1)[-1].splitlines()
    reason = " ".join(cause[0].split()) if cause else ""
    why = f" ({reason})" if reason else ""
    return f"cannot connect to PostgreSQL at {host}:{port}{why}. Start it with `docker compose up -d`."


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Any = args.func
    try:
        return int(handler(args) or 0)
    except RuntimeError as exc:
        # Configuration is resolved before a stage's own error handling, so a
        # misspelled region or auth mode would otherwise reach the user as a
        # traceback. It is a typo, not a crash, and should read like one.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except psycopg.OperationalError as exc:
        if exc.sqlstate:
            # The server answered and refused: a deadlock, a lock timeout, a
            # cancelled query. That is not a stopped container, and saying so
            # would send the operator away from the cause.
            raise
        print(f"error: {_unreachable(exc)}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        missing = str(exc.filename or "")
        if Path(missing).name == "arpenteur.toml":
            print(f"error: arpenteur.toml was expected at {missing} and is not there",
                  file=sys.stderr)
        else:
            print(f"error: no such file: {missing or exc}", file=sys.stderr)
        return 1
    except tomllib.TOMLDecodeError as exc:
        # A `ValueError`, caught on its own because its message does not say
        # which file it was reading, and `load_settings` reads only this one.
        print(f"error: arpenteur.toml is not valid TOML: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        # `config` refuses a value it cannot use, a mistyped effort level, with
        # a message that says what was wrong. Like the stores' refusals, it is
        # addressed to the person typing, and a traceback buries it.
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        # Every stage commits as it goes and resumes where it stopped, so an
        # interrupted run is a normal way to end one.
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
