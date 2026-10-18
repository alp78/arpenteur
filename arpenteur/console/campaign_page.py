"""Curated lists and the campaigns sent from them.

Four pages, read in the order the work is done.

  /audiences            the lists, and a form to start one from a rule
  /audience/{id}        one list: its funnel, its kept firms by canton and
                        score, and the dropped ones with the reason for each
  /campaigns            the campaigns, and a form to start one
  /campaign/{id}        one campaign: its plan and one card per wave
  /campaign/{id}/wave/n one wave: its recipients, its previews, and the steps

**Only the next step is offered.** A wave card shows render, test, approve and
send in that order, and only the first one not yet done is a live button. The
schema refuses any other order anyway; the page keeps the operator from having
to learn that from a refusal.

**The preview is the message a recipient gets.** Built with the same renderer
the send uses, in a sandboxed frame, so a stylesheet on this page cannot make
it look better than it will.

**Sending asks for the wave typed back.** `DRYMOS-A/2`, not a checkbox: a
person who types the wave's name has read which wave it is.
"""

from __future__ import annotations

from html import escape as _escape
from typing import Any, Sequence

from ..kernel.db import Row
from ..sources import letter, waves
from . import ui
from .render import page
from .ui import esc

STYLES = """
.cm-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(16rem, 1fr));
           gap:.8rem; }
.cm-funnel { display:flex; flex-wrap:wrap; gap:.4rem; margin:.4rem 0 1rem; }
.cm-funnel a { text-decoration:none; }
.cm-canton { margin:1.2rem 0 .3rem; font-size:.9rem; color:var(--dim); }
.cm-steps { display:flex; gap:.35rem; flex-wrap:wrap; margin:.5rem 0; }
.cm-step { font-size:var(--t-xs); padding:.1rem .45rem; border-radius:var(--r-pill);
           border:1px solid var(--line); color:var(--dim); }
.cm-step.done { background:var(--good-bg); border-color:var(--good-line);
                color:var(--ink); }
.cm-wave { border:1px solid var(--line); border-radius:var(--r); padding:.7rem .9rem;
           background:var(--paper); }
.cm-wave h3 { margin:0 0 .2rem; font-size:1rem; }
.cm-wave .meta { font-size:.8rem; color:var(--dim); }
.cm-tally { font-size:.8rem; margin-top:.4rem; }
.cm-preview { width:100%; min-height:28rem; border:1px solid var(--line);
              border-radius:var(--r); background:var(--paper); }
.cm-parts { font-size:var(--t-xs); color:var(--dim); }
.cm-reason { font-size:.8rem; color:var(--dim); }
.cm-why { width:16rem; }
"""


def shell(body: str, chrome: Any = None) -> str:
    return page("", body, chrome, css=STYLES)


# --- lists --------------------------------------------------------------------


def audiences_page(rows: Sequence[Row], segments: Sequence[str],
                   programmes: Sequence[Row]) -> str:
    items = [
        f"<tr><td><a href='/audience/{r['id']}'>{esc(r['code'])}</a></td>"
        f"<td>{esc(r['name'])}</td><td class='n'>{ui.num(r['kept'])}</td>"
        f"<td class='n'>{ui.num(r['dropped'])}</td><td class='n'>{ui.num(r['campaigns'])}</td>"
        f"<td>{'frozen' if r['frozen_at'] else ''}</td></tr>"
        for r in rows
    ]
    form = (
        "<form method='post' action='/audiences/new' class='grid'>"
        + ui.text_field("code", "Code", placeholder="DRYMOS-1", required=True)
        + ui.text_field("name", "Name", placeholder="Tourisme, première liste",
                        required=True)
        + ui.text_field("segments", "Segments and weights", wide=True, required=True,
                        placeholder="tourism-office=1, mountain-transport=0.7",
                        hint="The segments the list draws from, each weighted between "
                             "0 and 1. Known: " + ", ".join(segments))
        + ui.text_field("cantons", "Cantons", placeholder="VS, BE",
                        hint="Blank for every canton.")
        + ui.select_field("programme", "Programme",
                          [(p["code"], p["code"]) for p in programmes], blank="none")
        + ui.checkbox("members_only", "Only firms on the programme")
        + ui.checkbox("allow_unclear", "Accept unclear mailboxes",
                      hint="Neither clearly a desk nor a person. Off, only desks.")
        + ui.checkbox("allow_hearsay", "Accept addresses only a search reported",
                      hint="The page was never read. Off, only addresses read off "
                           "the firm's own site.")
        + "<div class='ctl ctl-wide'>" + ui.button("Create the list", primary=True)
        + "</div></form>"
    )
    listed = (
        ui.table(
            ["list", "name", "kept", "dropped", "campaigns", ""], items,
            caption="Curated lists", numeric=["kept", "dropped", "campaigns"])
        if items else ui.empty(
            "No list yet.",
            action="Start one below: name the segments it draws from and the "
                   "cantons it covers, then build it to see which firms it keeps.",
        )
    )
    return (
        "<h1>Lists</h1><p class='lede'>A curated list keeps one mailbox per firm, the "
        "right one, and says for every firm left out why it was. Campaigns send "
        "from a list in waves.</p>"
        + ui.panel("Your lists", listed, always=True)
        + ui.panel("Start a list", form, always=True)
    )


def _chips(row: Row) -> str:
    bits = []
    if row["contact_method"] == "inferred":
        bits.append(ui.pill("inferred", title="constructed at the domain"))
    if row["contact_status"] and row["contact_status"] != "deliverable":
        bits.append(ui.pill(row["contact_status"]))
    if row["pinned"]:
        bits.append(ui.pill("pinned", manual=True, title=str(row["pinned"].get("why") or "")))
    return " ".join(bits)


def _parts(row: Row) -> str:
    parts = row["score_parts"] or {}
    return ", ".join(f"{name} {float(p.get('points', 0)):.0f}" for name, p in parts.items())


def _pin_form(row: Row, *, frozen: bool) -> str:
    if frozen:
        return ""
    pinned = row["pinned"] or {}
    verdict = "dropped" if row["verdict"] == "kept" else "kept"
    language = ui.options(["", "fr", "de", "it", "rm"], pinned.get("language", ""),
                          labels={"": "lang"})
    return (
        f"<form method='post' action='/audience-row/{row['id']}/pin' class='inline'>"
        f"<input type='hidden' name='verdict' value='{verdict}'>"
        + ui.sr_label(f"Why {row['company_name']} is pinned", f"pin-why-{row['id']}")
        + f"<input class='cm-why' id='pin-why-{row['id']}' name='why' required placeholder='why'>"
        + ui.sr_label("Language of the message", f"pin-lang-{row['id']}")
        + f"<select name='language' id='pin-lang-{row['id']}'>{language}</select>"
        f"<button>{'drop' if verdict == 'dropped' else 'keep'}</button></form>"
        + (f"<form method='post' action='/audience-row/{row['id']}/unpin' class='inline'>"
           "<button>unpin</button></form>" if row["pinned"] else "")
    )


def audience_page(audience: Row, rows: Sequence[Row], funnel: Sequence[tuple[str, int]],
                  *, reason: str = "") -> str:
    frozen = bool(audience["frozen_at"])
    rule = audience["rule"] or {}
    summary = (
        f"Segments {esc(', '.join(f'{k} {v}' for k, v in (rule.get('segments') or {}).items()))}"
        f"; cantons {esc(', '.join(rule.get('cantons') or []) or 'all')}"
        f"; addresses {esc(', '.join(rule.get('kinds') or ['desk']))} read off "
        f"{esc(', '.join(rule.get('provenance') or ['page']))}"
        f"; cooldown {esc(rule.get('cooldown_days', 180))} days."
    )
    built = (f"built {esc(str(audience['built_at'])[:16])}" if audience["built_at"]
             else "never built")
    actions = "" if frozen else (
        f"<form method='post' action='/audience/{audience['id']}/refresh' class='inline'>"
        "<button>Refresh the addresses</button></form> "
        f"<form method='post' action='/audience/{audience['id']}/build' class='inline'>"
        "<button class='primary'>Build</button></form>"
    )
    chips = "".join(
        f"<a href='/audience/{audience['id']}?reason={esc(code)}'>"
        f"{ui.chip(f'{code} {n}')}</a>" for code, n in funnel
    )
    body = (
        f"{ui.crumbs([('/audiences', 'Lists'), ('', audience['code'])])}"
        f"<h1>{esc(audience['code'])}: {esc(audience['name'])}</h1>"
        f"<p class='lede'>{summary} {built}."
        + (" Frozen: a campaign has sent from it." if frozen else "") + "</p>"
        + f"<div class='row'>{actions} <a href='/audience/{audience['id']}/export.csv'>CSV</a></div>"
        + f"<div class='cm-funnel'>{chips}</div>"
    )
    here = None
    table_rows: list[str] = []
    sections: list[str] = []

    def flush() -> None:
        if table_rows:
            sections.append(
                f"<h2 class='cm-canton'>{esc(here or '??')}</h2>"
                + ui.table(["score", "firm", "lang", "address", "why", ""], table_rows,
                           caption=f"{here or '??'}", numeric=["score"]))
            table_rows.clear()

    for row in rows:
        if row["canton"] != here:
            flush()
            here = row["canton"]
        score = f"{float(row['score']):.1f}" if row["score"] is not None else "-"
        covered = (f" (covered by {esc(row['covered_by_name'])})"
                   if row["covered_by_name"] else "")
        table_rows.append(
            f"<tr><td class='n' title='{esc(_parts(row))}'>{score}</td>"
            f"<td>{esc(row['company_name'])}</td>"
            f"<td title='{esc(row['language_basis'] or '')}'>{esc(row['language'] or '')}</td>"
            f"<td>{esc(row['address'] or '')} {_chips(row)}</td>"
            f"<td class='cm-reason'>{esc(row['reason'])}{covered}</td>"
            f"<td>{_pin_form(row, frozen=frozen)}</td></tr>"
        )
    flush()
    title = f"Dropped: {reason}" if reason else "Kept, by canton then score"
    return body + ui.panel(title, "".join(sections) or ui.empty(
        "Nothing here. Build the list to see who it keeps."), always=True)


# --- campaigns ------------------------------------------------------------------


def campaigns_page(rows: Sequence[Row], audiences: Sequence[Row],
                   programmes: Sequence[Row]) -> str:
    items = [
        f"<tr><td><a href='/campaign/{r['id']}'>{esc(r['code'])}</a></td>"
        f"<td>{esc(r['name'])}</td><td>{esc(r['audience_code'])}</td>"
        f"<td>{esc(r['strategy'])}</td><td class='n'>{r['waves_sent']}/{r['waves']}</td></tr>"
        for r in rows
    ]
    if audiences:
        list_field = ui.select_field("audience", "List",
                                     [(a["code"], a["code"]) for a in audiences],
                                     required=True)
    else:
        # A campaign sends from a list, so with none there is nothing to pick.
        # Saying so beats a dropdown with nothing in it.
        list_field = ui.field(
            "List",
            "<select name='audience' id='f-audience' disabled>"
            "<option>No list yet</option></select>"
            "<span class='hint'>A campaign sends from a list, so "
            "<a href='/audiences'>make a list</a> first.</span>",
            for_="f-audience",
        )
    form = (
        "<form method='post' action='/campaigns/new' class='grid'>"
        + ui.text_field("code", "Code", placeholder="DRYMOS-A", required=True)
        + ui.text_field("name", "Name", placeholder="Drymos, essai", required=True)
        + list_field
        + ui.text_field("templates", "Message templates", placeholder="templates/drymos",
                        hint="The folder holding the message in each language: fr, "
                             "de and it. Leave it empty to start from the example.")
        + ui.checkbox("start_from_example", "Start from the example",
                      hint="Copies the example messages into a new folder named after "
                           "the campaign, to be rewritten on the campaign's page.")
        + ui.select_field("programme", "Programme",
                          [(p["code"], p["code"]) for p in programmes], blank="none")
        + _strategy_field("canton-blocks")
        + "<div class='ctl ctl-wide'>"
        + ui.button("Create the campaign", primary=True, disabled=not audiences)
        + "</div></form>"
    )
    listed = (
        ui.table(
            ["campaign", "name", "list", "strategy", "waves sent"], items,
            caption="Campaigns", numeric=["waves sent"])
        if items else ui.empty(
            "No campaign yet.",
            action=("Start one below." if audiences else
                    "A campaign sends from a list, so make a list first."),
        )
    )
    return (
        "<h1>Campaigns</h1><p class='lede'>One message plan over one list, sent in "
        "waves. Each wave is rendered, tested to you, approved and only then "
        "sent.</p>"
        + ui.panel("Your campaigns", listed, always=True)
        + ui.panel("Start a campaign", form, always=True)
    )


def _strategy_field(chosen: str) -> str:
    listed = waves.STRATEGIES.values()
    hint = " ".join(f"{s.label}: {s.what}" for s in listed)
    return ui.select_field("strategy", "How waves are cut",
                           [(s.name, s.label) for s in listed], chosen=chosen,
                           hint=hint, wide=True)


STEPS = (("render", "rendered_at"), ("test", "tested_at"),
         ("approve", "approved_at"), ("send", "sent_at"))


def _steps(wave: Row) -> str:
    return "<div class='cm-steps'>" + "".join(
        f"<span class='cm-step{' done' if wave[col] else ''}'>{name}</span>"
        for name, col in STEPS) + "</div>"


def _messages_panel(cid: int, folder: str, files: Sequence[dict[str, Any]]) -> str:
    """The folder's messages, one row per file, and the way to write a missing one."""
    rows = []
    for item in files:
        if item["state"] == "absent":
            cell = "<span class='dim'>not written</span>"
            link = f"<a href='/campaign/{cid}/template/{esc(item['name'])}'>Write it</a>"
        elif item["state"] == "ok":
            cell = esc(item["subject"])
            link = f"<a href='/campaign/{cid}/template/{esc(item['name'])}'>Edit</a>"
        else:
            cell = f"<span class='state-error'>{esc(item['problem'])}</span>"
            link = f"<a href='/campaign/{cid}/template/{esc(item['name'])}'>Fix it</a>"
        rows.append(
            f"<tr><td><code>{esc(item['name'])}</code></td>"
            f"<td>{esc(item['variant'])}</td><td>{cell}</td><td>{link}</td></tr>"
        )
    return ui.panel(
        "Messages",
        ui.table(["file", "language", "subject", ""], rows,
                 caption=f"The messages in {folder}, one per language"),
        always=True,
        note="A wave is rendered from these. The subject and the text are "
             "edited here; the opt-out and the signature are added when it is sent.",
    )


def template_editor_page(campaign: Row, name: str, text: str, *, exists: bool) -> str:
    """One message file, as a form; refused with the reason when it cannot be sent."""
    cid = campaign["id"]
    language = name.split(".")[0]
    label = {"fr": "Objet :", "de": "Betreff:", "it": "Oggetto:",
             "rm": "Object:"}.get(language, "Subject:")
    if not text.strip():
        text = (
            f"Notes for the person editing this file go above the first rule.\n\n"
            f"---\n\n{label} \n\n\n\n---\n"
        )
    return (
        f"{ui.crumbs([('/campaigns', 'Campaigns'), (f'/campaign/{cid}', campaign['code']), ('', name)])}"
        f"<h1>{esc(name)} of {esc(campaign['code'])}</h1>"
        "<p class='lede'>The message is the text between the two lines of "
        f"<code>---</code>. Its first line is <code>{esc(label)}</code> followed by "
        "the subject. No placeholders: every recipient of this language receives "
        "the same words. The file is refused, naming the reason, when it cannot "
        "be sent as it stands.</p>"
        f"<form method='post' action='/campaign/{cid}/template/{esc(name)}'>"
        + ui.sr_label("The message file", "template-text")
        + f"<textarea name='text' id='template-text' class='big-text' rows='22' "
        f"style='width:100%;font:13px/1.5 ui-monospace,Menlo,monospace'>{esc(text)}</textarea>"
        "<div class='ctl ctl-wide' style='margin-top:.6rem'>"
        + ui.button("Save the message" if exists else "Write the message", primary=True)
        + f" <a href='/campaign/{cid}'>Back to the campaign</a></div></form>"
    )


def campaign_page(campaign: Row, wave_rows: Sequence[Row], results: dict[int, Any],
                  state: str, *, files: Sequence[dict[str, Any]] = ()) -> str:
    cid = campaign["id"]
    plan_form = (
        f"<form method='post' action='/campaign/{cid}/plan' class='grid'>"
        + _strategy_field(campaign["strategy"])
        + ui.text_field("pilot_size", "Pilot", value=str(campaign["pilot_size"]), kind="number",
                        hint="Firms in the first wave, alone, before any other.")
        + ui.text_field("wave_size", "Wave size", value=str(campaign["wave_size"]),
                        kind="number")
        + ui.text_field("gap_days", "Days between waves", value=str(campaign["gap_days"]),
                        kind="number")
        + ui.text_field("start_on", "First wave", kind="date",
                        value=str(campaign["start_on"] or ""))
        + ui.checkbox("dry_run", "Show the cut, keep nothing", checked=True)
        + "<div class='ctl ctl-wide'>" + ui.button("Plan the waves", primary=True)
        + "</div></form>"
    )
    banner = ""
    if campaign["paused_at"]:
        banner = ui.banner(
            "", kind="bad", html_body=(
                f"Paused: {esc(campaign['paused_reason'])}. "
                f"<form method='post' action='/campaign/{cid}/resume' class='inline'>"
                + ui.sr_label("Why it may go on", "resume-why")
                + "<input name='why' id='resume-why' required placeholder='why it may go on'>"
                "<button>Resume</button></form>"))
    cards = []
    for w in wave_rows:
        done = results.get(int(w["number"]))
        tally = ""
        if done and done["tally"].sent:
            t = done["tally"]
            tally = (f"<div class='cm-tally'>sent {t.sent}, bounced {t.bounced}, replied "
                     f"{t.replied}, refused {t.refused}, enrolled {t.enrolled}. "
                     f"{esc(done['gate'].why)}</div>")
        cards.append(
            "<div class='cm-wave'>"
            f"<h3><a href='/campaign/{cid}/wave/{w['number']}'>Wave {w['number']}</a>"
            + (" · pilot" if w["pilot"] else "") + "</h3>"
            f"<div class='meta'>{int(w['live'])} firms · "
            f"{esc(str(w['planned_for'] or 'unscheduled'))} · {esc(w['label'])}</div>"
            + _steps(w) + tally + "</div>"
        )
    return (
        f"{ui.crumbs([('/campaigns', 'Campaigns'), ('', campaign['code'])])}"
        f"<h1>{esc(campaign['code'])}: {esc(campaign['name'])}</h1>"
        f"<p class='lede'>List <a href='/audience/{campaign['audience_id']}'>"
        f"{esc(campaign['audience_code'])}</a>, templates {esc(campaign['templates'])}, "
        f"programme {esc(campaign['programme_code'] or 'none')}. State: {esc(state)}. "
        'Every message carries your <a href="/signature">signature</a>.</p>'
        + banner
        + (_messages_panel(cid, campaign["templates"], files) if files else "")
        + ui.panel("Waves", "<div class='cm-grid'>" + "".join(cards) + "</div>"
                   if cards else ui.empty("No plan yet. Plan the waves below."), always=True)
        + ui.panel("Plan", plan_form, always=True,
                   note="Re-planning is free until the first message leaves.")
        + (f"<form method='post' action='/campaign/{cid}/pause' class='inline'>"
           + ui.sr_label("Why it should stop", "pause-why")
           + "<input name='why' id='pause-why' required placeholder='why it should stop'>"
           "<button>Pause</button></form>" if not campaign["paused_at"] else "")
    )


def preview(subject: str, body: str, signature: str) -> str:
    """The message in a sandboxed frame, rendered as the send renders it."""
    html = letter.render(body, signature_html=signature)
    return (f"<p><b>{esc(subject)}</b></p>"
            f"<iframe class='cm-preview' sandbox srcdoc=\"{_escape(html, quote=True)}\">"
            "</iframe>")


def wave_page(campaign: Row, wave: Row, messages: Sequence[Row], variants: Sequence[Row],
              tests: Sequence[Row], *, signature: str, quota_room: int,
              pause_seconds: int, confirmation: str, block: str = "") -> str:
    cid, number = campaign["id"], wave["number"]
    base = f"/campaign/{cid}/wave/{number}"
    live = [m for m in messages if not m["dropped_at"]]
    rows = [
        f"<tr><td>{esc(m['company_name'])}</td><td>{esc(m['canton'])}</td>"
        f"<td>{esc(m['language'])}</td><td>{esc(m['address'])}</td>"
        f"<td>{esc(m['variant'] or '')}</td>"
        f"<td>{'sent' if m['sent_at'] else ('dropped: ' + esc(m['dropped_reason']) if m['dropped_at'] else ('approved' if m['reviewed_at'] else ''))}</td></tr>"
        for m in messages
    ]
    rendered, tested, approved, sent = (bool(wave["rendered_at"]), bool(wave["tested_at"]),
                                        bool(wave["approved_at"]), bool(wave["sent_at"]))

    def step(label: str, action: str, enabled: bool, extra: str = "") -> str:
        return (f"<form method='post' action='{base}/{action}' class='inline'>{extra}"
                + ui.button(label, primary=enabled, disabled=not enabled) + "</form>")

    pipeline = (
        step("Render", "render", not sent)
        + step("Send the test to me", "test", rendered and not sent)
        + step("Approve", "approve", tested and not approved and not sent,
               "<label class='check'><input type='checkbox' name='read' value='1' required>"
               " I received and read every test</label> ")
        + (step("Withdraw approval", "unapprove", True) if approved and not sent else "")
    )
    minutes = max(0, len(live) - 1) * pause_seconds / 60
    send_form = (
        f"<form method='post' action='{base}/send'>"
        f"<p>{len(live)} message(s), {pause_seconds}s apart, about {minutes:.0f} min. "
        f"{quota_room} left in today's quota. Each one carries your "
        '<a href="/signature">signature</a>.</p>'
        + (f"<p class='cm-reason'>{esc(block)}</p>" if block else "")
        + ui.text_field("confirm", f"Type {confirmation} to send", placeholder=confirmation)
        + ui.button("Send the wave", primary=approved and not sent and not block,
                    disabled=not approved or sent or bool(block))
        + "</form>"
    )
    previews = "".join(
        f"<h3>{esc(v['variant'])}</h3>" + preview(v["subject"] or "", v["body"] or "", signature)
        for v in variants
    ) or ui.empty("Render the wave to see its messages.")
    test_rows = [
        f"<tr><td>{esc(str(t['attempted_at'])[:16])}</td><td>{esc(t['address'])}</td>"
        f"<td>{esc(t['variant'] or '')}</td>"
        f"<td>{'posted' if t['posted_at'] else ('failed: ' + esc(t['failure'] or '') if t['failed_at'] else 'no outcome')}</td></tr>"
        for t in tests
    ]
    return (
        f"{ui.crumbs([('/campaigns', 'Campaigns'), (f'/campaign/{cid}', campaign['code']), ('', f'Wave {number}')])}"
        f"<h1>{esc(campaign['code'])}, wave {number}" + (" (pilot)" if wave["pilot"] else "")
        + f"</h1><p class='lede'>{esc(wave['label'])}</p>"
        + _steps(wave)
        + ui.panel("Steps", pipeline, always=True,
                   note="Each step clears the ones after it when repeated: a new render "
                        "needs a new test and a new approval.")
        + ui.panel("Send", send_form, always=True)
        + ui.panel("What recipients receive", previews, always=True)
        + ui.panel("Tests sent", ui.table(["when", "to", "variant", "outcome"], test_rows,
                                          caption="Tests of this wave",
                                          empty_text="No test sent yet."), always=True)
        + ui.panel("Recipients", ui.table(
            ["firm", "canton", "lang", "address", "variant", "state"], rows,
            caption=f"Wave {number} recipients"), always=True)
    )
