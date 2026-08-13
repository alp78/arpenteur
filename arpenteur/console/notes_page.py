"""Notes: the operator's own writing about a client, in Markdown.

Written in whatever editor is already open, imported by path, and read here.
The stored form is the Markdown, always; HTML is made on the way to the page
and never kept, because a stored rendering drifts from its source the first
time the source is edited.

Two things the pages are careful about.

**The file is the master when there is one.** A note imported from a path shows
where it came from and re-imports in place, so editing the file and running the
command again does the obvious thing. A note typed here has no path and is
edited here. Both are the same row; which one is authoritative is visible
rather than assumed.

**A note is prose, and prose has to be readable.** The rendered body gets a
measure and a type scale of its own rather than inheriting the table styling
that surrounds it, because 8'000 characters of French set at full page width is
a document nobody finishes.
"""

from __future__ import annotations

from ..kernel.db import Row
from ..markdown import render
from .render import esc, page

STYLES = """
.note-doc { background:#fff; border:1px solid var(--line); padding:1.1rem 1.4rem;
            margin:0 0 1rem; }
.note-doc > :first-child { margin-top:0; }
.note-doc h1 { font-size:1.25rem; margin:1.2rem 0 .4rem; }
.note-doc h2 { font-size:1.02rem; margin:1.3rem 0 .35rem; color:var(--ink);
               text-transform:none; letter-spacing:0; }
.note-doc h3 { font-size:.94rem; margin:1rem 0 .3rem; }
.note-doc p, .note-doc li { max-width:74ch; }
.note-doc ul, .note-doc ol { padding-left:1.3rem; }
.note-doc li { margin:.18rem 0; }
.note-doc li > ul, .note-doc li > ol { margin:.2rem 0; }
.note-doc code { background:var(--bg); padding:.05rem .3rem; border-radius:2px;
                 font-size:.88em; }
.note-doc pre { background:var(--bg); border:1px solid var(--line);
                padding:.7rem .9rem; overflow:auto; }
.note-doc blockquote { border-left:3px solid var(--line); margin:.6rem 0;
                       padding:.1rem 0 .1rem .9rem; color:var(--dim); }
.note-doc table { width:auto; margin:.6rem 0; }
.note-doc hr { border:0; border-top:1px solid var(--line); margin:1.2rem 0; }
.note-head { display:flex; gap:.7rem; align-items:baseline; flex-wrap:wrap;
             margin-bottom:.3rem; }
.note-head h2 { margin:0; text-transform:none; letter-spacing:0; color:var(--ink);
                font-size:1.05rem; }
.note-meta { font-size:.78rem; color:var(--dim); }
.note-meta code { background:var(--bg); padding:.05rem .3rem; }
.tag { display:inline-block; border:1px solid var(--line); border-radius:1rem;
       padding:.02rem .5rem; font-size:.74rem; color:var(--dim); background:#fff; }
.pin { color:var(--accent); font-weight:700; }
.note-acts { display:flex; gap:.5rem; align-items:center; flex-wrap:wrap;
             margin-left:auto; }
.note-acts form { display:inline; }
.note-acts button { font-size:.78rem; padding:.15rem .5rem; }
textarea.md { min-height:16rem; font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; }
"""


def shell(body: str, territory: str = "") -> str:
    return page("arpenteur", body, territory, css=STYLES)


def _meta(note: Row) -> str:
    bits = []
    if note["pinned"]:
        bits.append('<span class="pin">pinned</span>')
    stamp = (note["updated_at"] or "")[:16].replace("T", " ")
    bits.append(f"updated {esc(stamp)}")
    if note["source_path"]:
        # Shown because it says which copy is the master. A note with a path is
        # edited in a file and re-imported; one without is edited here.
        bits.append(f"from <code>{esc(note['source_path'])}</code>")
    if note["author"]:
        bits.append(esc(note["author"]))
    tags = "".join(f'<span class="tag">{esc(t)}</span> ' for t in (note["tags"] or []))
    return f'<div class="note-meta">{" &middot; ".join(bits)} {tags}</div>'


def _acts(note: Row, *, on_page: bool = False) -> str:
    pin = "unpin" if note["pinned"] else "pin"
    return (
        '<div class="note-acts">'
        + (
            f'<a href="/note/{note["id"]}">open</a> '
            if not on_page
            else ""
        )
        + f'<a href="/note/{note["id"]}.md">markdown</a> '
        f'<form method="post" action="/note/{note["id"]}/pin">'
        f'<input type="hidden" name="pinned" value="{0 if note["pinned"] else 1}">'
        f"<button>{pin}</button></form>"
        '<details class="del"><summary>delete</summary>'
        f'<form method="post" action="/note/{note["id"]}/delete">'
        "<input name='reason' placeholder='Why, for the tombstone'>"
        "<button>Delete this note</button>"
        "<span class='hint'>Removes the note and leaves a line in the record "
        "saying what went and why. The file it came from is untouched.</span>"
        "</form></details></div>"
    )


def notes_page(
    client: Row, company: Row, notes: list[Row], *, q: str = "", note: str = "",
    projects: list[Row] | None = None,
) -> str:
    head = (
        f'<p class="crumbs"><a href="/clients">Clients</a> / '
        f'<a href="/client/{client["id"]}">{esc(client["code"])}</a> / notes</p>'
        f'<h1>Notes on {esc(company["name"])}</h1>'
        "<p class='lede'>Your own writing about this client, in Markdown. "
        "Written wherever you write, imported by path, and read here. Agents "
        "reading the client see these alongside the dossier.</p>"
    )
    banner = f'<div class="warn">{esc(note)}</div>' if note else ""

    if notes:
        body = "".join(
            f'<article id="note-{n["id"]}">'
            f'<div class="note-head"><h2>{esc(n["title"])}</h2>{_acts(n)}</div>'
            f"{_meta(n)}"
            f'<div class="note-doc">{render(n["body"])}</div></article>'
            for n in notes
        )
    elif q:
        body = f'<p class="dim">Nothing here matches “{esc(q)}”.</p>'
    else:
        body = (
            '<p class="dim">No notes yet. Paste one below, or import a file '
            "with <code>arpenteur note add "
            f"{esc(client['code'])} path/to/note.md</code>.</p>"
        )

    return (
        head + banner
        + '<form class="filters" method="get" '
        f'action="/client/{client["id"]}/notes">'
        f'<div class="ctl ctl-wide"><label>Search these notes</label>'
        f'<input name="q" value="{esc(q)}" placeholder="A word in any of them"></div>'
        '<div class="ctl"><label>&nbsp;</label><button class="primary">Search</button>'
        "</div></form>"
        + _write_form(client["id"], projects or [])
        + body
    )


def _write_form(client_id: int, projects: list[Row]) -> str:
    options = "".join(
        f'<option value="{p["id"]}">{esc(p["code"])} — {esc(p["name"])}</option>'
        for p in projects
    )
    return (
        '<details class="card"><summary><b>Write a note</b> '
        '<span class="dim small">Markdown, or upload a .md file</span></summary>'
        f'<form class="stack" method="post" action="/client/{client_id}/notes" '
        'enctype="multipart/form-data" style="margin-top:.7rem">'
        "<label for='nb'>Note</label>"
        "<textarea id='nb' class='md' name='body' placeholder='# Titre&#10;&#10;"
        "Le premier titre devient le nom de la note.'></textarea>"
        "<div class='row2'>"
        "<div><label for='nf'>or a file</label>"
        "<input id='nf' type='file' name='file' accept='.md,.markdown,.txt'>"
        "<span class='hint'>Imported by path, so running the same import again "
        "updates this note instead of adding a second one.</span></div>"
        "<div><label for='nt'>Tags</label>"
        "<input id='nt' name='tags' placeholder='accès, contrat'>"
        "<label for='np' style='margin-top:.5rem'>Project</label>"
        f"<select id='np' name='project_id'><option value=''>The relationship, "
        f"not a project</option>{options}</select></div></div>"
        '<div><button class="primary">Save the note</button></div></form></details>'
    )


def one_note(client: Row, company: Row, note: Row) -> str:
    return (
        f'<p class="crumbs"><a href="/clients">Clients</a> / '
        f'<a href="/client/{client["id"]}">{esc(client["code"])}</a> / '
        f'<a href="/client/{client["id"]}/notes">notes</a></p>'
        f'<div class="note-head"><h1 style="margin:0">{esc(note["title"])}</h1>'
        f"{_acts(note, on_page=True)}</div>"
        f"{_meta(note)}"
        f'<div class="note-doc">{render(note["body"])}</div>'
        + _edit_form(note)
    )


def _edit_form(note: Row) -> str:
    """Editing here, with the source path shown when there is one.

    Not hidden behind a warning: the row is the same row either way, and a
    console that refused to edit an imported note would be enforcing a rule
    about somebody's own filesystem. It says where the file is so that a person
    can decide which copy they mean to change.
    """
    warning = (
        "<span class='hint'>This note was imported from "
        f"<code>{esc(note['source_path'])}</code>. Editing here changes the "
        "stored copy; the file is untouched, and importing it again will "
        "overwrite what you write.</span>"
        if note["source_path"]
        else "<span class='hint'>The first heading becomes the title.</span>"
    )
    return (
        '<details class="card" style="margin-top:1rem"><summary><b>Edit</b></summary>'
        f'<form class="stack" method="post" action="/note/{note["id"]}" '
        'style="margin-top:.7rem">'
        f'<textarea class="md" name="body">{esc(note["body"])}</textarea>'
        "<label for='et'>Tags</label>"
        f'<input id="et" name="tags" value="{esc(", ".join(note["tags"] or []))}">'
        + warning
        + '<div><button class="primary">Save</button></div></form></details>'
    )


def strip(client_id: int, count: int, newest: Row | None) -> str:
    """The way in from the client page, and what is in there.

    A bare "Notes" link says nothing. The count and the most recent title are
    what make it worth following.
    """
    link = f'<a class="go" href="/client/{client_id}/notes">Notes'
    if not count:
        return f'{link}</a><span class="dim small">none yet</span>'
    latest = (
        f'<span class="dim small">latest: {esc(newest["title"])}</span>'
        if newest is not None
        else ""
    )
    return f"{link} ({count})</a>{latest}"
