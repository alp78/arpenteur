"""HTML, written as functions rather than templates.

No Jinja. The console is a handful of pages, the markup is generated from
Row objects, and a template language would add a directory, a loader
and a second syntax to learn for no gain at this size. If the console grows
past a dozen views this is the first thing to reconsider.

Everything user-supplied goes through `esc`. Company names come from a public
register and notes are typed by hand, so neither is trusted: a company called
`<script>` is a perfectly legal Swiss registration.
"""

from __future__ import annotations

import html
import json
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
.lede {{ color:var(--dim); max-width:62ch; margin:.2rem 0 1.4rem; }}
.stages {{ display:flex; flex-direction:column; gap:1rem; }}
.stage {{ background:#fff; border:1px solid var(--line); border-radius:3px; }}
.stage-head {{ display:flex; gap:.9rem; align-items:flex-start; padding:1rem 1.1rem .3rem; }}
.step {{ flex:0 0 1.6rem; height:1.6rem; border-radius:50%; background:var(--ink);
         color:#fff; font-size:.8rem; display:flex; align-items:center;
         justify-content:center; margin-top:.1rem; }}
.stage-title {{ flex:1; }}
.stage-title h3 {{ margin:0; font-size:1.05rem; }}
.what {{ margin:.25rem 0 0; color:var(--dim); max-width:64ch; }}
.stage-count {{ display:flex; gap:1.1rem; text-align:right; }}
.stage-count div {{ display:flex; flex-direction:column; }}
.stage-count b {{ font-size:1.15rem; font-variant-numeric:tabular-nums; }}
.stage-count span {{ font-size:.7rem; text-transform:uppercase;
                     letter-spacing:.05em; color:var(--dim); }}
.stage-count .waiting b {{ color:var(--accent); }}
dl.facts {{ display:grid; grid-template-columns:9rem 1fr; gap:.15rem .8rem;
            margin:.8rem 1.1rem; padding:.7rem .9rem; background:var(--bg);
            border:1px solid var(--line); font-size:.86rem; }}
dl.facts dt {{ color:var(--dim); text-transform:uppercase; font-size:.7rem;
               letter-spacing:.05em; padding-top:.18rem; }}
dl.facts dd {{ margin:0; }}
dl.facts dd.money {{ color:var(--accent); font-weight:600; }}
.controls {{ display:flex; gap:1rem 1.3rem; flex-wrap:wrap; align-items:start;
             margin:0 1.1rem 1rem; }}
.ctl {{ display:flex; flex-direction:column; gap:.2rem; max-width:19rem; }}
.ctl.ctl-wide {{ max-width:26rem; }}
.ctl > label {{ font-size:.74rem; text-transform:uppercase; letter-spacing:.05em;
                color:var(--dim); font-weight:600; }}
.ctl input[type=text] {{ width:17rem; }}
.hint {{ font-size:.79rem; color:var(--dim); line-height:1.4; }}
label.check {{ display:flex; gap:.45rem; align-items:flex-start; }}
label.check input {{ margin:.25rem 0 0; }}
label.check b {{ display:block; font-weight:600; font-size:.9rem; }}
.action {{ display:flex; gap:.8rem; align-items:center; flex-wrap:wrap;
           padding:.8rem 1.1rem; border-top:1px solid var(--line); background:var(--bg); }}
.action button {{ padding:.45rem 1.1rem; font-weight:600; }}
.action .hint {{ max-width:56ch; }}
.stamp {{ display:inline-block; width:1.15rem; height:1.15rem; line-height:1.15rem;
          text-align:center; border-radius:2px; font-size:.66rem; font-weight:700;
          margin-right:.12rem; border:1px solid var(--line); color:var(--dim);
          background:#fff; }}
.stamp.on {{ background:var(--ink); border-color:var(--ink); color:#fff; }}
.stamp.on.paid {{ background:var(--accent); border-color:var(--accent); }}
.acts {{ display:flex; gap:.8rem; flex-wrap:wrap; align-items:flex-start;
         background:#fff; border:1px solid var(--line); padding:.8rem .9rem;
         margin:0 0 1.2rem; }}
.act {{ display:flex; flex-direction:column; gap:.2rem; max-width:17rem; }}
.act button {{ font-weight:600; }}
.nowrap {{ white-space:nowrap; }}
header .sep {{ color:var(--line); }}
details.card summary {{ cursor:pointer; }}
.state-running {{ color:var(--accent); font-weight:600; }}
.state-error {{ color:#a3341c; font-weight:600; }}
.state-done {{ color:var(--dim); }}
tr.past td {{ color:var(--dim); background:#f7f5f1; }}
pre.log {{ background:#fff; border:1px solid var(--line); padding:.8rem 1rem; margin:0;
           max-height:70vh; overflow:auto; font-size:.82rem; line-height:1.5;
           white-space:pre-wrap; word-break:break-word; }}
{css}
</style></head><body>
<header>
  <span class="brand">ARPENTEUR</span>
  <a href="/clients">Clients</a>
  <a href="/mail">Mail</a>
  <a href="/calendar">Calendar</a>
  <span class="sep">|</span>
  <a href="/">Survey</a>
  <a href="/board">Board</a>
  <a href="/people">People</a>
  <a href="/map">Map</a>
  <a href="/pipeline">Operations</a>
  <a href="/runs">History</a>
  <a href="/settings">Settings</a>
  <span class="dim small" style="margin-left:auto">{territory}</span>
</header><main>
"""

FOOT = "</main></body></html>"


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def page(title: str, body: str, territory: str = "", css: str = "") -> str:
    """The shell, with a section's own stylesheet folded into the head.

    `css` goes through the format call rather than being concatenated into the
    body, and that is the whole point of the parameter. A section used to pass
    its rules as `<style>` inside `body`, which meant every brace had to be
    doubled to survive an f-string, and the doubled braces then reached the
    browser: `.crumbs {{ ... }}` is a selector followed by a block whose only
    contents are an invalid declaration, so CSS error recovery discarded every
    rule in the file. The Clients and Settings pages were rendering with none
    of their own styling and nothing anywhere said so. A substituted value is
    not re-scanned for placeholders, so passing it here lets the rules be
    written once, normally, and arrive intact.
    """
    return (
        HEAD.format(title=esc(title), territory=esc(territory), css=css)
        + body
        + FOOT
    )


def as_list(value: Any) -> list[str]:
    """A jsonb column, and a half-written row as None.

    psycopg parses jsonb on the way out, so the list branch is now the ordinary
    path rather than the exception. The string branch stays for values written
    before the column was jsonb and for anything handed in by a test.
    """
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


def band(row: Row | None) -> str:
    if row is None or row["score"] is None:
        return '<span class="dim">&mdash;</span>'
    name = row["band"] or "low"
    mark = " *" if row["source"] == "manual" else ""
    return f'<span class="band-{esc(name)}">{row["score"]}{esc(mark)}</span>'


# --- the company list ---------------------------------------------------------


# How far each company has been taken, as five stamps in pipeline order. The
# letter is the stage; filled means done. One glance answers "what has actually
# happened to this company", which a row of blank cells does not.
PROGRESS = (
    ("D", "hydrated", "Detail fetched from the register"),
    ("S", "classified", "Sorted into segments"),
    ("W", "researched", "Website researched"),
    ("F", "scored", "Scored against the campaign"),
    ("@", "reached", "An address was looked for"),
)


def progress(row: Row) -> str:
    """The five stamps for one company."""
    keys = set(row.keys())
    out = []
    for letter, key, title in PROGRESS:
        on = " on" if key in keys and row[key] else ""
        paid = " paid" if key in ("researched", "scored") else ""
        out.append(
            f'<span class="stamp{on}{paid if on else ""}" title="{esc(title)}'
            f'{"" if on else " — not yet"}">{esc(letter)}</span>'
        )
    return "".join(out)


def company_rows(rows: list[Row]) -> str:
    if not rows:
        return (
            '<tr><td colspan="7" class="dim">Nothing matches. Widen the filter, '
            'or run a stage on the <a href="/pipeline">operations</a> page.</td></tr>'
        )
    out = []
    for row in rows:
        segments = pills(as_list(row["segments"]))
        url = row["url"] or ""
        site = (
            f'<a href="{esc(url)}" rel="noreferrer noopener" target="_blank">'
            f'{esc(url[:38])}</a>'
            if url
            else '<span class="dim">&mdash;</span>'
        )
        out.append(
            f'<tr><td><a href="/company/{row["id"]}">{esc(row["name"])}</a>'
            f'<div class="dim small">{esc(row["uid"])}</div></td>'
            f'<td class="small">{esc(row["canton"])}</td>'
            f'<td class="small nowrap">{progress(row)}</td>'
            f'<td class="small">{segments}</td>'
            f'<td>{band(row)}</td>'
            f'<td class="small">{site}</td>'
            f'<td class="small">{esc(row["state"] or "")}</td></tr>'
        )
    return "".join(out)


def company_table(rows: list[Row], total: int) -> str:
    legend = " &middot; ".join(
        f"<b>{esc(letter)}</b> {esc(title.lower())}" for letter, _key, title in PROGRESS
    )
    return (
        f'<p class="dim small">{total:,} companies match. '
        f'Progress: {legend}</p>'
        '<table><thead><tr><th>company</th><th>canton</th><th>progress</th>'
        '<th>segments</th><th>fit</th><th>website</th><th>state</th></tr></thead>'
        f"<tbody>{company_rows(rows)}</tbody></table>"
    )


# The pipeline as a filter, worded as a question about the company rather than
# as the name of a stage: an operator asks "which ones still need research",
# not "which ones fail the enrich selection predicate".
STAGE_FILTERS = (
    ("", "Anywhere in the pipeline"),
    ("no-detail", "Name only, no detail fetched"),
    ("changed", "Changed in the register, needs re-reading"),
    ("detail", "Detail fetched"),
    ("unclassified", "Detail fetched, not yet segmented"),
    ("classified", "Segmented"),
    ("unresearched", "Segmented, website not researched"),
    ("researched", "Website researched"),
    ("unscored", "Researched, not yet scored"),
    ("scored", "Scored against the campaign"),
    ("with-address", "Has a usable email address"),
)


def filter_form(
    *, q: str, canton: str, segment: str, band_name: str, state: str, stage: str,
    cantons: list[str], segments: list[str], states: list[str],
) -> str:
    def options(values: list[str], chosen: str, label: str) -> str:
        out = [f'<option value="">{esc(label)}</option>']
        for value in values:
            sel = " selected" if value == chosen else ""
            out.append(f'<option value="{esc(value)}"{sel}>{esc(value)}</option>')
        return "".join(out)

    stages = "".join(
        f'<option value="{esc(value)}"'
        + (" selected" if value == stage else "")
        + f">{esc(label)}</option>"
        for value, label in STAGE_FILTERS
    )
    return (
        '<form class="filters" hx-get="/companies" hx-target="#list" hx-trigger="submit, change">'
        '<div class="ctl"><label>Search</label>'
        f'<input name="q" value="{esc(q)}" placeholder="Name, or CHE164075515"></div>'
        f'<div class="ctl"><label>Canton</label><select name="canton">'
        f'{options(cantons, canton, "All cantons")}</select></div>'
        f'<div class="ctl"><label>Segment</label><select name="segment">'
        f'{options(segments, segment, "All segments")}</select></div>'
        '<div class="ctl"><label>Fit band</label><select name="band">'
        + options(["high", "medium", "low", "excluded"], band_name, "Any band")
        + "</select></div>"
        f'<div class="ctl"><label>Pipeline state</label><select name="state">'
        f'{options(states, state, "Any state")}</select></div>'
        f'<div class="ctl ctl-wide"><label>Progress</label>'
        f'<select name="stage">{stages}</select></div>'
        '<div class="ctl"><label>&nbsp;</label><button class="primary">Filter</button></div>'
        "</form>"
    )


# --- one company --------------------------------------------------------------


def _act(uid: str, stage: str, label: str, note: str, busy: int | None) -> str:
    """One per-company action. Runs exactly this company, so no size is needed.

    The same POST the operations page uses, with the UID filled in. Naming a
    company *is* declaring the size of the run, which is why the paid stages
    accept it without a limit.
    """
    disabled = " disabled" if busy else ""
    return (
        f"<form method='post' action='/run/{esc(stage)}' class='act'>"
        f"<input type='hidden' name='uids' value='{esc(uid)}'>"
        f"<button{disabled}>{esc(label)}</button>"
        f"<span class='hint'>{esc(note)}</span></form>"
    )


def _client_action(company: Row, client: Row | None) -> str:
    """Promote to client, or the link to the client this already is.

    A reference rather than a copy. The surveyed tools call the alternative
    the handoff gap: a system that stops at "closed won" makes the first day
    of an engagement a copy-paste job, when the register entry, the officers,
    the research and the notes are exactly what is worth having.
    """
    if client is not None:
        return (
            f"<form class='act'><a href='/client/{client['id']}'>"
            f"<button type='button' class='primary'>Open client "
            f"{esc(client['code'])}</button></a>"
            "<span class='hint'>This company is a client. Projects, "
            "correspondence and the diary are on its client page.</span></form>"
        )
    return (
        f"<form method='post' action='/company/{company['id']}/client' class='act'>"
        "<button class='primary'>Make this a client</button>"
        "<span class='hint'>Opens a client record for it, keeping everything "
        "already known: the register entry, the officers, the research and "
        "the notes.</span></form>"
    )


def _actions(
    company: Row, site: Row | None, fit: Row | None,
    busy: int | None, client: Row | None = None,
) -> str:
    """What can be done to this one company, right here.

    The operations page runs a stage over a queue. This is the other half an
    operator needs: having opened a company and decided it is interesting,
    acting on it without going back and constructing a filter that selects it.
    """
    uid = company["uid"]
    acts = [
        _client_action(company, client),
        _act(uid, "enrich",
             "Research again" if site else "Research this website",
             "Searches the web for this company and reads what it finds. "
             "About 1 franc, and anywhere from 7 to 45 minutes.", busy),
        _act(uid, "match",
             "Score again" if fit else "Score this company",
             "Reads it against campaign.md and writes the fit and the opening line.",
             busy),
    ]
    if site is not None and site["url"]:
        acts.append(
            _act(uid, "reach", "Find an address",
                 "Opens the site and its contact pages for a published address. Free.",
                 busy)
        )
    banner = (
        f"<div class='hint'>A stage is running. "
        f"<a href='/runs/{busy}'>Watch it</a> before starting another.</div>"
        if busy
        else ""
    )
    return f"<div class='acts'>{''.join(acts)}{banner}</div>"


def company_page(
    company: Row,
    segments: list[Row],
    officers: list[Row],
    site: Row | None,
    fit: Row | None,
    contacts: list[Row],
    notes: list[Row],
    prospect: Row | None,
    publications: list[Row],
    vocabulary: list[str],
    states: list[str],
    attribution: str,
    busy: int | None = None,
    client: Row | None = None,
    parties: list[Row] | None = None,
    overlap: str = "",
    mail_links: str = "",
    score_parts: str = "",
) -> str:
    out = [
        f"<h1>{esc(company['name'])}</h1>",
        f'<p class="dim small">{esc(company["uid"])} &middot; '
        f'{esc(company["legal_form_label"])} &middot; '
        f'{esc(company["legal_seat"])} ({esc(company["canton"])}) &middot; '
        f'{esc(company["status"])}</p>',
        _actions(company, site, fit, busy, client),
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

    if parties:
        # Linked to the person rather than printed as a string, and carrying
        # how many other boards each sits on. That number is the whole reason
        # the party table exists: a director on nine boards is a route into
        # nine companies, and a name in a cell is not.
        rows = "".join(
            f'<tr class="{"past" if o["valid_to"] else ""}">'
            f'<td><a href="/party/{o["id"]}">{esc(o["full_name"])}</a>'
            + ('<span class="dim small"> (left)</span>' if o["valid_to"] else "")
            + "</td>"
            f"<td>{esc(o['role'] or '')}</td>"
            f"<td class='small dim'>{esc((o['valid_from'] or '')[:10])}"
            + (f" to {esc((o['valid_to'] or '')[:10])}" if o["valid_to"] else "")
            + "</td>"
            + (
                f'<td class="small"><a href="/party/{o["id"]}">'
                f'{o["boards"] - 1} other board'
                f'{"s" if o["boards"] - 1 != 1 else ""}</a></td>'
                if o["boards"] > 1
                else '<td class="small dim">only here</td>'
            )
            + "</tr>"
            for o in parties
        )
        out.append(
            "<h2>officers</h2><table><tbody>" + rows + "</tbody></table>"
            '<p class="dim small">Parsed from FOSC publications, not a registry '
            "field. Verify against the certified excerpt before relying on it.</p>"
        )
    elif officers:
        rows = "".join(
            f"<tr><td>{esc(o['full_name'])}</td><td>{esc(o['role'])}</td>"
            f"<td class='small dim'>{esc(o['signature'] or '')}</td></tr>"
            for o in officers
        )
        out.append(
            "<h2>officers</h2><table><tbody>" + rows + "</tbody></table>"
            '<p class="dim small">Parsed from FOSC publications, and not yet '
            'resolved into people. Run <a href="/pipeline">resolve</a>, which '
            "is free, to see what else they sit on.</p>"
        )

    if overlap:
        out.append(overlap)
    # How the number above was arrived at. Directly under the score it
    # explains, because a reader who has to go looking for the reasoning will
    # take the number on faith instead.
    if score_parts:
        out.append(score_parts)
    out.append(_site_card(site))
    out.append(_contacts_card(contacts))
    # What filing a message taught the matcher. An inference nobody can see is
    # one nobody can correct, so it sits beside the contacts it behaves like.
    if mail_links:
        out.append(mail_links)
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


def _fit_card(fit: Row | None) -> str:
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


def _prospect_card(company_id: int, prospect: Row | None, states: list[str]) -> str:
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
        '<button class="primary">save</button></form>'
        '<p class="dim small" style="margin:.5rem 0 0">Saving a state puts this '
        'company on the <a href="/board">board</a>, where it can be moved from '
        "column to column against everything else being worked.</p></div>"
    )


def _segments_card(
    company_id: int, segments: list[Row], vocabulary: list[str]
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


def _site_card(site: Row | None) -> str:
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


def contact_row(c: Row) -> str:
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


def _contacts_card(contacts: list[Row]) -> str:
    if not contacts:
        return ""
    rows = "".join(contact_row(c) for c in contacts)
    return f"<h2>contacts</h2><table><tbody>{rows}</tbody></table>"


def _notes_card(company_id: int, notes: list[Row]) -> str:
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


def _compact(value: Any) -> str:
    """A jsonb column as one short line, for a table cell."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def runs_table(rows: list[Row]) -> str:
    """The table alone, which is what the poll replaces.

    Separate from the page so the refresh swaps exactly this element. It
    carries its own hx-get, so each replacement re-arms the next poll.
    """
    out = []
    for row in rows:
        state = "error" if row["error"] else ("done" if row["finished_at"] else "running")
        # stats_json is a parsed dict now, not the text SQLite stored, so it
        # is rendered rather than sliced. `detail[:160]` on a dict raises a
        # KeyError naming a slice object, which is a memorable way to discover
        # that a column changed type.
        detail = row["error"] or _compact(row["stats_json"])
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


def runs_page(rows: list[Row]) -> str:
    return (
        "<h1>Runs</h1>"
        "<p class='dim small'>Every stage records what it did. This view refreshes "
        "itself while a run is in progress. Open one to read it line by line.</p>"
        + runs_table(rows)
    )


# --- the operations page ------------------------------------------------------
#
# The first version of this was a stack of unlabelled boxes, which is a debug
# form rather than a product: it did not say what a stage does, what it needs
# first, what it costs, what the button will do, or what to type in any field.
#
# Three rules came out of that and are worth keeping. Anything with a knowable
# set of values is a list to pick from. Anything that must be typed carries an
# example and says where to get it. And every stage states what it will do
# before you press it, next to how much of the work is still waiting.


def _options(field: Any, options: dict[str, tuple]) -> tuple:
    if field.source:
        return tuple(options.get(field.source, ()))
    return tuple(field.choices)


def _control(field: Any, stage_name: str, options: dict[str, tuple]) -> str:
    """One labelled control, with its explanation attached rather than implied."""
    ident = f"{stage_name}-{field.name}"
    note = " ".join(part for part in (field.help, field.where) if part)

    if field.kind == "bool":
        checked = " checked" if field.default else ""
        return (
            f"<div class='ctl ctl-wide'><label class='check'>"
            f"<input type='checkbox' name='{esc(field.name)}' id='{esc(ident)}'"
            f" value='1'{checked}>"
            f"<span><b>{esc(field.label)}</b>"
            f"<span class='hint'>{esc(note)}</span></span></label></div>"
        )

    if field.kind == "select":
        chosen = _options(field, options)
        multi = getattr(field, "multi", False)
        body = "".join(
            f"<option value='{esc(value)}'"
            + (" selected" if not multi and str(value) == str(field.default) else "")
            + f">{esc(label)}</option>"
            for value, label in chosen
        )
        attrs = (
            f" multiple size='{min(len(chosen), 8)}'" if multi else ""
        )
        inner = (
            f"<select name='{esc(field.name)}' id='{esc(ident)}'{attrs}>{body}</select>"
        )
    elif field.kind == "date":
        inner = (
            f"<input type='date' name='{esc(field.name)}' id='{esc(ident)}'>"
        )
    else:
        inner = (
            f"<input type='text' name='{esc(field.name)}' id='{esc(ident)}'"
            f" placeholder='{esc(field.example or 'optional')}'>"
        )
    return (
        f"<div class='ctl'><label for='{esc(ident)}'>{esc(field.label)}</label>"
        f"{inner}<span class='hint'>{esc(note)}</span></div>"
    )


def stage_card(
    stage: Any, status: dict[str, int], options: dict[str, tuple],
    busy: int | None, index: int,
) -> str:
    """One stage: what it is, where it stands, and the control to advance it."""
    controls = "".join(_control(f, stage.name, options) for f in stage.fields)
    waiting = status.get("waiting", 0)
    done = status.get("done", 0)

    if busy:
        action = (
            f"<button disabled>{esc(stage.button)}</button>"
            f"<span class='hint'>Run {busy} is still going. "
            "One stage at a time.</span>"
        )
    elif waiting == 0:
        # Not disabled: a filter or a re-run flag can still make work for it,
        # and a button that refuses to be pressed cannot say why.
        action = (
            f"<button class='primary'>{esc(stage.button)}</button>"
            f"<span class='hint'>Nothing is waiting. {esc(stage.button_does)}</span>"
        )
    else:
        action = (
            f"<button class='primary'>{esc(stage.button)}</button>"
            f"<span class='hint'>{esc(stage.button_does)}</span>"
        )

    money = "cost" in stage.cost.lower() or "centimes" in stage.cost.lower()
    return (
        f"<form class='stage' method='post' action='/run/{esc(stage.name)}'>"
        f"<div class='stage-head'>"
        f"<span class='step'>{index}</span>"
        f"<div class='stage-title'><h3>{esc(stage.title)}</h3>"
        f"<p class='what'>{esc(stage.what)}</p></div>"
        f"<div class='stage-count'>"
        f"<div><b>{done:,}</b><span>{esc(stage.done_label)}</span></div>"
        f"<div class='{'waiting' if waiting else ''}'>"
        f"<b>{waiting:,}</b><span>{esc(stage.waiting_label)}</span></div>"
        f"</div></div>"
        f"<dl class='facts'>"
        f"<dt>Needs first</dt><dd>{esc(stage.needs)}</dd>"
        f"<dt>Produces</dt><dd>{esc(stage.produces)}</dd>"
        f"<dt>Cost</dt><dd class='{'money' if money else ''}'>{esc(stage.cost)}</dd>"
        f"</dl>"
        f"<div class='controls'>{controls}</div>"
        f"<div class='action'>{action}</div>"
        f"</form>"
    )


def pipeline_page(
    stages: Any, statuses: dict[str, dict[str, int]], options: dict[str, tuple],
    busy: int | None, note: str = "",
) -> str:
    cards = "".join(
        stage_card(stage, statuses.get(stage.name, {}), options, busy, index)
        for index, stage in enumerate(stages, 1)
    )
    banner = f"<div class='warn'>{esc(note)}</div>" if note else ""
    running = (
        f"<div class='warn'>A stage is running. "
        f"<a href='/runs/{busy}'>Watch it</a>, or wait for it to finish.</div>"
        if busy
        else ""
    )
    return (
        "<h1>Operations</h1>"
        "<p class='lede'>The pipeline runs top to bottom. Each stage works on what "
        "the one above it produced, and every stage picks up where it left off, so "
        "running one twice is safe and costs nothing extra.</p>"
        + banner + running + f"<div class='stages'>{cards}</div>"
    )
# --- watching one run ---------------------------------------------------------


def run_log(rows: list[Row], run_id: int, done: bool, after: int) -> str:
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


def run_page(run: Row, rows: list[Row], after: int) -> str:
    done = bool(run["finished_at"])
    state = "error" if run["error"] else ("done" if done else "running")
    detail = run["error"] or _compact(run["stats_json"])
    header = (
        f"<h1>{esc(run['stage'])} <span class='dim small'>run {run['id']}</span></h1>"
        f"<div class='card small'>"
        f"<div>started {esc(run['started_at'][:19])} by {esc(run['started_by'] or '?')}"
        f" &middot; <span class='state-{state}'>{esc(state)}</span></div>"
        f"<div class='dim'>{esc(_compact(run['args_json']))}</div>"
        f"<div>{esc(detail)}</div></div>"
    )
    return header + f"<pre id='log' class='log'>{run_log(rows, run['id'], done, after)}</pre>"
