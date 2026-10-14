"""The documentation as the program finds it.

The pages are Markdown files under `docs/`, the same files the repository
ships. The console renders them under `/help`, and `arpenteur help` prints
them in a terminal, so a page is written once and read in both places.

The command reference is the one page that is generated rather than written:
`cli_reference` walks the parsers, and a test compares the file with what the
walk produces, so the reference cannot drift from the commands.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from . import markdown
from .config import project_root

# Only the characters a docs filename uses, and no dots, so `..` cannot be
# spelled and a request cannot read outside `docs/`.
_NAME = re.compile(r"^[a-z0-9][a-z0-9_/-]*$")

REFERENCE = "cli"


def root() -> Path:
    return project_root() / "docs"


def clean(name: str) -> str:
    """A page name as requested, or an empty string when it is not one."""
    text = (name or "").strip().strip("/")
    if text.endswith(".md"):
        text = text[:-3]
    if not text:
        return "index"
    return text if _NAME.match(text) else ""


def path_of(name: str, base: Path | None = None) -> Path | None:
    """The file behind a page name, or None when there is no such page."""
    cleaned = clean(name)
    if not cleaned:
        return None
    file = (base or root()) / f"{cleaned}.md"
    return file if file.is_file() else None


def pages(base: Path | None = None) -> list[tuple[str, str]]:
    """Every page as (name, title): the index first, then by name."""
    base = base or root()
    found = []
    for file in sorted(base.rglob("*.md")):
        name = file.relative_to(base).with_suffix("").as_posix()
        found.append((name, markdown.title_of(file.read_text(encoding="utf-8"), name)))
    found.sort(key=lambda item: (item[0] != "index", item[0]))
    return found


# --- The command reference -----------------------------------------------------

_HEAD = """# Command reference

Every command runs as `uv run arpenteur <command>` from the project folder, or as `arpenteur <command>` once the virtual environment is active. `arpenteur <command> --help` prints the same text in the terminal.

This page is generated from the commands themselves by `scripts/cli_reference.py`, and a test refuses a copy that no longer matches them. The pages under [the guide](index.md) say when to run each one.
"""


def cli_reference(parser: argparse.ArgumentParser) -> str:
    """The command reference as Markdown, from the parsers themselves."""
    out = [_HEAD]
    for name, sub, blurb in _commands(parser):
        out.append(_command(name, sub, blurb, level=2))
    return "\n".join(out).rstrip() + "\n"


def _commands(parser: argparse.ArgumentParser) -> list[tuple[str, argparse.ArgumentParser, str]]:
    """The subcommands of a parser as (name, parser, help), in name order."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            blurbs = {choice.dest: choice.help or "" for choice in action._choices_actions}
            return sorted(
                (name, sub, blurbs.get(name, "")) for name, sub in action.choices.items()
            )
    return []


def _command(name: str, parser: argparse.ArgumentParser, blurb: str, *, level: int) -> str:
    prog = parser.prog
    lines = [f"{'#' * level} {prog}", ""]
    if blurb:
        lines += [_sentence(blurb), ""]
    lines += ["```text", _usage(parser), "```", ""]
    arguments = [
        action for action in parser._actions
        if not isinstance(action, (argparse._HelpAction, argparse._SubParsersAction))
    ]
    if arguments:
        lines += [f"- {_argument(action)}" for action in arguments]
        lines.append("")
    nested = _commands(parser)
    for sub_name, sub, sub_blurb in nested:
        lines.append(_command(sub_name, sub, sub_blurb, level=level + 1))
    return "\n".join(lines)


def _usage(parser: argparse.ArgumentParser) -> str:
    parts = [parser.prog]
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        if isinstance(action, argparse._SubParsersAction):
            parts.append("{" + ",".join(action.choices) + "} ...")
        elif action.option_strings:
            parts.append(f"[{_option(action)}]")
        else:
            parts.append(_positional(action))
    return " ".join(parts)


def _option(action: argparse.Action) -> str:
    flag = action.option_strings[-1]
    if action.nargs == 0:
        return flag
    return f"{flag} {_value(action)}"


def _positional(action: argparse.Action) -> str:
    text = _value(action)
    if action.nargs in ("?", "*"):
        return f"[{text}]"
    return text


def _value(action: argparse.Action) -> str:
    if action.choices:
        return "{" + ",".join(str(choice) for choice in action.choices) + "}"
    name = action.metavar or action.dest.upper()
    if action.nargs in ("*", "+"):
        return f"{name} ..."
    return name


def _argument(action: argparse.Action) -> str:
    shown = _option(action) if action.option_strings else _value(action)
    text = (action.help or "").replace("%%", "%")
    default = action.default
    if default not in (None, False, "", argparse.SUPPRESS) and action.nargs != 0 \
            and "default" not in text.lower():
        text = f"{text} (default {default})" if text else f"default {default}"
    return f"`{shown}`: {text}" if text else f"`{shown}`"


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."
