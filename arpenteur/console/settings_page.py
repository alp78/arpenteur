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

from typing import Any, Sequence

from ..config import EFFORTS, ENV_NAMES, Settings
from ..kernel.db import Row
from ..modules.sales.store import UNITS
from ..modules.scout import scoring
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
.settings-index { display:flex; flex-wrap:wrap; gap:.3rem .9rem; margin:0 0 1rem;
                  padding:0; list-style:none; font-size:var(--t-sm); }
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(14rem, 1fr));
        gap:.5rem .9rem; }
.grid .ctl { max-width:none; }
.grid .ctl.ctl-wide { grid-column:1 / -1; }
.rows { display:grid; grid-template-columns:7rem 1fr 6rem 6rem 6rem 4rem;
        gap:.3rem .5rem; align-items:start; }
.rows .head { font-size:.75rem; color:var(--dim); }
.rows input, .rows select { width:100%; }
.rows .wide { grid-column:1 / -1; display:grid; grid-template-columns:1fr 1fr 1fr;
              gap:.3rem .5rem; margin-bottom:.6rem; }
.env { list-style:none; padding:0; margin:0; columns:2; column-gap:2rem; }
.env li { display:flex; justify-content:space-between; gap:.6rem; padding:.15rem 0;
          border-bottom:1px solid var(--line); break-inside:avoid; }
.env code { font-size:.82rem; }
.env .set { color:var(--good); } .env .unset { color:var(--dim); }
"""


def shell(body: str, chrome: Any = None) -> str:
    return page("", body, chrome, css=STYLES)


def _block(
    title: str, what: str, inner: str, button: str, does: str, action: str,
    note: str = "", id: str = "",
) -> str:
    return (
        f'<form class="setting" method="post" action="{esc(action)}"{ui.attrs(id=id)}>'
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
        changed, id="territory",
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
        changed, id="segments",
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
        changed, id="sectors",
    )


def signature_block() -> str:
    """The way to the signature editor, which has no other door."""
    return (
        '<section class="setting" id="signature"><header><h3>Signature</h3>'
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
        changed, id="signal",
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
        changed, id="rate-card",
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
        "/settings/campaign", id="scoring-brief",
    )


def _disabled(label: str, value: str, hint: str, ident: str) -> str:
    """A field the environment decides, shown and not editable."""
    control = (
        f'<input type="text" id="{esc(ident)}" value="{esc(value)}" disabled>'
    )
    return ui.field(label, control, hint=hint, for_=ident)


def identity_block(issuer: Any, changed: str) -> str:
    """Who bills: what an invoice and its payment part print."""
    inner = '<div class="grid">' + "".join([
        ui.text_field("name", "Name", value=issuer.name, required=True,
                      hint="As it appears on an invoice and on the payment part."),
        ui.text_field("street", "Street", value=issuer.street),
        ui.text_field("building", "Number", value=issuer.building),
        ui.text_field("postcode", "Postcode", value=issuer.postcode),
        ui.text_field("town", "Town", value=issuer.town),
        ui.text_field("country", "Country", value=issuer.country or "CH",
                      hint="Two letters."),
        ui.text_field("iban", "IBAN", value=issuer.iban, required=True,
                      hint="The account a client pays into. Checked when saved."),
        ui.text_field("vat_number", "VAT number", value=issuer.vat_number,
                      hint="Empty when the business is under the threshold; "
                           "the invoice then says so."),
        ui.text_field("payment_terms", "Payment terms", value=issuer.payment_terms,
                      wide=True, hint="One sentence printed under the total."),
        ui.text_field("payment_days", "Days to pay", value=str(issuer.payment_days),
                      kind="number", minimum="0"),
    ]) + "</div>"
    return _block(
        "Identity", "Who is billing. Every field here is printed on paper that "
        "goes to a client, so none of it is secret and all of it must be right.",
        inner, "Save identity",
        "An invoice issued after this carries these details; one already "
        "issued keeps the ones it was issued with.",
        "/settings/issuer", changed, id="identity",
    )


def sending_block(smtp: Any, env_set: dict[str, bool], changed: str) -> str:
    """How a message leaves, and how many may."""
    def either(name: str, label: str, value: str, env: str, hint: str, **kw: Any) -> str:
        if env_set.get(env):
            return _disabled(label, value, f"Set by {env} in the environment.", f"s-{name}")
        return ui.text_field(name, label, value=value, hint=hint, id=f"s-{name}", **kw)

    if env_set.get("SMTP_SECURITY"):
        security = _disabled("Security", smtp.security,
                             "Set by SMTP_SECURITY in the environment.", "s-security")
    else:
        security = ui.select_field(
            "security", "Security", (("starttls", "STARTTLS, port 587"),
                                     ("ssl", "SSL, port 465")),
            chosen=smtp.security, id="s-security",
            hint="A mismatch fails at the handshake.",
        )
    inner = '<div class="grid">' + "".join([
        either("sender", "From address", smtp.sender, "SMTP_SENDER",
               "The authenticated mailbox or one of its aliases."),
        ui.text_field("sender_name", "From name", value=smtp.sender_name,
                      id="s-sender_name", hint="Shown beside the address."),
        either("reply_to", "Reply-To", smtp.reply_to, "SMTP_REPLY_TO",
               "Where a reply goes. The mailbox read on the Mail page, or an "
               "opt-out arrives where nothing reads it."),
        security,
        ui.text_field("sent_folder", "Sent folder", value=smtp.sent_folder,
                      id="s-sent_folder",
                      hint="Where a copy is filed. Empty asks the server."),
        ui.text_field("test_to", "Test recipient", value=smtp.test_to, id="s-test_to",
                      hint="Where a wave's test is sent. Empty means the From address."),
        ui.text_field("per_run", "Messages per run", value=str(smtp.per_run),
                      kind="number", minimum="1", id="s-per_run",
                      hint="A review limit: a batch nobody reads sends the wrong "
                           "thing that many times."),
        ui.text_field("daily_quota", "Messages per day", value=str(smtp.daily_quota),
                      kind="number", minimum="0", id="s-daily_quota",
                      hint="Your host's limit, from your plan. Mail sent by hand "
                           "counts against it too and is not seen here."),
        ui.text_field("pause_seconds", "Seconds between messages",
                      value=str(smtp.pause_seconds), kind="number", minimum="0",
                      id="s-pause_seconds", hint="At most 120."),
        ui.text_field("signature_image", "Signature picture", value=smtp.signature_image,
                      id="s-signature_image", wide=True,
                      hint="The one picture the signature carries, as a file under "
                           "the project folder. Empty for none."),
    ]) + "</div>"
    return _block(
        "Sending",
        "How a campaign's messages leave. The server and its password are in "
        "the environment; what a recipient sees and how fast the waves go are "
        "here.",
        inner, "Save sending",
        "Applies to the next test or wave. A wave already going keeps its pace.",
        "/settings/delivery", changed, id="sending",
    )


def mailbox_block(mail: Any, changed: str) -> str:
    inner = ui.field(
        "Your own domains",
        '<textarea name="own_domains" id="own-domains" rows="3">'
        + esc("\n".join(mail.own_domains)) + "</textarea>",
        for_="own-domains",
        hint="One per line. Mail from these is never matched to a company: a "
             "website contact form sends from your own domain.",
        wide=True,
    )
    return _block(
        "Mailbox",
        "What the Mail page reads. The server and the password are in the "
        "environment; the domains that are yours are here.",
        inner, "Save mailbox", "Applies to the next read of the mailbox.",
        "/settings/mail", changed, id="mailbox",
    )


def register_block(contact: str, changed: str) -> str:
    inner = ui.text_field(
        "contact", "Contact", value=contact, required=True, wide=True,
        hint="Sent as the User-Agent on every request to the register and to "
             "the map services, so their operators can reach whoever is "
             "asking. A name and an address they can write to.",
    )
    return _block(
        "Register", "How this deployment introduces itself to the services it "
        "reads. Credentials for the register are in the environment.",
        inner, "Save contact", "Applies to the next request.",
        "/settings/zefix", changed, id="register",
    )


MODEL_ROLES = (
    ("classify_model", "Sorting", "Puts each company in a sector and against your "
                                  "segments. The small model."),
    ("enrich_model", "Research and drafting", "Reads a website, and writes a draft."),
    ("match_model", "Scoring", "Scores a company against the brief."),
    ("advise_model", "Advice", "Suggests an approach for one client. The large model."),
    ("prep_model", "Preparation", "Extracts a client's vocabulary before a meeting."),
    ("draft_model", "Fallback", "Used where a role above names no model."),
)


def model_block(llm: Any, env_set: dict[str, bool], changed: str) -> str:
    key_state = (
        "ANTHROPIC_API_KEY is set." if env_set.get("ANTHROPIC_API_KEY")
        else "ANTHROPIC_API_KEY is not set."
    )
    inner = '<div class="grid">' + "".join([
        ui.select_field(
            "auth_mode", "How the model is paid for",
            (("subscription", "A Claude subscription, through the claude command"),
             ("api_key", "API credit, through the key in the environment"),
             ("auto", "The subscription when signed in, else the key")),
            chosen=llm.auth_mode, wide=True, id="m-auth",
            hint="The two draw on different accounts. " + key_state,
        ),
    ] + [
        ui.text_field(key, label, value=getattr(llm, key), id=f"m-{key}", hint=hint)
        for key, label, hint in MODEL_ROLES
    ] + [
        ui.select_field("advise_effort", "Effort for advice", ("",) + EFFORTS,
                        chosen=llm.advise_effort, id="m-advise_effort",
                        hint="Empty is the model's own default."),
        ui.select_field("prep_effort", "Effort for preparation", ("",) + EFFORTS,
                        chosen=llm.prep_effort, id="m-prep_effort",
                        hint="Empty is the model's own default."),
    ]) + "</div>"
    return _block(
        "Model access",
        "Which model does which job, and which account pays. A model id "
        "starts with claude-; the family's current ids are the defaults.",
        inner, "Save model access", "Applies to the next run that calls a model.",
        "/settings/llm", changed, id="models",
    )


def enrol_block(enrol: Any, segments: Sequence[str], programmes: Sequence[str],
                changed: str) -> str:
    chosen = set(enrol.segments)
    picker = "".join(
        f'<label><input type="checkbox" name="segments" value="{esc(name)}"'
        + (" checked" if name in chosen else "")
        + f"> {esc(name)}</label>"
        for name in segments
    )
    inner = '<div class="grid">' + "".join([
        ui.select_field(
            "programme", "Programme", [(code, code) for code in programmes],
            chosen=enrol.programme, blank="None", id="e-programme",
            hint="Whose members the drafts and the replies move. Marking a draft "
                 "sent moves its card.",
        ),
        ui.text_field("brief", "Message brief", value=enrol.brief, id="e-brief",
                      hint="The file the draft stage writes from, under the "
                           "project folder. Empty means the stage refuses to run."),
        ui.field("Segments written to", f'<div class="picker">{picker}</div>', wide=True,
                 hint="Empty means every segment. A draft run can still narrow it."),
        ui.checkbox("include_in_draft", "Add the invitation to each draft",
                    checked=enrol.include_in_draft, id="e-include",
                    hint="The invitation is read from the programme's templates "
                         "folder, one file per language, and never from a model."),
        ui.text_field("ios_link", "iOS link", value=enrol.ios_link, id="e-ios",
                      hint="Written into the invitation as given. Empty leaves a "
                           "visible marker a draft cannot be sent with."),
        ui.text_field("android_window_start", "Android window start",
                      value=enrol.android_window_start, id="e-start",
                      hint="As it should read in the message."),
        ui.text_field("android_window_end", "Android window end",
                      value=enrol.android_window_end, id="e-end"),
        ui.text_field("android_testers", "Testers Google asks for",
                      value=str(enrol.android_testers), kind="number", minimum="1",
                      id="e-testers"),
        ui.text_field("android_days", "Days of testing", value=str(enrol.android_days),
                      kind="number", minimum="1", id="e-days"),
    ]) + "</div>"
    return _block(
        "Enrolment",
        "What a drafted message is about and whom it is written to: the brief "
        "it is written from, the programme it moves, and the invitation it "
        "carries. Nothing here reaches a model prompt except the brief.",
        inner, "Save enrolment", "Applies to the next draft run.",
        "/settings/enrol", changed, id="enrolment",
    )


def scoring_block(weights: dict[str, str], name: str, note: str, changed: str) -> str:
    inner = '<div class="grid">' + "".join([
        ui.text_field(
            component.key, component.name,
            value=weights.get(component.key, str(scoring.DEFAULT_WEIGHTS[component.key])),
            kind="number", minimum="0", step="0.01", id=f"w-{component.key}",
            hint=component.asks,
        )
        for component in scoring.COMPONENTS
    ] + [
        ui.text_field("name", "Name of this weighting", value=name, id="w-name",
                      hint="A score cites the set it was made with."),
        ui.text_field("note", "Why these weights", value=note, id="w-note", wide=True),
    ]) + "</div>"
    return _block(
        "Scoring weights",
        "What each part of a score is worth. They are scaled to add up to one, "
        "so say how much more one counts than another rather than working "
        "out the fractions.",
        inner, "Save weights",
        "Opens a new version when they changed; scores already made keep the "
        "set they cite.",
        "/settings/scoring", changed, id="weights",
    )


def services_block(catalogue: Sequence[Any], card_note: str, changed: str) -> str:
    """The rate card as rows, with a blank one to add to."""
    def row(entry: Any, i: int) -> str:
        unit = "".join(
            f'<option value="{u}"{" selected" if u == entry.unit else ""}>{u}</option>'
            for u in UNITS
        )
        return (
            f'{ui.sr_label("Code", f"r-code-{i}")}'
            f'<input name="code" id="r-code-{i}" value="{esc(entry.code)}" placeholder="code">'
            f'{ui.sr_label("Name", f"r-name-{i}")}'
            f'<input name="name" id="r-name-{i}" value="{esc(entry.name)}" placeholder="name">'
            f'{ui.sr_label("Unit", f"r-unit-{i}")}'
            f'<select name="unit" id="r-unit-{i}">{unit}</select>'
            f'{ui.sr_label("From", f"r-low-{i}")}'
            f'<input name="low" id="r-low-{i}" value="{esc(entry.low or "")}" placeholder="from">'
            f'{ui.sr_label("To", f"r-high-{i}")}'
            f'<input name="high" id="r-high-{i}" value="{esc(entry.high or "")}" placeholder="to">'
            f'{ui.sr_label("Order", f"r-order-{i}")}'
            f'<input name="sort_order" id="r-order-{i}" value="{esc(entry.sort_order)}">'
            '<div class="wide">'
            f'{ui.sr_label("What it is", f"r-what-{i}")}'
            f'<input name="what" id="r-what-{i}" value="{esc(entry.what)}" placeholder="what it is">'
            f'{ui.sr_label("Prerequisite", f"r-pre-{i}")}'
            f'<input name="prerequisite" id="r-pre-{i}" value="{esc(entry.prerequisite)}" '
            'placeholder="prerequisite">'
            f'{ui.sr_label("What the price depends on", f"r-basis-{i}")}'
            f'<input name="basis" id="r-basis-{i}" value="{esc(entry.basis)}" '
            'placeholder="what the price depends on">'
            "</div>"
        )

    class _Blank:
        code = name = what = prerequisite = basis = ""
        unit, low, high, sort_order = "fixed", "", "", 100

    rows = "".join(row(entry, i) for i, entry in enumerate(catalogue))
    rows += row(_Blank(), len(catalogue))
    head = "".join(
        f'<span class="head">{label}</span>'
        for label in ("code", "name", "unit", "from", "to", "order")
    )
    inner = (
        f'<div class="rows">{head}{rows}</div>'
        + ui.field(
            "Note under the card",
            f'<input type="text" name="card_note" id="card-note" value="{esc(card_note)}">',
            for_="card-note", wide=True,
            hint="One sentence, such as what is not billed.",
        )
    )
    return _block(
        "Catalogue",
        "What you sell and what it costs, as lines. Prices are in francs; a "
        "line with one price has the same figure in both columns. A row with "
        "no code is dropped.",
        inner, "Save catalogue",
        "Opens a new rate card when the lines changed; a quote already sent "
        "keeps the card it cites.",
        "/settings/services", changed, id="catalogue",
    )


def secrets_block(env_set: dict[str, bool]) -> str:
    """What the environment holds, as set or not set. Never a value."""
    groups = "".join(
        f"<h4 class='dim small'>{esc(group)}</h4><ul class='env'>"
        + "".join(
            f"<li><code>{esc(name)}</code>"
            + (
                "<span class='set'>set</span>" if env_set.get(name)
                else "<span class='unset'>not set</span>"
            )
            + "</li>"
            for name in names
        )
        + "</ul>"
        for group, names in ENV_NAMES
    )
    return (
        '<section class="setting" id="secrets"><header><h3>Secrets</h3>'
        "<p class='what'>Passwords, keys and servers live in the environment, "
        "read from the .env file beside the configuration, and are never "
        "stored in the database or shown here. This says which are set.</p>"
        f"</header><div class='inner'>{groups}</div></section>"
    )


INDEX = (
    ("territory", "Territory"), ("sectors", "Sectors"), ("segments", "Segments"),
    ("signal", "Research"), ("scoring-brief", "Scoring brief"), ("weights", "Weights"),
    ("identity", "Identity"), ("catalogue", "Catalogue"), ("rate-card", "Rate card"),
    ("sending", "Sending"), ("mailbox", "Mailbox"), ("enrolment", "Enrolment"),
    ("register", "Register"), ("models", "Model access"), ("secrets", "Secrets"),
    ("signature", "Signature"),
)


def settings_page(
    *,
    live: Settings,
    catalogue: list[dict[str, Any]],
    counts: dict[str, int],
    campaign: str, campaign_digest: str, scored: int,
    history: list[Row],
    programmes: Sequence[str] = (),
    env_set: dict[str, bool] | None = None,
    enabled: Sequence[str] = (),
) -> str:
    env_set = env_set or {}
    when = {row["key"]: (row["updated_at"] or "")[:10] for row in history}

    def changed(key: str) -> str:
        return f"changed {when[key]}" if key in when else "shipped default"

    catalogue_when = when.get("_legal_form_catalogue", "")
    index = "".join(
        f'<li><a href="#{anchor}">{esc(label)}</a></li>' for anchor, label in INDEX
        if not (anchor in ("identity", "catalogue") and "sales" not in enabled)
    )
    return (
        "<h1>Settings</h1>"
        "<p class='lede'>What the register is swept for and how companies are "
        "scored. Everything here decides what the stages on "
        "<a href='/pipeline'>Operations</a> do, so it lives in the application "
        "rather than in a file you have to open a terminal to edit.</p>"
        f'<ul class="settings-index">{index}</ul>'
        + territory_block(list(live.territory.cantons), list(live.territory.legal_forms),
                          catalogue, counts, changed("cantons"))
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
        + sectors_block(dict(live.sectors), changed("sectors"))
        + segments_block(dict(live.segments), changed("segments"))
        + signal_block(live.signal_brief, changed("signal_brief"))
        + campaign_block(campaign, campaign_digest, scored)
        + scoring_block(dict(live.weights), live.weight_set_name, live.weight_set_note,
                        changed("scoring"))
        + (identity_block(live.issuer, changed("issuer")) if "sales" in enabled else "")
        + (services_block(live.catalogue, live.card_note, changed("rates"))
           if "sales" in enabled else "")
        + rate_card_block(live.rate_card, changed("rate_card"))
        + sending_block(live.smtp, env_set, changed("smtp"))
        + mailbox_block(live.mail, changed("mail"))
        + enrol_block(live.enrol, sorted(live.segments), programmes, changed("enrol"))
        + register_block(live.zefix.contact, changed("zefix"))
        + model_block(live.llm, env_set, changed("llm"))
        + secrets_block(env_set)
        + signature_block()
    )
