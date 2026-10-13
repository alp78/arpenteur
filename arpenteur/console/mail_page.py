"""The mail queue: what was read, what it might belong to, and nothing filed.

The page exists because the alternative was worse in both directions. Filing
automatically from a known address puts a supplier's invoice in a client's
project history; filing nothing leaves the real record in Outlook, which is
where every tool surveyed left it. So the import proposes and a person decides,
and the page is built so that deciding is quick enough to actually happen:
the matched messages first, the reason for each match spelled out, and the
whole chain visible so a reply is not judged without the message it answers.

Two things this page is careful to say out loud.

**Why a company is being proposed.** "Matched on domain example.ch" is checkable in
a second; a bare client name asks the reader to trust a join they cannot see.
When the proposal is wrong, knowing which rule produced it is what tells you
whether to fix the message or the rule.

**That filing teaches the matcher.** Accepting a message records the
counterparty's address, and its domain when that domain belongs to nobody else,
so the rest of a thread and everything after it matches on its own. That is a
real inference from one click, so it is written on the button rather than
discovered later in a table.
"""

from __future__ import annotations

from typing import Any

from ..kernel.db import Row
from . import ui
from .render import esc, page

STYLES = """
.queue { display:flex; flex-direction:column; gap:.55rem; }
.msg { background:var(--paper); border:1px solid var(--line); border-left:3px solid var(--line);
       padding:.6rem .8rem; }
.msg.matched { border-left-color:var(--accent); }
.msg-head { display:flex; gap:.6rem; align-items:baseline; flex-wrap:wrap; }
.msg-head .subj { font-weight:600; }
.msg-head .when { color:var(--dim); font-size:.78rem; margin-left:auto; }
.msg-who { font-size:.8rem; color:var(--dim); margin:.15rem 0 .35rem; }
.msg-who code { background:var(--bg); padding:.02rem .3rem; }
.msg-why { font-size:.78rem; color:var(--dim); }
.msg-why b { color:var(--ink); font-weight:600; }
.msg-acts { display:flex; gap:.45rem; align-items:center; flex-wrap:wrap;
            margin-top:.45rem; }
.msg-acts form { display:inline; margin:0; }
.msg-acts button { font-size:.78rem; padding:.15rem .55rem; }
.msg-acts select { font-size:.78rem; padding:.1rem .3rem; }
.dir { display:inline-block; border:1px solid var(--line); border-radius:1rem;
       padding:.02rem .5rem; font-size:.72rem; color:var(--dim); background:var(--paper); }
.dir.out { border-color:var(--accent); color:var(--accent); }
.no-body { font-size:.76rem; color:var(--dim); font-style:italic; }
.excerpt { font-size:.82rem; color:var(--dim); margin:.3rem 0 0;
           max-width:88ch; white-space:pre-wrap; }
.counts { color:var(--dim); font-size:.82rem; margin:0 0 .8rem; }
.links { font-size:.82rem; }
.links td { padding:.2rem .5rem .2rem 0; }
"""


def _when(row: Row | dict[str, Any]) -> str:
    when = row["sent_at"] or row["fetched_at"]
    return str(when)[:16].replace("T", " ") if when else ""


def _why(row: Row | dict[str, Any]) -> str:
    """The rule that proposed this company, in words that can be checked."""
    method, value = row["match_method"], row["match_value"]
    if not method:
        return ('<span class="msg-why">No company proposed. Filing it will '
                'teach the matcher who this address belongs to.</span>')
    gloss = {
        "address": "an address published by the company",
        "link": "an address or domain learned from a message you filed",
        "domain": "the domain of an address published by the company",
        "site": "the company's own website",
    }.get(method, method)
    return (f'<span class="msg-why">Matched on <b>{esc(value)}</b>, '
            f'{esc(gloss)}.</span>')


def _proposal(row: Row | dict[str, Any], clients: list[Row]) -> str:
    """The accept control: a client already chosen, or a list to choose from."""
    options = []
    for client in clients:
        chosen = " selected" if row["client_id"] == client["id"] else ""
        options.append(
            f'<option value="{client["id"]}"{chosen}>'
            f'{esc(client["code"])}</option>'
        )
    if not options:
        return ('<span class="msg-why">No clients yet. Promote a company on '
                'the <a href="/board">board</a> first.</span>')
    return (
        f'<form method="post" action="/mail/{row["id"]}/file">'
        f'<select name="client_id">{"".join(options)}</select> '
        f'<button type="submit">File</button></form>'
    )


def _message(row: Row | dict[str, Any], clients: list[Row], *, decided: bool) -> str:
    matched = " matched" if row["company_id"] else ""
    direction = "out" if row["direction"] == "out" else "in"
    label = "sent" if direction == "out" else "received"
    who = row["from_email"] or "?"
    others = ", ".join(list(row["to_emails"] or [])[:3])
    body = row["body"] or ""
    excerpt = " ".join(body.split())[:400]
    parts = [
        f'<div class="msg{matched}">',
        '<div class="msg-head">',
        f'<span class="dir {direction}">{label}</span>',
        f'<span class="subj">{esc(row["subject"] or "(no subject)")}</span>',
        f'<span class="when">{esc(_when(row))}</span>',
        "</div>",
        f'<div class="msg-who">from <code>{esc(who)}</code>'
        + (f' to <code>{esc(others)}</code>' if others else "")
        + f' &middot; {esc(row["folder"])}</div>',
    ]
    if row["company_name"]:
        parts.append(
            f'<div class="msg-why">Proposed: <b>{esc(row["company_name"])}</b>'
            + (f' ({esc(row["client_code"])})' if row["client_code"] else
               ' &mdash; not a client yet')
            + "</div>"
        )
    parts.append(_why(row))
    if excerpt:
        parts.append(f'<p class="excerpt">{esc(excerpt)}</p>')
    elif not decided:
        parts.append(
            '<p class="no-body">The text has not been read. Nothing unmatched '
            'is copied out of the mailbox until you file it.</p>'
        )
    if not decided:
        parts.append('<div class="msg-acts">')
        parts.append(_proposal(row, clients))
        parts.append(
            f'<form method="post" action="/mail/{row["id"]}/ignore">'
            f'<button type="submit">Ignore</button></form>'
        )
        parts.append("</div>")
    else:
        parts.append('<div class="msg-acts">')
        if row["interaction_id"] and row["client_id"]:
            parts.append(
                f'<a href="/client/{row["client_id"]}">Filed to '
                f'{esc(row["client_code"] or "the client")}</a>'
            )
        parts.append(
            f'<form method="post" action="/mail/{row["id"]}/reopen">'
            f'<button type="submit">Put back in the queue</button></form>'
        )
        parts.append("</div>")
    parts.append("</div>")
    return "".join(parts)


def mail_page(
    rows: list[Row], clients: list[Row], counts: dict[str, int], *,
    state: str = "pending", chrome: Any = None, configured: bool = True,
) -> str:
    body = [
        "<h1>Mail</h1>",
        '<p class="counts">Read from the mailbox and held here. Nothing reaches '
        "a client's timeline until you file it, and nothing in the mailbox is "
        "marked read, moved or deleted by this tool.</p>",
        ui.tabs(
            [(f"/mail?state={name}", f"{label} ({counts.get(name, 0)})")
             for name, label in (("pending", "Waiting"), ("filed", "Filed"),
                                 ("ignored", "Ignored"))],
            f"/mail?state={state}",
        ),
    ]
    if not configured:
        body.append(
            '<p class="counts">No mailbox is configured. Set '
            "<code>MAIL_IMAP_SERVER</code>, <code>MAIL_USERNAME</code> and "
            "<code>MAIL_PASSWORD</code> in <code>.env</code>. Most Swiss hosts "
            "issue one password per application rather than accepting the "
            "account password over IMAP.</p>"
        )
    elif not rows:
        body.append(
            ui.empty(
                "Nothing is waiting.",
                action='Run the <a href="/pipeline">mail stage</a> to read the '
                       "mailbox.",
            )
            if state == "pending"
            else ui.empty(f"Nothing has been {state} yet.")
        )
    decided = state != "pending"
    body.append('<div class="queue">')
    body.extend(_message(row, clients, decided=decided) for row in rows)
    body.append("</div>")
    return page("Mail", "".join(body), chrome, css=STYLES)


def links_panel(links: list[Row]) -> str:
    """Addresses and domains this company's mail is recognised by.

    Shown on the client page because it is an inference the tool made from a
    person's click, and an inference nobody can see is one nobody can correct.
    """
    if not links:
        return ""
    rows = "".join(
        f'<tr><td>{esc(link["kind"])}</td><td><code>{esc(link["value"])}</code></td>'
        f'<td class="small">{esc(link["source"])}</td>'
        f'<td><form method="post" action="/mail/link/{link["id"]}/delete">'
        f'<button type="submit">Forget</button></form></td></tr>'
        for link in links
    )
    return (
        '<section><h2>Mail recognised by</h2>'
        '<table class="links"><tbody>' + rows + "</tbody></table>"
        '<p class="small">Learned when you filed a message. Forgetting one '
        "stops future mail matching on it; what is already filed stays.</p>"
        "</section>"
    )
