"""Settings: what the register is swept for, changed from the application.

The territory, the segment vocabulary, what counts as a signal and the scoring
brief decide what every stage below them does. They lived in `arpenteur.toml`
and `campaign.md`, which made the most consequential decisions in the tool the
ones you had to leave the tool to make.

It also hid them. A register sweep that silently contained no foundations
because the code `0110` was absent from a list in a file is not a discoverable
state: the canton was in scope, the sweep ran, and nothing anywhere said that
an entire class of entity had never been asked for.

So every one of them is here, with the register's own catalogue as the source
for legal forms rather than a code typed from memory.
"""

from __future__ import annotations

from typing import Any

from ..kernel.db import Row
from . import ui
from .render import esc, page

STYLES = """
.setting { background:var(--paper); border:1px solid var(--line); margin-bottom:1.2rem; }
.setting > header { padding:.9rem 1.1rem .2rem; border:0; position:static;
                     display:block; background:var(--paper); }
.setting h3 { margin:0; font-size:1.05rem; }
.setting .what { margin:.25rem 0 0; color:var(--dim); max-width:66ch; }
.setting .inner { padding:.5rem 1.1rem 1rem; }
.setting footer { padding:.7rem 1.1rem; border-top:1px solid var(--line);
                   background:var(--bg); display:flex; gap:.8rem;
                   align-items:center; flex-wrap:wrap; }
.picker { display:flex; flex-wrap:wrap; gap:.3rem .5rem; margin:.4rem 0; }
.picker label { display:flex; align-items:center; gap:.3rem; border:1px solid var(--line);
                 padding:.15rem .5rem; background:var(--paper); font-size:.85rem;
                 cursor:pointer; border-radius:2px; }
.picker label:has(input:checked) { border-color:var(--accent); color:var(--accent);
                                    font-weight:600; }
.picker input { margin:0; }
.picker.forms label { min-width:19rem; }
.picker .code { font-family:ui-monospace,Menlo,monospace; color:var(--dim); }
.seg { display:grid; grid-template-columns:11rem 1fr auto; gap:.4rem;
        align-items:start; margin-bottom:.4rem; }
.seg input[type=text] { width:100%; }
.big-text { width:100%; min-height:11rem; font:13px/1.6 ui-monospace,Menlo,monospace; }
.changed { font-size:.78rem; color:var(--dim); }
"""


def shell(body: str, chrome: Any = None) -> str:
    return page("", body, chrome, css=STYLES)


def _block(
    title: str, what: str, inner: str, button: str, does: str, action: str,
    note: str = "",
) -> str:
    return (
        f'<form class="setting" method="post" action="{esc(action)}">'
        f"<header><h3>{esc(title)}</h3><p class='what'>{esc(what)}</p></header>"
        f'<div class="inner">{inner}</div>'
        f'<footer><button class="primary">{esc(button)}</button>'
        f"<span class='hint'>{esc(does)}</span>"
        + (f"<span class='changed'>{esc(note)}</span>" if note else "")
        + "</footer></form>"
    )


CANTONS = (
    ("GE", "Genève"), ("VD", "Vaud"), ("VS", "Valais"), ("FR", "Fribourg"),
    ("NE", "Neuchâtel"), ("JU", "Jura"), ("BE", "Bern"), ("ZH", "Zürich"),
    ("BS", "Basel-Stadt"), ("BL", "Basel-Landschaft"), ("AG", "Aargau"),
    ("SO", "Solothurn"), ("LU", "Luzern"), ("ZG", "Zug"), ("SZ", "Schwyz"),
    ("SG", "St. Gallen"), ("TG", "Thurgau"), ("SH", "Schaffhausen"),
    ("GR", "Graubünden"), ("TI", "Ticino"), ("UR", "Uri"), ("OW", "Obwalden"),
    ("NW", "Nidwalden"), ("GL", "Glarus"), ("AR", "Appenzell A.Rh."),
    ("AI", "Appenzell I.Rh."),
)


def territory_block(
    chosen_cantons: list[str], chosen_forms: list[str],
    forms: list[dict[str, Any]], counts: dict[str, int], changed: str,
) -> str:
    cantons = "".join(
        f'<label><input type="checkbox" name="cantons" value="{esc(code)}"'
        + (" checked" if code in chosen_cantons else "")
        + f'> {esc(name)} <span class="code">{esc(code)}</span></label>'
        for code, name in CANTONS
    )
    if forms:
        rows = "".join(
            f'<label><input type="checkbox" name="legal_forms" value="{esc(f["uid"])}"'
            + (" checked" if str(f["uid"]) in chosen_forms else "")
            + f'> <span class="code">{esc(f["uid"])}</span> {esc(f.get("label") or "")}'
            + (
                f' <span class="dim">({ui.num(counts.get(str(f["uid"]), 0))} held)</span>'
                if counts.get(str(f["uid"]))
                else ""
            )
            + "</label>"
            for f in forms
        )
        form_picker = f'<div class="picker forms">{rows}</div>'
    else:
        form_picker = (
            "<p class='hint'>The register's catalogue has not been fetched yet. "
            "Use the button below and it will appear here.</p>"
            + "".join(
                f'<input type="hidden" name="legal_forms" value="{esc(code)}">'
                for code in chosen_forms
            )
        )
    return _block(
        "Territory",
        "Which cantons the register is swept for, and which kinds of entity. A "
        "legal form that is not ticked is never asked for, in any canton: this "
        "is the setting that decides whether foundations, associations and "
        "cooperatives exist for this tool at all.",
        f'<h4 class="dim small">Cantons</h4><div class="picker">{cantons}</div>'
        f'<h4 class="dim small" style="margin-top:.9rem">Legal forms</h4>'
        f"{form_picker}",
        "Save territory",
        "Applies to every stage from the next run onwards. Companies already "
        "held are kept whatever this says, so narrowing never loses data.",
        "/settings/territory",
        changed,
    )


def segments_block(segments: dict[str, str], changed: str) -> str:
    rows = "".join(
        '<div class="seg">'
        + ui.sr_label(f"Name of the segment {name}", f"seg-n-{i}")
        + f'<input type="text" id="seg-n-{i}" name="seg_name" value="{esc(name)}">'
        + ui.sr_label(f"Definition of {name}", f"seg-t-{i}")
        + f'<input type="text" id="seg-t-{i}" name="seg_text" value="{esc(text)}">'
        '<label class="check small"><input type="checkbox" name="seg_drop" '
        f'value="{esc(name)}"> drop</label></div>'
        for i, (name, text) in enumerate(sorted(segments.items()))
    )
    rows += (
        '<div class="seg">'
        + ui.sr_label("Name of a new segment", "seg-n-new")
        + '<input type="text" id="seg-n-new" name="seg_name" placeholder="new-segment">'
        + ui.sr_label("Definition of the new segment", "seg-t-new")
        + '<input type="text" id="seg-t-new" name="seg_text" placeholder="One concrete line. '
        'The classifier sees nothing else.">'
        "<span></span></div>"
    )
    return _block(
        "Segments",
        "The vocabulary Companies is sliced by. These definitions are the "
        "classifier's prompt, word for word, so they are the whole of what it "
        "knows. Keep each to one concrete line, and keep them distinguishable "
        "from each other.",
        rows,
        "Save segments",
        f"Existing labels are kept. Run {ui.stage_title('profile', 'Sort into segments')} "
        "with 're-label' to apply a changed definition; labels you set by hand "
        "are never overwritten.",
        "/settings/segments",
        changed,
    )


def sectors_block(sectors: dict[str, str], changed: str) -> str:
    """The trade vocabulary, laid out exactly like the segments above it.

    Same shape on purpose: they are the same kind of setting, edited the same
    way, read by the same stage in the same call. What differs is the count a
    company gets, and that is said in words rather than left to be inferred
    from two identical-looking boxes.
    """
    rows = "".join(
        '<div class="seg">'
        + ui.sr_label(f"Name of the sector {name}", f"sec-n-{i}")
        + f'<input type="text" id="sec-n-{i}" name="sec_name" value="{esc(name)}">'
        + ui.sr_label(f"Definition of {name}", f"sec-t-{i}")
        + f'<input type="text" id="sec-t-{i}" name="sec_text" value="{esc(text)}">'
        '<label class="check small"><input type="checkbox" name="sec_drop" '
        f'value="{esc(name)}"> drop</label></div>'
        for i, (name, text) in enumerate(sorted(sectors.items()))
    )
    rows += (
        '<div class="seg">'
        + ui.sr_label("Name of a new sector", "sec-n-new")
        + '<input type="text" id="sec-n-new" name="sec_name" placeholder="new-sector">'
        + ui.sr_label("Definition of the new sector", "sec-t-new")
        + '<input type="text" id="sec-t-new" name="sec_text" placeholder="One concrete line '
        'naming the trades it covers.">'
        "<span></span></div>"
    )
    return _block(
        "Sectors",
        "What line of business a company is in, which is a different question "
        "from how it scores against the scoring brief. Exactly one per company, chosen from "
        "this list and never invented, which is what makes 'restaurants in "
        "Vaud' a filter rather than a search for a word. Keep an entry for the "
        "purposes too general to place, or they will be forced into one that "
        "does not fit.",
        rows,
        "Save sectors",
        "Costs nothing on its own: the same classify call that applies the "
        f"segments picks the sector. Run {ui.stage_title('profile', 'Sort into segments')} "
        "with 're-label' to apply a changed list; sectors set by hand are "
        "never overwritten.",
        "/settings/sectors",
        changed,
    )


def signature_block() -> str:
    """The way to the signature editor, which has no other door."""
    return (
        '<section class="setting"><header><h3>Signature</h3>'
        "<p class='what'>The closing block appended to every message a "
        "campaign wave sends, below a thin rule. Paste it from your mail "
        "client; it is cleaned before it is stored.</p></header>"
        '<footer><a href="/signature">Edit the signature</a>'
        "<span class='hint'>It has its own page, with a preview of what a "
        "recipient sees.</span></footer></section>"
    )


def signal_block(brief: str, changed: str) -> str:
    return _block(
        "What the research looks for",
        "The website research reads every company against this sentence and "
        "returns what it found. It is the difference between scouting for "
        "work, scouting for hiring and scouting for partners. It must also ask "
        "for the evidence that argues against approaching, because the scoring "
        "cannot weigh what was never collected.",
        ui.sr_label("What the research looks for", "signal-brief")
        + f'<textarea class="big-text" id="signal-brief" name="signal_brief">'
        f"{esc(brief)}</textarea>",
        "Save",
        "Applies to companies researched from now on. Companies already "
        "researched keep their previous reading until re-researched.",
        "/settings/signal",
        changed,
    )


def rate_card_block(text: str, changed: str) -> str:
    return _block(
        "What the work costs",
        "Your rate card, in your own words. The suggested approach on a client "
        "dossier prices work from this and from nothing else: it must name the "
        "line it used, and work this card does not cover comes back with no "
        "price rather than an invented one.",
        ui.sr_label("Your rate card", "rate-card")
        + f'<textarea class="big-text" id="rate-card" name="rate_card" '
        f'placeholder="Diagnostic, fixed fee. 2 to 4 days on site: inventory of '
        f'systems, mapping of flows, written report. CHF 3\'600 to CHF 7\'200 '
        f'depending on the number of sites.&#10;Day rate, time and materials: '
        f'CHF 1\'400 to CHF 1\'800.&#10;Retainer, monthly: CHF 1\'200 to '
        f'CHF 3\'500, minimum six months.">{esc(text)}</textarea>'
        "<p class='hint'>Write it the way you actually quote. Name the unit "
        "(day, package, month), give a range where you quote a range, and say "
        "what moves a price inside it. One arrangement per line.</p>"
        "<p class='hint'><b>Why this is a setting rather than something the "
        "model works out.</b> Asked what a Swiss IT diagnostic costs, a model "
        "answers with a confident four-figure number from nowhere, and the "
        "first time that number is repeated to a client it stops being a "
        "suggestion and becomes a commitment. Leaving this empty is a "
        "legitimate choice: the advice then identifies the work and prices "
        "none of it.</p>",
        "Save rate card",
        "Applies to approaches suggested from now on. Approaches already "
        "generated keep the prices they were given, and record whether a card "
        "was set when they ran.",
        "/settings/rates",
        changed,
    )


def campaign_block(text: str, digest: str, scored: int) -> str:
    return _block(
        "Scoring brief",
        "What companies are scored against: what is on offer, who it serves "
        "well, who it serves badly, what to look for, what warns off, and how "
        "to open a conversation. It is read into the prompt verbatim.",
        ui.sr_label("The scoring brief", "scoring-brief")
        + f'<textarea class="big-text" id="scoring-brief" name="campaign" '
        f'style="min-height:22rem">{esc(text)}</textarea>'
        f"<p class='hint'>Kept in version control, so a surprising run can be "
        f"answered with a diff. Current version {esc(digest)}, "
        f"{ui.num(scored)} scores taken against it.</p>",
        "Save brief",
        "Every score taken against the previous text becomes stale and the "
        "next scoring run re-takes exactly those. Scores set by hand survive.",
        "/settings/campaign",
    )


def settings_page(
    *,
    cantons: list[str], forms: list[str], catalogue: list[dict[str, Any]],
    counts: dict[str, int], segments: dict[str, str], signal_brief: str,
    campaign: str, campaign_digest: str, scored: int,
    history: list[Row], rate_card: str = "",
    sectors: dict[str, str] | None = None,
) -> str:
    when = {row["key"]: (row["updated_at"] or "")[:10] for row in history}

    def changed(key: str) -> str:
        return f"changed {when[key]}" if key in when else "shipped default"

    catalogue_when = when.get("_legal_form_catalogue", "")
    return (
        "<h1>Settings</h1>"
        "<p class='lede'>What the register is swept for and how companies are "
        "scored. Everything here decides what the stages on "
        "<a href='/pipeline'>Operations</a> do, so it lives in the application "
        "rather than in a file you have to open a terminal to edit.</p>"
        + territory_block(cantons, forms, catalogue, counts, changed("cantons"))
        + '<form class="setting" method="post" action="/settings/catalogue">'
        "<div class='inner'><b>Legal form catalogue</b>"
        "<p class='what'>Fetched from the register rather than typed from "
        "memory, so a code that changes there stops being silently wrong here."
        "</p></div><footer><button>Refresh from the register</button>"
        "<span class='hint'>One request. "
        + (
            f"Last fetched {esc(catalogue_when)}, {len(catalogue)} forms."
            if catalogue
            else "Never fetched."
        )
        + "</span></footer></form>"
        + sectors_block(sectors or {}, changed("sectors"))
        + segments_block(segments, changed("segments"))
        + signal_block(signal_brief, changed("signal_brief"))
        + campaign_block(campaign, campaign_digest, scored)
        + rate_card_block(rate_card, changed("rate_card"))
        + signature_block()
    )
