"""Programmes: one endeavour across many companies, drawn as a board.

The client pages answer "what is happening with this one organisation". This
answers the other shape of work: one application, one pilot, one recruitment
drive, sent to fifty companies at once, where the question every morning is how
many replied, how many are testing, and who has gone quiet.

It is deliberately the board's twin. The columns come from the programme's own
`stages` rather than from a constant, because the next programme will not speak
of `onboarding`, but the card, the drag, the column counts and the fold that
works without JavaScript are the same furniture doing the same job. Reusing the
drag handler from `board.py` is what keeps them the same board rather than two
that diverge.

Three things this page shows that the prospect board cannot. A member's stage
belongs to one programme, so the same agency can be testing here and untouched
elsewhere. Inbound mail is counted per card, which is how "who replied" is
answered without opening a mailbox. And feedback is collected under the board,
because what fifty testers said is the output of the testing phase and reading
it one card at a time is how it goes unread.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..kernel.db import Row
from . import ui
from .board import STYLES as BOARD_STYLES
from .board import drag_script
from .render import esc, page

STYLES = BOARD_STYLES + """
.prog-list { display:flex; flex-direction:column; gap:.6rem; }
.prog { display:flex; gap:.8rem; align-items:baseline; flex-wrap:wrap;
        border-bottom:1px solid var(--line); padding:.55rem 0; }
.prog .code { font-weight:600; letter-spacing:.03em; text-decoration:none; }
.prog .what { color:var(--dim); font-size:.82rem; flex-basis:100%; margin:0; }
.prog .tally { display:flex; gap:.35rem; flex-wrap:wrap; margin-left:auto; }
.prog .tally span { background:var(--soft); border:1px solid var(--line);
                    border-radius:2px; padding:0 .35rem; font-size:.74rem;
                    color:var(--dim); }
.prog .tally b { color:var(--ink); font-variant-numeric:tabular-nums; }
.kard .mail { color:var(--good); font-weight:600; }
.kard .mail.cold { color:var(--dim); font-weight:400; }
.kard .contact { font-size:.74rem; color:var(--dim); margin-top:.2rem; }
.kard details form { flex-wrap:wrap; }
.kard details textarea { width:100%; font-size:.75rem; font-family:inherit;
                         margin-top:.25rem; }
.say { display:flex; flex-direction:column; gap:.5rem; }
.say article { border-bottom:1px solid var(--line); padding-bottom:.45rem; }
.say .head { display:flex; gap:.5rem; align-items:baseline; font-size:.8rem; }
.say .head b { font-weight:600; }
.say .head .when { color:var(--dim); font-variant-numeric:tabular-nums; }
.say .kind { border-radius:2px; padding:0 .35rem; font-size:.72rem;
             border:1px solid var(--line); }
.say .kind-bug { background:var(--bad-bg); border-color:var(--bad); }
.say .kind-request { background:var(--caution-bg); }
.say .kind-praise { background:var(--good-bg); }
.say p { margin:.2rem 0 0; font-size:.85rem; }
.stage-edit textarea { width:100%; min-height:7rem; font-family:inherit;
                       font-size:.82rem; }
"""

def shell(body: str, chrome: Any = None) -> str:
    return page("", body, chrome, css=STYLES)


# --- the list -----------------------------------------------------------------


def programmes_page(rows: list[Row]) -> str:
    """Every programme, with its members counted by stage.

    The tally is the whole row. A programme with forty members all still in the
    first column and one with forty spread across testing and live are the same
    number and completely different situations, and a single total says so
    neither way.
    """
    listed = "".join(_programme_row(row) for row in rows)
    body = (
        f'<div class="prog-list">{listed}</div>'
        if rows
        else ui.empty(
            "No programmes yet. A programme is one endeavour across many "
            "companies: an application sent to fifty agencies, a pilot, a "
            "recruitment drive. Each company sits at its own stage inside it.",
            action='<a href="#start">Start the first one</a> below.',
        )
    )
    return (
        "<h1>Programmes</h1>"
        "<p class='lede'>One endeavour, many companies, each at its own stage. "
        "Distinct from a project, which is one client's piece of work, and from "
        "the board, where a company has one state across everything.</p>"
        + body
        + _new_form()
    )


def _programme_row(row: Row) -> str:
    by_stage = dict(row["by_stage"] or {})
    tally = "".join(
        f"<span>{esc(stage)} <b>{by_stage[stage]}</b></span>"
        for stage in (row["stages"] or [])
        if by_stage.get(stage)
    )
    dates = " to ".join(
        part for part in ((row["started_at"] or "")[:10], (row["released_at"] or "")[:10])
        if part
    )
    what = f'<p class="what">{esc(row["what"])}</p>' if row["what"] else ""
    return (
        '<div class="prog">'
        f'<a class="code" href="/programme/{row["id"]}">{esc(row["code"])}</a>'
        f'<span>{esc(row["name"])}</span>'
        f'<span class="dim">{esc(row["status"])}</span>'
        f'<span class="dim">{esc(dates)}</span>'
        f'<span class="dim">{row["members"]} member(s)</span>'
        f'<div class="tally">{tally}</div>{what}</div>'
    )


def _new_form() -> str:
    return (
        '<h2 id="start">start a programme</h2>'
        + ui.toolbar(
            ui.text_field("code", "Code", placeholder="FLATCHECK", required=True,
                          hint="Its own reference, said out loud and written "
                               "in a contract.")
            + ui.text_field("name", "Name", placeholder="Etat des lieux app",
                            wide=True, required=True)
            + ui.text_field("what", "What it is", wide=True,
                            placeholder="Handover application, sent to régies")
            + "<span class='hint' style='flex-basis:100%'>It starts with the "
              "default stages, which you can rewrite on its own page. Nothing "
              "is sent from here and nothing is scheduled: a programme is a "
              "place to record what happened.</span>",
            action="/programmes/new", method="post", submit="Start it",
            css="card bring",
        )
    )


# --- one programme ------------------------------------------------------------


def _sitting(since: str | None, today: date) -> str:
    """How long this member has been at this stage.

    The same question the prospect board asks, answered from the same table.
    A card at `testing` for five days and one there for five months look
    identical on a board that does not say.
    """
    if not since:
        return ""
    try:
        moved = date.fromisoformat((since or "")[:10])
    except ValueError:
        return ""
    days = (today - moved).days
    if days <= 0:
        return '<span class="sat">today</span>'
    label = f"{days}d" if days < 14 else f"{days // 7}w"
    late = " long" if days >= 21 else ""
    return (
        f'<span class="sat{late}" title="At this stage since {since[:10]}">'
        f"{label}</span>"
    )


def _card(
    row: Row, stages: list[str], client: Row | None, today: date,
    since: str | None,
) -> str:
    """One member, and the two ways to move it.

    Dragging is the fast one; the fold underneath posts the same change without
    JavaScript, from a keyboard and on a phone. The second fold is the one this
    board has and the other does not: somewhere to type what they said, on the
    card of the company that said it, because a feedback note filed anywhere
    else is a feedback note nobody attributes.
    """
    marks = []
    inbound = int(row["inbound"] or 0)
    if inbound:
        when = (row["last_inbound"] or "")[:10]
        marks.append(
            f'<span class="mail" title="Inbound messages held from this '
            f'company, last on {esc(when)}. Read only: a message arriving '
            f'never moves a card.">{inbound} in</span>'
        )
    else:
        marks.append('<span class="mail cold" title="No inbound mail held">no reply</span>')
    sat = _sitting(since, today)
    if sat:
        marks.append(sat)
    if row["feedback"]:
        marks.append(f'<span class="seg">{row["feedback"]} said</span>')
    if client is not None:
        marks.append(
            f'<a class="chip" href="/client/{client["id"]}" '
            f'title="Already a client">{esc(client["code"])}</a>'
        )

    options = "".join(
        f'<option value="{esc(s)}"{" selected" if s == row["stage"] else ""}>'
        f"{esc(s)}</option>"
        for s in stages
    )
    move = (
        "<details><summary>move</summary>"
        f'<form method="post" action="/member/{row["id"]}/move">'
        + ui.sr_label(f"Move {row['company_name']} to", f"mv-{row['id']}")
        + f'<select name="stage" id="mv-{row["id"]}">{options}</select>'
        "<button>go</button></form></details>"
    )
    say = (
        "<details><summary>they said</summary>"
        f'<form method="post" action="/member/{row["id"]}/feedback">'
        + ui.sr_label("What kind of remark", f"fk-{row['id']}")
        + f'<select name="kind" id="fk-{row["id"]}">'
        '<option value="note">note</option><option value="bug">bug</option>'
        '<option value="request">request</option>'
        '<option value="praise">praise</option></select>'
        + ui.sr_label(f"What {row['company_name']} told you", f"fb-{row['id']}")
        + f'<textarea name="body" id="fb-{row["id"]}" rows="2" required '
        'placeholder="What they told you"></textarea>'
        "<button>keep it</button></form></details>"
    )
    contact = (
        f'<div class="contact">{esc(row["contact"])}</div>' if row["contact"] else ""
    )
    return (
        f'<article class="kard" draggable="true" data-id="{row["id"]}">'
        f'<a class="who" href="/company/{row["company_id"]}">'
        f'{esc(row["company_name"])}</a>'
        f'<div class="meta">{esc(row["canton"] or "")} &middot; '
        f'{esc(row["legal_form_label"] or "")}</div>'
        f'{contact}<div class="marks">{"".join(marks)}</div>'
        f"{move}{say}</article>"
    )


def _column(
    stage: str, rows: list[Row], stages: list[str], clients: dict[int, Row],
    today: date, moved: dict[int, str], notes: dict[str, str],
) -> str:
    cards = "".join(
        _card(row, stages, clients.get(int(row["company_id"])), today,
              moved.get(int(row["id"])))
        for row in rows
    )
    gloss = notes.get(stage, "")
    return (
        f'<section class="col"><h3 title="{esc(gloss)}">'
        f'{esc(stage)}<b data-count>{len(rows)}</b></h3>'
        f'<div class="why">{esc(gloss)}</div>'
        f'<div class="drop" data-state="{esc(stage)}" data-lane="">'
        f"{cards}</div></section>"
    )


def programme_page(
    programme: Row, members: list[Row], *, clients: dict[int, Row],
    today: date, moved: dict[int, str], feedback: list[Row],
    sectors: list[str], cantons: list[str],
) -> str:
    stages = list(programme["stages"] or [])
    notes = dict(programme["stage_notes"] or {})
    grouped: dict[str, list[Row]] = {stage: [] for stage in stages}
    for row in members:
        grouped.setdefault(row["stage"], []).append(row)

    columns = "".join(
        _column(stage, grouped.get(stage, []), stages, clients, today, moved, notes)
        for stage in stages
    )
    banner = ""
    if not members:
        banner += ui.banner(
            "No members yet. Bring a shortlist in from Companies below, or add "
            "companies one at a time by UID. Nothing arrives here by scoring "
            "well: a company is a member because somebody put it here.",
            kind="note",
        )
    dates = " to ".join(
        part for part in ((programme["started_at"] or "")[:10],
                          (programme["released_at"] or "")[:10])
        if part
    )
    return (
        ui.crumbs([("/programmes", "Programmes"), ("", programme["code"])])
        + f'<h1>{esc(programme["code"])} · {esc(programme["name"])}</h1>'
        + (f'<p class="lede">{esc(programme["what"])}</p>'
           if programme["what"] else "")
        + f'<p class="dim">{esc(programme["status"])}'
        + (f' · {esc(dates)}' if dates else "")
        + f' · {len(members)} member(s)'
        + f' · <a href="/programme/{programme["id"]}/ledger">ledger of every'
          ' firm</a></p>'
        + banner
        + f'<div class="lane"><div class="board">{columns}</div></div>'
        + _bring_in(programme["id"], sectors, cantons)
        + _add_one(programme["id"])
        + _feedback(feedback)
        + _stage_editor(programme["id"], stages, notes)
        + drag_script(f"/programme/{programme['id']}/move", "member_id")
    )


def _bring_in(programme_id: int, sectors: list[str], cantons: list[str]) -> str:
    return (
        "<h2>bring in a shortlist</h2>"
        + ui.toolbar(
            ui.select_field("sector", "Sector", sectors, blank="Any sector")
            + ui.text_field("canton", "Cantons", placeholder="VD,GE",
                            hint="Comma separated, or blank for all.")
            + ui.select_field("band", "Fit band", ["high", "medium", "low"],
                              blank="Any band")
            + ui.text_field("limit", "How many", value="25", kind="number")
            + "<span class='hint' style='flex-basis:100%'>Puts companies "
              "matching this into the first stage. It only ever adds: a member "
              "already at &quot;testing&quot; is left where it is, so running "
              "this again after another sweep cannot walk anybody back to the "
              "start.</span>",
            action=f"/programme/{programme_id}/bring-in", method="post",
            submit="Add them", css="card bring",
        )
    )


def _add_one(programme_id: int) -> str:
    return ui.toolbar(
        ui.text_field("uid", "Company", placeholder="CHE-123.456.789",
                      hint="A UID, or part of a name.", wide=True, required=True)
        + ui.text_field("contact", "Who you deal with", wide=True,
                        placeholder="Mme Dubois, direction"),
        action=f"/programme/{programme_id}/members", method="post",
        submit="Add one", css="card bring",
    )


def _feedback(rows: list[Row]) -> str:
    """What every member said, in one stream.

    Per programme rather than per member, because "what are they all telling
    us" is the question that decides what gets fixed and it cannot be asked one
    card at a time.
    """
    if not rows:
        return ui.panel(
            "what they said", "",
            note="Nothing yet. Feedback is typed on a member's card, under "
                 "\"they said\", so it stays attached to whoever said it.",
            always=True,
        )
    listed = "".join(
        "<article>"
        f'<div class="head"><span class="kind kind-{esc(row["kind"])}">'
        f'{esc(row["kind"])}</span>'
        f'<b><a href="/company/{row["company_id"]}">'
        f'{esc(row["company_name"])}</a></b>'
        f'<span class="dim">{esc(row["stage"])}</span>'
        f'<span class="when">{esc((row["occurred_at"] or "")[:10])}</span></div>'
        f'<p>{esc(row["body"])}</p></article>'
        for row in rows
    )
    return ui.panel(
        "what they said", f'<div class="say">{listed}</div>',
        note="Newest first, across every member.",
    )


def _stage_editor(programme_id: int, stages: list[str], notes: dict[str, str]) -> str:
    """The pipeline itself, as text.

    One stage per line, an optional gloss after a colon. A textarea rather than
    a row of controls because the order is the content and reordering a list is
    what a textarea is already good at.

    Removing a stage that members are standing in is refused rather than
    absorbed, and the refusal names the count. Absorbing it would mean deleting
    those members or moving them somewhere nobody chose.
    """
    lines = "\n".join(
        stage + (f": {notes[stage]}" if notes.get(stage) else "") for stage in stages
    )
    return ui.panel(
        "stages",
        '<form method="post" class="stage-edit" '
        f'action="/programme/{programme_id}/stages">'
        + ui.sr_label("Stages, one per line", "stage-lines")
        + f'<textarea name="stages" id="stage-lines" rows="{max(6, len(stages) + 1)}">{esc(lines)}'
        "</textarea>"
        '<p class="hint">One per line, in the order the board draws them. Text '
        "after a colon is the note under the column head, which is what stops a "
        "column called &quot;replied&quot; being used for three different "
        "things by the end of a month.</p>"
        '<button class="primary">Save the stages</button></form>',
        note="This programme's own pipeline. Another programme has its own.",
    )
