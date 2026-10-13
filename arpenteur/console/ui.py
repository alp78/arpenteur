"""The furniture, written once.

Every page in this console builds its own cards, its own filter bars, its own
empty states and its own tables out of f-strings. That was right when there
were three pages. At fourteen it means a table gains a caption in one place and
not in the other thirteen, a heading is `<h2>` here and `<h3>` there, and the
answer to "what does an empty list look like" is nine different answers.

So: the pieces as functions. A page composes them and stops deciding what a
card is.

Three rules hold this together.

**Everything user-supplied goes through `esc`.** `esc` lives here rather than in
`render` because this is the leaf module: `render` imports from `ui`, pages
import from both, and nothing imports back. A company called `<script>` is a
legal Swiss registration and the register is a public one, so nothing arriving
from it is trusted.

**A component that is handed nothing renders nothing.** Not an empty box with a
heading, not a bare `<div>`. A page that shows a card called "contacts" with no
contacts in it has said something false about the record.

**Markup that carries behaviour states it in an attribute, never in a script.**
The sortable and the drop zone emit `data-` attributes and the one console
script picks them up. A component whose behaviour lived in a `<script>` tag it
emitted would run its handler once per instance on the page.
"""

from __future__ import annotations

import html
from typing import Any, Iterable, Sequence

# --- the bottom of everything -------------------------------------------------


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def num(value: Any) -> str:
    """A count, grouped the way it is written in Switzerland: 184'939.

    The console has been writing `184,939` through `f"{n:,}"` while
    `sales.money.format_chf` next door writes `1'234.50`, so the same page
    could show a Swiss franc and an Anglo-Saxon count. This is the integer
    half of that, kept here rather than imported from the sales module,
    because the scout must not need the sales module to count companies.
    """
    if not isinstance(value, int):
        return esc(value)
    sign = "-" if value < 0 else ""
    digits = str(abs(value))
    groups = []
    while len(digits) > 3:
        groups.insert(0, digits[-3:])
        digits = digits[:-3]
    groups.insert(0, digits)
    return sign + "'".join(groups)


def classes(*parts: Any) -> str:
    """A class attribute from parts, any of which may be empty or falsy.

    `classes("card", overdue and "late")` rather than a conditional inside an
    f-string. The empty case returns nothing at all rather than `class=""`,
    because an empty attribute in the output is a tell that the markup was
    assembled by string arithmetic.
    """
    names = [str(p).strip() for p in parts if p]
    return f' class="{esc(" ".join(names))}"' if names else ""


def attrs(**pairs: Any) -> str:
    """Optional attributes. `None`, `False` and `""` are omitted entirely.

    `True` renders the attribute bare, which is what HTML boolean attributes
    want: `disabled`, not `disabled="true"`.
    """
    out = []
    for key, value in pairs.items():
        if value is None or value is False or value == "":
            continue
        name = key.rstrip("_").replace("_", "-")
        out.append(f" {name}" if value is True else f' {name}="{esc(value)}"')
    return "".join(out)


# --- containers ---------------------------------------------------------------


def card(body: str, *, id: str = "", css: str = "", actions: str = "") -> str:
    """A white box. The unit almost everything on this console sits in."""
    if not body and not actions:
        return ""
    head = f'<div class="card-acts">{actions}</div>' if actions else ""
    return f'<div{classes("card", css)}{attrs(id=id)}>{head}{body}</div>'


def panel(
    title: str, body: str, *, note: str = "", id: str = "", actions: str = "",
    css: str = "", always: bool = False,
) -> str:
    """A titled section: the heading, and the card under it.

    `always` is for the sections that must appear even when empty, because
    their absence would be read as "this does not apply" rather than "there is
    none of this yet". Fit is one: a company nobody scored and a company that
    scored zero are different facts.
    """
    if not body and not always:
        return ""
    lede = f'<p class="lede">{esc(note)}</p>' if note else ""
    return (
        f'<section{classes("panel", css)}{attrs(id=id)}>'
        f"<h2>{esc(title)}</h2>{lede}"
        f"{card(body, actions=actions) if body else ''}</section>"
    )


def banner(text: str, *, kind: str = "note", html_body: str = "") -> str:
    """Something the page needs to say before you read the rest of it.

    `kind` is one of note, warn, bad, good. The distinction is not decoration:
    "a stage is running" and "this company has been struck off the register"
    are both banners and only one of them is a warning about the data.
    """
    inner = html_body or esc(text)
    if not inner:
        return ""
    return f'<div{classes("banner", f"banner-{kind}")} role="status">{inner}</div>'


def empty(text: str, *, action: str = "") -> str:
    """What there is instead of a list.

    Always a sentence saying what would put something here. "No results" is
    a dead end; "Nothing matches, widen the filter" is an instruction.
    """
    return f'<p class="empty">{esc(text)} {action}</p>'


def toolbar(
    controls: str, *, action: str = "", method: str = "get", target: str = "",
    push: bool = False, submit: str = "Show", css: str = "",
) -> str:
    """A row of filters, as a form.

    When `target` is given the form goes through htmx and, if `push` is set,
    writes the query into the address bar. That last part is the difference
    between a filtered list you can send to somebody and one that exists only
    in your own browser.
    """
    if target:
        verb = "hx_post" if method == "post" else "hx_get"
        wiring = attrs(**{verb: action}) + attrs(
            hx_target=target,
            hx_trigger="submit, change",
            hx_push_url="true" if push else "",
        )
    else:
        wiring = attrs(method=method, action=action)
    # The blank above the button is a spacer, not a label. It was a `<label>`
    # with a non-breaking space in it, which is a word a screen reader
    # announces as naming a control and which names nothing at all.
    go = (
        '<div class="ctl"><span class="spacer" aria-hidden="true">&nbsp;</span>'
        f'<button class="primary">{esc(submit)}</button>'
        '<span class="spinner htmx-indicator" aria-hidden="true"></span></div>'
    )
    return f'<form{classes("toolbar", css)}{wiring}>{controls}{go}</form>'


# --- controls -----------------------------------------------------------------


def field(
    label: str, control: str, *, hint: str = "", wide: bool = False,
    for_: str = "",
) -> str:
    """One labelled control with its explanation attached rather than implied.

    The hint is part of the control, not a paragraph somewhere near it. The
    operations page learned this the hard way and the rest of the console never
    got the lesson.
    """
    tag = f'<label{attrs(for_=for_)}>{esc(label)}</label>'
    note = f'<span class="hint">{esc(hint)}</span>' if hint else ""
    return f'<div{classes("ctl", wide and "ctl-wide")}>{tag}{control}{note}</div>'


def options(
    values: Iterable[Any], chosen: Any = "", blank: str = "",
    labels: dict[Any, str] | None = None,
) -> str:
    """`<option>` elements, with the blank one first when there is one.

    Accepts either a flat sequence or (value, label) pairs, because half the
    call sites in this console have one shape and half have the other.
    """
    labels = labels or {}
    out = [f'<option value="">{esc(blank)}</option>'] if blank else []
    for value in values:
        if isinstance(value, (tuple, list)) and len(value) == 2:
            value, label = value
        else:
            label = labels.get(value, value)
        sel = " selected" if str(value) == str(chosen) else ""
        out.append(f'<option value="{esc(value)}"{sel}>{esc(label)}</option>')
    return "".join(out)


def select_field(
    name: str, label: str, values: Iterable[Any], *, chosen: Any = "",
    blank: str = "", hint: str = "", wide: bool = False, id: str = "",
    multiple: bool = False,
) -> str:
    ident = id or f"f-{name}"
    size = ""
    if multiple:
        listed = list(values)
        values = listed
        size = f" multiple size='{min(len(listed), 8)}'"
    control = (
        f'<select name="{esc(name)}" id="{esc(ident)}"{size}>'
        f"{options(values, chosen, blank)}</select>"
    )
    return field(label, control, hint=hint, wide=wide, for_=ident)


def text_field(
    name: str, label: str, *, value: str = "", placeholder: str = "",
    hint: str = "", kind: str = "text", wide: bool = False, id: str = "",
) -> str:
    ident = id or f"f-{name}"
    control = (
        f'<input type="{esc(kind)}" name="{esc(name)}" id="{esc(ident)}"'
        f'{attrs(value=value, placeholder=placeholder)}>'
    )
    return field(label, control, hint=hint, wide=wide, for_=ident)


def checkbox(
    name: str, label: str, *, checked: bool = False, hint: str = "",
    value: str = "1", id: str = "",
) -> str:
    ident = id or f"f-{name}"
    return (
        '<div class="ctl ctl-wide"><label class="check">'
        f'<input type="checkbox" name="{esc(name)}" id="{esc(ident)}"'
        f' value="{esc(value)}"{" checked" if checked else ""}>'
        f"<span><b>{esc(label)}</b>"
        + (f'<span class="hint">{esc(hint)}</span>' if hint else "")
        + "</span></label></div>"
    )


def button(
    label: str, *, primary: bool = False, disabled: bool = False,
    hint: str = "", css: str = "", **extra: Any,
) -> str:
    note = f'<span class="hint">{esc(hint)}</span>' if hint else ""
    return (
        f"<button{classes(primary and 'primary', css)}"
        f"{attrs(disabled=disabled, **extra)}>{esc(label)}</button>{note}"
    )


def confirm_button(
    label: str, action: str, message: str, *, hidden: dict[str, Any] | None = None,
    css: str = "", target: str = "", swap: str = "",
) -> str:
    """A destructive action that says what it will destroy before it does it.

    The message is the whole point and it names the thing. "Are you sure?" is
    a question nobody can answer; "Delete the 2024 site survey and its four
    attachments?" is.
    """
    fields = "".join(
        f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">'
        for k, v in (hidden or {}).items()
    )
    # `hx-confirm` is read by htmx on a request it makes itself. A plain form
    # is posted by the browser, which never asks htmx, so the question went
    # unasked on every form that used it; `data-confirm` is read by the
    # console's own submit listener, and the form stays a form.
    if target:
        hx = attrs(hx_post=action, hx_target=target, hx_swap=swap, hx_confirm=message)
        method = ""
    else:
        hx = attrs(data_confirm=message)
        method = f' method="post" action="{esc(action)}"'
    return (
        f'<form class="inline"{method}{hx}>'
        f"{fields}<button{classes('danger', css)}>{esc(label)}</button></form>"
    )


# --- small pieces -------------------------------------------------------------


def pill(text: str, *, manual: bool = False, title: str = "") -> str:
    return (
        f'<span{classes("pill", manual and "manual")}{attrs(title=title)}>'
        f"{esc(text)}</span>"
    )


def pills(items: Iterable[str], manual: Iterable[str] = ()) -> str:
    held = set(manual)
    return "".join(pill(item, manual=item in held) for item in items)


def chip(text: str, *, href: str = "", title: str = "") -> str:
    if href:
        return (
            f'<a class="chip"{attrs(href=href, title=title)}>{esc(text)}</a>'
        )
    return f'<span class="chip"{attrs(title=title)}>{esc(text)}</span>'


def stat(value: Any, label: str, *, emphasis: bool = False, title: str = "") -> str:
    """One number and what it counts. Never a number on its own."""
    shown = num(value)
    return (
        f'<div{classes("stat", emphasis and "stat-on")}{attrs(title=title)}>'
        f"<b>{shown}</b><span>{esc(label)}</span></div>"
    )


def tabs(items: Sequence[tuple[str, str]], current: str, *, css: str = "") -> str:
    """A strip of links where exactly one is where you are.

    `aria-current` rather than a class alone, because "which of these am I on"
    is the question and a colour is not an answer for everybody.
    """
    out = []
    for href, label in items:
        here = href == current
        out.append(
            f'<a{attrs(href=href)}{classes(here and "on")}'
            f'{attrs(aria_current="page" if here else "")}>{esc(label)}</a>'
        )
    return f'<nav{classes("tabs", css)}>{"".join(out)}</nav>'


def crumbs(trail: Sequence[tuple[str, str]]) -> str:
    """Where this page sits. The last entry is the page itself and is not a link."""
    if not trail:
        return ""
    parts = [
        f'<a href="{esc(href)}">{esc(label)}</a>' if href else f"<b>{esc(label)}</b>"
        for href, label in trail
    ]
    return (
        '<nav class="crumbs" aria-label="Breadcrumb">'
        + '<span class="sep" aria-hidden="true">/</span>'.join(parts)
        + "</nav>"
    )


# --- tables -------------------------------------------------------------------


def table(
    columns: Sequence[str], rows: Sequence[str], *, caption: str = "",
    numeric: Iterable[str] = (), empty_text: str = "Nothing here yet.",
    css: str = "", id: str = "",
) -> str:
    """A table with a caption and headed columns.

    `rows` are pre-rendered `<tr>` strings, because a cell in this console is
    rarely just a value: it is a link, a stamp row, a form. What is shared is
    the caption, the scope on the headers and the empty case, and those are
    exactly the three things the fourteen hand-built tables disagree about.

    The caption is visible rather than hidden. A table of numbers with no title
    is a table somebody will screenshot into a meeting.
    """
    right = set(numeric)
    head = "".join(
        f'<th scope="col"{classes(c in right and "n")}>{esc(c.replace("_", " "))}</th>'
        for c in columns
    )
    body = "".join(rows) or (
        f'<tr><td colspan="{max(len(columns), 1)}" class="dim">{esc(empty_text)}</td></tr>'
    )
    cap = f"<caption>{esc(caption)}</caption>" if caption else ""
    # Wrapped in something that can scroll, and focusable so it can be scrolled
    # from a keyboard. A seven-column table on a phone otherwise either pushes
    # the whole page sideways or has three columns nobody can reach.
    return (
        '<div class="scroller" tabindex="0">'
        f'<table{classes(css)}{attrs(id=id)}>{cap}'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


# --- reordering ---------------------------------------------------------------
#
# WCAG 2.2 SC 2.5.7 is the reason this is a component rather than a handler on
# one page. It requires that anything achievable by dragging is achievable with
# a single pointer without dragging, and a keyboard path does not satisfy it,
# because the people most likely to be unable to drag are on a touch screen with
# no keyboard at all.
#
# So every sortable ships three ways to move a row, and the buttons are visible
# rather than folded away: the board's existing `<details>move</details>` is the
# right instinct in a place nobody will find it.
#
#   pointer   drag the handle
#   keyboard  focus the handle, space to pick up, arrows to move, space to drop
#   buttons   move up / move down, one click each, no dragging at all
#
# All three post the same thing to the same endpoint: the ids in their new
# order. The server is not told which of the three was used, because it makes no
# difference to what is being said.


HOW_TO_SORT = (
    "Drag, or use the arrow buttons. With a keyboard: focus a handle, press "
    "space to pick it up, the arrows to move it, space again to put it down."
)


def sort_handle(index: int, count: int, *, label: str = "row") -> str:
    """The three controls, which are three separate ways to move one row.

    The grip is focusable and carries the keyboard behaviour. The two arrows
    are the single-pointer path, and they are what SC 2.5.7 is actually asking
    for: somebody on a touch screen who cannot drag has no keyboard either.
    """
    return (
        '<span class="sort-ctl">'
        '<button type="button" class="grip" data-sort-grip '
        f'aria-label="Reorder this {esc(label)}. Press space to pick it up, '
        'then the arrow keys.">&#8942;&#8942;</button>'
        '<button type="button" class="nudge" data-sort-up '
        f'aria-label="Move this {esc(label)} up"'
        + (" disabled" if index == 0 else "")
        + ">&uarr;</button>"
        '<button type="button" class="nudge" data-sort-down '
        f'aria-label="Move this {esc(label)} down"'
        + (" disabled" if index == count - 1 else "")
        + ">&darr;</button></span>"
    )


def sort_say(id: str) -> str:
    """Where a move is announced, for anybody not watching it happen.

    Separate from the list so it can sit outside a `<table>`, where a `<div>`
    between `<tbody>` elements is not markup a parser will keep.
    """
    return f'<div class="sort-say" id="{esc(id)}" role="status" aria-live="polite"></div>'


def _sort_list_attrs(endpoint: str, target: str, say: str) -> str:
    return attrs(
        data_sortable="true", data_sort_post=endpoint, data_sort_target=target,
        data_sort_say=say,
    )


def sortable(
    items: Sequence[tuple[Any, str]], *, endpoint: str, id: str = "",
    label: str = "row", target: str = "", css: str = "", frozen: str = "",
) -> str:
    """A list whose order is part of the record.

    `items` are (id, inner html) pairs. `frozen`, when given, is the reason
    reordering is refused, shown in place of the controls: a sent quote and an
    issued invoice are documents somebody else is holding a copy of, and the
    order of their lines is not ours to change any more.
    """
    if not items:
        return ""
    say = f"{id or 'sort'}-say"
    rows = []
    for index, (item_id, inner) in enumerate(items):
        controls = "" if frozen else sort_handle(index, len(items), label=label)
        rows.append(
            f'<li class="sort-item" data-sort-id="{esc(item_id)}"'
            f'{attrs(draggable=None if frozen else "true")}>'
            f'{controls}<div class="sort-body">{inner}</div></li>'
        )
    note = esc(frozen) if frozen else esc(HOW_TO_SORT)
    return (
        f'<div{classes("sortable", css)}>'
        f'<ol class="sort-list"{attrs(id=id)}'
        + ("" if frozen else _sort_list_attrs(endpoint, target, say))
        + f'>{"".join(rows)}</ol>'
        + sort_say(say)
        + f'<p class="hint">{note}</p></div>'
    )


def sortable_body(
    items: Sequence[tuple[Any, str]], *, endpoint: str, id: str,
    label: str = "row", target: str = "", frozen: str = "",
) -> str:
    """The same thing as a `<tbody>`, for the lists that are genuinely tables.

    A quote's lines have columns: what, how many, at what price, for how much.
    Rendering them as a stack of boxes to make them draggable would be the
    interaction deciding the layout, which is the wrong way round. The script
    keys off `data-sortable` and the row class rather than off a tag name, so
    the same three paths work here with no second implementation.

    `items` are (id, the row's `<td>` cells) pairs. The handle cell is added
    here so the column count is the caller's business and the control is not.
    """
    say = f"{id}-say"
    rows = []
    for index, (item_id, cells) in enumerate(items):
        handle = (
            '<td class="sort-cell"></td>' if frozen
            else f'<td class="sort-cell">{sort_handle(index, len(items), label=label)}</td>'
        )
        rows.append(
            f'<tr class="sort-item" data-sort-id="{esc(item_id)}"'
            f'{attrs(draggable=None if frozen else "true")}>{handle}{cells}</tr>'
        )
    return (
        f'<tbody class="sort-list" id="{esc(id)}"'
        + ("" if frozen else _sort_list_attrs(endpoint, target, say))
        + f'>{"".join(rows)}</tbody>'
    )


def dropzone(
    action: str, *, label: str = "Drop files here", hint: str = "",
    accept: str = "", name: str = "file", target: str = "", id: str = "",
    fields: str = "", submit: str = "Upload", css: str = "",
) -> str:
    """Files by dropping, and the same files by choosing.

    The button is not a fallback grudgingly kept. A drop zone with no visible
    control is unusable by anyone who cannot drag, and unfindable by anyone who
    has not been told it is there, which on a first visit is everybody. It is
    also the path that works with no JavaScript at all: the form posts.

    `fields` are the controls that describe what is being uploaded. They are
    inside the form rather than beside it because a dropped file is sent with
    whatever they say at that moment, and a control the drop did not read would
    be a control that silently did nothing.
    """
    ident = id or "drop"
    return (
        f'<div{classes("dropzone", css)} data-dropzone'
        f' data-drop-post="{esc(action)}"'
        f'{attrs(data_drop_target=target, id=ident)}>'
        f'<form method="post" action="{esc(action)}" enctype="multipart/form-data">'
        f"{fields}"
        f'<p class="drop-say">{esc(label)}</p>'
        f'<input type="file" name="{esc(name)}" multiple{attrs(accept=accept)}'
        f' id="{esc(ident)}-input">'
        f'<button class="primary" type="submit">{esc(submit)}</button>'
        + (f'<p class="hint">{esc(hint)}</p>' if hint else "")
        + '<div class="drop-progress" role="status" aria-live="polite"></div>'
        "</form></div>"
    )
