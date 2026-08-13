"""People, the boards they sit on, and the way from one company to another.

The survey answers "who exists". This answers "who do we already reach", which
is the question that decides whether a first approach is a cold letter or a
conversation with somebody's colleague.

Two things the pages have to keep visible, because both are easy to lose in a
list that looks authoritative.

**How the identity was decided.** A party is a judgement about which rows are
one human, and the judgement varies in strength: a name with a stated origin
repeated across five notices is not the same claim as a name with no origin
anywhere. Every party carries its method, and the weaker ones say so where they
are read rather than in a footnote.

**Where an edge came from.** Every role links back to the company and to the
notice behind it. A relationship graph nobody can audit is a rumour with an
index.
"""

from __future__ import annotations


from typing import Any

from ..kernel.db import Row
from . import ui
from .render import band, esc, page

STYLES = """
.who { display:flex; gap:1rem; align-items:baseline; flex-wrap:wrap; }
.method { font-size:.72rem; text-transform:uppercase; letter-spacing:.05em;
          border:1px solid var(--line); border-radius:2px; padding:.05rem .4rem;
          color:var(--dim); background:var(--paper); }
.method.weak { border-color:var(--late-line); color:var(--late); background:var(--late-bg); }
.boards { font-variant-numeric:tabular-nums; }
.hop { display:inline-block; }
.hop::after { content:" \\2192 "; color:var(--dim); }
.hop:last-child::after { content:""; }
.route { background:var(--paper); border:1px solid var(--line); padding:.5rem .7rem;
         margin-bottom:.5rem; }
.route .via { font-size:.8rem; color:var(--dim); margin-top:.2rem; }
.past-tag { font-size:.7rem; text-transform:uppercase; letter-spacing:.05em;
            color:var(--dim); }
"""


def _span(role: Row) -> str:
    """How long somebody has held a seat, or when they gave it up.

    "Since 2011" and "appointed six weeks ago" are different conversations,
    and a table that shows neither reduces both to a name in a cell.
    """
    start = (role["valid_from"] or "")[:10]
    end = (role["valid_to"] or "")[:10]
    if end:
        return f'{esc(start)} to {esc(end)}'
    return f'since {esc(start)}' if start else '<span class="dim">&mdash;</span>' 

# What each resolution method means, said where it is read. A table of parties
# with no gloss on the method column is a table that gets trusted uniformly.
METHODS: dict[str, tuple[str, str]] = {
    "single": ("one row", "Named at one company only, so nothing was merged "
                          "and nothing is claimed."),
    "name-and-origin": ("name and origin", "Several notices, all giving the "
                                           "same name and the same registered "
                                           "origin. The strongest evidence "
                                           "the register offers."),
    "sole-origin": ("sole origin", "Several notices under one name, one of "
                                   "which states an origin and the rest are "
                                   "silent. Probably one person."),
    "name-only": ("name only", "Several notices under one name and no origin "
                               "anywhere. The weakest kind: two people with "
                               "one name would look exactly like this."),
    "manual": ("set by hand", "Decided by a person, which outranks the rule."),
}
WEAK = {"name-only", "sole-origin"}


def shell(body: str, chrome: Any = None) -> str:
    return page("arpenteur", body, chrome, css=STYLES)


def method_tag(party: Row) -> str:
    label, why = METHODS.get(party["method"], (party["method"] or "?", ""))
    weak = " weak" if party["method"] in WEAK else ""
    return f'<span class="method{weak}" title="{esc(why)}">{esc(label)}</span>'


def people_page(rows: list[Row], *, q: str = "", min_boards: int = 1,
                counts: dict[str, int] | None = None) -> str:
    counts = counts or {}
    body = []
    for row in rows:
        body.append(
            f'<tr><td><a href="/party/{row["id"]}">{esc(row["full_name"])}</a></td>'
            f'<td class="small">{esc(row["origin"] or "")}</td>'
            f'<td class="small">{esc(row["domicile"] or "")}</td>'
            f'<td class="boards">{row["boards"]}</td>'
            f"<td>{method_tag(row)}</td></tr>"
        )
    table = ui.table(
        ["person", "origin", "domicile", "boards", "resolved on"],
        body, caption="People resolved from the officer rows",
        numeric=["boards"],
        empty_text="Nobody matches. Run resolve, which is free, if the officer "
                   "rows have changed.",
    )
    tiles = (
        '<div class="grid" style="margin-bottom:1.2rem">'
        f'<div class="tile"><h4>People</h4><div class="big">{ui.num(counts.get("parties", 0))}</div>'
        f'<div class="dim small">from {ui.num(counts.get("people", 0))} officer rows</div></div>'
        f'<div class="tile"><h4>On several boards</h4>'
        f'<div class="big">{ui.num(counts.get("connectors", 0))}</div>'
        '<div class="dim small">the routes into a company</div></div>'
        f'<div class="tile"><h4>Roles</h4><div class="big">{ui.num(counts.get("roles", 0))}</div>'
        "</div></div>"
        if counts
        else ""
    )
    return (
        "<h1>People</h1>"
        "<p class='lede'>Officers named in the register's own notices, resolved "
        "into the humans behind them. Sorted by how many boards each sits on, "
        "because a name on nine boards is a route into nine companies.</p>"
        + tiles
        + '<form class="filters" method="get" action="/people">'
        f'<div class="ctl"><label>Search</label>'
        f'<input name="q" value="{esc(q)}" placeholder="Surname Firstname"></div>'
        '<div class="ctl"><label>Sitting on at least</label>'
        f'<input name="min_boards" type="number" min="1" max="20" '
        f'value="{min_boards}" style="width:5rem"></div>'
        '<div class="ctl"><label>&nbsp;</label><button class="primary">Show</button>'
        "</div></form>"
        + table
        + "<p class='dim small' style='max-width:70ch'>Officers are parsed from "
        "FOSC publications and are not registry fields. Identity is a judgement "
        "made from a name and the origin the notice states; where that evidence "
        "is weaker the row says so. Verify against the certified excerpt before "
        "relying on it.</p>"
    )


def party_page(
    party: Row, roles: list[Row], *, also_named: list[Row] | None = None
) -> str:
    label, why = METHODS.get(party["method"], (party["method"] or "?", ""))
    head = (
        f'<p class="crumbs"><a href="/people">People</a> / {esc(party["full_name"])}</p>'
        f'<div class="who"><h1 style="margin:0">{esc(party["full_name"])}</h1>'
        f"{method_tag(party)}</div>"
        f'<p class="dim small">'
        + " &middot; ".join(
            part for part in (
                f"de {esc(party['origin'])}" if party["origin"] else "",
                f"à {esc(party['domicile'])}" if party["domicile"] else "",
                f"{len(roles)} role{'s' if len(roles) != 1 else ''}",
            ) if part
        )
        + "</p>"
        + f'<div class="card small"><b>Resolved on {esc(label)}.</b> {esc(why)}</div>'
    )

    rows = "".join(
        f'<tr class="{"past" if r["valid_to"] else ""}">'
        f'<td><a href="/company/{r["company_id"]}">{esc(r["name"])}</a>'
        f'<div class="dim small">{esc(r["uid"])} &middot; {esc(r["canton"] or "")}</div></td>'
        f'<td class="small">{esc(r["role"] or "")}</td>'
        f'<td class="small">{_span(r)}</td>'
        f"<td>{band(r)}</td>"
        f'<td class="small">{esc(r["state"] or "")}</td></tr>'
        for r in roles
    )
    ended = sum(1 for r in roles if r["valid_to"])
    return (
        head
        + "<h2>boards</h2>"
        + ui.table(
            ["company", "role", "when", "fit", "pipeline"], [rows],
            caption="Seats held, current and finished",
            empty_text="No seat is recorded for this person.",
        )
        + (
            f"<p class='dim small'>{ended} of these has ended. A seat somebody "
            "has left is still a link, and a poor introduction; both are shown "
            "and the finished ones are greyed.</p>"
            if ended
            else ""
        )
        + (_also_named(also_named) if also_named else "")
    )


def _also_named(candidates: list[Row]) -> str:
    """Other parties with the same folded name, which may be the same person.

    Shown rather than merged. Two of these turned up in the first live
    resolution and neither is a mistake in the rule: `Macédoine` and `Macédoine
    du Nord` are one country renamed in 2019, and `Rossens` was absorbed into
    the commune of `Gibloux` in 2016. A rule that merged across those would
    also merge two genuinely different people, so the split stands and the
    judgement is offered to a person.
    """
    rows = "".join(
        f'<tr><td><a href="/party/{r["id"]}">{esc(r["full_name"])}</a></td>'
        f'<td class="small">{esc(r["origin"] or "no origin stated")}</td>'
        f'<td class="small">{r["boards"]} boards</td></tr>'
        for r in candidates
    )
    return (
        "<h2>possibly the same person</h2>"
        f"<table><tbody>{rows}</tbody></table>"
        "<p class='dim small' style='max-width:70ch'>Same name, different "
        "stated origin, so they are held apart. Sometimes that is two people "
        "and sometimes it is one: a commune merger or a country renaming moves "
        "an origin without moving the person. Judged here rather than by a "
        "rule, because a rule loose enough to join these would also join two "
        "strangers.</p>"
    )


def overlap_panel(rows: list[Row], company_id: int) -> str:
    """Companies sharing an officer with this one, for the company page."""
    if not rows:
        return ""
    body = "".join(
        f'<tr><td><a href="/company/{r["company_id"]}">{esc(r["name"])}</a>'
        f'<div class="dim small">{esc(r["canton"] or "")}</div></td>'
        f'<td class="small">{esc(", ".join(r["people"] or []))}'
        + ("" if r["current"] else '<div class="past-tag">both have left</div>')
        + "</td>"
        f"<td>{band(r)}</td>"
        f'<td class="small">{esc(r["state"] or "")}</td></tr>'
        for r in rows[:12]
    )
    more = (
        f'<p class="dim small">{len(rows) - 12} more.</p>' if len(rows) > 12 else ""
    )
    return (
        "<h2>shares an officer with</h2>"
        + ui.table(
            ["company", "through", "fit", "pipeline"], [body],
            caption="Companies reachable through a shared officer",
        )
        + more
        + "<p class='dim small'>Parsed from published notices, and only as good "
          "as the identity resolution behind it. Each name links to what else "
          "it is on.</p>"
    )


def route_panel(routes: list[Row], names: dict[int, str], target: str) -> str:
    """The chain of shared officers from here to somewhere else."""
    if not routes:
        return (
            f'<div class="warn">No route to {esc(target)} within four boards. '
            "That is an answer: a chain longer than that is a chain of "
            "strangers.</div>"
        )
    items = []
    for route in routes:
        hops = "".join(
            f'<span class="hop"><a href="/company/{cid}">'
            f'{esc(names.get(int(cid), str(cid)))}</a></span>'
            for cid in route["path"]
        )
        via = ", ".join(dict.fromkeys(route["people"] or []))
        items.append(
            f'<div class="route">{hops}'
            f'<div class="via">{route["depth"]} hop'
            f'{"s" if route["depth"] != 1 else ""} &middot; through {esc(via)}</div>'
            "</div>"
        )
    return "<h2>the way there</h2>" + "".join(items)
