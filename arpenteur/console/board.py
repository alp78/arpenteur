"""The board: where every company being worked stands, in columns.

The survey list answers "what is out there" and sorts 184'939 rows by score.
That is the wrong shape for the other question an operator asks every morning,
which is "what is moving and what has stalled". A list cannot show that: a
company at `contacted` since June looks exactly like one contacted yesterday,
and the eight companies waiting on a reply are scattered through nine hundred
that nobody has touched.

So: one column per state, one card per company, and the card is dragged from
column to column. The count in a column head is the number that matters — five
contacted and nothing replied is a different instruction from five replied and
no meetings booked.

**Swimlanes** cut the board horizontally by something that does not change when
the state does: fit band, canton, owner, how overdue the next action is,
or sector. The overdue one is the useful one and the reason the option
exists. Grouping by *segment* is deliberately not offered, and the difference
is arithmetic rather than taste: a company carries several segments, so the
card would have to appear in several lanes, and a card you can drag in one lane
while a stale copy of it sits in another is worse than no grouping at all. A
company carries one sector, so a sector lane holds each card exactly once.
Segment stays a filter, where one company is one row.

The board is a scout page. It reads `intel.prospect` and knows nothing about
`crm`: the client chip on a won card is passed in by the console, which is
allowed to know both. That is the module rule holding in the one place it was
most tempting to break.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from ..kernel.db import Row
from . import ui
from .render import esc, page

# What each column means, written for someone deciding where to drop a card.
# A column called "replied" with no gloss gets used for three different things
# by the end of a month, and then the counts say nothing.
STATE_NOTES: dict[str, str] = {
    "new": "On the board, not yet looked at.",
    "researching": "Being read: the site, the notices, who decides.",
    "contacted": "Written to or called. Nothing back yet.",
    "replied": "They answered. The conversation is live.",
    "meeting": "A meeting is agreed, or has happened.",
    "won": "Work agreed. Promote it to a client.",
    "lost": "Not going ahead. Kept, with the reason in the notes.",
    "parked": "Real, but not now.",
}

LANES: tuple[tuple[str, str], ...] = (
    ("", "No swimlanes"),
    ("due", "What the next action is doing"),
    ("band", "Fit band"),
    ("canton", "Canton"),
    ("owner", "Owner"),
    ("sector", "Sector"),
)

# Ordering for the computed lanes. Overdue first: a board is read top down and
# the thing that has slipped should not be below the thing that has not.
DUE_LANES = ("overdue", "today", "this week", "later", "no next action")
BAND_LANES = ("high", "medium", "low", "excluded", "not scored")

STYLES = """
.board { display:flex; gap:.7rem; overflow-x:auto; padding-bottom:.8rem;
         align-items:flex-start; }
.col { flex:0 0 15.5rem; background:var(--soft); border:1px solid var(--line);
       border-radius:3px; display:flex; flex-direction:column; min-height:5rem; }
.col > h3 { margin:0; padding:.5rem .6rem .3rem; font-size:.74rem;
            text-transform:uppercase; letter-spacing:.06em; color:var(--dim);
            display:flex; gap:.4rem; align-items:baseline; }
.col > h3 b { color:var(--ink); font-variant-numeric:tabular-nums;
              margin-left:auto; font-size:.9rem; }
.col .why { padding:0 .6rem .4rem; font-size:.72rem; color:var(--dim);
            line-height:1.35; }
.drop { padding:.35rem; display:flex; flex-direction:column; gap:.35rem;
        min-height:3.2rem; flex:1; }
.drop.over { background:var(--good-bg); outline:2px dashed var(--good); outline-offset:-3px; }
.kard { background:var(--paper); border:1px solid var(--line); border-left:3px solid var(--line);
        padding:.45rem .55rem; font-size:.84rem; cursor:grab; }
.kard.dragging { opacity:.4; }
.kard.band-high { border-left-color:var(--good); }
.kard.band-medium { border-left-color:var(--caution); }
.kard.overdue { background:var(--bad-bg); }
.kard .who { font-weight:600; text-decoration:none; display:block;
             line-height:1.3; }
.kard .meta { color:var(--dim); font-size:.74rem; }
.kard .marks { display:flex; gap:.3rem; align-items:center; flex-wrap:wrap;
               margin-top:.25rem; font-size:.72rem; }
.kard .seg { background:var(--bg); border:1px solid var(--line); border-radius:1rem;
             padding:0 .4rem; color:var(--dim); }
.kard .sector { font-weight:600; color:var(--ink); }
.kard .next { margin-top:.25rem; font-size:.74rem; border-top:1px dotted var(--line);
              padding-top:.22rem; }
.kard .next.late { color:var(--late); font-weight:600; }
.kard details { margin-top:.25rem; }
.kard details summary { cursor:pointer; font-size:.72rem; color:var(--dim);
                        list-style:none; }
.kard details summary::-webkit-details-marker { display:none; }
.kard details form { display:flex; gap:.25rem; margin-top:.25rem; }
.kard details select { flex:1; font-size:.75rem; padding:.1rem; }
.kard .sat { color:var(--dim); font-variant-numeric:tabular-nums; }
.kard .sat.long { color:var(--late); font-weight:600; }
.kard .chip { background:var(--ink); color:var(--paper); border-radius:2px;
              padding:0 .3rem; text-decoration:none; font-weight:600;
              font-size:.7rem; letter-spacing:.02em; }
.lane { margin-bottom:.4rem; }
.lane > h2 { display:flex; gap:.5rem; align-items:baseline; margin:.9rem 0 .35rem;
             border-bottom:1px solid var(--line); padding-bottom:.2rem; }
.lane > h2 b { color:var(--ink); font-variant-numeric:tabular-nums; }
.lane.empty { display:none; }
.bring { display:flex; gap:.6rem; align-items:end; flex-wrap:wrap; }
"""


def shell(body: str, chrome: Any = None) -> str:
    return page("arpenteur", body, chrome, css=STYLES)


# --- putting a row in a lane --------------------------------------------------


def _due_lane(row: Row, today: date) -> str:
    when = (row["next_at"] or "")[:10]
    if not when:
        return "no next action"
    try:
        at = date.fromisoformat(when)
    except ValueError:
        return "no next action"
    if at < today:
        return "overdue"
    if at == today:
        return "today"
    return "this week" if (at - today).days <= 7 else "later"


def lane_of(row: Row, lane: str, today: date) -> str:
    if lane == "due":
        return _due_lane(row, today)
    if lane == "band":
        return row["band"] or "not scored"
    if lane == "canton":
        return row["canton"] or "no canton"
    if lane == "owner":
        return row["owner"] or "nobody"
    if lane == "sector":
        # The one classification that can be a lane at all: exactly one per
        # company, so a card belongs to exactly one row of the board.
        return (
            row["sector"] if "sector" in row.keys() and row["sector"] else "no sector"
        )
    return ""


def _lane_order(lane: str, keys: Iterable[str]) -> list[str]:
    """Lanes in a stated order where there is one, alphabetical otherwise."""
    present = set(keys)
    if lane == "due":
        return [k for k in DUE_LANES if k in present]
    if lane == "band":
        return [k for k in BAND_LANES if k in present]
    trailing = ("nobody", "no canton", "no sector")
    named = sorted(k for k in present if k not in trailing)
    return named + [k for k in trailing if k in present]


# --- one card -----------------------------------------------------------------


def _sitting(since: str | None, today: date) -> str:
    """How long this card has been where it is.

    The question a column of eight cards provokes, and the one a status column
    could never answer because a change to it was an overwrite. Five days and
    five months look identical on a board that does not say.
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
    weeks = days // 7
    label = f"{days}d" if days < 14 else f"{weeks}w"
    late = " long" if days >= 21 else ""
    return f'<span class="sat{late}" title="In this column since {since[:10]}">{label}</span>'


def card(
    row: Row, states: list[str], client: Row | None, today: date, back: str,
    since: str | None = None,
) -> str:
    """One company, as much of it as fits, and the two ways to move it.

    Dragging is the fast one. The `move` fold underneath is the one that works
    without JavaScript, from a keyboard, and on a phone, and it costs six
    lines: a board that can only be operated by dragging is a board that cannot
    be operated at all on half the devices it will be opened on.
    """
    late = bool(row["next_at"]) and (row["next_at"] or "")[:10] < today.isoformat()
    segments = row["segments"] or []
    marks = [
        f'<span class="band-{esc(row["band"] or "low")}">{row["score"]}</span>'
        if row["score"] is not None
        else '<span class="dim">unscored</span>'
    ]
    sat = _sitting(since, today)
    if sat:
        marks.append(sat)
    if row["reachable"]:
        marks.append('<span class="dim" title="A usable email address is held">@</span>')
    if row["url"]:
        marks.append('<span class="dim" title="Website researched">w</span>')
    # The trade first and in its own mark, because it is the one thing on a
    # card that says what the company actually does. Two segments after it are
    # a reminder of why it was brought in; a third would be noise on a card
    # this size.
    if "sector" in row.keys() and row["sector"]:
        marks.append(f'<span class="sector">{esc(row["sector"])}</span>')
    marks += [f'<span class="seg">{esc(s)}</span>' for s in segments[:2]]
    if client is not None:
        marks.append(
            f'<a class="chip" href="/client/{client["id"]}" '
            f'title="Already a client">{esc(client["code"])}</a>'
        )
    elif row["state"] == "won":
        marks.append(
            f'<form method="post" action="/company/{row["id"]}/client" '
            'class="inline"><button style="padding:0 .3rem;font-size:.7rem">'
            "make a client</button></form>"
        )

    next_line = ""
    if row["next_action"] or row["next_at"]:
        when = (row["next_at"] or "")[:10]
        next_line = (
            f'<div class="next{" late" if late else ""}">'
            f'{esc(when)} {esc(row["next_action"] or "")}</div>'
        )

    options = "".join(
        f'<option value="{esc(s)}"{" selected" if s == row["state"] else ""}>'
        f"{esc(s)}</option>"
        for s in states
    )
    move = (
        '<details><summary>move</summary>'
        '<form method="post" action="/board/move">'
        f'<input type="hidden" name="company_id" value="{row["id"]}">'
        f'<input type="hidden" name="back" value="{esc(back)}">'
        f'<select name="state">{options}</select>'
        "<button>go</button></form></details>"
    )
    classes = f'kard band-{esc(row["band"] or "none")}{" overdue" if late else ""}'
    return (
        f'<article class="{classes}" draggable="true" data-id="{row["id"]}">'
        f'<a class="who" href="/company/{row["id"]}">{esc(row["name"])}</a>'
        f'<div class="meta">{esc(row["canton"] or "")} &middot; '
        f'{esc(row["legal_form_label"] or "")}</div>'
        f'<div class="marks">{"".join(marks)}</div>'
        f"{next_line}{move}</article>"
    )


# --- the board ----------------------------------------------------------------


def _column(
    state: str, rows: list[Row], states: list[str], clients: dict[int, Row],
    today: date, back: str, lane_key: str, moved: dict[int, str] | None = None,
) -> str:
    moved = moved or {}
    cards = "".join(
        card(r, states, clients.get(int(r["id"])), today, back,
             since=moved.get(int(r["id"])))
        for r in rows
    )
    return (
        f'<section class="col"><h3 title="{esc(STATE_NOTES.get(state, ""))}">'
        f'{esc(state)}<b data-count>{len(rows)}</b></h3>'
        f'<div class="why">{esc(STATE_NOTES.get(state, ""))}</div>'
        f'<div class="drop" data-state="{esc(state)}" data-lane="{esc(lane_key)}">'
        f"{cards}</div></section>"
    )


def board_page(
    rows: list[Row],
    *,
    states: list[str],
    clients: dict[int, Row],
    today: date,
    moved: dict[int, str] | None = None,
    lane: str = "",
    q: str = "",
    canton: str = "",
    segment: str = "",
    sector: str = "",
    band: str = "",
    owner: str = "",
    cantons: list[str],
    segments: list[str],
    owners: list[str],
    sectors: list[str] | None = None,
    back: str = "",
    note: str = "",
) -> str:
    grouped: dict[str, dict[str, list[Row]]] = {}
    for row in rows:
        key = lane_of(row, lane, today)
        grouped.setdefault(key, {}).setdefault(row["state"] or "new", []).append(row)
    # An empty board still draws its columns. A page that answers "nothing
    # here" with nothing at all does not show what the states are or where a
    # card would go, and the shape of the pipeline is half of what this view
    # is for.
    grouped = grouped or {"": {}}

    lanes = []
    for key in _lane_order(lane, grouped):
        columns = "".join(
            _column(state, grouped[key].get(state, []), states, clients, today,
                    back, key, moved)
            for state in states
        )
        total = sum(len(v) for v in grouped[key].values())
        head = (
            f'<h2>{esc(key)} <b>{total}</b></h2>' if lane else ""
        )
        lanes.append(f'<div class="lane">{head}<div class="board">{columns}</div></div>')

    banner = f'<div class="warn">{esc(note)}</div>' if note else ""
    if not rows:
        banner += (
            '<div class="warn">Nothing is on the board. Bring a shortlist in '
            "below, or set a state on any company's page in the survey. Only "
            "companies somebody put here appear, which is what makes the counts "
            "mean something.</div>"
        )
    return (
        "<h1>Board</h1>"
        "<p class='lede'>Where each company being worked stands. Drag a card to "
        "move it; the state is saved as it lands. Only companies with a state "
        "are here, which is the difference between this and the survey.</p>"
        + banner
        + _filters(lane, q, canton, segment, band, owner, cantons, segments,
                   owners, sector, sectors or [])
        + "".join(lanes)
        + _bring_in(band or "high", segment, canton, segments, cantons,
                    sector, sectors or [])
        + SCRIPT
    )


def _filters(
    lane: str, q: str, canton: str, segment: str, band: str, owner: str,
    cantons: list[str], segments: list[str], owners: list[str],
    sector: str = "", sectors: list[str] | None = None,
) -> str:
    return ui.toolbar(
        ui.text_field("q", "Search", value=q, placeholder="Name")
        + ui.select_field("canton", "Canton", cantons, chosen=canton,
                          blank="All cantons")
        + (ui.select_field("sector", "Sector", sectors, chosen=sector,
                           blank="All sectors") if sectors else "")
        + ui.select_field("segment", "Segment", segments, chosen=segment,
                          blank="All segments")
        + ui.select_field("band", "Fit band",
                          ["high", "medium", "low", "excluded"],
                          chosen=band, blank="Any band")
        + ui.select_field("owner", "Owner", owners, chosen=owner,
                          blank="Anyone")
        + ui.select_field("lane", "Swimlanes", LANES, chosen=lane, wide=True),
        action="/board", css="filters",
    )


def _bring_in(band: str, segment: str, canton: str,
              segments: list[str], cantons: list[str],
              sector: str = "", sectors: list[str] | None = None) -> str:
    """The step between a scored survey and a pipeline.

    Stated as a count and a band rather than a button called "sync", because
    what it does is put named companies in front of a person, and the number of
    them is the only thing that makes it a decision rather than a reflex.
    """
    return (
        "<h2>bring in a shortlist</h2>"
        + ui.toolbar(
            ui.select_field("band", "Fit band", ["high", "medium", "low"],
                            chosen=band or "high")
            + (ui.select_field("sector", "Sector", sectors, chosen=sector,
                               blank="Any sector") if sectors else "")
            + ui.select_field("segment", "Segment", segments, chosen=segment,
                              blank="Any segment")
            + ui.select_field("canton", "Canton", cantons, chosen=canton,
                              blank="All cantons")
            + ui.text_field("limit", "How many", value="25", kind="number")
            + "<span class='hint' style='flex-basis:100%'>Takes the "
              "best-scoring companies that are not already on the board and "
              "puts them in the first column. Nothing already here is touched, "
              "so running it again after another sweep cannot walk a company "
              "at &quot;meeting&quot; back to the start.</span>",
            action="/board/bring-in", method="post",
            submit="Put them in new", css="card bring",
        )
    )


# The one piece of client-side behaviour in this console that is not HTMX
# swapping a fragment. Dragging cannot be expressed as a request, so it is
# thirty lines of the platform's own drag events and one fetch. No library.
#
# A card that has been dropped is moved in the DOM immediately and the state is
# saved behind it. If that save fails the page reloads, because the alternative
# is a board that shows a move the database never took.
#
# Drops are refused across swimlanes. A lane is grouped by something a state
# change does not alter — the canton does not move when a company replies — so
# a card dragged into another lane's column would be filed under a fact that is
# not true of it.
#
# It takes its endpoint and its id field as arguments because there are two
# boards now: this one moves a company between prospect states, and a programme
# moves a member between its own stages. The behaviour is identical and the
# markup is identical, so duplicating thirty lines of drag handling would mean
# fixing the next bug in it twice.


def drag_script(url: str, field: str) -> str:
    """The drag handler, posting `field` and `state` to `url`."""
    return """
<script>
(function () {
  let held = null;
  document.addEventListener('dragstart', function (e) {
    const card = e.target.closest('.kard');
    if (!card) return;
    held = card;
    card.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', card.dataset.id);
  });
  document.addEventListener('dragend', function () {
    if (held) held.classList.remove('dragging');
    held = null;
    document.querySelectorAll('.drop.over').forEach(d => d.classList.remove('over'));
  });
  function lane(node) { return node.closest('.drop') ? node.closest('.drop').dataset.lane : null; }
  document.addEventListener('dragover', function (e) {
    const drop = e.target.closest('.drop');
    if (!drop || !held || drop.dataset.lane !== lane(held)) return;
    e.preventDefault();
    drop.classList.add('over');
  });
  document.addEventListener('dragleave', function (e) {
    const drop = e.target.closest('.drop');
    if (drop) drop.classList.remove('over');
  });
  document.addEventListener('drop', function (e) {
    const drop = e.target.closest('.drop');
    if (!drop || !held || drop.dataset.lane !== lane(held)) return;
    e.preventDefault();
    const card = held, from = card.closest('.drop');
    drop.classList.remove('over');
    if (from === drop) return;
    drop.appendChild(card);
    [from, drop].forEach(function (column) {
      const head = column.parentElement.querySelector('[data-count]');
      if (head) head.textContent = column.querySelectorAll('.kard').length;
    });
    const body = new URLSearchParams(
      {__FIELD__: card.dataset.id, state: drop.dataset.state}
    );
    fetch('__URL__', {method: 'POST', body: body,
      headers: {'Content-Type': 'application/x-www-form-urlencoded'}})
      .then(function (r) { if (!r.ok) location.reload(); })
      .catch(function () { location.reload(); });
  });
})();
</script>
""".replace("__FIELD__", field).replace("__URL__", url)


SCRIPT = drag_script("/board/move", "company_id")
