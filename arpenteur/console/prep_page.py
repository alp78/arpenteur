"""Interview prep: the vocabulary of the meeting, and what to revise first.

A separate page from the dossier because it is read at a different moment. The
dossier is worked on over weeks; this is opened the evening before, and what it
needs is to be scannable, tickable and short at the top.

So: the agenda first, three to six things, then the glossary grouped by shelf
with the unrevised ones surfacing above the rest. Ticking a term as known is
the only writable field, and a regeneration never touches it, which is what
makes the ticking worth doing.
"""

from __future__ import annotations

import json
from typing import Any

from ..stages.prep import CATEGORIES, CATEGORY_LABELS
from .clients import STYLES as CLIENT_STYLES
from ..kernel.db import Connection, Row
from .render import esc, page

STYLES = """
.agenda { background:#fff; border:1px solid var(--line); border-left:3px solid var(--accent);
          padding:.9rem 1.2rem; margin-bottom:1.2rem; }
.agenda ol { margin:.5rem 0 0; padding-left:1.3rem; }
.agenda li { margin-bottom:.45rem; }
.agenda li b { display:block; }
.agenda li span { color:var(--dim); font-size:.9rem; }
.orient { background:#fff; border:1px solid var(--line); padding:.9rem 1.2rem;
          margin-bottom:1.2rem; line-height:1.6; max-width:78ch; }
.shelf { margin:1.7rem 0 .5rem; }
.shelf h2 { margin:0 0 .1rem; }
.shelf p { margin:0; color:var(--dim); font-size:.86rem; }
.term { background:#fff; border:1px solid var(--line); margin-bottom:.5rem;
        display:grid; grid-template-columns:auto 1fr; }
.term.known { opacity:.55; }
.term .tick { padding:.75rem .5rem .75rem .9rem; }
.term .body { padding:.7rem 1rem .7rem 0; }
.term h3 { margin:0; font-size:1rem; display:flex; gap:.5rem; align-items:baseline;
           flex-wrap:wrap; }
.term h3 .exp { font-weight:400; color:var(--dim); font-size:.86rem; }
.term dl { display:grid; grid-template-columns:7.5rem 1fr; gap:.1rem .8rem;
           margin:.45rem 0 0; font-size:.9rem; }
.term dt { color:var(--dim); font-size:.7rem; text-transform:uppercase;
           letter-spacing:.05em; padding-top:.22rem; }
.term dd { margin:0; }
.term dd.ask { color:var(--accent); }
.term dd.ref { font-family:ui-monospace,Menlo,monospace; font-size:.82rem; }
.prio { font-size:.66rem; font-weight:700; text-transform:uppercase;
        letter-spacing:.05em; padding:.05rem .4rem; border-radius:2px;
        border:1px solid var(--line); color:var(--dim); }
.prio.must { background:var(--accent); border-color:var(--accent); color:#fff; }
.prio.should { border-color:#d9b483; color:#8a5a1f; background:#fdf3e6; }
.counts { display:flex; gap:1.4rem; flex-wrap:wrap; margin-bottom:1rem; }
.counts div { display:flex; flex-direction:column; }
.counts b { font-size:1.4rem; font-variant-numeric:tabular-nums; }
.counts span { font-size:.7rem; text-transform:uppercase; letter-spacing:.05em;
               color:var(--dim); }
"""


def shell(body: str, chrome: Any = None) -> str:
    return page("arpenteur", body, chrome, css=CLIENT_STYLES + STYLES)


def _agenda(row: Row | None) -> str:
    if row is None:
        return ""
    try:
        items = row["agenda_json"] or []
    except (TypeError, ValueError):
        items = []
    if not items:
        return ""
    entries = "".join(
        f'<li><b>{esc(i.get("revise"))}</b><span>{esc(i.get("why"))}</span></li>'
        for i in items
        if isinstance(i, dict)
    )
    return (
        "<div class='agenda'><b>Revise in this order</b>"
        "<p class='hint' style='margin:.1rem 0 0'>Twenty terms is a list. Three "
        "hours before a meeting the useful thing is knowing which four decide "
        "the conversation.</p>"
        f"<ol>{entries}</ol></div>"
    )


def _term(row: Row) -> str:
    priority = row["priority"] or "nice"
    facts = []
    for label, key, css in (
        ("What it is", "what_it_is", ""),
        ("Why here", "why_here", ""),
        ("Know cold", "must_know", ""),
        ("Ask them", "question", "ask"),
        ("Check", "reference", "ref"),
    ):
        value = row[key]
        if value:
            facts.append(f"<dt>{label}</dt><dd class='{css}'>{esc(value)}</dd>")
    note = (
        f"<dt>Your note</dt><dd>{esc(row['note'])}</dd>" if row["note"] else ""
    )
    # Not shown when the term already carries it. Asked for the expansion of a
    # term written `GA4 (Google Analytics 4)`, a model obligingly gives
    # `Google Analytics 4`, and printing both reads as a stutter.
    expansion = row["expansion"] or ""
    if expansion and expansion.lower() in (row["term"] or "").lower():
        expansion = ""
    return (
        f'<div class="term{" known" if row["known"] else ""}">'
        f'<div class="tick">'
        f'<form method="post" action="/term/{row["id"]}/known">'
        f'<input type="hidden" name="known" value="{0 if row["known"] else 1}">'
        f'<button title="{"Mark as still to revise" if row["known"] else "Mark as known"}">'
        f'{"&#10003;" if row["known"] else "&#9633;"}</button></form></div>'
        f'<div class="body"><h3>{esc(row["term"])}'
        + (f'<span class="exp">{esc(expansion)}</span>' if expansion else "")
        + f'<span class="prio {esc(priority)}">{esc(priority)}</span></h3>'
        f"<dl>{''.join(facts)}{note}</dl></div></div>"
    )


def generate_form(client_id: int, filled: int, model: str, effort: str) -> str:
    blocked = filled == 0
    return (
        (
            "<div class='warn'>The dossier is empty, so there is no vocabulary "
            "to extract. Fill in a section on the "
            f"<a href='/client/{client_id}/dossier'>dossier</a> first.</div>"
            if blocked else ""
        )
        + f'<form class="acts" method="post" action="/client/{client_id}/prep">'
        '<div class="act"><button class="primary"'
        + (" disabled" if blocked else "")
        + ">Extract the vocabulary</button>"
        "<span class='hint'>Reads the whole dossier and every readable document "
        "and returns every term, acronym, technology, standard, law, vendor and "
        "project name in it, with what each is, why it appears in "
        "<i>this</i> client's file, what to know without looking it up, and one "
        "question it lets you ask. Re-running adds what is new and refreshes "
        "the rest; anything you have ticked as known stays ticked.</span></div>"
        '<div class="act"><button name="dry_run" value="1"'
        + (" disabled" if blocked else "")
        + ">Show what it would read</button>"
        "<span class='hint'>Costs nothing.</span></div>"
        f'<div class="act"><span class="hint">{esc(model)}'
        + (f", effort {esc(effort)}" if effort else "")
        + ". Set under <a href='/settings'>Settings</a>. This stage is recall "
        "and organisation over material already in front of it, so it does not "
        "use the arbitration model the suggested approach uses.</span></div>"
        "</form>"
    )


def prep_page(
    client: Row,
    company: Row,
    terms: list[Row],
    briefing: Row | None,
    filled: int,
    model: str,
    effort: str,
    note: str = "",
) -> str:
    known = sum(1 for t in terms if t["known"])
    must = sum(1 for t in terms if t["priority"] == "must" and not t["known"])

    shelves = []
    for name, label in CATEGORIES:
        rows = [t for t in terms if (t["category"] or "") == name]
        if not rows:
            continue
        shelves.append(
            f'<div class="shelf"><h2>{esc(name)}</h2><p>{esc(label)}</p></div>'
            + "".join(_term(row) for row in rows)
        )
    loose = [t for t in terms if (t["category"] or "") not in CATEGORY_LABELS]
    if loose:
        shelves.append(
            '<div class="shelf"><h2>other</h2><p>Terms whose shelf has since '
            "been retired.</p></div>" + "".join(_term(row) for row in loose)
        )

    if not terms:
        shelves = [
            "<p class='dim small'>Nothing extracted yet. The button above reads "
            "the dossier and the documents and builds the list.</p>"
        ]

    meta = ""
    if briefing is not None:
        meta = (
            f"<p class='meta'>Last extracted "
            f'{esc((briefing["created_at"] or "")[:16].replace("T", " "))} '
            f'&middot; {esc(briefing["model"] or "?")}'
            + (f' at effort {esc(briefing["effort"])}' if briefing["effort"] else "")
            + f' &middot; inputs {esc(briefing["inputs_hash"] or "?")}</p>'
        )

    orientation = (
        f'<div class="orient">{esc(briefing["orientation"])}</div>'
        if briefing is not None and briefing["orientation"]
        else ""
    )

    return (
        f'<p class="crumbs"><a href="/clients">Clients</a> / '
        f'<a href="/client/{client["id"]}">{esc(company["name"])}</a> / '
        f'<a href="/client/{client["id"]}/dossier">dossier</a> / prep</p>'
        f'<div class="ident"><h1 style="margin:0">Interview prep</h1>'
        f'<span class="code">{esc(client["code"])}</span></div>'
        "<p class='lede'>The vocabulary of the meeting. Every term in this "
        "client's file, what it is, and above all why it is in "
        "<i>their</i> file, which is the part a general glossary cannot give "
        "you and the only part worth reading twice.</p>"
        + (f'<div class="warn">{esc(note)}</div>' if note else "")
        + '<div class="counts">'
        f'<div><b>{len(terms)}</b><span>terms</span></div>'
        f'<div><b>{must}</b><span>to know cold, unticked</span></div>'
        f'<div><b>{known}</b><span>ticked as known</span></div></div>'
        + generate_form(client["id"], filled, model, effort)
        + meta
        + orientation
        + _agenda(briefing)
        + "".join(shelves)
    )
