"""The client dossier, the suggested approach, and the client's own papers.

Three things on one page, because they are one thing in use: what is known,
what follows from it, and what it was built on. Splitting them across three
pages is how a proposal comes to be read without its evidence.

The form follows what the operations page had to learn the hard way. Every
field says what belongs in it, shows a real example of the expected shape, and
every button says exactly what it will do before it is pressed. A field with a
bare label is a field people fill in wrongly and then stop trusting.

One deliberate asymmetry: the dossier is editable everywhere and the approach
is not editable at all. An approach is a record of what was suggested at a
moment, from stated inputs. Editing it in place would destroy the only thing
that makes it worth keeping, which is that it can be compared with what was
actually proposed. Disagree with one and generate another; both stay.
"""

from __future__ import annotations

import json
from typing import Any

from ..kernel.db import Connection, Row
from .. import dossier as vocab
from ..attachments import KINDS as ATTACHMENT_KINDS
from ..attachments import KIND_LABELS
from .clients import STYLES as CLIENT_STYLES
from .render import esc, page

STYLES = """
.sect { background:#fff; border:1px solid var(--line); margin-bottom:.9rem; }
.sect > header { padding:.75rem 1rem .1rem; border:0; position:static;
                  display:block; background:#fff; }
.sect h3 { margin:0; font-size:1rem; }
.sect .what { margin:.2rem 0 0; color:var(--dim); max-width:70ch; font-size:.86rem; }
.sect .inner { padding:.5rem 1rem .9rem; }
.sect textarea { width:100%; font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
.sect .srcline { display:flex; gap:.5rem; align-items:center; margin-top:.4rem; }
.sect .srcline label { font-size:.72rem; text-transform:uppercase;
                        letter-spacing:.05em; color:var(--dim); font-weight:600;
                        white-space:nowrap; }
.sect .srcline input { flex:1; }
.sect footer { padding:.5rem 1rem; border-top:1px solid var(--line);
                background:var(--bg); display:flex; gap:.8rem; align-items:center;
                flex-wrap:wrap; }
.grp { margin:1.6rem 0 .6rem; }
.grp h2 { margin:0 0 .15rem; }
.grp p { margin:0 0 .8rem; color:var(--dim); max-width:70ch; font-size:.88rem; }
.filled { font-size:.72rem; text-transform:uppercase; letter-spacing:.05em;
           color:#1d6b2f; font-weight:700; }
.empty { font-size:.72rem; text-transform:uppercase; letter-spacing:.05em;
          color:var(--dim); font-weight:700; }
.adv { background:#fff; border:1px solid var(--line); padding:1rem 1.2rem;
        margin-bottom:1rem; }
.adv h3 { margin:1.1rem 0 .4rem; font-size:.78rem; text-transform:uppercase;
           letter-spacing:.06em; color:var(--dim); }
.adv h3:first-child { margin-top:0; }
.adv .lead { font-size:1rem; line-height:1.6; }
.adv .open { background:var(--bg); border-left:3px solid var(--accent);
              padding:.6rem .9rem; white-space:pre-wrap; }
.pt { border-left:2px solid var(--line); padding:.15rem 0 .5rem .8rem; margin:.6rem 0; }
.pt b { display:block; }
.pt .why { color:#3a352e; font-size:.9rem; }
.pt .ask { color:var(--accent); font-size:.9rem; margin-top:.2rem; }
.pt .ev { color:var(--dim); font-size:.78rem; margin-top:.2rem; }
table.price td.num { text-align:right; font-variant-numeric:tabular-nums;
                      white-space:nowrap; }
table.price td.none { color:#8a3a2c; font-size:.85rem; }
.meta { font-size:.78rem; color:var(--dim); }
.docs td.f { font-weight:600; }
.drop { border:1px dashed var(--line); background:#fff; padding:.9rem 1.1rem; }
.two { display:grid; grid-template-columns:1fr 1fr; gap:.6rem; }
@media (max-width:760px) { .two { grid-template-columns:1fr; } }
"""


def shell(body: str, chrome: Any = None) -> str:
    """This page borrows the client section's furniture, so it takes its
    stylesheet too rather than restating the breadcrumbs and the code chip."""
    return page("arpenteur", body, chrome, css=CLIENT_STYLES + STYLES)


def _money(value: Any, currency: str) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):,.0f} {currency or 'CHF'}".replace(",", "'")
    except (TypeError, ValueError):
        return esc(str(value))


def _rows(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return []
    return [row for row in parsed if isinstance(row, dict)] if isinstance(parsed, list) else []


def _strings(value: Any) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return []
    return [str(v) for v in parsed] if isinstance(parsed, list) else []


# --- the dossier form ---------------------------------------------------------


def section_block(section: vocab.Section, row: dict[str, Any] | None) -> str:
    body = (row or {}).get("body") or ""
    source = (row or {}).get("source") or ""
    when = ((row or {}).get("updated_at") or "")[:10]
    state = (
        f'<span class="filled">filled {esc(when)}</span>'
        if body.strip()
        else '<span class="empty">empty</span>'
    )
    return (
        f'<form class="sect" method="post" action="/dossier/{{cid}}/section">'
        f'<input type="hidden" name="section" value="{esc(section.name)}">'
        f"<header><h3>{esc(section.title)} {state}</h3>"
        f'<p class="what">{esc(section.what)}</p></header>'
        f'<div class="inner">'
        f'<textarea name="body" rows="{section.rows}" '
        f'placeholder="{esc(section.example)}">{esc(body)}</textarea>'
        f'<div class="srcline"><label for="src-{esc(section.name)}">Source</label>'
        f'<input id="src-{esc(section.name)}" name="source" value="{esc(source)}" '
        f'placeholder="public DNS, 12.08.2026 &middot; said by the director on the call '
        f'&middot; rapport annuel 2025 p. 12 &middot; inferred"></div>'
        f"</div>"
        f'<footer><button class="primary">Save this section</button>'
        f"<span class='hint'>Saves this section only. The source line is what "
        f"separates a fact you can show a client from one you assumed, so it is "
        f"worth the five seconds.</span></footer></form>"
    )


def dossier_form(client_id: int, sections: dict[str, dict[str, Any]]) -> str:
    out = []
    for name, title, why in vocab.GROUPS:
        blocks = "".join(
            section_block(section, sections.get(section.name))
            for section in vocab.group_sections(name)
        )
        out.append(
            f'<div class="grp"><h2>{esc(title)}</h2><p>{esc(why)}</p></div>{blocks}'
        )
    return "".join(out).replace("{cid}", str(client_id))


# --- the suggested approach ---------------------------------------------------


def approach_block(row: Row | None, rate_card: bool) -> str:
    if row is None:
        return (
            '<div class="card"><p>No approach suggested yet.</p>'
            "<p class='dim small'>The button above reads the dossier, the "
            "documents below and your rate card, and returns the conversation: "
            "what to raise, what to offer, what it costs, and what is still "
            "unknown. One model call, no web access, a few centimes.</p></div>"
        )

    read = {}
    try:
        read = row["read_json"] or {}
    except (TypeError, ValueError):
        pass
    documents = read.get("documents") or []
    parts = [
        f"{len(read.get('sections') or [])} dossier sections",
        f"{len(documents)} documents",
        "rate card" if read.get("rate_card") else "NO rate card",
    ]
    accepted = (
        f' &middot; <b>used {esc((row["accepted_at"] or "")[:10])}</b>'
        if row["accepted_at"] else ""
    )

    points = "".join(
        f'<div class="pt"><b>{esc(p.get("point"))}</b>'
        f'<div class="why">{esc(p.get("why"))}</div>'
        f'<div class="ask">Ask: {esc(p.get("ask"))}</div>'
        f'<div class="ev">Evidence: {esc(p.get("evidence"))}</div></div>'
        for p in _rows(row["points_json"])
    )

    services = "".join(
        f'<div class="pt"><b>{esc(s.get("name"))}</b>'
        f'<div class="why">{esc(s.get("what"))}</div>'
        f'<div class="why" style="color:var(--dim)">Why them: {esc(s.get("why_them"))}</div>'
        + (
            f'<div class="ev">First: {esc(s.get("prerequisite"))}</div>'
            if s.get("prerequisite") else ""
        )
        + "</div>"
        for s in _rows(row["services_json"])
    )

    price_rows = []
    for p in _rows(row["pricing_json"]):
        low, high = p.get("low"), p.get("high")
        currency = str(p.get("currency") or "CHF")
        if low is None and high is None:
            figure = (
                '<td class="none" colspan="2">not priced &mdash; '
                "the rate card does not cover it</td>"
            )
        elif high is None or low == high:
            figure = f'<td class="num" colspan="2">{_money(low or high, currency)}</td>'
        else:
            figure = (
                f'<td class="num">{_money(low, currency)}</td>'
                f'<td class="num">to {_money(high, currency)}</td>'
            )
        price_rows.append(
            f'<tr><td>{esc(p.get("item"))}</td>{figure}'
            f'<td class="small">{esc(p.get("unit") or "")}</td>'
            f'<td class="small dim">{esc(p.get("basis") or "")}</td></tr>'
        )
    pricing = (
        '<table class="price"><thead><tr><th>work</th><th colspan="2">range</th>'
        "<th>unit</th><th>from which rate card line</th></tr></thead>"
        f"<tbody>{''.join(price_rows)}</tbody></table>"
        if price_rows
        else "<p class='dim small'>No prices returned.</p>"
    )
    if not read.get("rate_card"):
        pricing = (
            "<div class='warn'>No rate card was set when this ran, so nothing "
            "could be priced. Prices are quoted only from your own card, never "
            "from the model's idea of the market: a figure invented here and "
            "repeated to a client becomes a commitment. Set one under "
            "<a href='/settings'>Settings</a> and generate again.</div>" + pricing
        )

    risks = "".join(f"<li>{esc(r)}</li>" for r in _strings(row["risks_json"]))
    questions = "".join(f"<li>{esc(q)}</li>" for q in _strings(row["questions_json"]))

    accept = (
        ""
        if row["accepted_at"]
        else (
            f'<form class="inline" method="post" action="/approach/{row["id"]}/accept">'
            '<button>Mark as used</button></form> '
            "<span class='hint'>Records that this went into a real proposal. "
            "Advice nobody acted on and advice that was quoted to a client are "
            "different things, and only one should be repeated later.</span>"
        )
    )

    return (
        '<div class="adv">'
        f'<p class="meta">Generated {esc((row["created_at"] or "")[:16].replace("T", " "))} '
        f'&middot; {esc(row["model"] or "?")} &middot; read {esc(" · ".join(parts))} '
        f'&middot; inputs {esc(row["inputs_hash"] or "?")}{accepted}</p>'
        f'<h3>Where they are</h3><p class="lead">{esc(row["situation"])}</p>'
        f'<h3>How to open</h3><div class="open">{esc(row["opening"])}</div>'
        f"<h3>What to raise</h3>{points}"
        f"<h3>What to offer</h3>{services}"
        f"<h3>What it costs</h3>{pricing}"
        + (f"<h3>What could go wrong</h3><ul>{risks}</ul>" if risks else "")
        + (f"<h3>Still unknown</h3><ul>{questions}</ul>" if questions else "")
        + (
            "<p style='margin-top:1rem'>"
            + accept
            + "</p>"
        )
        + "</div>"
    )


def generate_form(client_id: int, filled: int, docs: int, rate_card: bool) -> str:
    blocked = filled == 0 and docs == 0
    warn = (
        "<div class='warn'>Nothing to advise from yet. Fill in at least one "
        "dossier section, or upload a document. Advice generated from a "
        "register entry alone is a horoscope.</div>"
        if blocked else ""
    )
    return (
        warn
        + f'<form class="acts" method="post" action="/dossier/{client_id}/advise">'
        '<div class="act"><button class="primary"'
        + (" disabled" if blocked else "")
        + ">Suggest an approach</button>"
        "<span class='hint'>Reads the dossier, every readable document below "
        "and your rate card, and returns discussion points, the services that "
        "fit, a price range for each, the risks and what is still unknown. One "
        "model call, no web search. Nothing is sent to the client and nothing "
        "is overwritten: each run is a new record you can compare.</span></div>"
        '<div class="act"><button name="dry_run" value="1"'
        + (" disabled" if blocked else "")
        + ">Show what it would read</button>"
        "<span class='hint'>Costs nothing. Lists the sections and documents "
        "that would go into the prompt, and their size, so a surprising answer "
        "can be traced to a surprising input.</span></div>"
        + (
            ""
            if rate_card
            else '<div class="act"><span class="hint" style="color:#8a3a2c">'
                 "No rate card is set, so the work will be identified with no "
                 "prices on it. <a href='/settings'>Set one</a> first if you "
                 "want figures.</span></div>"
        )
        + "</form>"
    )


# --- the client's papers ------------------------------------------------------


def upload_form(client_id: int, projects: list[Row]) -> str:
    kinds = "".join(
        f'<option value="{esc(value)}">{esc(label)}</option>'
        for value, label in ATTACHMENT_KINDS
    )
    options = "".join(
        f'<option value="{p["id"]}">{esc(p["code"])} &mdash; {esc(p["name"])}</option>'
        for p in projects
    )
    return (
        '<form class="drop" method="post" enctype="multipart/form-data" '
        f'action="/dossier/{client_id}/documents">'
        "<div class='two'>"
        "<div><label class='small' for='file'><b>File</b></label><br>"
        "<input id='file' type='file' name='file' required>"
        "<p class='hint'>PDF, Word and plain text are read into searchable "
        "text automatically. Anything else is stored and can be opened by "
        "hand. Spreadsheets read far better if exported as CSV first, and a "
        "scanned page holds no text at all: ask for the file it was printed "
        "from. Up to 25 MB.</p></div>"
        f"<div><label class='small' for='kind'><b>What it is</b></label><br>"
        f"<select id='kind' name='kind'>{kinds}</select>"
        "<p class='hint'>Used when the document is put in front of the model: "
        "a contract and a brochure carry very different weight in a proposal, "
        "and it should not have to guess which it is holding.</p>"
        f"<label class='small' for='pid'><b>Project</b></label><br>"
        f"<select id='pid' name='project_id'>"
        f"<option value=''>The relationship, not a project</option>{options}</select>"
        "</div></div>"
        "<label class='small' for='dnote'><b>Note</b></label><br>"
        "<input id='dnote' name='note' style='width:100%' "
        "placeholder='Sent by the director 14.08.2026 after the kickoff. Pages 8-11 are the IT budget.'>"
        "<p class='hint'>Where it came from and what to look at. Shown to the "
        "model alongside the document.</p>"
        "<p><button class='primary'>Store and read it</button> "
        "<span class='hint'>Stores the file under its own content digest, so "
        "the same document sent twice is one entry rather than two, extracts "
        "the text, and makes it available to the approach above.</span></p>"
        "</form>"
    )


def documents_table(rows: list[Row]) -> str:
    if not rows:
        return (
            "<p class='dim small'>No documents yet. Anything the client sends "
            "goes here: the accounts, a supplier's proposal, an internal "
            "policy, a system inventory. What is stored here is what the "
            "suggested approach is allowed to read.</p>"
        )
    out = []
    for row in rows:
        if row["extract_error"]:
            state = (
                f'<span style="color:#8a3a2c">not readable</span>'
                f'<div class="hint">{esc(row["extract_error"])}</div>'
            )
        else:
            state = (
                f'<span class="dim">{row["text_chars"] or 0:,} characters</span> '
                f'<a href="/attachment/{row["id"]}/text">read</a>'
            )
        out.append(
            f'<tr><td class="f">{esc(row["filename"])}'
            + (f'<div class="hint">{esc(row["note"])}</div>' if row["note"] else "")
            + "</td>"
            f'<td class="small">{esc(KIND_LABELS.get(row["kind"] or "", row["kind"] or "—"))}</td>'
            f'<td class="small nowrap">{row["bytes"] / 1024:,.0f} kB</td>'
            f'<td class="small">{state}</td>'
            f'<td class="small dim nowrap">{esc((row["created_at"] or "")[:10])}</td>'
            f'<td class="small dim" title="content digest">{esc((row["sha256"] or "")[:12])}</td>'
            f'<td class="small"><details class="del"><summary>delete</summary>'
            f'<form method="post" action="/attachment/{row["id"]}/delete">'
            "<input name='reason' placeholder='Why, for the tombstone'>"
            "<button>Delete document</button>"
            "<span class='hint'>Removes the entry, leaves a note saying what "
            "was removed and why, and erases the stored file unless another "
            "client holds the same bytes.</span></form></details></td>"
            "</tr>"
        )
    return (
        '<table class="docs"><thead><tr><th>document</th><th>what it is</th>'
        "<th>size</th><th>text</th><th>added</th><th>digest</th><th></th>"
        f"</tr></thead><tbody>{''.join(out)}</tbody></table>"
    )


# --- the page -----------------------------------------------------------------


def dossier_page(
    client: Row,
    company: Row,
    sections: dict[str, dict[str, Any]],
    approach: Row | None,
    older: list[Row],
    papers: list[Row],
    projects: list[Row],
    rate_card: bool,
    note: str = "",
) -> str:
    filled = len(vocab.filled(sections))
    history = ""
    if older:
        history = (
            "<h2>earlier suggestions</h2>"
            "<p class='dim small'>Kept rather than replaced. When the client "
            "asks in six months why a number was what it was, the answer has "
            "to still exist along with what it was computed from.</p>"
            + "".join(
                f'<details class="card"><summary>'
                f'{esc((row["created_at"] or "")[:16].replace("T", " "))} '
                f'&middot; inputs {esc(row["inputs_hash"] or "?")}'
                + (" &middot; <b>used</b>" if row["accepted_at"] else "")
                + f"</summary>{approach_block(row, rate_card)}</details>"
                for row in older
            )
        )

    return (
        f'<p class="crumbs"><a href="/clients">Clients</a> / '
        f'<a href="/client/{client["id"]}">{esc(company["name"])}</a> / dossier</p>'
        f'<div class="ident"><h1 style="margin:0">{esc(company["name"])}</h1>'
        f'<span class="code">{esc(client["code"])}</span></div>'
        "<p class='lede'>What is known about this client, what follows from it, "
        "and what it was built on. The register says what a company is; this "
        "says what it runs, who decides, what is exposed and what has already "
        "been tried, none of which is in any register.</p>"
        + (f'<div class="warn">{esc(note)}</div>' if note else "")
        + f'<p class="meta">{filled} of {len(vocab.SECTIONS)} sections filled '
          f'&middot; {len(papers)} documents held &middot; '
          f'<a href="/client/{client["id"]}/prep">interview prep</a></p>'
        + "<h2>suggested approach</h2>"
        + generate_form(client["id"], filled, len(papers), rate_card)
        + approach_block(approach, rate_card)
        + history
        + "<h2>documents from the client</h2>"
        + documents_table(papers)
        + upload_form(client["id"], projects)
        + "<h2>the dossier</h2>"
        + dossier_form(client["id"], sections)
    )
