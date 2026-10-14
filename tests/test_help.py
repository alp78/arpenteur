"""The manual: one set of pages under docs/, read in the console and in the
terminal, and a command reference that cannot drift from the commands.

These walk the files rather than naming pages, so a page added to the manual
is covered the day it appears.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from arpenteur import manual
from arpenteur.cli import build_parser, main
from arpenteur.console.render import SHELL_NAV
from arpenteur.kernel import modules as modules_lib

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_MAILBOX = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# The register's own address, and the fictional domains the examples use.
_ALLOWED_MAILBOXES = re.compile(r"@(bj\.admin\.ch|example\.(ch|com|test))$")


def _pages() -> list[Path]:
    found = sorted(DOCS.rglob("*.md"))
    assert found, "docs/ holds no page"
    return found


def _every_nav():
    yield from SHELL_NAV
    for module in modules_lib.load(modules_lib.DEFAULT).modules:
        yield from module.nav


# --- The pages ------------------------------------------------------------------


def test_every_menu_entry_has_a_guide_page() -> None:
    """The "?" in the header must land somewhere."""
    absent = [
        f"{item.label} -> docs/{item.guide}.md"
        for item in _every_nav() if not (DOCS / f"{item.guide}.md").is_file()
    ]
    assert not absent, absent


def test_every_page_opens_on_a_title() -> None:
    for page in _pages():
        first = page.read_text(encoding="utf-8").lstrip().splitlines()[0]
        assert first.startswith("# "), page


def test_every_link_between_pages_resolves() -> None:
    """A link written for the file tree must point at a file in it."""
    broken = []
    for page in _pages():
        for target in _LINK.findall(page.read_text(encoding="utf-8")):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith(("/", "#")):
                continue
            file = (page.parent / target.split("#")[0]).resolve()
            if not file.is_file():
                broken.append(f"{page.relative_to(ROOT)} -> {target}")
    assert not broken, broken


def test_the_readme_links_into_the_manual_resolve() -> None:
    readme = ROOT / "README.md"
    broken = [
        target for target in _LINK.findall(readme.read_text(encoding="utf-8"))
        if not re.match(r"^[a-z][a-z0-9+.-]*:", target)
        and not target.startswith("#")
        and not (ROOT / target.split("#")[0]).exists()
    ]
    assert not broken, broken


def test_no_page_carries_an_em_dash_or_a_personal_mailbox() -> None:
    """The house rule, and the stage 1 promise: nothing of the operator's in
    the tree."""
    faults = []
    for page in [*_pages(), ROOT / "README.md"]:
        text = page.read_text(encoding="utf-8")
        if "—" in text:
            faults.append(f"{page.name}: em dash")
        for mailbox in _MAILBOX.findall(text):
            if not _ALLOWED_MAILBOXES.search(mailbox):
                faults.append(f"{page.name}: {mailbox}")
    assert not faults, faults


def test_a_page_name_cannot_leave_the_docs_folder() -> None:
    assert manual.clean("") == "index"
    assert manual.clean("guide/companies.md") == "guide/companies"
    assert manual.clean("/guide/companies/") == "guide/companies"
    for bad in ("../README", "guide/../../etc/passwd", "Guide", "a b", ".hidden"):
        assert manual.clean(bad) == "", bad
    assert manual.path_of("../README") is None
    assert manual.path_of("index") == DOCS / "index.md"


def test_the_index_comes_first_in_the_listing() -> None:
    names = [name for name, _title in manual.pages()]
    assert names[0] == "index"
    assert names[1:] == sorted(names[1:])
    assert len(names) == len(_pages())


# --- The terminal --------------------------------------------------------------


def test_help_prints_the_page_as_written(capsys) -> None:
    assert main(["help"]) == 0
    assert capsys.readouterr().out == (DOCS / "index.md").read_text(encoding="utf-8")
    assert main(["help", "guide/companies"]) == 0
    assert capsys.readouterr().out.startswith("# ")


def test_help_names_the_pages_and_refuses_an_unknown_one(capsys) -> None:
    assert main(["help", "--list"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("index")
    assert "guide/companies" in out
    assert main(["help", "guide/nothing-here"]) == 1
    err = capsys.readouterr().err
    assert "guide/nothing-here" in err and "--list" in err


# --- The command reference ----------------------------------------------------


def _arguments(parser: argparse.ArgumentParser, path: str = "arpenteur"):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                yield from _arguments(sub, f"{path} {name}")
        elif not isinstance(action, argparse._HelpAction):
            yield path, action


def test_every_command_argument_explains_itself() -> None:
    silent = [
        f"{path}: {action.option_strings or action.dest}"
        for path, action in _arguments(build_parser()) if not (action.help or "").strip()
    ]
    assert not silent, silent


def test_every_command_explains_itself() -> None:
    silent = [name for name, _sub, blurb in manual._commands(build_parser()) if not blurb]
    assert not silent, silent


def test_the_command_reference_matches_the_commands() -> None:
    """`scripts/cli_reference.py` writes it; this refuses a copy that drifted."""
    written = (DOCS / f"{manual.REFERENCE}.md").read_text(encoding="utf-8")
    assert written == manual.cli_reference(build_parser()), (
        "docs/cli.md is out of date: run `uv run python scripts/cli_reference.py`"
    )


@pytest.mark.parametrize("command", ["help", "doctor", "settings", "console"])
def test_the_reference_names_each_command_once(command) -> None:
    text = (DOCS / f"{manual.REFERENCE}.md").read_text(encoding="utf-8")
    assert text.count(f"\n## arpenteur {command}\n") == 1


def test_every_page_is_reachable_from_the_index() -> None:
    """A page nobody can reach from Help is a page nobody reads."""
    index = (DOCS / "index.md").read_text(encoding="utf-8")
    linked = {target.split("#")[0] for target in _LINK.findall(index)}
    unreached = [
        name for name, _title in manual.pages()
        if name != "index" and f"{name}.md" not in linked
    ]
    assert not unreached, unreached
