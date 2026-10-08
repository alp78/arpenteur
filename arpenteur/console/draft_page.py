"""Drafts awaiting a person, and one draft read in full.

The stage writes proposals and stops. Reading them was SQL, which is how a
queue of twenty-five messages goes out unread: the point of a human review is
that it happens, and a review that costs a psql session does not.

**The whole message, never a snippet.** A card showing a subject and the first
line is enough to approve by and not enough to catch what is wrong, and what
goes wrong in these is in the body: an invented feature, a placeholder nobody
filled, a French sentence the house rules refuse. So the queue shows the full
text of one draft at a time rather than a grid of excerpts.

**The markers are shown as faults, not as text.** A draft carrying
`[[ MANQUE : lien TestFlight ]]` cannot be sent, so the page says so in a
banner and the approve button is not offered. Leaving the operator to notice a
bracketed string inside a paragraph is how it reaches a recipient.

**Approved is not sent.** Nothing in this program sends mail. `reviewed_at`
says a person read it; `sent_at` is set afterwards, by hand, by whoever sent
it from their own client. Collapsing the two would make the record claim
something it cannot know.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from ..kernel.db import Row
from . import ui
from .ui import esc
from .render import page

STYLES = """
.draft-tally { display:flex; gap:1.4rem; margin:.6rem 0 1.2rem;
               font-size:.85rem; }
.draft-tally b { font-size:1.3rem; display:block; }
.draft-q { display:flex; flex-direction:column; gap:.5rem; }
.draft-row { display:grid; grid-template-columns:3rem 1fr 14rem 5rem auto;
             gap:.7rem; align-items:center; padding:.55rem .7rem;
             border:1px solid var(--line); border-radius:6px; }
.draft-row.flawed { border-color:var(--bad); }
.draft-row .who { font-weight:600; }
.draft-row .addr { font-family:ui-monospace, monospace; font-size:.8rem;
                   color:var(--muted); overflow:hidden;
                   text-overflow:ellipsis; white-space:nowrap; }
.lang { text-transform:uppercase; font-size:.72rem; letter-spacing:.04em;
        color:var(--muted); }
.one-draft { max-width:46rem; }
.one-draft .meta { display:flex; gap:1.1rem; flex-wrap:wrap;
                   font-size:.8rem; color:var(--muted); margin:.2rem 0 1rem; }
.one-draft .subject { font-weight:600; font-size:1.05rem; margin:.8rem 0 .3rem; }
.one-draft pre.body { white-space:pre-wrap; font-family:inherit;
                      font-size:.92rem; line-height:1.55;
                      background:var(--panel); padding:1rem 1.1rem;
                      border-radius:6px; border:1px solid var(--line);
                      margin:0; }
.one-draft .acts { display:flex; gap:.6rem; margin-top:1rem;
                   flex-wrap:wrap; align-items:center; }
.marker { background:var(--bad-bg); font-weight:600; padding:0 .2rem; }
"""

# What makes a draft unsendable. The stage writes these in place of a setting
# it was not given, so they are the one thing a review must never pass.
MARKER = "[[ MANQUE"


def shell(body: str, chrome: Any = None) -> str:
    return page("arpenteur", body, chrome, css=STYLES)


def flawed(row: Row) -> bool:
    return MARKER in (row["body"] or "") or MARKER in (row["subject"] or "")


def _ago(when: str | None, today: date | None = None) -> str:
    """How long ago, from an ISO string.

    A timestamp arrives here as text, which is what this codebase's row
    factory produces and what `programme_page._sitting` already assumes. The
    first version of this took a datetime and raised on every row.
    """
    if not when:
        return ""
    try:
        then = date.fromisoformat(str(when)[:10])
    except ValueError:
        return ""
    days = ((today or date.today()) - then).days
    if days <= 0:
        return "today"
    return f"{days}d" if days < 60 else then.isoformat()


def queue_page(
    rows: list[Row], tally: dict[str, int], *, note: str = "",
    stale: int = 0, language: str = "",
) -> str:
    """The review queue, with what cannot be sent marked before it is opened."""
    listed = "".join(_row(row) for row in rows)
    body = (
        f'<div class="draft-q">{listed}</div>'
        if rows
        else ui.empty(
            "No drafts awaiting review. `arpenteur draft` writes proposals "
            "here; they stay until somebody reads them."
        )
    )
    banners = ui.banner(note, kind="good") if note else ""
    bad = sum(1 for row in rows if flawed(row))
    if bad:
        banners += ui.banner(
            f"{bad} of these carry a [[ MANQUE ]] marker where a setting was "
            "not filled in. They cannot be approved until arpenteur.toml "
            "[enrol] is complete and `arpenteur draft` has run again.",
            kind="bad",
        )
    if stale:
        banners += ui.banner(
            f"{stale} unsent draft(s) were written against an older version of "
            "the brief. Rewriting the brief does not rewrite them; run "
            "`arpenteur draft` again to replace them.",
            kind="warn",
        )
    return (
        "<h1>Drafts</h1>"
        "<p class='lede'>Proposals, each for one mailbox at one firm. Nothing "
        "here has been sent, and nothing in this program sends: approving "
        "records that a person read it.</p>"
        + _tally(tally)
        + banners
        + _filter(language)
        + body
    )


def _tally(tally: dict[str, int]) -> str:
    cells = "".join(
        f"<span>{esc(label)}<b>{tally.get(key, 0):,}</b></span>"
        for key, label in (
            ("awaiting", "awaiting review"),
            ("approved", "approved, not sent"),
            ("sent", "sent"),
            ("briefs", "brief versions"),
        )
    )
    return f'<div class="draft-tally">{cells}</div>'


def _filter(language: str) -> str:
    options = "".join(
        f'<option value="{code}"{" selected" if language == code else ""}>'
        f"{label}</option>"
        for code, label in (("", "every language"), ("fr", "français"),
                            ("de", "Deutsch"), ("it", "italiano"))
    )
    return (
        '<form method="get" action="/drafts" class="row" '
        'style="margin:0 0 .9rem">'
        f'<select name="language">{options}</select> '
        '<button type="submit">Show</button></form>'
    )


def _row(row: Row) -> str:
    bad = ' flawed' if flawed(row) else ""
    mark = " ⚠" if flawed(row) else ""
    return (
        f'<div class="draft-row{bad}">'
        f'<span class="lang">{esc(row["language"] or "?")}</span>'
        f'<span class="who">{esc(row["company_name"])}{mark}<br>'
        f'<span class="addr">{esc(row["subject"] or "(no subject)")}</span></span>'
        f'<span class="addr">{esc(row["address"] or "")}</span>'
        f'<span class="addr">{esc(_ago(row["created_at"]))}</span>'
        f'<a class="btn" href="/draft/{row["id"]}">Read</a>'
        "</div>"
    )


def one_page(row: Row, *, note: str = "") -> str:
    """One draft, whole, with the actions a reviewer has.

    `Approve` is withheld from a draft carrying a marker rather than shown and
    refused: an action offered and then rejected teaches the operator to click
    it and read the error, which is the opposite of reading the message.
    """
    # The marker wrapped whole, by pattern rather than by two replacements
    # that have to meet in the middle. It is escaped first, so the pattern runs
    # over the escaped text and cannot reopen a tag.
    body = re.sub(
        r"(\[\[ MANQUE[^\]]*\]\])",
        r'<span class="marker">\1</span>',
        esc(row["body"] or ""),
    )
    reviewed = row["reviewed_at"] is not None
    acts = []
    if flawed(row):
        acts.append(
            "<span class='muted'>Not approvable: fill arpenteur.toml [enrol] "
            "and run <code>arpenteur draft</code> again.</span>"
        )
    elif reviewed:
        acts.append(_post(f"/draft/{row['id']}/sent", "Mark as sent"))
        acts.append(_post(f"/draft/{row['id']}/unreview", "Undo approval"))
    else:
        acts.append(_post(f"/draft/{row['id']}/review", "Approve"))
    acts.append(_post(f"/draft/{row['id']}/drop", "Discard"))
    state = (
        "sent " + _ago(row["sent_at"]) if row["sent_at"]
        else ("approved " + _ago(row["reviewed_at"]) if reviewed
              else "awaiting review")
    )
    return (
        '<div class="one-draft">'
        f'<a href="/drafts">&larr; drafts</a>'
        f'<h1>{esc(row["company_name"])}</h1>'
        '<div class="meta">'
        f'<span>{esc(row["canton"] or "")} {esc(row["legal_seat"] or "")}</span>'
        f'<span>{esc(row["address"] or "")}</span>'
        f'<span>{esc(row["language"] or "")}</span>'
        f'<span>{esc(state)}</span>'
        f'<span>brief {esc((row["brief_digest"] or "?")[:8])}</span>'
        f'<span>{esc(row["model"] or "")}</span>'
        "</div>"
        + (ui.banner(note, kind="good") if note else "")
        + f'<div class="subject">{esc(row["subject"] or "(no subject)")}</div>'
        f'<pre class="body">{body}</pre>'
        f'<div class="acts">{"".join(acts)}</div>'
        f'<p class="muted" style="margin-top:1.2rem;font-size:.8rem">'
        "Registered purpose, which is the only thing this message may assert "
        f"about them: {esc(row['purpose'] or '(not recorded)')}</p>"
        "</div>"
    )


def _post(action: str, label: str) -> str:
    return (
        f'<form method="post" action="{action}" style="display:inline">'
        f'<button type="submit">{esc(label)}</button></form>'
    )
