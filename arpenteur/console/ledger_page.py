"""Where every firm on a programme stands, and what it answered.

The board shows a card per stage, which answers how far the campaign has got.
It does not answer the question asked while working one firm: was a message
sent, did it arrive, did they write back, and what did they say. Those four
facts come from four tables, and until `campaign_ledger` joined them the answer
was four queries and a join done by eye.

**One row per firm, every status in it.** What went out is counted from
`intel.draft.sent_at`, what came back from `crm.email_message`, whether the
address survived from `core.contact`, and the stage from the member. A row that
reads `3 out, 0 in, address failed` is a firm never reached, which looks
identical on a board to one that ignored three messages.

**The enrolment is accepted here, never computed here.** The answers stage
reads a platform and an account out of a reply and proposes them. This page
shows the proposal beside the firm and a person presses the button. A token
match putting somebody into a cohort of twelve is the failure worth the extra
click.

**The account is shown, because hiding it would mean looking it up elsewhere.**
It is a working address volunteered for enrolment, held on the member and
nowhere else, and whoever reads this page is the person who types it into a
store console.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Sequence

from ..kernel.db import Row
from ..modules.clients.store import LEDGER_FLAGS
from . import ui
from .ui import esc
from .render import page

STYLES = """
.led-tally { display:flex; gap:1.4rem; margin:.5rem 0 1rem; font-size:.85rem; }
.led-tally b { font-size:1.25rem; display:block; }
.led { width:100%; border-collapse:collapse; font-size:.85rem; }
.led th { text-align:left; font-weight:600; color:var(--muted);
          font-size:.74rem; text-transform:uppercase; letter-spacing:.04em;
          padding:.35rem .5rem; border-bottom:1px solid var(--line); }
.led td { padding:.45rem .5rem; border-bottom:1px solid var(--line);
          vertical-align:top; }
.led tr.silent td { color:var(--muted); }
.led tr.dead td { background:var(--bad-bg); }
.led .firm { font-weight:600; }
.led .addr { font-family:ui-monospace, monospace; font-size:.78rem;
             color:var(--muted); }
.led .n { text-align:right; font-variant-numeric:tabular-nums; }
.enrol-form { display:flex; gap:.3rem; align-items:center; flex-wrap:wrap; }
.enrol-form select, .enrol-form input { font-size:.78rem; padding:.15rem .3rem; }
.enrol-form input[name=account] { width:12rem; }
.hist { width:100%; border-collapse:collapse; font-size:.85rem; }
.hist td { padding:.4rem .5rem; border-bottom:1px solid var(--line); }
.hist .out { color:var(--muted); }
.why { font-size:.76rem; color:var(--muted); margin:.15rem 0 0; }
"""


def shell(body: str, chrome: Any = None) -> str:
    return page("arpenteur", body, chrome, css=STYLES)


def _when(value: Any) -> str:
    if not value:
        return ""
    return str(value)[:10]


def _tally(tally: dict[str, int]) -> str:
    cells = "".join(
        f"<span>{esc(label)}<b>{tally.get(key, 0):,}</b></span>"
        for key, label in (
            ("members", "on the programme"),
            ("answered", "answered"),
            ("silent", "written to, silent"),
            ("failed", "address failed"),
            ("refused", "asked to stop"),
            ("enrollable", "enrolled"),
        )
    )
    return f'<div class="led-tally">{cells}</div>'


def _filters(programme_id: int, flag: str, stage: str, stages: Sequence[str]) -> str:
    flags = "".join(
        f'<option value="{esc(code)}"{" selected" if flag == code else ""}>'
        f"{esc(label)}</option>"
        for code, label in (
            ("", "every firm"),
            ("answered", "answered"),
            ("silent", "written to, silent"),
            ("failed", "address failed"),
            ("refused", "asked to stop"),
            ("enrollable", "enrolled on a track"),
        )
    )
    cells = "".join(
        f'<option value="{esc(name)}"{" selected" if stage == name else ""}>'
        f"{esc(name)}</option>"
        for name in stages
    )
    return (
        f'<form method="get" action="/programme/{programme_id}/ledger" '
        'class="row" style="margin:0 0 .9rem;gap:.5rem">'
        f'<select name="flag">{flags}</select>'
        f'<select name="stage"><option value="">every stage</option>{cells}'
        "</select>"
        '<button type="submit">Show</button></form>'
    )


def ledger_page(
    programme: Row,
    rows: list[Row],
    tally: dict[str, int],
    *,
    readings: dict[int, Any] | None = None,
    flag: str = "",
    stage: str = "",
    note: str = "",
    error: str = "",
) -> str:
    """The whole programme as one table, with the enrolment form per firm."""
    if flag and flag not in LEDGER_FLAGS:
        flag = ""
    proposals = readings or {}
    banners = ui.banner(note, kind="good") if note else ""
    if error:
        banners += ui.banner(error, kind="bad")
    if tally.get("failed"):
        banners += ui.banner(
            f"{tally['failed']} firm(s) hold an address that returned a "
            "permanent failure. A message counted as sent to one of those "
            "never arrived. `arpenteur verify` and `arpenteur bounces` write "
            "those verdicts.",
            kind="warn",
        )
    body = (
        _table(rows, proposals)
        if rows
        else ui.empty(
            "No firm matches. `arpenteur programme bring-in` puts a shortlist "
            "on the programme, and marking a draft sent moves the first card."
        )
    )
    return (
        f'<a href="/programme/{programme["id"]}">&larr; board</a>'
        f'<h1>{esc(programme["code"])} status</h1>'
        "<p class='lede'>One row per firm: what went out, what came back, "
        "whether the address survived, and what they enrolled on. Nothing "
        "here sends anything.</p>"
        + _tally(tally)
        + banners
        + _filters(int(programme["id"]), flag, stage, programme["stages"] or [])
        + body
    )


def _table(rows: list[Row], proposals: dict[int, Any]) -> str:
    head = (
        "<tr><th>stage</th><th>firm</th><th class='n'>out</th>"
        "<th class='n'>in</th><th>last reply</th><th>enrolment</th></tr>"
    )
    return f'<table class="led"><thead>{head}</thead><tbody>' + "".join(
        _row(row, proposals.get(int(row["member_id"]))) for row in rows
    ) + "</tbody></table>"


def _row(row: Row, reading: Any = None) -> str:
    kind = ""
    if row["failed"] or row["refused"]:
        kind = "dead"
    elif row["sent"] and not row["inbound"]:
        kind = "silent"
    note = ""
    if row["refused"]:
        note = "asked to stop"
    elif row["failed"]:
        note = "address failed"
    return (
        f'<tr class="{kind}">'
        f'<td>{esc(row["stage"])}</td>'
        f'<td><span class="firm">{esc(row["company_name"])}</span> '
        f'<span class="addr">{esc(row["canton"] or "")}</span><br>'
        f'<a class="addr" href="/member/{row["member_id"]}/history">'
        f'{esc(row["contact"] or "no address recorded")}</a></td>'
        f'<td class="n">{row["sent"]}</td>'
        f'<td class="n">{row["inbound"]}</td>'
        f'<td>{esc(_when(row["last_inbound"]))}</td>'
        f'<td>{_enrolment(row, reading)}{_note(note)}</td>'
        "</tr>"
    )


def _note(text: str) -> str:
    return f'<p class="why">{esc(text)}</p>' if text else ""


def _enrolment(row: Row, reading: Any = None) -> str:
    """What the firm enrolled on, or the form that records it.

    A member already carrying a platform shows it as settled, since changing
    it means moving a tester between store tracks and is an edit rather than a
    first answer. Everything else gets the form, pre-filled from the reading
    when the answers stage proposed one.
    """
    if row["platform"]:
        parts = [f"<b>{esc(row['platform'])}</b>"]
        if row["tester_account"]:
            parts.append(f'<span class="addr">{esc(row["tester_account"])}</span>')
        if row["window_ok"] is True:
            parts.append("dates accepted")
        elif row["window_ok"] is False:
            parts.append("dates declined")
        else:
            parts.append('<span class="why">dates not confirmed</span>')
        return " ".join(parts)
    if not row["inbound"]:
        return '<span class="why">nothing back</span>'
    platform = getattr(reading, "platform", "") or ""
    account = getattr(reading, "account", "") or ""
    why = list(getattr(reading, "why", ()) or ())
    options = "".join(
        f'<option value="{code}"{" selected" if platform == code else ""}>'
        f"{label}</option>"
        for code, label in (("", "platform"), ("ios", "iOS"),
                            ("android", "Android"))
    )
    return (
        f'<form method="post" action="/member/{row["member_id"]}/answer" '
        'class="enrol-form">'
        f'<select name="platform">{options}</select>'
        f'<input name="account" value="{esc(account)}" '
        'placeholder="Google account">'
        '<label title="the tester accepted the dated window">'
        '<input type="checkbox" name="window_ok" value="1"> dates</label>'
        '<button type="submit">Record</button></form>'
        + (f'<p class="why">{esc("; ".join(why))}</p>' if why else "")
    )


def history_page(
    member: Row, rows: list[Row], *, note: str = "", today: date | None = None
) -> str:
    """Every message to and from one firm, oldest first.

    Both directions, because the question is what happened in order. A row
    marked `out` was written by the operator in their own client and read back
    from the Sent folder, which is the only way this program ever learns that a
    message left.
    """
    listed = "".join(
        f'<tr class="{esc(row["direction"])}">'
        f'<td>{esc(_when(row["sent_at"]))}</td>'
        f'<td>{"&rarr;" if row["direction"] == "out" else "&larr;"}</td>'
        f'<td class="addr">{esc(_counterparty(row))}</td>'
        f'<td>{esc(row["subject"] or "(no subject)")}</td>'
        f'<td class="addr">{esc(row["state"] or "")}</td>'
        "</tr>"
        for row in rows
    )
    body = (
        f'<table class="hist"><tbody>{listed}</tbody></table>'
        if rows
        else ui.empty(
            "No message is attributed to this firm. `arpenteur mail` reads "
            "every readable folder and matches a message by a published "
            "address, a learnt link, or the domain."
        )
    )
    return (
        f'<a href="/programme/{member["programme_id"]}/ledger">&larr; status</a>'
        f'<h1>{esc(member["company_name"])}</h1>'
        '<div class="meta" style="font-size:.8rem;color:var(--muted);'
        'margin:.2rem 0 1rem">'
        f'<span>{esc(member["stage"])}</span> &middot; '
        f'<span>{esc(member["canton"] or "")}</span> &middot; '
        f'<span>{len(rows)} message(s)</span></div>'
        + (ui.banner(note, kind="good") if note else "")
        + body
    )


def _counterparty(row: Row) -> str:
    if row["direction"] == "out":
        return ", ".join(row["to_emails"] or [])
    return row["from_email"] or ""
