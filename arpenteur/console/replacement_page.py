"""The company page's replacement panel: what a search for a bounced address found.

Each proposal says what the address reaches, where it was found and what is
uncertain about it, and offers the two decisions. Nothing becomes the firm's
address until a person presses Use this address.
"""

from __future__ import annotations

from typing import Sequence

from ..kernel.db import Row
from . import ui
from .ui import esc

FOUND = {"page": "on its own site", "listing": "on another site", "inferred": "a guess"}
REACHES = {
    "desk": "a desk",
    "person": "a named person",
    "unclear": "unclear",
    "administration": "a public administration",
    "role": "a role one person holds",
    "venue": "the firm's restaurant or shop",
}
USABLE = ("deliverable", "maybe")


def find_button(company_id: int) -> str:
    """Shown under the addresses when one of them bounced."""
    return (
        f"<form method='post' action='/company/{company_id}/replacement' class='inline'>"
        + ui.button("Find a replacement", primary=True)
        + "</form> <span class='small dim'>An address here bounced. This checks the "
          "firm's mail domain, reads its own pages and runs one web search, then "
          "lists what it found for you to accept or refuse.</span>"
    )


def _decision(p: Row) -> str:
    if p["decision"]:
        when = str(p["decided_at"])[:10]
        return f"<span class='small'>{esc(p['decision'])} on {esc(when)}</span>"
    base = f"/proposal/{p['id']}"
    return (
        f"<form method='post' action='{base}/accept' class='inline'>"
        + ui.button("Use this address", primary=True) + "</form> "
        f"<form method='post' action='{base}/refuse' class='inline'>"
        + ui.button("Refuse") + "</form>"
    )


def proposal_row(p: Row) -> str:
    label = FOUND.get(p["found_by"], p["found_by"])
    found = (f"<a href='{esc(p['source_url'])}' rel='noreferrer noopener' "
             f"target='_blank'>{esc(label)}</a>" if p["source_url"] else esc(label))
    return (
        f"<tr><td>{esc(p['address'])}</td>"
        f"<td class='small'>{esc(REACHES.get(p['kind'], p['kind']))}</td>"
        f"<td class='small'>{found}</td>"
        f"<td class='small dim'>{esc(p['note'])}</td>"
        f"<td>{_decision(p)}</td></tr>"
    )


def panel(search: Row | None, proposals: Sequence[Row]) -> str:
    """The last search for this firm and its proposals, or nothing."""
    if search is None and not proposals:
        return ""
    head = ""
    if search is not None:
        when = str(search["searched_at"])[:16].replace("T", " ")
        replaced = ", ".join(search["replaces"] or [])
        head = (f"<p class='small dim'>Searched on {esc(when)}"
                + (f" for {esc(replaced)}" if replaced else "") + ".</p>")
        domains = "".join(
            f"<li><b>{esc(d['domain'])}</b>: {esc(d['why'])}"
            + ("" if d["verdict"] in USABLE else ", so no address at it can work")
            + "</li>"
            for d in search["domains"] or []
        )
        if domains:
            head += f"<ul class='small'>{domains}</ul>"
    table = ui.table(
        ["address", "reaches", "found", "note", ""],
        [proposal_row(p) for p in proposals],
        caption="Addresses proposed to replace one that bounced",
        empty_text="Nothing new was found.",
    )
    tried = "".join(f"<li>{esc(t['what'])}: {esc(t['why'])}</li>"
                    for t in (search["tried"] if search is not None else []) or [])
    more = (f"<details class='small'><summary>Also looked at</summary><ul>{tried}</ul>"
            "</details>" if tried else "")
    return f"<h2 id='replacement'>replacement</h2><div class='card'>{head}{table}{more}</div>"
