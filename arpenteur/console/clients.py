"""Rendering for the client side: portfolio, client card, project, calendar.

Kept apart from `render.py` because this is the half of the product that
starts after a company says yes, and the two halves have different vocabulary.
A company is surveyed, scored and approached; a client has projects, a
history and a diary.

The design follows what the professional-services tools were surveyed for.
Three things they agree on, and one they mostly get wrong.

Agreed: a project needs its own identity, because it outlives the
conversation that created it. Billing arrangement is not a number, because
fixed fee, time and materials and retainer are different agreements. And the
handoff has to carry the file, not restart it.

Mostly wrong: the audit trail and the client-facing timeline get built as two
systems, and they drift. Here they are one `event` table read three ways, so
the diary, the story and the record cannot disagree.
"""

from __future__ import annotations

import calendar as calmod
from datetime import date, timedelta
from typing import Any

from ..kernel.db import Connection, Row
from .render import esc, page

# What each kind of event looks like in a list. The mark is deliberately a
# character rather than an icon font: no build step, and it survives a paste
# into an email or a PDF export.
KINDS: dict[str, tuple[str, str]] = {
    "email": ("@", "Email"),
    "call": ("T", "Call"),
    "meeting": ("M", "Meeting"),
    "deadline": ("!", "Deadline"),
    "note": ("N", "Note"),
    "milestone": ("*", "Milestone"),
    "document": ("D", "Document"),
    "decision": ("=", "Decision"),
}

# Rows the calendar derives from a project's own dates rather than from the
# log. Not offered in the "record something" form: you set them by editing the
# project, which is the point of deriving them.
PROJECT_MARKS: dict[str, tuple[str, str]] = {
    "project-start": (">", "Project starts"),
    "project-due": ("<", "Project due"),
}

STYLES = """
.crumbs { font-size:.82rem; color:var(--dim); margin:0 0 .5rem; }
.crumbs a { color:var(--dim); }
.ident { display:flex; gap:1.4rem; align-items:baseline; flex-wrap:wrap; }
.code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-weight:700;
         background:var(--ink); color:#fff; padding:.1rem .5rem; border-radius:2px;
         font-size:.82rem; letter-spacing:.03em; }
.code.soft { background:#fff; color:var(--accent); border:1px solid var(--accent); }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:1rem; }
.tile { background:#fff; border:1px solid var(--line); padding:.8rem .95rem; }
.tile h4 { margin:0 0 .3rem; font-size:.72rem; text-transform:uppercase;
            letter-spacing:.06em; color:var(--dim); }
.tile .big { font-size:1.5rem; font-weight:600; font-variant-numeric:tabular-nums; }
.timeline { border-left:2px solid var(--line); margin:.6rem 0 0 .7rem; padding:0; }
.ev { position:relative; padding:.55rem 0 .55rem 1.5rem; }
.ev::before { content:attr(data-mark); position:absolute; left:-.72rem; top:.6rem;
               width:1.35rem; height:1.35rem; border-radius:50%; background:#fff;
               border:1px solid var(--line); font-size:.7rem; font-weight:700;
               display:flex; align-items:center; justify-content:center; color:var(--dim); }
.ev.planned::before { border-color:var(--accent); color:var(--accent); }
.ev.cancelled { opacity:.5; text-decoration:line-through; }
.ev .when { font-size:.78rem; color:var(--dim); }
.ev .subj { font-weight:600; }
.ev .body { white-space:pre-wrap; font-size:.88rem; margin-top:.2rem;
             border-left:2px solid var(--line); padding-left:.7rem; color:#3a352e; }
.cal { width:100%; table-layout:fixed; }
.cal td { height:6.2rem; vertical-align:top; padding:.3rem .35rem; }
.cal td.out { background:#f4f2ee; }
.cal td.today { outline:2px solid var(--accent); outline-offset:-2px; }
.cal .day { font-size:.74rem; color:var(--dim); font-variant-numeric:tabular-nums; }
.cal .slot { display:block; font-size:.74rem; margin-top:.12rem; padding:.05rem .25rem;
              background:var(--bg); border-left:2px solid var(--accent);
              overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.cal .slot.done { border-left-color:var(--line); color:var(--dim); }
.cal .slot.derived { background:#fff; border-left-style:dashed; }
.split { display:grid; grid-template-columns:1fr 20rem; gap:1.4rem; align-items:start; }
@media (max-width:900px) { .split { grid-template-columns:1fr; } }
.stack { display:flex; flex-direction:column; gap:.45rem; }
.stack input, .stack select, .stack textarea { width:100%; }
.stack label { font-size:.72rem; text-transform:uppercase; letter-spacing:.05em;
                color:var(--dim); font-weight:600; }
.row2 { display:grid; grid-template-columns:1fr 1fr; gap:.5rem; }
.badge { display:inline-block; padding:.05rem .45rem; border-radius:2px;
          font-size:.72rem; font-weight:600; text-transform:uppercase;
          letter-spacing:.04em; border:1px solid var(--line); }
.badge.active, .badge.done { background:#e8f1e8; border-color:#9fc0a2; color:#1d6b2f; }
.badge.proposed, .badge.planned { background:#fdf3e6; border-color:#d9b483; color:#8a5a1f; }
.badge.paused, .badge.dormant { background:#f1f0ee; color:var(--dim); }
.badge.cancelled, .badge.former { background:#f6ecea; border-color:#c9a29b; color:#8a3a2c; }
.dossier-strip { display:flex; gap:1rem; align-items:center; flex-wrap:wrap;
                 background:#fff; border:1px solid var(--line);
                 border-left:3px solid var(--accent); padding:.6rem .9rem;
                 margin:.9rem 0 0; }
.dossier-strip .go { font-weight:600; text-decoration:none; }
.dossier-strip .go::after { content:" \\2192"; }
details.del { display:inline-block; }
details.del summary { cursor:pointer; color:var(--dim); font-size:.8rem;
                      list-style:none; }
details.del summary::-webkit-details-marker { display:none; }
details.del summary:hover { color:#8a3a2c; }
details.del form { display:flex; gap:.4rem; align-items:center; flex-wrap:wrap;
                   margin-top:.35rem; padding:.5rem .6rem; background:#fdf6f4;
                   border:1px solid #d9bdb6; }
details.del input { min-width:20rem; }
details.del .hint { flex-basis:100%; }
"""


def shell(title: str, body: str, territory: str = "") -> str:
    """The ordinary page shell plus the styles this section needs."""
    return page(title, body, territory, css=STYLES)


def _badge(value: str | None) -> str:
    value = value or ""
    return f'<span class="badge {esc(value)}">{esc(value or "—")}</span>'


def _kind(kind: str | None) -> tuple[str, str]:
    """The mark and the label for a kind, whether logged or derived."""
    name = kind or ""
    return KINDS.get(name) or PROJECT_MARKS.get(name) or ("·", name or "—")


def _when(row: Row) -> str:
    stamp = (row["occurred_at"] or "")[:16].replace("T", " ")
    if row["ends_at"]:
        return f'{stamp} &rarr; {esc((row["ends_at"] or "")[11:16])}'
    return esc(stamp)


# --- the portfolio ------------------------------------------------------------


def portfolio(rows: list[Row], total_projects: int, upcoming: int) -> str:
    if not rows:
        body = (
            '<div class="card"><p>No clients yet.</p>'
            "<p class='dim small'>A client is a company you are working with. "
            "Promote one from its page in the survey, or add one by UID below "
            "if it was never surveyed.</p></div>"
        )
    else:
        cells = []
        for row in rows:
            last = (row["last_contact"] or "")[:10] or "never"
            nxt = (row["next_at"] or "")[:16].replace("T", " ")
            cells.append(
                f'<tr><td><a href="/client/{row["id"]}">{esc(row["name"])}</a>'
                f'<div class="dim small">{esc(row["uid"])} &middot; '
                f'{esc(row["legal_form_label"])} &middot; {esc(row["canton"])}</div></td>'
                f'<td><span class="code">{esc(row["code"])}</span></td>'
                f"<td>{_badge(row['status'])}</td>"
                f'<td class="small">{row["active_projects"]} active '
                f'<span class="dim">of {row["projects"]}</span></td>'
                f'<td class="small">{esc(last)}</td>'
                f'<td class="small">{esc(nxt) or "<span class=dim>—</span>"}</td></tr>'
            )
        body = (
            '<table><thead><tr><th>client</th><th>code</th><th>status</th>'
            "<th>projects</th><th>last contact</th><th>next</th></tr></thead>"
            f"<tbody>{''.join(cells)}</tbody></table>"
        )

    tiles = (
        '<div class="grid" style="margin-bottom:1.2rem">'
        f'<div class="tile"><h4>Clients</h4><div class="big">{len(rows)}</div></div>'
        f'<div class="tile"><h4>Projects</h4><div class="big">{total_projects}</div></div>'
        f'<div class="tile"><h4>In the diary</h4><div class="big">{upcoming}</div>'
        '<div class="dim small">next 30 days</div></div></div>'
    )
    add = (
        '<h2>add a client the survey never saw</h2>'
        '<form class="card stack" method="post" action="/clients/import" '
        'style="max-width:32rem">'
        "<p class='dim small' style='margin:0'>Fetches the company from the "
        "commercial register and makes it a client. Use this when the client "
        "is outside the surveyed territory, which is normal: the survey covers "
        "a region and a set of legal forms, and a client does not have to be "
        "inside either.</p>"
        "<label for='uid'>Swiss UID</label>"
        "<input id='uid' name='uid' placeholder='CHE-103.895.771' required>"
        "<span class='hint'>On any company's page at zefix.ch, and on its "
        "letterhead. Dots and dashes optional.</span>"
        '<div><button class="primary">Fetch and add</button></div></form>'
    )
    return f"<h1>Clients</h1>{tiles}{body}{add}"


# --- one client ---------------------------------------------------------------


def client_page(
    client: Row,
    company: Row,
    projects: list[Row],
    events: list[Row],
    contacts: list[Row],
    officers: list[Row],
    attribution_notice: str,
    dossier_filled: int = 0,
    dossier_total: int = 0,
    documents: int = 0,
    has_approach: bool = False,
    sites: list[Row] | None = None,
    note: str = "",
    notes: str = "",
) -> str:
    head = (
        f'<p class="crumbs"><a href="/clients">Clients</a> / {esc(client["code"])}</p>'
        f'<div class="ident"><h1 style="margin:0">{esc(company["name"])}</h1>'
        f'<span class="code">{esc(client["code"])}</span>{_badge(client["status"])}</div>'
        f'<p class="dim small">{esc(company["uid"])} &middot; '
        f'{esc(company["legal_form_label"])} &middot; '
        f'{esc(company["legal_seat"])} ({esc(company["canton"])}) &middot; '
        f'client since {esc(client["since"] or "?")} &middot; '
        f'<a href="/company/{company["id"]}">survey record</a></p>'
        + _dossier_strip(
            client["id"], dossier_filled, dossier_total, documents, has_approach,
            notes,
        )
    )

    if projects:
        rows = "".join(
            f'<tr><td><a href="/project/{p["id"]}">{esc(p["name"])}</a></td>'
            f'<td><span class="code soft">{esc(p["code"])}</span></td>'
            f"<td>{_badge(p['status'])}</td>"
            f'<td class="small">{esc(p["billing"] or "")}</td>'
            f'<td class="small">{esc(p["due_at"] or "")}</td></tr>'
            for p in projects
        )
        project_table = (
            "<table><thead><tr><th>project</th><th>code</th><th>status</th>"
            "<th>billing</th><th>due</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    else:
        project_table = (
            '<p class="dim small">No projects yet. A project is a piece of work '
            "with its own reference, so it can be named in a contract, an "
            "invoice and a file.</p>"
        )

    who = ""
    if contacts or officers:
        lines = [
            f'<div>{esc(c["value"])} <span class="dim small">'
            f'{esc(c["method"] or "")}{" · suppressed" if c["suppressed"] else ""}'
            "</span></div>"
            for c in contacts
        ] + [
            f'<div>{esc(o["full_name"])} <span class="dim small">{esc(o["role"])}</span></div>'
            for o in officers[:8]
        ]
        who = f'<h2>who</h2><div class="card small">{"".join(lines)}</div>'

    return (
        head
        + (f'<div class="warn">{esc(note)}</div>' if note else "")
        + '<div class="split"><div>'
        + "<h2>projects</h2>"
        + project_table
        + _new_project_form(client["id"])
        + _sites(client["id"], sites or [])
        + "<h2>record</h2>"
        + _timeline(events)
        + "</div><aside>"
        + _log_form(client["id"], projects)
        + _client_brief(client)
        + who
        + f'<p class="dim small" style="white-space:pre-line;margin-top:1rem">'
        + esc(attribution_notice)
        + "</p></aside></div>"
    )


def _dossier_strip(
    client_id: int, filled: int, total: int, documents: int, has_approach: bool,
    notes: str = "",
) -> str:
    """The way in to the dossier, and how much of it exists.

    A bare link would say nothing. The counts are what make it actionable: two
    of ten sections filled and no approach yet is a different instruction from
    nine of ten with a proposal already generated.
    """
    state = (
        f"{filled} of {total} sections filled &middot; {documents} documents"
        + (" &middot; an approach has been suggested" if has_approach
           else " &middot; no approach suggested yet")
    )
    return (
        '<div class="dossier-strip">'
        f'<a class="go" href="/client/{client_id}/dossier">Dossier and approach</a>'
        f'<a class="go" href="/client/{client_id}/prep">Interview prep</a>'
        f"{notes}"
        f'<span class="dim small">{state}</span>'
        "<span class='dim small'>What they run, who decides, what is exposed, "
        "the documents they sent, and what to propose. None of it is in the "
        "register.</span></div>"
    )


def _client_brief(client: Row) -> str:
    """Standing context, as distinct from something that happened.

    An event is what occurred on a date. This is what remains true: who
    decides, how they buy, what the relationship is. Keeping them apart is
    what stops the timeline becoming a place people hide facts.
    """
    return (
        '<h2>standing brief</h2>'
        f'<form class="card stack" method="post" action="/client/{client["id"]}/brief">'
        "<label for='brief'>What stays true about this client</label>"
        f'<textarea id="brief" name="brief" placeholder="Who decides, how they buy, '
        'constraints, history.">{}</textarea>'.format(esc(client["brief"] or ""))
        + "<div class='row2'>"
        "<div><label for='owner'>Owner</label>"
        f'<input id="owner" name="owner" value="{esc(client["owner"] or "")}"></div>'
        "<div><label for='status'>Status</label><select id='status' name='status'>"
        + "".join(
            f'<option value="{esc(s)}"'
            + (" selected" if s == client["status"] else "")
            + f">{esc(s)}</option>"
            for s in ("active", "prospect", "dormant", "former")
        )
        + "</select></div></div>"
        '<div><button class="primary">Save</button></div></form>'
    )


def _new_project_form(client_id: int) -> str:
    return (
        '<details class="card"><summary><b>New project</b> '
        '<span class="dim small">gets its own reference, like BOIS-GENTIL-01</span>'
        "</summary>"
        f'<form class="stack" method="post" action="/client/{client_id}/projects" '
        'style="margin-top:.7rem">'
        "<label for='pname'>Name</label>"
        "<input id='pname' name='name' required placeholder='Refonte du socle de données'>"
        "<div class='row2'>"
        "<div><label for='billing'>Billing</label>"
        "<select id='billing' name='billing'>"
        "<option value=''>Not decided</option>"
        "<option value='fixed'>Fixed fee</option>"
        "<option value='tm'>Time and materials</option>"
        "<option value='retainer'>Retainer</option>"
        "<option value='diagnostic'>Diagnostic</option>"
        "</select>"
        "<span class='hint'>These are different agreements, not one number: "
        "a retainer and a fixed fee are billed and tracked differently.</span></div>"
        "<div><label for='budget'>Budget (CHF)</label>"
        "<input id='budget' name='budget' type='number' step='100' placeholder='4800'>"
        "</div></div>"
        "<div class='row2'>"
        "<div><label for='started'>Starts</label>"
        "<input id='started' name='started_at' type='date'></div>"
        "<div><label for='due'>Due</label>"
        "<input id='due' name='due_at' type='date'></div></div>"
        "<label for='psummary'>What it is</label>"
        "<textarea id='psummary' name='summary' placeholder='One paragraph: the "
        "problem, the deliverable, the boundary.'></textarea>"
        '<div><button class="primary">Create project</button></div></form></details>'
    )


def _sites(client_id: int, sites: list[Row]) -> str:
    """Where this client actually operates, and the way to add a site.

    The register holds one address per legal entity. A foundation running an
    EMS, a day centre and three residences has one entry and several places,
    and the difference matters the moment anyone asks where to drive to. So
    the seat arrives from the register and everything else is typed by the
    person who knows it; nothing here is inferred.
    """
    rows = []
    for site in sites:
        address = " ".join(
            str(part) for part in (
                site["street"], site["house_number"], site["postal_code"],
                site["city"]
            ) if part
        )
        if site["lat"] is None:
            where = (
                '<span class="dim">no point</span>'
                if site["street"]
                else '<span class="dim">no address</span>'
            )
        else:
            exact = site["precision"] == "address"
            where = (
                f'<a href="/map">{site["lat"]:.5f}, {site["lon"]:.5f}</a>'
                + (
                    ""
                    if exact
                    else f' <span class="dim">on the {esc(site["precision"])}</span>'
                )
            )
        rows.append(
            f'<tr><td>{esc(site["name"] or "—")}'
            + (' <span class="badge active">seat</span>' if site["is_seat"] else "")
            + f'</td><td class="small">{esc(address)}</td>'
            f'<td class="small">{where}</td>'
            '<td><form method="post" class="inline" '
            f'action="/establishment/{site["id"]}/delete">'
            "<button>remove</button></form></td></tr>"
        )
    table = (
        "<table><thead><tr><th>site</th><th>address</th><th>where</th><th></th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        if rows
        else '<p class="dim small">No site recorded. The registered address '
             "arrives with the geocode stage; the other sites are typed here.</p>"
    )
    return (
        '<h2 id="sites">sites</h2>' + table
        + '<details class="card"><summary><b>Add a site</b> '
        '<span class="dim small">geocoded as it is saved</span></summary>'
        f'<form class="stack" method="post" action="/client/{client_id}/sites" '
        'style="margin-top:.7rem">'
        "<label for='sname'>Name</label>"
        "<input id='sname' name='name' required placeholder='EMS Petit-Flon'>"
        "<div class='row2'>"
        "<div><label for='sstreet'>Street</label>"
        "<input id='sstreet' name='street' placeholder='Chemin du Petit-Flon'></div>"
        "<div><label for='snum'>Number</label>"
        "<input id='snum' name='house_number' placeholder='49'></div></div>"
        "<div class='row2'>"
        "<div><label for='spc'>Postcode</label>"
        "<input id='spc' name='postal_code' placeholder='1018'></div>"
        "<div><label for='scity'>Town</label>"
        "<input id='scity' name='city' placeholder='Lausanne'></div></div>"
        "<label for='skind'>What it is</label>"
        "<input id='skind' name='kind' placeholder='EMS, day centre, office'>"
        "<span class='hint'>Placed through the federal address search when you "
        "save it. An address that does not resolve is kept without a point "
        "rather than guessed at, and the page will say so.</span>"
        '<div><button class="primary">Add the site</button></div></form></details>'
    )


def _log_form(client_id: int, projects: list[Row]) -> str:
    """Record something that happened, or is going to.

    One form for the whole record. The surveyed tools split correspondence,
    calls, notes and meetings across four places and then need a fifth to
    reassemble them; here the kind is a field.
    """
    options = "".join(
        f'<option value="{p["id"]}">{esc(p["code"])} — {esc(p["name"])}</option>'
        for p in projects
    )
    kinds = "".join(
        f'<option value="{esc(key)}">{esc(label)}</option>'
        for key, (_mark, label) in KINDS.items()
    )
    return (
        "<h2>record something</h2>"
        f'<form class="card stack" method="post" action="/client/{client_id}/events">'
        "<div class='row2'>"
        f"<div><label for='kind'>What</label><select id='kind' name='kind'>{kinds}</select></div>"
        "<div><label for='state'>State</label><select id='state' name='state'>"
        "<option value='done'>Happened</option>"
        "<option value='planned'>Planned</option></select>"
        "<span class='hint'>Planned puts it in the calendar, whatever it is. "
        "Once it has happened only meetings and deadlines stay there; "
        "everything else lives here, in the record.</span></div></div>"
        "<div class='row2'>"
        "<div><label for='at'>When</label>"
        "<input id='at' name='occurred_at' type='datetime-local' required></div>"
        "<div><label for='dir'>Direction</label><select id='dir' name='direction'>"
        "<option value=''>—</option><option value='in'>Received</option>"
        "<option value='out'>Sent</option></select>"
        "<span class='hint'>For correspondence.</span></div></div>"
        f"<label for='proj'>Project</label><select id='proj' name='project_id'>"
        f"<option value=''>The relationship, not a project</option>{options}</select>"
        "<label for='with'>With</label>"
        "<input id='with' name='with_whom' placeholder='Name, or name@example.ch'>"
        "<label for='subject'>Subject</label>"
        "<input id='subject' name='subject' placeholder='Réunion de cadrage'>"
        "<label for='body'>Body</label>"
        "<textarea id='body' name='body' placeholder='Paste the email in full, or "
        "write what was said. Stored verbatim and never edited afterwards.'></textarea>"
        "<label for='mid'>Message-ID</label>"
        "<input id='mid' name='external_id' placeholder='&lt;CAF...@mail.gmail.com&gt;'>"
        "<span class='hint'>Optional, for email. Paste it and the same message "
        "can never be recorded twice. It is in the message source, under "
        "Show original.</span>"
        '<div><button class="primary">Record it</button></div></form>'
    )


def _timeline(events: list[Row]) -> str:
    if not events:
        return (
            '<p class="dim small">Nothing recorded yet. Everything logged here '
            "is kept: states change, rows are never removed.</p>"
        )
    out = []
    for e in events:
        mark, label = _kind(e["kind"])
        keys = set(e.keys())
        project = (
            f'<a href="/project/{e["project_id"]}" class="code soft">'
            f'{esc(e["project_code"])}</a> '
            if "project_code" in keys and e["project_code"]
            else ""
        )
        direction = {"in": "received", "out": "sent"}.get(e["direction"] or "", "")
        meta = " &middot; ".join(
            part for part in (label, direction, esc(e["with_whom"] or "")) if part
        )
        body = (
            f'<div class="body">{esc(e["body"])}</div>'
            if e["body"]
            else ""
        )
        actions = ""
        if e["state"] == "planned":
            actions = (
                f'<form class="inline" method="post" action="/event/{e["id"]}/state">'
                "<input type='hidden' name='state' value='done'>"
                "<button>Mark as happened</button></form> "
                f'<form class="inline" method="post" action="/event/{e["id"]}/state">'
                "<input type='hidden' name='state' value='cancelled'>"
                "<button>Cancel</button></form> "
            )
        # Cancelling says a meeting was planned and did not happen. Deleting
        # says it was never true. They are different corrections and the record
        # needs both: a row describing something that never occurred cannot be
        # struck through, it has to go, and its going leaves a tombstone.
        actions += (
            f'<details class="del"><summary>delete</summary>'
            f'<form method="post" action="/event/{e["id"]}/delete">'
            "<input name='reason' placeholder='Why, for the tombstone. "
            "&quot;Never happened, seeded by mistake&quot;'>"
            "<button>Delete this entry</button>"
            "<span class='hint'>Removes the row and leaves a note saying what "
            "was removed and why. Use this when the entry is not true. Use "
            "Cancel when it was true and then did not happen.</span>"
            "</form></details>"
        )
        out.append(
            f'<li class="ev {esc(e["state"])}" data-mark="{esc(mark)}">'
            f'<div class="when">{_when(e)} &middot; {meta}</div>'
            f'<div class="subj">{project}{esc(e["subject"] or label)}</div>'
            f"{body}"
            f'<div class="small" style="margin-top:.3rem">{actions}</div></li>'
        )
    return f'<ul class="timeline">{"".join(out)}</ul>'


# --- one project --------------------------------------------------------------


def project_page(
    project: Row,
    client: Row,
    company: Row,
    events: list[Row],
) -> str:
    money = (
        f'{project["budget"]:,.0f} {esc(project["currency"] or "CHF")}'
        if project["budget"]
        else "&mdash;"
    )
    tiles = (
        '<div class="grid" style="margin:1rem 0">'
        f'<div class="tile"><h4>Status</h4><div>{_badge(project["status"])}</div></div>'
        f'<div class="tile"><h4>Billing</h4><div>{esc(project["billing"] or "—")}</div></div>'
        f'<div class="tile"><h4>Budget</h4><div class="big">{money}</div></div>'
        f'<div class="tile"><h4>Due</h4><div>{esc(project["due_at"] or "—")}</div></div>'
        "</div>"
    )
    return (
        f'<p class="crumbs"><a href="/clients">Clients</a> / '
        f'<a href="/client/{client["id"]}">{esc(company["name"])}</a> / '
        f'{esc(project["code"])}</p>'
        f'<div class="ident"><h1 style="margin:0">{esc(project["name"])}</h1>'
        f'<span class="code">{esc(project["code"])}</span></div>'
        + tiles
        + (
            f'<div class="card">{esc(project["summary"])}</div>'
            if project["summary"]
            else ""
        )
        + '<div class="split"><div><h2>record</h2>'
        + _timeline(events)
        + "</div><aside>"
        + _project_form(project)
        + "</aside></div>"
    )


def _project_form(project: Row) -> str:
    states = "".join(
        f'<option value="{esc(s)}"'
        + (" selected" if s == project["status"] else "")
        + f">{esc(s)}</option>"
        for s in ("proposed", "active", "paused", "delivered", "cancelled")
    )
    billings = "".join(
        f'<option value="{esc(v)}"'
        + (" selected" if v == (project["billing"] or "") else "")
        + f">{esc(label)}</option>"
        for v, label in (
            ("", "Not decided"), ("fixed", "Fixed fee"),
            ("tm", "Time and materials"), ("retainer", "Retainer"),
            ("diagnostic", "Diagnostic"),
        )
    )
    return (
        "<h2>project</h2>"
        f'<form class="card stack" method="post" action="/project/{project["id"]}">'
        "<label for='name'>Name</label>"
        f'<input id="name" name="name" value="{esc(project["name"])}">'
        "<div class='row2'>"
        f"<div><label for='st'>Status</label><select id='st' name='status'>{states}</select>"
        "<span class='hint'>Delivered or cancelled stamps the closing date.</span></div>"
        f"<div><label for='bl'>Billing</label><select id='bl' name='billing'>{billings}</select></div>"
        "</div>"
        "<div class='row2'>"
        "<div><label for='bg'>Budget</label>"
        f'<input id="bg" name="budget" type="number" step="100" '
        f'value="{esc(project["budget"] or "")}"></div>'
        "<div><label for='du'>Due</label>"
        f'<input id="du" name="due_at" type="date" value="{esc(project["due_at"] or "")}"></div>'
        "</div>"
        "<label for='sm'>What it is</label>"
        f'<textarea id="sm" name="summary">{esc(project["summary"] or "")}</textarea>'
        '<div><button class="primary">Save</button></div></form>'
        + '<details class="del" style="display:block;margin-top:.6rem">'
        "<summary>delete this project</summary>"
        f'<form method="post" action="/project/{project["id"]}/delete">'
        "<input name='reason' placeholder='Why, for the tombstone'>"
        "<button>Delete project</button>"
        "<span class='hint'>Removes the project. Anything logged against it "
        "survives, detached: correspondence that happened is still "
        "correspondence that happened. Use Cancelled instead when the work was "
        "real and did not go ahead.</span></form></details>"
    )


# --- the calendar -------------------------------------------------------------


def _slot_link(row: Row) -> str:
    """Where a calendar entry goes when it is clicked.

    A project's start or due date belongs to the project; everything else was
    logged against the relationship and belongs on the client's page.
    """
    if row["kind"] in PROJECT_MARKS and row["project_id"]:
        return f"/project/{row['project_id']}"
    return f"/client/{row['client_id']}"


def calendar_page(
    year: int, month: int, events: list[Row], today: date
) -> str:
    """A month, and what has to be attended in it.

    Not the record. The record is on the client's page, one line per thing that
    happened, and it is the right place for it: an email received, a document
    filed, a status changed. None of those is something to turn up to, and a
    month grid that carries them buries the two appointments of that week under
    forty entries that need nothing from anyone.

    So this reads the same rows as the timeline, filtered to the appointments —
    see `APPOINTMENT_KINDS` in the clients store — plus each project's own
    start and due dates, derived from the project so that rescheduling the work
    reschedules the calendar.
    """
    by_day: dict[str, list[Row]] = {}
    for row in events:
        by_day.setdefault((row["occurred_at"] or "")[:10], []).append(row)

    first = date(year, month, 1)
    prev = (first - timedelta(days=1)).strftime("%Y-%m")
    nxt = (first + timedelta(days=32)).replace(day=1).strftime("%Y-%m")

    weeks = calmod.Calendar(firstweekday=0).monthdatescalendar(year, month)
    body = []
    for week in weeks:
        cells = []
        for day in week:
            key = day.isoformat()
            classes = []
            if day.month != month:
                classes.append("out")
            if day == today:
                classes.append("today")
            slots = []
            for e in by_day.get(key, [])[:4]:
                mark, label = _kind(e["kind"])
                derived = " derived" if e["kind"] in PROJECT_MARKS else ""
                # A derived marker has no clock time to show; it is a day, not
                # an hour, and "00:00" would be a fact nobody asserted.
                clock = "" if derived else (e["occurred_at"] or "")[11:16] + " "
                slots.append(
                    f'<a class="slot{" done" if e["state"] == "done" else ""}{derived}" '
                    f'href="{_slot_link(e)}" '
                    f'title="{esc(e["company_name"])} — {esc(label)}'
                    f'{" — " + esc(e["subject"]) if e["subject"] else ""}">'
                    f'{esc(clock)}{esc(e["subject"] or label)}</a>'
                )
            more = len(by_day.get(key, []))
            if more > 4:
                slots.append(f'<span class="slot dim">+{more - 4} more</span>')
            cells.append(
                f'<td class="{" ".join(classes)}">'
                f'<div class="day">{day.day}</div>{"".join(slots)}</td>'
            )
        body.append(f"<tr>{''.join(cells)}</tr>")

    # The grid is drawn with a week of spill either side, so that the cells
    # belonging to the neighbouring months are not blank. The agenda below it
    # says "this month" and has to mean it.
    stamp = f"{year:04d}-{month:02d}"
    ahead = [
        e for e in events
        if e["state"] == "planned" and (e["occurred_at"] or "").startswith(stamp)
    ]
    agenda = "".join(
        f'<tr><td class="small">{_when(e)}</td>'
        f'<td><a href="/client/{e["client_id"]}">{esc(e["company_name"])}</a></td>'
        f'<td class="small">{esc(e["project_code"] or "")}</td>'
        f'<td><a href="{_slot_link(e)}">'
        f'{esc(e["subject"] or _kind(e["kind"])[1])}</a></td>'
        f'<td class="small">{esc(e["with_whom"] or "")}</td></tr>'
        for e in ahead
    )
    return (
        f'<div class="ident"><h1 style="margin:0">'
        f'{calmod.month_name[month]} {year}</h1>'
        f'<a href="/calendar?month={prev}">&larr; previous</a>'
        f'<a href="/calendar">today</a>'
        f'<a href="/calendar?month={nxt}">next &rarr;</a></div>'
        "<p class='dim small' style='max-width:70ch'>Meetings, interviews, "
        "deadlines and the start and end of each project. Not the running "
        "record: an email, a document or a change of status goes on the "
        "client's own timeline, where it can be read in order, and a diary "
        "carrying all of it would hide the two appointments of the week among "
        "forty entries nobody has to attend.</p>"
        '<table class="cal"><thead><tr>'
        + "".join(f"<th>{d}</th>" for d in
                  ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
        + f"</tr></thead><tbody>{''.join(body)}</tbody></table>"
        + (
            "<h2>still to come this month</h2>"
            "<table><thead><tr><th>when</th><th>client</th><th>project</th>"
            f"<th>what</th><th>with</th></tr></thead><tbody>{agenda}</tbody></table>"
            if agenda
            else "<p class='dim small'>Nothing is scheduled in this month.</p>"
        )
    )
