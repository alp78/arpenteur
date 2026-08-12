"""HTML, written as functions rather than templates.

No Jinja. The console is a handful of pages, the markup is generated from
sqlite3.Row objects, and a template language would add a directory, a loader
and a second syntax to learn for no gain at this size. If the console grows
past a dozen views this is the first thing to reconsider.

Everything user-supplied goes through `esc`. Company names come from a public
register and notes are typed by hand, so neither is trusted: a company called
`<script>` is a perfectly legal Swiss registration.
"""

from __future__ import annotations

import html
import json
import sqlite3
from typing import Any, Iterable

# HTMX from a CDN, and about sixty lines of CSS. No build step, no bundler, no
# node_modules. The whole console is server-rendered and the only client-side
# behaviour is swapping fragments in, which is what HTMX is for.
HEAD = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<script src="https://unpkg.com/htmx.org@2.0.4"></script>
<style>
:root {{ --line:#d8d4cc; --ink:#1c1a17; --dim:#6b665e; --bg:#faf8f5; --accent:#7a1f1f; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
        color:var(--ink); background:var(--bg); }}
header {{ border-bottom:1px solid var(--line); padding:.7rem 1.2rem; display:flex;
          gap:1.2rem; align-items:baseline; background:#fff; position:sticky; top:0; z-index:5; }}
header a {{ color:var(--dim); text-decoration:none; }}
header a:hover, header a.on {{ color:var(--accent); }}
header .brand {{ font-weight:600; color:var(--ink); letter-spacing:.02em; }}
main {{ padding:1.2rem; max-width:1100px; margin:0 auto; }}
h1 {{ font-size:1.35rem; margin:0 0 .2rem; }}
h2 {{ font-size:1rem; margin:1.6rem 0 .5rem; color:var(--dim);
      text-transform:uppercase; letter-spacing:.06em; font-weight:600; }}
table {{ border-collapse:collapse; width:100%; background:#fff; }}
th,td {{ text-align:left; padding:.45rem .6rem; border-bottom:1px solid var(--line);
         vertical-align:top; }}
th {{ font-size:.78rem; text-transform:uppercase; letter-spacing:.05em; color:var(--dim); }}
tr:hover td {{ background:#fffdf8; }}
a {{ color:var(--accent); }}
.dim {{ color:var(--dim); }}
.small {{ font-size:.85rem; }}
.card {{ background:#fff; border:1px solid var(--line); padding:.9rem 1.1rem; margin-bottom:1rem; }}
.pill {{ display:inline-block; padding:.05rem .5rem; border:1px solid var(--line);
         border-radius:1rem; font-size:.78rem; margin:0 .25rem .25rem 0; background:#fff; }}
.pill.manual {{ border-color:var(--accent); color:var(--accent); }}
.band-high {{ color:#1d6b2f; font-weight:600; }}
.band-medium {{ color:#8a6d1f; }}
.band-low, .band-excluded {{ color:var(--dim); }}
form.inline {{ display:inline; }}
input,select,textarea,button {{ font:inherit; padding:.3rem .45rem; border:1px solid var(--line);
                                background:#fff; color:inherit; }}
textarea {{ width:100%; min-height:4.5rem; }}
button {{ cursor:pointer; }}
button.primary {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
.filters {{ display:flex; gap:.5rem; flex-wrap:wrap; align-items:end; margin-bottom:1rem; }}
.note {{ border-left:2px solid var(--line); padding:.15rem 0 .15rem .7rem; margin:.6rem 0; }}
.warn {{ background:#fff7f0; border-left:3px solid var(--accent); padding:.6rem .9rem;
         margin:1rem 0; font-size:.9rem; }}
.controls {{ display:flex; gap:.8rem; flex-wrap:wrap; align-items:end; margin:.7rem 0; }}
.ctl {{ display:flex; flex-direction:column; gap:.15rem; }}
.ctl input[type=text] {{ width:11rem; }}
.ctl input[type=number] {{ width:6rem; }}
label.check {{ display:flex; gap:.35rem; align-items:center; white-space:nowrap; }}
label.check input {{ margin:0; }}
.cost {{ margin-top:.15rem; }}
.state-running {{ color:var(--accent); font-weight:600; }}
.state-error {{ color:#a3341c; font-weight:600; }}
.state-done {{ color:var(--dim); }}
pre.log {{ background:#fff; border:1px solid var(--line); padding:.8rem 1rem; margin:0;
           max-height:70vh; overflow:auto; font-size:.82rem; line-height:1.5;
           white-space:pre-wrap; word-break:break-word; }}
</style></head><body>
<header>
  <span class="brand">ARPENTEUR</span>
  <a href="/">companies</a>
  <a href="/pipeline">pipeline</a>
  <a href="/run">run</a>
  <a href="/runs">runs</a>
  <span class="dim small" style="margin-left:auto">{territory}</span>
</header><main>
"""

FOOT = "</main></body></html>"


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def page(title: str, body: str, territory: str = "") -> str:
    return HEAD.format(title=esc(title), territory=esc(territory)) + body + FOOT


def as_list(value: Any) -> list[str]:
    """JSON columns come back as text, and a half-written row as None."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(v) for v in parsed] if isinstance(parsed, list) else []


def pills(items: Iterable[str], manual: Iterable[str] = ()) -> str:
    manual_set = set(manual)
    return "".join(
        f'<span class="pill{" manual" if item in manual_set else ""}">{esc(item)}</span>'
        for item in items
    )


def band(row: sqlite3.Row | None) -> str:
    if row is None or row["score"] is None:
        return '<span class="dim">&mdash;</span>'
    name = row["band"] or "low"
    mark = " *" if row["source"] == "manual" else ""
    return f'<span class="band-{esc(name)}">{row["score"]}{esc(mark)}</span>'


# --- the company list ---------------------------------------------------------


def company_rows(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return (
            '<tr><td colspan="6" class="dim">Nothing matches. Widen the filter, '
            "or run a stage that would fill this in.</td></tr>"
        )
    out = []
    for row in rows:
        segments = pills(as_list(row["segments"]))
        state = row["state"] or ""
        out.append(
            f'<tr><td><a href="/company/{row["id"]}">{esc(row["name"])}</a>'
            f'<div class="dim small">{esc(row["uid"])}</div></td>'
            f'<td>{esc(row["canton"])}</td>'
            f'<td class="small">{segments}</td>'
            f'<td>{band(row)}</td>'
            f'<td class="small">{esc(row["url"] or "")}</td>'
            f'<td class="small">{esc(state)}</td></tr>'
        )
    return "".join(out)


def company_table(rows: list[sqlite3.Row], total: int) -> str:
    return (
        f'<p class="dim small">{total} companies match.</p>'
        '<table><thead><tr><th>company</th><th>canton</th><th>segments</th>'
        '<th>fit</th><th>website</th><th>state</th></tr></thead>'
        f"<tbody>{company_rows(rows)}</tbody></table>"
    )


def filter_form(
    *, q: str, canton: str, segment: str, band_name: str, state: str,
    cantons: list[str], segments: list[str], states: list[str],
) -> str:
    def options(values: list[str], chosen: str, label: str) -> str:
        out = [f'<option value="">{esc(label)}</option>']
        for value in values:
            sel = " selected" if value == chosen else ""
            out.append(f'<option value="{esc(value)}"{sel}>{esc(value)}</option>')
        return "".join(out)

    return (
        '<form class="filters" hx-get="/companies" hx-target="#list" hx-trigger="submit, change">'
        f'<label>search<br><input name="q" value="{esc(q)}" placeholder="name or UID"></label>'
        f'<label>canton<br><select name="canton">{options(cantons, canton, "any")}</select></label>'
        f'<label>segment<br><select name="segment">{options(segments, segment, "any")}</select></label>'
        '<label>fit<br><select name="band">'
        + options(["high", "medium", "low", "excluded"], band_name, "any")
        + "</select></label>"
        f'<label>state<br><select name="state">{options(states, state, "any")}</select></label>'
        "<button>filter</button></form>"
    )


# --- one company --------------------------------------------------------------


def company_page(
    company: sqlite3.Row,
    segments: list[sqlite3.Row],
    officers: list[sqlite3.Row],
    site: sqlite3.Row | None,
    fit: sqlite3.Row | None,
    contacts: list[sqlite3.Row],
    notes: list[sqlite3.Row],
    prospect: sqlite3.Row | None,
    publications: list[sqlite3.Row],
    vocabulary: list[str],
    states: list[str],
    attribution: str,
) -> str:
    out = [
        f"<h1>{esc(company['name'])}</h1>",
        f'<p class="dim small">{esc(company["uid"])} &middot; '
        f'{esc(company["legal_form_label"])} &middot; '
        f'{esc(company["legal_seat"])} ({esc(company["canton"])}) &middot; '
        f'{esc(company["status"])}</p>',
    ]

    if company["status"] and company["status"] != "ACTIVE":
        out.append(
            '<div class="warn">This company is winding up or has been struck off '
            "the register. It is kept for the record and excluded from the stages "
            "that cost money.</div>"
        )

    out.append(_fit_card(fit))
    out.append(_prospect_card(company["id"], prospect, states))
    out.append(_segments_card(company["id"], segments, vocabulary))

    if company["purpose"]:
        out.append(
            f'<h2>registered purpose</h2><div class="card">{esc(company["purpose"])}</div>'
        )

    if officers:
        rows = "".join(
            f"<tr><td>{esc(o['full_name'])}</td><td>{esc(o['role'])}</td>"
            f"<td class='small dim'>{esc(o['signature'] or '')}</td></tr>"
            for o in officers
        )
        out.append(
            "<h2>officers</h2><table><tbody>" + rows + "</tbody></table>"
            '<p class="dim small">Parsed from FOSC publications, not a registry '
            "field. Verify against the certified excerpt before relying on it.</p>"
        )

    out.append(_site_card(site))
    out.append(_contacts_card(contacts))
    out.append(_notes_card(company["id"], notes))

    if publications:
        items = "".join(
            f'<div class="note"><div class="dim small">{esc(p["sogc_date"])}</div>'
            f'{esc(p["message"][:400])}</div>'
            for p in publications[:8]
        )
        out.append(f"<h2>register notices</h2><div class='card small'>{items}</div>")

    out.append(f'<p class="dim small" style="white-space:pre-line">{esc(attribution)}</p>')
    return "".join(out)


def _fit_card(fit: sqlite3.Row | None) -> str:
    if fit is None:
        return (
            '<h2>fit</h2><div class="card dim">Not scored yet. '
            "<code>arpenteur match</code></div>"
        )
    against = as_list(fit["against"])
    matched = as_list(fit["matched"])
    body = [
        f'<p><span class="band-{esc(fit["band"])}" style="font-size:1.5rem">'
        f'{fit["score"]}</span> <span class="dim">/ 100 &middot; {esc(fit["band"])}'
        f'{" &middot; set by hand" if fit["source"] == "manual" else ""}</span></p>'
    ]
    if fit["reasoning"]:
        body.append(f"<p>{esc(fit['reasoning'])}</p>")
    if matched:
        body.append(f'<p class="small"><b>matched</b><br>{pills(matched)}</p>')
    if against:
        body.append(
            '<p class="small"><b>against</b><br>'
            + "<br>".join(esc(a) for a in against)
            + "</p>"
        )
    if fit["advice"]:
        body.append(
            "<h2 style='margin-top:1rem'>suggested opening</h2>"
            f'<div style="white-space:pre-line">{esc(fit["advice"])}</div>'
            '<p class="dim small">A draft for a human to rewrite. Nothing here '
            "sends, and this tool has no send path at all.</p>"
        )
    return f'<h2>fit</h2><div class="card">{"".join(body)}</div>'


def _prospect_card(company_id: int, prospect: sqlite3.Row | None, states: list[str]) -> str:
    current = prospect["state"] if prospect else "new"
    options = "".join(
        f'<option value="{esc(s)}"{" selected" if s == current else ""}>{esc(s)}</option>'
        for s in states
    )
    return (
        '<h2>pipeline</h2><div class="card" id="prospect">'
        f'<form hx-post="/company/{company_id}/prospect" hx-target="#prospect" '
        'hx-swap="outerHTML" class="filters" style="margin:0">'
        f'<label>state<br><select name="state">{options}</select></label>'
        f'<label>owner<br><input name="owner" value="{esc(prospect["owner"] if prospect else "")}"></label>'
        f'<label>next action<br><input name="next_action" '
        f'value="{esc(prospect["next_action"] if prospect else "")}"></label>'
        f'<label>when<br><input name="next_at" type="date" '
        f'value="{esc((prospect["next_at"] or "")[:10] if prospect else "")}"></label>'
        '<button class="primary">save</button></form></div>'
    )


def _segments_card(
    company_id: int, segments: list[sqlite3.Row], vocabulary: list[str]
) -> str:
    held = {row["segment"]: row for row in segments}
    boxes = []
    for name in vocabulary:
        row = held.get(name)
        checked = " checked" if row is not None else ""
        mark = ""
        if row is not None:
            mark = " *" if row["source"] == "manual" else ""
        boxes.append(
            f'<label class="pill{" manual" if mark else ""}">'
            f'<input type="checkbox" name="segment" value="{esc(name)}"{checked}> '
            f"{esc(name)}{mark}</label> "
        )
    return (
        '<h2>segments</h2><div class="card" id="segments">'
        f'<form hx-post="/company/{company_id}/segments" hx-target="#segments" '
        'hx-swap="outerHTML">'
        + "".join(boxes)
        + '<div style="margin-top:.6rem"><button class="primary">save</button> '
        '<span class="dim small">A change here is marked as yours and survives '
        "re-running the classifier. * means already set by hand.</span></div>"
        "</form></div>"
    )


def _site_card(site: sqlite3.Row | None) -> str:
    if site is None:
        return (
            '<h2>web research</h2><div class="card dim">Not researched. '
            "<code>arpenteur enrich</code></div>"
        )
    body = [
        f'<p><a href="{esc(site["url"])}" target="_blank" rel="noreferrer">'
        f'{esc(site["url"] or "no site found")}</a> '
        f'<span class="dim small">{esc(site["status"])} &middot; '
        f'confidence {site["confidence"]}</span></p>'
    ]
    if site["status"] != "found":
        body.append(
            '<p class="dim small">Not confirmed as this company, so no address '
            "from it has been recorded.</p>"
        )
    if site["summary"]:
        body.append(f"<p>{esc(site['summary'])}</p>")
    for column, label in (
        ("signals", "signals"),
        ("offerings", "offerings"),
        ("partnerships", "partnerships"),
        ("tech_stack", "technology"),
    ):
        items = as_list(site[column])
        if items:
            body.append(f'<p class="small"><b>{label}</b><br>{pills(items)}</p>')
    return f'<h2>web research</h2><div class="card">{"".join(body)}</div>'


def contact_row(c: sqlite3.Row) -> str:
    """One contact, as the page draws it and as the suppress route redraws it.

    One function rather than two matching string literals: the route swaps
    this row in place, so a column added here and forgotten there produces a
    table that changes shape when a button is pressed.

    The source is a link because that is what `source_url` is for. Deciding
    months later whether to keep suppressing an address means looking at the
    page that published it, and a row that only says "published" cannot
    answer where.
    """
    state = "suppressed" if c["suppressed"] else ""
    source = (
        f"<a href='{esc(c['source_url'])}' rel='noreferrer noopener' "
        f"target='_blank'>{esc(c['method'])}</a>"
        if c["source_url"]
        else esc(c["method"])
    )
    return (
        f"<tr><td>{esc(c['value'])}</td><td class='small dim'>{source}</td>"
        f"<td class='small'>{esc(state)}</td>"
        f"<td><form class='inline' hx-post='/contact/{c['id']}/suppress' "
        "hx-target='closest tr' hx-swap='outerHTML'>"
        f"<button>{'restore' if c['suppressed'] else 'suppress'}</button>"
        "</form></td></tr>"
    )


def _contacts_card(contacts: list[sqlite3.Row]) -> str:
    if not contacts:
        return ""
    rows = "".join(contact_row(c) for c in contacts)
    return f"<h2>contacts</h2><table><tbody>{rows}</tbody></table>"


def _notes_card(company_id: int, notes: list[sqlite3.Row]) -> str:
    items = "".join(
        f'<div class="note"><div class="dim small">{esc(n["occurred_at"][:10])} '
        f'&middot; {esc(n["kind"])}{" &middot; " + esc(n["author"]) if n["author"] else ""}</div>'
        f'<div style="white-space:pre-line">{esc(n["body"])}</div></div>'
        for n in notes
    ) or '<p class="dim small">No notes yet.</p>'
    kinds = "".join(
        f'<option value="{k}">{k}</option>'
        for k in ("note", "call", "meeting", "email")
    )
    return (
        '<h2>notes</h2><div class="card" id="notes">'
        f'<form hx-post="/company/{company_id}/notes" hx-target="#notes" hx-swap="outerHTML">'
        '<textarea name="body" placeholder="What happened, and what you thought."></textarea>'
        f'<div class="filters" style="margin:.5rem 0 0"><select name="kind">{kinds}</select>'
        '<input name="occurred_at" type="date"><input name="author" placeholder="who">'
        '<button class="primary">add</button></div></form>'
        f"<div style='margin-top:.8rem'>{items}</div></div>"
    )


# --- runs ---------------------------------------------------------------------


def runs_table(rows: list[sqlite3.Row]) -> str:
    """The table alone, which is what the poll replaces.

    Separate from the page so the refresh swaps exactly this element. It
    carries its own hx-get, so each replacement re-arms the next poll.
    """
    out = []
    for row in rows:
        state = "error" if row["error"] else ("done" if row["finished_at"] else "running")
        detail = row["error"] or row["stats_json"] or ""
        out.append(
            f"<tr><td class='small'>{esc(row['started_at'][:19])}</td>"
            f"<td><a href='/runs/{row['id']}'>{esc(row['stage'])}</a></td>"
            f"<td class='state-{state}'>{esc(state)}</td>"
            f"<td class='small dim'>{esc(row['started_by'] or '')}</td>"
            f"<td class='small dim'>{esc(detail[:160])}</td></tr>"
        )
    body = "".join(out) or (
        '<tr><td colspan="5" class="dim">Nothing has been run yet.</td></tr>'
    )
    return (
        '<table id="runs" hx-get="/runs/rows" hx-trigger="every 3s" hx-swap="outerHTML">'
        "<thead><tr><th>started</th><th>stage</th><th>state</th><th>by</th>"
        "<th>result</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def runs_page(rows: list[sqlite3.Row]) -> str:
    return (
        "<h1>Runs</h1>"
        "<p class='dim small'>Every stage records what it did. This view refreshes "
        "itself while a run is in progress. Open one to read it line by line.</p>"
        + runs_table(rows)
    )


# --- starting a stage ---------------------------------------------------------


def _control(field: Any, stage_name: str) -> str:
    """One labelled control. `field` is a runner.Field."""
    ident = f"{stage_name}-{field.name}"
    if field.kind == "bool":
        checked = " checked" if field.default else ""
        control = (
            f"<label class='check'><input type='checkbox' name='{esc(field.name)}' "
            f"id='{esc(ident)}' value='1'{checked}> {esc(field.label)}</label>"
        )
        note = f"<span class='dim small'> {esc(field.help)}</span>" if field.help else ""
        return f"<div class='ctl'>{control}{note}</div>"
    if field.kind == "choice":
        options = "".join(
            f"<option value='{esc(c)}'>{esc(c or 'any')}</option>" for c in field.choices
        )
        inner = f"<select name='{esc(field.name)}' id='{esc(ident)}'>{options}</select>"
    else:
        kind = "number" if field.kind == "int" else "text"
        inner = (
            f"<input type='{kind}' name='{esc(field.name)}' id='{esc(ident)}' "
            f"placeholder='{esc(field.help)}'>"
        )
    return (
        f"<div class='ctl'><label for='{esc(ident)}' class='small dim'>"
        f"{esc(field.label)}</label>{inner}</div>"
    )


def stage_form(stage: Any, busy: int | None) -> str:
    """The start form for one stage. `stage` is a runner.Stage."""
    controls = "".join(_control(f, stage.name) for f in stage.fields)
    disabled = " disabled" if busy else ""
    button = (
        f"<button class='primary'{disabled}>run {esc(stage.name)}</button>"
        if not busy
        else f"<button disabled>run {esc(stage.name)}</button>"
        f"<span class='dim small'> run {busy} is still going</span>"
    )
    return (
        f"<form class='card' method='post' action='/run/{esc(stage.name)}'>"
        f"<b>{esc(stage.name)}</b> <span class='dim small'>{esc(stage.summary)}</span>"
        f"<div class='dim small cost'>{esc(stage.cost)}</div>"
        f"<div class='controls'>{controls}</div>{button}</form>"
    )


def start_page(stages: Any, busy: int | None, note: str = "") -> str:
    forms = "".join(stage_form(stage, busy) for stage in stages)
    banner = f"<div class='warn'>{esc(note)}</div>" if note else ""
    return (
        "<h1>Run a stage</h1>"
        "<p class='dim small'>One at a time. The paid stages will not start without "
        "a limit or a named list, and every stage that can be run dry offers it "
        "first. Nothing here sends anything to anybody.</p>"
        + banner + forms
    )


# --- watching one run ---------------------------------------------------------


def run_log(rows: list[sqlite3.Row], run_id: int, done: bool, after: int) -> str:
    """The lines, followed by the element that asks for the ones after them.

    The tail replaces itself, carrying the sequence number it has reached, so
    each poll fetches only what is new. A run emitting sixty thousand lines
    would otherwise re-send all of them every two seconds.

    When the run has finished the tail is rendered without a trigger, which is
    how the polling stops: there is no timer to cancel and no connection left
    hanging if the run died.
    """
    lines = "".join(
        f"<span class='dim'>{esc(row['at'][11:19])}</span> {esc(row['text'])}\n"
        for row in rows
    )
    poll = (
        ""
        if done
        else f" hx-get='/runs/{run_id}/lines?after={after}' hx-trigger='every 2s' "
        "hx-swap='outerHTML'"
    )
    return f"{lines}<span id='tail'{poll}></span>"


def run_page(run: sqlite3.Row, rows: list[sqlite3.Row], after: int) -> str:
    done = bool(run["finished_at"])
    state = "error" if run["error"] else ("done" if done else "running")
    detail = run["error"] or run["stats_json"] or ""
    header = (
        f"<h1>{esc(run['stage'])} <span class='dim small'>run {run['id']}</span></h1>"
        f"<div class='card small'>"
        f"<div>started {esc(run['started_at'][:19])} by {esc(run['started_by'] or '?')}"
        f" &middot; <span class='state-{state}'>{esc(state)}</span></div>"
        f"<div class='dim'>{esc(run['args_json'] or '')}</div>"
        f"<div>{esc(detail)}</div></div>"
    )
    return header + f"<pre id='log' class='log'>{run_log(rows, run['id'], done, after)}</pre>"
