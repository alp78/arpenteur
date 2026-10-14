"""Write docs/cli.md from the command-line parsers.

    uv run python scripts/cli_reference.py

A test compares the file with what this produces, so the reference cannot
drift from the commands: change a parser, run this, commit both.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arpenteur import manual  # noqa: E402
from arpenteur.cli import build_parser  # noqa: E402


def main() -> int:
    target = manual.root() / f"{manual.REFERENCE}.md"
    target.write_text(manual.cli_reference(build_parser()), encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
