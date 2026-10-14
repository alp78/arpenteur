"""Home: where things stand, and what to do first.

The landing page used to be the company list, which is the right first page
for someone who has already swept a register and the wrong one for everybody
else: on a fresh install it said that nothing matched a filter. This page says
three things instead. Whether the application is set up, read from the same
checks `arpenteur doctor` prints. How much there is of each thing, as tiles
that are links. And what ran last, since a run is how anything here changes.
"""

from __future__ import annotations

from typing import Any, Sequence

from ..health import Check
from ..kernel.db import Row
from . import ui
from .render import esc, page

STYLES = """
.tiles { display:grid; grid-template-columns:repeat(auto-fill, minmax(11rem, 1fr));
         gap:var(--s3); margin:var(--s3) 0 var(--s5); }
.tile { display:block; background:var(--paper); border:1px solid var(--line);
        padding:.8rem 1rem; text-decoration:none; color:var(--ink); }
.tile:hover { border-color:var(--accent); }
.tile b { display:block; font-size:1.6rem; font-weight:600; line-height:1.2; }
.tile span { color:var(--dim); font-size:var(--t-sm); }
.checks { list-style:none; padding:0; margin:var(--s2) 0 var(--s4); }
.checks li { display:flex; gap:var(--s3); align-items:baseline; padding:.35rem 0;
             border-bottom:1px solid var(--line); }
.checks li:last-child { border-bottom:0; }
.checks .mark { flex:0 0 1.3rem; text-align:center; font-weight:600; }
.checks .check-ok .mark { color:var(--good); }
.checks .check-missing .mark { color:var(--late); }
.checks .check-warn .mark { color:var(--caution-ink); }
.checks .name { flex:0 0 8rem; font-weight:600; }
.checks .detail { color:var(--dim); }
"""

MARKS = {"ok": "✓", "warn": "!", "missing": "✗"}


def shell(body: str, chrome: Any = None) -> str:
    return page("", body, chrome, css=STYLES)


def _checklist(found: Sequence[Check]) -> str:
    items = "".join(
        f'<li class="check check-{esc(check.state)}">'
        f'<span class="mark" aria-hidden="true">{MARKS.get(check.state, "?")}</span>'
        f'<span class="vh">{esc(check.state)}</span>'
        f'<span class="name">{esc(check.name)}</span>'
        f'<span class="detail">{esc(check.detail)}'
        + (f' <a href="{esc(check.href)}">Fix it</a>' if check.href and not check.ok else "")
        + "</span></li>"
        for check in found
    )
    missing = sum(1 for check in found if not check.ok)
    lede = (
        f"{missing} thing(s) to set before the first run."
        if missing else "Everything the first run needs is in place."
    )
    return (
        f'<section class="panel"><h2>Setup</h2><p class="lede">{esc(lede)}</p>'
        f'<ul class="checks">{items}</ul></section>'
    )


def _tiles(tiles: Sequence[tuple[str, int, str]]) -> str:
    if not tiles:
        return ""
    cells = "".join(
        f'<a class="tile" href="{esc(href)}"><b>{ui.num(value)}</b>'
        f"<span>{esc(label)}</span></a>"
        for label, value, href in tiles
    )
    return f'<div class="tiles">{cells}</div>'


def _runs(rows: Sequence[Row]) -> str:
    if not rows:
        body = ui.empty(
            "Nothing has run yet.",
            action='Start with <a href="/pipeline">Operations</a>: the first stage '
                   "sweeps the register for your territory.",
        )
    else:
        lines = []
        for row in rows:
            state = "error" if row["error"] else ("done" if row["finished_at"] else "running")
            lines.append(
                f"<tr><td class='small'>{esc((row['started_at'] or '')[:16])}</td>"
                f"<td><a href='/runs/{row['id']}'>{esc(row['stage'])}</a></td>"
                f"<td class='state-{state}'>{esc(state)}</td></tr>"
            )
        body = ui.table(["started", "stage", "state"], lines) + (
            '<p class="small"><a href="/runs">Every run</a></p>'
        )
    return f'<section class="panel"><h2>Last runs</h2>{body}</section>'


def home_page(
    *, checks: Sequence[Check], tiles: Sequence[tuple[str, int, str]],
    runs: Sequence[Row], territory: str = "",
) -> str:
    where = f" in {esc(territory)}" if territory else ""
    return (
        "<h1>Home</h1>"
        f"<p class='lede'>Where things stand{where}. Each number is a link to "
        "the page that holds it; the groups in the header follow the work from "
        "finding a company to billing a client.</p>"
        + _tiles(tiles)
        + _checklist(checks)
        + _runs(runs)
    )
