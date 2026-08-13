"""A score taken apart: its components, their weights, and their evidence.

The number on the company page ranks a list. This is what it is made of, and
it exists because the question anybody actually asks of a ranked list is why
this one is above that one, and "78, because the reasoning says so" has never
been an answer that survives disagreement.

Two things the panel is careful to show.

**Which components were judged and which were counted.** Reachability and
relationship come from rows in this database: contacts held, board seats
shared. The other four are a model's opinion. Presenting the six identically
would hide the only distinction a reader needs in order to know which of them
to argue with.

**What was weighed against.** A component's evidence is listed for and against,
so a high score with a serious reservation reads as one rather than as a high
score. The reservation is usually the useful part.
"""

from __future__ import annotations

from typing import Any

from ..kernel.db import Row
from ..modules.scout import scoring
from .render import esc

STYLES = """
.parts { display:flex; flex-direction:column; gap:.5rem; margin:.6rem 0 0; }
.part { border:1px solid var(--line); background:#fff; padding:.55rem .75rem; }
.part-head { display:flex; gap:.6rem; align-items:baseline; }
.part-head b { min-width:7rem; }
.part-head .n { font-variant-numeric:tabular-nums; font-weight:700;
                min-width:2.4rem; text-align:right; }
.part-head .w { color:var(--dim); font-size:.78rem; }
.origin { border:1px solid var(--line); border-radius:1rem; padding:.02rem .5rem;
          font-size:.7rem; color:var(--dim); }
.origin.computed { border-color:#1d6b2f; color:#1d6b2f; }
.bar { height:.3rem; background:var(--bg); border:1px solid var(--line);
       margin:.3rem 0 .35rem; }
.bar span { display:block; height:100%; background:var(--accent); }
.part .why { font-size:.84rem; color:var(--dim); margin:.1rem 0 .3rem; }
.ev { font-size:.8rem; margin:.1rem 0 0; padding-left:.9rem; }
.ev li { margin:.1rem 0; }
.ev.for li::marker { content:"+ "; color:#1d6b2f; }
.ev.against li::marker { content:"- "; color:var(--accent); }
.score-note { font-size:.8rem; color:var(--dim); margin:.5rem 0 0; }
.selection { font-size:.8rem; color:var(--dim); margin:.4rem 0 0; }
"""


def _bar(value: Any) -> str:
    try:
        width = max(0.0, min(100.0, float(value or 0)))
    except (TypeError, ValueError):
        width = 0.0
    return f'<div class="bar"><span style="width:{width:.0f}%"></span></div>'


def _evidence(rows: list[Row], direction: str) -> str:
    items = [row for row in rows if row["direction"] == direction]
    if not items:
        return ""
    body = "".join(
        f'<li>{esc(row["statement"])}</li>' for row in items
    )
    return f'<ul class="ev {direction}">{body}</ul>'


def parts_panel(found: dict[str, Any] | None, prospect: Row | None = None) -> str:
    """The newest run, taken apart. Empty when nothing has been scored."""
    if not found:
        return ""
    run = found["run"]
    blocks = []
    for part in found["components"]:
        row = part["row"]
        component = scoring.BY_KEY.get(row["component"])
        name = component.name if component else row["component"]
        origin = row["origin"] or "model"
        blocks.append(
            '<div class="part"><div class="part-head">'
            f'<b>{esc(name)}</b>'
            f'<span class="n">{_round(row["score"])}</span>'
            f'<span class="origin {esc(origin)}">{esc(origin)}</span>'
            + (f'<span class="w">weight {row["weight"]}</span>'
               if row["weight"] is not None else "")
            + "</div>"
            + _bar(row["score"])
            + (f'<p class="why">{esc(row["reasoning"])}</p>'
               if row["reasoning"] else "")
            + _evidence(part["evidence"], "for")
            + _evidence(part["evidence"], "against")
            + "</div>"
        )

    head = (
        f'<p><span class="band-{esc(run["band"] or "low")}" '
        f'style="font-size:1.5rem">{_round(run["total"])}</span> '
        f'<span class="dim">/ 100 &middot; {esc(run["band"] or "")}</span></p>'
    )
    note = (
        '<p class="score-note">Weighted from the components below. '
        + (f'Weight set: {esc(run["weight_set"])} v{run["weight_version"]}. '
           if run["weight_set"] else "Default weights, which are a starting "
           "point rather than a finding. ")
        + "Reach and relationship are counted from contacts and shared board "
        "seats; the rest are judged.</p>"
    )
    return (
        "<h2>how the score is made</h2>" + head + note
        + f'<div class="parts">{"".join(blocks)}</div>'
        + _selection(prospect)
    )


def _selection(prospect: Row | None) -> str:
    """Where this company stood in the ranking when somebody picked it.

    Shown because it is otherwise invisible and quietly important: it is the
    only record of how likely this company was to be chosen at all, and any
    later reading of what converted is biased without it.
    """
    if prospect is None or prospect["selected_at"] is None:
        return ""
    percentile = prospect["selection_p"]
    try:
        place = f"{float(percentile) * 100:.0f}th percentile"
    except (TypeError, ValueError):
        place = "unknown percentile"
    return (
        f'<p class="selection">Picked out of the list on '
        f'{str(prospect["selected_at"])[:10]}, at the {esc(place)} '
        f'({esc(str(prospect["selection_score"] or ""))}, '
        f'{esc(prospect["selection_band"] or "")}). Kept so that what is later '
        "learned from the outcome is not learned purely from the system's own "
        "past choices.</p>"
    )


def _round(value: Any) -> str:
    try:
        return f"{float(value):.0f}"
    except (TypeError, ValueError):
        return "-"


def disagreement_panel(rows: list[Row]) -> str:
    """Attributes where two live observations say different things.

    A reader who only ever sees the winning claim cannot tell a settled fact
    from a contested one, and the contested ones are where the questions are.
    """
    if not rows:
        return ""
    items = "".join(
        f'<li><code>{esc(row["attribute"])}</code>: {row["answers"]} different '
        f'answers across {row["claims"]} observations</li>'
        for row in rows
    )
    return (
        "<h2>sources disagree</h2>"
        f'<div class="card"><ul class="ev">{items}</ul>'
        '<p class="dim small">Both claims are kept. Nothing here has been '
        "resolved, and that it is unresolved is the finding.</p></div>"
    )
