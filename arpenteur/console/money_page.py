"""Quotes and invoices, on screen and on paper.

There is no PDF library here and that is a decision rather than an omission.
The two candidates want either a C toolchain or cairo and pango on the machine,
and this codebase has kept itself to pure Python with no build step throughout.
What a browser prints from a page with a proper print stylesheet is a PDF, made
by software every machine already has and that renders the page the operator
just checked on screen. The alternative is a second renderer that produces a
document nobody has looked at until a client has it.

The QR-bill is the part with no room for interpretation. Its geometry is
prescribed to the millimetre: the payment part is 148 mm wide by 105 mm high
at the foot of the page, the receipt 62 mm beside it, the code 46 mm square,
and the whole thing separated by a line a person is expected to cut along. So
the sizes below are in millimetres and not in rem, and they are not rounded to
something tidier.

What is deliberately absent: a send button. The quote is marked as sent by a
person who sent it, which is what freezes the version. Nothing in this codebase
transmits anything.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from . import ui
from ..config import IssuerConfig
from ..kernel.db import Row
from ..modules.sales import money
from .render import esc, page

STYLES = """
.money-head { display:flex; gap:.8rem; align-items:baseline; flex-wrap:wrap; }
.money-head h1 { margin:0; }
.state { display:inline-block; border:1px solid var(--line); border-radius:1rem;
         padding:.05rem .6rem; font-size:.78rem; background:var(--paper); color:var(--dim); }
.state.sent, .state.issued { border-color:var(--accent); color:var(--accent); }
.state.accepted, .state.paid { border-color:var(--good); color:var(--good); font-weight:600; }
.state.declined, .state.cancelled, .state.credited { text-decoration:line-through; }
.lines { width:100%; }
.lines td.num, .lines th.num { text-align:right; font-variant-numeric:tabular-nums;
                               white-space:nowrap; }
.lines tfoot td { border-bottom:0; padding-top:.5rem; }
.lines tfoot tr.total td { font-weight:700; border-top:2px solid var(--ink); }
.lines .basis { font-size:.76rem; color:var(--dim); }
.versions { display:flex; gap:.5rem; flex-wrap:wrap; margin:.3rem 0 1rem; }
.versions a { border:1px solid var(--line); padding:.1rem .55rem; background:var(--paper);
              text-decoration:none; font-size:.82rem; }
.versions a.on { border-color:var(--accent); font-weight:600; }
.frozen { background:var(--bad-bg); border-left:3px solid var(--accent); padding:.6rem .9rem;
          margin:1rem 0; font-size:.88rem; }
.addline { display:flex; gap:.6rem; flex-wrap:wrap; align-items:end;
           background:var(--paper); border:1px solid var(--line); padding:.8rem .9rem; }
.addline .ctl { display:flex; flex-direction:column; gap:.15rem; }
.addline label { font-size:.72rem; text-transform:uppercase; letter-spacing:.05em;
                 color:var(--dim); }
.addline input[type=number] { width:7rem; }
.money-acts { display:flex; gap:.6rem; flex-wrap:wrap; margin:1rem 0; }
.money-acts form { display:inline; }
"""

# The printed document. A separate stylesheet because almost none of the
# console's own rules belong on paper, and because the payment part is measured
# in millimetres against a physical page rather than in anything relative.
PRINT = """
@page { size:A4; margin:0; }
body { margin:0; background:#fff; color:#000;
       font:10pt/1.4 Helvetica, Arial, sans-serif; }
.sheet { width:210mm; min-height:297mm; margin:0 auto; padding:20mm 20mm 0;
         box-sizing:border-box; position:relative; background:#fff; }
.doc-head { display:flex; justify-content:space-between; gap:10mm; }
.issuer { font-size:9pt; line-height:1.35; }
.issuer b { font-size:11pt; }
.addressee { margin:22mm 0 10mm; font-size:10pt; line-height:1.4; }
h1.doc { font-size:14pt; margin:0 0 1mm; }
.doc-meta { font-size:9pt; color:#333; margin:0 0 8mm; }
table.doc-lines { width:100%; border-collapse:collapse; font-size:9.5pt; }
table.doc-lines th { text-align:left; border-bottom:.4mm solid #000;
                     padding:1.5mm 2mm; font-size:8.5pt; text-transform:uppercase;
                     letter-spacing:.03em; }
table.doc-lines td { padding:1.8mm 2mm; border-bottom:.2mm solid #ccc;
                     vertical-align:top; }
table.doc-lines td.num, table.doc-lines th.num { text-align:right;
                                                 white-space:nowrap; }
table.doc-lines .detail { font-size:8.5pt; color:#444; }
table.doc-lines tfoot td { border-bottom:0; padding-top:2mm; }
table.doc-lines tfoot tr.total td { font-weight:700; border-top:.4mm solid #000; }
.terms { margin:8mm 0; font-size:9pt; color:#222; max-width:150mm; }
.novat { font-size:8.5pt; color:#222; margin:2mm 0; }
.printbar { padding:.6rem 1.2rem; background:#faf8f5; border-bottom:1px solid #d8d4cc;
            font:14px/1.4 -apple-system, sans-serif; }
.printbar a { margin-right:1rem; }

/* The QR-bill. 105 mm tall at the foot of the page, receipt 62 mm, payment
   part 148 mm, separated where the client is expected to cut. */
.qrbill { position:absolute; left:0; bottom:0; width:210mm; height:105mm;
          border-top:.2mm dashed #000; display:flex;
          font:8pt/1.25 Helvetica, Arial, sans-serif; box-sizing:border-box; }
.qrbill .receipt { width:62mm; padding:5mm; border-right:.2mm dashed #000;
                   box-sizing:border-box; }
.qrbill .payment { width:148mm; padding:5mm; box-sizing:border-box; display:flex;
                   gap:5mm; }
.qrbill h2 { font-size:11pt; font-weight:700; margin:0 0 3mm; }
.qrbill .receipt h2 { font-size:11pt; }
.qrbill .block { margin-bottom:2.5mm; }
.qrbill .block .caption { font-weight:700; font-size:6pt; line-height:1.4; }
.qrbill .receipt .block .caption { font-size:6pt; }
.qrbill .payment .col-left { width:51mm; }
.qrbill .payment .col-right { width:87mm; }
.qrbill .amount-row { display:flex; gap:6mm; margin-top:3mm; }
.qrbill .blank { border:.3mm solid #000; height:8mm; width:40mm; }
.qrbill .accept { font-weight:700; font-size:6pt; margin-top:5mm; text-align:right; }
.qr { display:block; margin:2mm 0 4mm; }
@media print { .printbar { display:none; } .sheet { margin:0; } }
"""


def _day(value: Any) -> str:
    """An ISO date as 13.08.2026.

    Dates arrive from the store as strings: the kernel's row factory renders
    every temporal with `isoformat()` on the way out, a convention carried over
    from the SQLite original. Formatting one with strftime works in a test that
    passes a date object and raises on every real row.
    """
    text = str(value or "")[:10]
    if len(text) != 10:
        return text
    year, month, day = text.split("-")
    return f"{day}.{month}.{year}"


def _num(value: Any) -> str:
    return money.format_chf(value or 0)


def state_tag(state: str) -> str:
    return f'<span class="state {esc(state)}">{esc(state.replace("_", " "))}</span>'


# --- the ledgers -----------------------------------------------------------------
#
# A quote and an invoice were reachable only from the client they belong to.
# So "what is out and unanswered" and "who has not paid" could be asked only by
# opening every client in turn and remembering, or through the Reports page,
# which aggregates and therefore cannot be acted on: a total tells you there is
# 14'200 francs outstanding and not whose.

QUOTE_STATES = ("draft", "sent", "accepted", "declined", "expired", "withdrawn")
INVOICE_STATES = ("draft", "issued", "part_paid", "paid", "overdue",
                  "credited", "cancelled")


def quotes_page(
    quotes: list[Row], totals: list[Row], *, status: str = "", q: str = "",
    chrome: Any = None,
) -> str:
    rows = [quote_row(row) for row in quotes]
    summary = "".join(
        ui.stat(f"{_num(t['total'])}", f"{t['status']} ({t['n']})")
        for t in totals
    )
    body = [
        "<h1>Quotes</h1>",
        '<p class="lede">Everything proposed, whoever it was proposed to. The '
        "money is the latest version of each quote, because that is the one the "
        "client is holding.</p>",
        f'<div class="acts">{summary}</div>' if summary else "",
        ui.toolbar(
            ui.text_field("q", "Search", value=q,
                          placeholder="A code, a client, a company")
            + ui.select_field("status", "State", QUOTE_STATES, chosen=status,
                              blank="Any state"),
            action="/quotes", target="#ledger", push=True,
        ),
        f'<div id="ledger">{_quote_table(rows)}</div>',
    ]
    return page("Quotes", "".join(body), chrome, css=STYLES)


def quote_row(row: Row) -> str:
    """One quote, as the page draws it and as the filter redraws it.

    One function rather than two matching string literals: the filter swaps the
    table in place, so a column added here and forgotten there gives a table
    that changes shape when somebody types.
    """
    late = bool(row["valid_until"]) and (
        str(row["valid_until"])[:10] < date.today().isoformat()
        and row["status"] in ("sent", "draft")
    )
    return (
        f"<tr><td><a href='/quote/{row['id']}'>{esc(row['code'])}</a>"
        f"<div class='dim small'>v{row['versions'] or 1}</div></td>"
        f"<td><a href='/client/{row['client_id']}'>{esc(row['company_name'])}</a>"
        f"<div class='dim small'>{esc(row['client_code'])}</div></td>"
        f"<td>{state_tag(row['status'])}</td>"
        f"<td class='n'>{_num(row['total'])}</td>"
        f"<td class='small'>{_day(row['sent_at']) if row['sent_at'] else '&mdash;'}</td>"
        f"<td class='small{' state-error' if late else ''}'>"
        f"{_day(row['valid_until']) if row['valid_until'] else '&mdash;'}"
        + ("<div class='dim'>expired</div>" if late else "")
        + "</td></tr>"
    )


def _quote_table(rows: list[str]) -> str:
    return ui.table(
        ["quote", "client", "state", "total", "sent", "valid until"],
        rows, caption="Quotes, newest first", numeric=["total"],
        empty_text="No quote matches. Quotes are raised from a client's page.",
    )


def invoices_page(
    invoices: list[Row], *, status: str = "", q: str = "", chrome: Any = None,
) -> str:
    rows = [invoice_row(row) for row in invoices]
    owed_total = sum(
        float(r["owed"] or 0) for r in invoices
        if r["status"] in ("issued", "part_paid", "overdue")
    )
    body = [
        _ledger_tabs("/invoices"),
        "<h1>Invoices</h1>",
        '<p class="lede">The ledger: drafts, credit notes and settled ones '
        'included. "Was that ever raised" gets asked at least as often as '
        '"who has not paid".</p>',
        f'<div class="acts">{ui.stat(_num(owed_total), "still owed", emphasis=True)}'
        f'{ui.stat(len(invoices), "shown")}</div>',
        ui.toolbar(
            ui.text_field("q", "Search", value=q,
                          placeholder="A reference, a rank, a client")
            + ui.select_field("status", "State", INVOICE_STATES, chosen=status,
                              blank="Any state"),
            action="/invoices", target="#ledger", push=True,
        ),
        f'<div id="ledger">{_invoice_table(rows)}</div>',
    ]
    return page("Invoices", "".join(body), chrome, css=STYLES)


def invoice_row(row: Row) -> str:
    """One invoice, drawn once for the page and the filter alike.

    Late is a state and not just a date: a credited or cancelled invoice with a
    due date in the past is not owing anything, and colouring it red would send
    somebody chasing a client who paid.
    """
    owed = row["owed"]
    late = (
        row["days_late"] is not None
        and int(row["days_late"]) > 0
        and row["status"] in ("issued", "part_paid", "overdue")
    )
    return (
        f"<tr><td><a href='/invoice/{row['id']}'>"
        f"{esc(row['reference'] or 'draft')}</a>"
        f"<div class='dim small'>{esc(row['number'] or 'unnumbered')}</div></td>"
        f"<td><a href='/client/{row['client_id']}'>{esc(row['company_name'])}</a>"
        f"<div class='dim small'>{esc(row['client_code'])}</div></td>"
        f"<td>{state_tag(row['status'])}</td>"
        f"<td class='n'>{_num(row['gross_total'])}</td>"
        f"<td class='n'>{_num(owed) if owed and float(owed) else '&mdash;'}</td>"
        f"<td class='small'>{_day(row['issued_on']) if row['issued_on'] else '&mdash;'}</td>"
        f"<td class='small{' state-error' if late else ''}'>"
        f"{_day(row['due_on']) if row['due_on'] else '&mdash;'}"
        + (f"<div class='dim'>{int(row['days_late'])} days late</div>" if late else "")
        + "</td></tr>"
    )


def _invoice_table(rows: list[str]) -> str:
    return ui.table(
        ["reference", "client", "state", "total", "owed", "issued", "due"],
        rows, caption="Invoices, newest first", numeric=["total", "owed"],
        empty_text="No invoice matches. Invoices are raised from a client's "
                   "page or from an accepted quote.",
    )


def _ledger_tabs(current: str) -> str:
    """Two readings of the same rows: what is owed, and whether any is missing."""
    return ui.tabs(
        [("/invoices", "Ledger"), ("/invoices/register", "Register")], current
    )


def register_page(
    rows: list[Row], gaps: list[str], *, year: str = "", chrome: Any = None,
) -> str:
    """The series by rank, which is the one view that can show something missing.

    The ledger answers what is owed. This answers whether anything left the
    building without being written down, which is the question CO art. 957a
    al. 2 asks when it demands that entries stay traceable. So it is ordered by
    the rank and not by the date, and a skipped rank gets a row of its own
    rather than being quietly stepped over.
    """
    missing = set(gaps)
    drawn: list[str] = []
    for row in rows:
        while missing and min(missing) < str(row["number"]):
            drawn.append(_gap_row(min(missing)))
            missing.discard(min(missing))
        drawn.append(_register_row(row))
    for rank in sorted(missing):
        drawn.append(_gap_row(rank))

    billed = sum(float(r["gross_total"] or 0) for r in rows)
    owed = sum(float(r["owed"] or 0) for r in rows
               if r["status"] in ("issued", "part_paid", "overdue"))
    body = [
        _ledger_tabs("/invoices/register"),
        "<h1>Register</h1>",
        '<p class="lede">Every invoice that was issued, in rank order. The rank '
        "is the register's and never appears on the document; the reference is "
        "what the client quotes. A rank with no invoice is shown rather than "
        "skipped, because a series that hides its holes proves nothing.</p>",
        f'<div class="acts">{ui.stat(_num(billed), "billed")}'
        f'{ui.stat(_num(owed), "still owed", emphasis=bool(owed))}'
        f'{ui.stat(len(rows), "documents")}'
        f'{ui.stat(len(gaps), "gaps", emphasis=bool(gaps))}</div>',
        ui.table(
            ["rank", "reference", "client", "work", "issued", "due", "paid",
             "total", "owed"],
            drawn, caption="The series, oldest first",
            numeric=["total", "owed"],
            empty_text="Nothing issued yet. A draft has no rank and is not in "
                       "the series until it is issued.",
        ),
        f'<p class="dim small"><a href="/invoices/register.csv{"?year=" + esc(year) if year else ""}">'
        "Download as CSV</a></p>",
    ]
    return page("Register", "".join(body), chrome, css=STYLES)


def _register_row(row: Row) -> str:
    owed = row["owed"]
    work = row["project_name"] or row["project_code"] or ""
    return (
        f"<tr><td class='small'>{esc(row['number'])}</td>"
        f"<td><a href='/invoice/{row['id']}'>{esc(row['reference'])}</a></td>"
        f"<td><a href='/client/{row['client_id']}'>{esc(row['company_name'])}</a>"
        f"<div class='dim small'>{esc(row['client_code'])}</div></td>"
        f"<td class='small'>{esc(work) if work else '&mdash;'}</td>"
        f"<td class='small'>{_day(row['issued_on']) if row['issued_on'] else '&mdash;'}</td>"
        f"<td class='small'>{_day(row['due_on']) if row['due_on'] else '&mdash;'}</td>"
        f"<td class='small'>{_day(row['paid_on']) if row['paid_on'] else state_tag(row['status'])}</td>"
        f"<td class='n'>{_num(row['gross_total'])}</td>"
        f"<td class='n'>{_num(owed) if owed and float(owed) else '&mdash;'}</td></tr>"
    )


def _gap_row(rank: str) -> str:
    """A rank nothing claims. Usually an issue that rolled back after taking one."""
    return (
        f"<tr class='gap'><td class='small'>{esc(rank)}</td>"
        "<td colspan='8' class='dim small'>No document carries this rank. "
        "A number is drawn outside the transaction, so an issue that failed "
        "consumed one. Note why, here or in the accounts.</td></tr>"
    )


def register_csv(rows: list[Row]) -> str:
    """The same series, for a spreadsheet. Separators and quoting by the csv module."""
    import csv
    import io

    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["rank", "reference", "client", "company", "work",
                     "issued", "due", "paid", "currency", "net", "vat",
                     "total", "received", "owed", "state"])
    for row in rows:
        writer.writerow([
            row["number"], row["reference"], row["client_code"],
            row["company_name"], row["project_name"] or row["project_code"] or "",
            row["issued_on"] or "", row["due_on"] or "", row["paid_on"] or "",
            row["currency"], row["net_total"], row["vat_total"],
            row["gross_total"], row["paid_total"], row["owed"], row["status"],
        ])
    return out.getvalue()


# --- the quote on screen ---------------------------------------------------------


def quotes_panel(quotes: list[Row], client_id: int) -> str:
    """The client page's list of what has been proposed."""
    if not quotes:
        rows = ('<tr><td colspan="5" class="dim small">Nothing quoted yet.</td></tr>')
    else:
        rows = "".join(
            f'<tr><td><a href="/quote/{q["id"]}">{esc(q["code"])}</a></td>'
            f'<td>{state_tag(q["status"])}</td>'
            f'<td class="small">v{q["versions"] or 0}</td>'
            f'<td class="num">{_num(q["total"])}</td>'
            f'<td class="small dim">{str(q["created_at"])[:10]}</td></tr>'
            for q in quotes
        )
    return (
        "<section><h2>Quotes</h2>"
        + ui.table(
            ["reference", "state", "version", "total", "opened"], [rows],
            caption="What this client has been proposed", numeric=["total"],
            css="lines",
        )
        + f'<form method="post" action="/client/{client_id}/quotes">'
          '<button type="submit">Start a quote</button></form></section>'
    )


def invoices_panel(invoices: list[Row], client_id: int) -> str:
    if not invoices:
        rows = '<tr><td colspan="5" class="dim small">Nothing billed yet.</td></tr>'
    else:
        rows = "".join(
            f'<tr><td><a href="/invoice/{i["id"]}">'
            f'{esc(i["number"] or "draft")}</a></td>'
            f'<td>{state_tag(i["status"])}</td>'
            f'<td class="small dim">{str(i["issued_on"] or "")[:10]}</td>'
            f'<td class="small dim">{str(i["due_on"] or "")[:10]}</td>'
            f'<td class="num">{_num(i["gross_total"])}</td></tr>'
            for i in invoices
        )
    return (
        "<section><h2>Invoices</h2>"
        + ui.table(
            ["number", "state", "issued", "due", "total"], [rows],
            caption="What this client has been billed", numeric=["total"],
            css="lines",
        )
        + f'<form method="post" action="/client/{client_id}/invoices">'
        + ui.text_field("what", "What it bills", placeholder="expertise",
                        hint="Joined to the client code to make the reference "
                             "the client quotes, such as FBG-EXPERTISE. No date "
                             "and no rank: those belong in the register.")
        + ui.checkbox("series", "One of several",
                      hint="Numbers it from the first, FBG-SEANCE-01, for work "
                           "that recurs. A client receiving several needs to see "
                           "which one they are paying.")
        + '<button type="submit">Start an invoice</button></form></section>'
    )


def _line_cells(line: Row, *, editable: bool, delete_to: str = "") -> str:
    detail = (f'<div class="basis">{esc(line["detail"])}</div>'
              if line["detail"] else "")
    basis = (f'<div class="basis">from the card: {esc(line["basis"])}</div>'
             if line["basis"] else "")
    remove = ""
    if editable and delete_to:
        remove = (f'<td><form method="post" action="{delete_to}/{line["id"]}/delete">'
                  '<button type="submit">Remove</button></form></td>')
    return (
        f'<td>{esc(line["label"])}{detail}{basis}</td>'
        f'<td class="num">{line["quantity"]}</td>'
        f'<td class="small">{esc(line["unit"])}</td>'
        f'<td class="num">{_num(line["unit_price"])}</td>'
        f'<td class="num">{_num(line["amount"])}</td>{remove}'
    )


def lines_table(
    lines: list[Row], totals: Row, *, editable: bool, delete_to: str,
    reorder_to: str, id: str, frozen_because: str = "",
) -> str:
    """The lines, in the order they will be read, and movable while they can be.

    A quote is a document somebody reads top to bottom, and the order is part
    of what it says: the survey before the plan before the travel, not the
    order they were entered in at half past six. There was no ordering control
    at all, and `position` had been in the schema since the first migration.

    Reordering is refused exactly where editing is, for the same reason and
    with the same sentence. A sent version and an issued invoice are documents
    somebody else holds a copy of.
    """
    frozen = frozen_because if not editable else ""
    body = ui.sortable_body(
        [(line["id"], _line_cells(line, editable=editable, delete_to=delete_to))
         for line in lines],
        endpoint=reorder_to, id=id, label="line", frozen=frozen,
    )
    # The handle, the label, qty, unit, price, amount. The Remove column is
    # deliberately not counted, exactly as before: the totals span the columns
    # that carry the document, so the amount lands under Amount rather than
    # under a button.
    columns = 6
    head = (
        '<tr><th class="sort-cell"><span class="vh">Order</span></th>'
        '<th>Line</th><th class="num">Qty</th><th>Unit</th>'
        '<th class="num">Price</th><th class="num">Amount</th>'
        + ("<th>&nbsp;</th>" if editable and delete_to else "")
        + "</tr>"
    )
    # The reason goes under the table rather than inside it: a `<tbody>` has
    # nowhere to put a sentence, and "why can I not move these" is asked at the
    # lines, not at the banner three paragraphs above them.
    if frozen:
        hint = f'<p class="hint">{esc(frozen)}</p>'
    elif len(lines) > 1:
        hint = f'<p class="hint">{esc(ui.HOW_TO_SORT)}</p>'
    else:
        hint = ""
    return (
        f'<table class="lines"><thead>{head}</thead>{body}'
        f'<tfoot>{_totals_rows(totals, columns=columns)}</tfoot></table>'
        + ui.sort_say(f"{id}-say") + hint
    )


def _totals_rows(row: Row, *, columns: int) -> str:
    net, rate = row["net_total"], row["vat_rate"]
    out = [f'<tr><td colspan="{columns - 1}" class="num">Net</td>'
           f'<td class="num">{_num(net)}</td></tr>']
    if rate is None:
        out.append(
            f'<tr><td colspan="{columns}" class="basis">Not subject to TVA.</td></tr>'
        )
    else:
        out.append(
            f'<tr><td colspan="{columns - 1}" class="num">TVA {rate}%</td>'
            f'<td class="num">{_num(row["vat_total"])}</td></tr>'
        )
    out.append(
        f'<tr class="total"><td colspan="{columns - 1}" class="num">Total</td>'
        f'<td class="num">{_num(row["gross_total"])}</td></tr>'
    )
    return "".join(out)


def quote_page(
    quote: Row, versions: list[Row], shown: Row, lines: list[Row],
    services: list[Row], *, chrome: Any = None,
) -> str:
    frozen = shown["sent_at"] is not None
    tabs = "".join(
        f'<a href="/quote/{quote["id"]}?v={v["version"]}"'
        + (' class="on"' if v["id"] == shown["id"] else "")
        + f'>v{v["version"]}'
        + (" &middot; sent" if v["sent_at"] else "")
        + "</a>"
        for v in versions
    )
    body = [
        f'<p class="crumbs"><a href="/clients">Clients</a> / '
        f'<a href="/client/{quote["client_id"]}">{esc(quote["client_code"])}</a>'
        f" / {esc(quote['code'])}</p>",
        '<div class="money-head">',
        f"<h1>{esc(quote['code'])}</h1>",
        state_tag(quote["status"]),
        f'<span class="dim small">{esc(quote["company_name"])}</span>',
        "</div>",
        f'<div class="versions">{tabs}</div>',
    ]
    if frozen:
        body.append(
            '<p class="frozen">Version ' + str(shown["version"])
            + " was sent on " + str(shown["sent_at"])[:10]
            + (f' to {esc(shown["sent_to"])}' if shown["sent_to"] else "")
            + ". It cannot be changed: what the client is holding has to go on "
            "saying what it said. Open a new version to revise it.</p>"
        )

    body.append(lines_table(
        lines, shown, editable=not frozen, delete_to="/quote-line",
        reorder_to=f'/quote-version/{shown["id"]}/order',
        id=f'quote-lines-{shown["id"]}',
        frozen_because="This version was sent, so its lines keep the order the "
                       "client received them in.",
    ))

    if not frozen:
        options = "".join(
            f'<option value="{s["id"]}">{esc(s["code"])} &middot; {esc(s["name"])}</option>'
            for s in services
        )
        body.append(
            f'<form class="addline" method="post" action="/quote-version/{shown["id"]}/lines">'
            f'<div class="ctl"><label>From the catalogue</label>'
            f'<select name="service_id"><option value="">(free line)</option>'
            f"{options}</select></div>"
            '<div class="ctl"><label>Or a label of your own</label>'
            '<input type="text" name="label" size="26"></div>'
            '<div class="ctl"><label>Quantity</label>'
            '<input type="number" name="quantity" value="1" step="0.25" min="0"></div>'
            '<div class="ctl"><label>Price</label>'
            '<input type="number" name="unit_price" step="0.05" min="0" '
            'placeholder="from the card"></div>'
            '<div class="ctl"><label>Card end</label>'
            '<select name="at"><option value="low">low</option>'
            '<option value="high">high</option></select></div>'
            '<button type="submit">Add</button></form>'
        )
        body.append('<div class="money-acts">')
        body.append(
            f'<form method="post" action="/quote-version/{shown["id"]}/sent">'
            '<input type="text" name="to" placeholder="sent to" size="24"> '
            '<button type="submit">Mark as sent</button></form>'
        )
        body.append("</div>")
        body.append(
            '<p class="dim small">Marking it sent freezes this version. '
            "Arpenteur does not send anything: you send it, and this records "
            "that you did.</p>"
        )
    else:
        body.append('<div class="money-acts">')
        for state, label in (("accepted", "Accepted"), ("declined", "Declined"),
                             ("withdrawn", "Withdrawn")):
            body.append(
                f'<form method="post" action="/quote/{quote["id"]}/status">'
                f'<input type="hidden" name="status" value="{state}">'
                f'<button type="submit">{label}</button></form>'
            )
        body.append(
            f'<form method="post" action="/quote/{quote["id"]}/versions">'
            '<button type="submit">New version</button></form>'
        )
        if quote["status"] == "accepted":
            body.append(
                f'<form method="post" action="/quote/{quote["id"]}/invoice">'
                '<button type="submit" class="primary">Raise an invoice</button></form>'
            )
        body.append("</div>")

    body.append(f'<p><a href="/quote/{quote["id"]}/print">Print this version</a></p>')
    return page(quote["code"], "".join(body), chrome, css=STYLES)


def invoice_page(
    invoice: Row, lines: list[Row], services: list[Row], *,
    issuer: IssuerConfig, chrome: Any = None,
) -> str:
    draft = invoice["status"] == "draft"
    body = [
        f'<p class="crumbs"><a href="/clients">Clients</a> / '
        f'<a href="/client/{invoice["client_id"]}">{esc(invoice["client_code"])}</a>'
        f" / {esc(invoice['number'] or 'draft invoice')}</p>",
        '<div class="money-head">',
        f"<h1>{esc(invoice['number'] or 'Draft invoice')}</h1>",
        state_tag(invoice["status"]),
        f'<span class="dim small">{esc(invoice["company_name"])}</span>',
        "</div>",
    ]
    if not draft:
        body.append(
            '<p class="frozen">Issued on ' + str(invoice["issued_on"])[:10]
            + ", due " + str(invoice["due_on"] or "")[:10]
            + ". An issued invoice is never edited: a correction is a credit "
            "note, because the client and their accounts hold the document as "
            "it was sent.</p>"
        )
    body.append(lines_table(
        lines, invoice, editable=draft, delete_to="/invoice-line",
        reorder_to=f'/invoice/{invoice["id"]}/order',
        id=f'invoice-lines-{invoice["id"]}',
        frozen_because="This invoice was issued, so its lines keep the order "
                       "the client received them in.",
    ))
    if draft:
        options = "".join(
            f'<option value="{s["id"]}">{esc(s["code"])} &middot; {esc(s["name"])}</option>'
            for s in services
        )
        body.append(
            f'<form class="addline" method="post" action="/invoice/{invoice["id"]}/lines">'
            '<div class="ctl"><label>From the catalogue</label>'
            f'<select name="service_id"><option value="">(free line)</option>{options}'
            "</select></div>"
            '<div class="ctl"><label>Or a label of your own</label>'
            '<input type="text" name="label" size="26"></div>'
            '<div class="ctl"><label>Quantity</label>'
            '<input type="number" name="quantity" value="1" step="0.25"></div>'
            '<div class="ctl"><label>Price</label>'
            '<input type="number" name="unit_price" step="0.05" value="0"></div>'
            '<button type="submit">Add</button></form>'
        )
        body.append('<div class="money-acts">')
        if issuer.can_bill:
            body.append(
                f'<form method="post" action="/invoice/{invoice["id"]}/issue">'
                '<button type="submit" class="primary">Issue</button></form>'
            )
        else:
            body.append(
                '<p class="dim small">This invoice cannot be issued: '
                "<code>[issuer]</code> in arpenteur.toml needs at least a name "
                "and an IBAN. A bill with no way to pay it is worse than no "
                "bill.</p>"
            )
        body.append("</div>")
    else:
        body.append('<div class="money-acts">')
        body.append(
            f'<form method="post" action="/invoice/{invoice["id"]}/payment">'
            '<input type="number" name="amount" step="0.05" '
            f'value="{invoice["gross_total"]}"> '
            "<button type=\"submit\">Record a payment</button></form>"
        )
        body.append(
            f'<form method="post" action="/invoice/{invoice["id"]}/credit">'
            '<input type="text" name="reason" placeholder="why" size="22"> '
            '<button type="submit">Credit note</button></form>'
        )
        body.append("</div>")
        if invoice["paid_total"]:
            body.append(
                f'<p class="dim small">Received: {_num(invoice["paid_total"])} '
                f'of {_num(invoice["gross_total"])}.</p>'
            )
    body.append(f'<p><a href="/invoice/{invoice["id"]}/print">Print</a></p>')
    return page(invoice["number"] or "Draft invoice", "".join(body),
                chrome, css=STYLES)


# --- on paper ---------------------------------------------------------------------


def _issuer_block(issuer: IssuerConfig) -> str:
    parts = [f"<b>{esc(issuer.name)}</b>"]
    if issuer.street:
        parts.append(esc(f"{issuer.street} {issuer.building}".strip()))
    if issuer.postcode or issuer.town:
        parts.append(esc(f"{issuer.postcode} {issuer.town}".strip()))
    if issuer.vat_number:
        parts.append(esc(issuer.vat_number))
    return f'<div class="issuer">{"<br>".join(parts)}</div>'


def _addressee(name: str, address: dict[str, Any] | None) -> str:
    lines = [esc(name)]
    for key in ("street", "houseNumber", "swissZipCode", "town", "city"):
        value = (address or {}).get(key)
        if value:
            lines.append(esc(str(value)))
    return f'<div class="addressee">{"<br>".join(lines)}</div>'


def _doc_lines(lines: list[Row], row: Row, currency: str) -> str:
    body = "".join(
        f"<tr><td>{esc(line['label'])}"
        + (f'<div class="detail">{esc(line["detail"])}</div>' if line["detail"] else "")
        + f'</td><td class="num">{line["quantity"]}</td>'
        f'<td class="num">{_num(line["unit_price"])}</td>'
        f'<td class="num">{_num(line["amount"])}</td></tr>'
        for line in lines
    )
    foot = [f'<tr><td colspan="3" class="num">Net</td>'
            f'<td class="num">{_num(row["net_total"])}</td></tr>']
    if row["vat_rate"] is None:
        foot.append(
            '<tr><td colspan="4" class="novat">Not subject to TVA '
            "(art. 10 al. 2 let. a LTVA).</td></tr>"
        )
    else:
        foot.append(
            f'<tr><td colspan="3" class="num">TVA {row["vat_rate"]}%</td>'
            f'<td class="num">{_num(row["vat_total"])}</td></tr>'
        )
    foot.append(
        f'<tr class="total"><td colspan="3" class="num">Total {esc(currency)}</td>'
        f'<td class="num">{_num(row["gross_total"])}</td></tr>'
    )
    return (
        '<table class="doc-lines"><thead><tr><th>Designation</th>'
        '<th class="num">Qty</th><th class="num">Unit price</th>'
        '<th class="num">Amount</th></tr></thead>'
        f"<tbody>{body}</tbody><tfoot>{''.join(foot)}</tfoot></table>"
    )


def quote_print(
    quote: Row, shown: Row, lines: list[Row], address: dict[str, Any] | None, *,
    issuer: IssuerConfig,
) -> str:
    head = [
        '<div class="printbar">'
        f'<a href="/quote/{quote["id"]}">Back</a>'
        "Print from the browser to produce a PDF. What you see is what prints."
        "</div>",
        '<div class="sheet">',
        f'<div class="doc-head">{_issuer_block(issuer)}</div>',
        _addressee(quote["company_name"], address),
        f'<h1 class="doc">Offer {esc(quote["code"])}'
        + (f" &middot; version {shown['version']}" if shown["version"] > 1 else "")
        + "</h1>",
        '<p class="doc-meta">'
        + (f'Valid until {_day(shown["valid_until"])}. '
           if shown["valid_until"] else "")
        + "</p>",
    ]
    if shown["intro"]:
        head.append(f'<div class="terms">{esc(shown["intro"])}</div>')
    head.append(_doc_lines(lines, shown, quote["currency"]))
    if shown["terms"]:
        head.append(f'<div class="terms">{esc(shown["terms"])}</div>')
    head.append("</div>")
    return _print_shell(f"Offer {quote['code']}", "".join(head))


def invoice_print(
    invoice: Row, lines: list[Row], address: dict[str, Any] | None, *,
    issuer: IssuerConfig, qr: str = "", payload_error: str = "",
) -> str:
    body = [
        '<div class="printbar">'
        f'<a href="/invoice/{invoice["id"]}">Back</a>'
        "Print from the browser to produce a PDF. What you see is what prints."
        "</div>",
        '<div class="sheet">',
        f'<div class="doc-head">{_issuer_block(issuer)}</div>',
        _addressee(invoice["company_name"], address),
        f'<h1 class="doc">Invoice {esc(invoice["number"] or "(draft)")}</h1>',
        '<p class="doc-meta">'
        + (f'Issued {_day(invoice["issued_on"])}. ' if invoice["issued_on"] else "")
        + (f'Due {_day(invoice["due_on"])}. ' if invoice["due_on"] else "")
        + (f'Reference {esc(money.format_reference(invoice["qr_reference"]))}.'
           if invoice["qr_reference"] else "")
        + "</p>",
        _doc_lines(lines, invoice, invoice["currency"]),
    ]
    if issuer.payment_terms:
        body.append(f'<div class="terms">{esc(issuer.payment_terms)}</div>')
    if invoice["note"]:
        body.append(f'<div class="terms">{esc(invoice["note"])}</div>')
    if payload_error:
        body.append(
            '<div class="terms"><b>No payment part.</b> '
            + esc(payload_error) + "</div>"
        )
    elif qr:
        body.append(qr)
    body.append("</div>")
    return _print_shell(f"Invoice {invoice['number'] or 'draft'}", "".join(body))


def qr_bill(
    invoice: Row, address: dict[str, Any] | None, *, issuer: IssuerConfig,
    svg: str,
) -> str:
    """The payment part and the receipt, in the prescribed layout.

    The captions are fixed by the standard and are not the place for house
    style: a client's accounts department, and their bank's scanner, both
    expect these exact words in these exact positions.
    """
    creditor = "<br>".join(esc(p) for p in (
        issuer.name, f"{issuer.street} {issuer.building}".strip(),
        f"{issuer.postcode} {issuer.town}".strip(),
    ) if p)
    debtor_lines = [invoice["company_name"]]
    for key in ("street", "houseNumber", "swissZipCode", "town"):
        value = (address or {}).get(key)
        if value:
            debtor_lines.append(str(value))
    debtor = "<br>".join(esc(p) for p in debtor_lines if p)
    reference = esc(money.format_reference(invoice["qr_reference"] or ""))
    # The IBAN is printed in groups of four, which is how it is written
    # everywhere else and how somebody checks it against their bank's screen.
    iban = money.clean_iban(invoice["qr_iban"] or issuer.iban)
    account = " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))
    amount = _num(invoice["gross_total"])
    currency = esc(invoice["currency"])

    ref_block = (
        f'<div class="block"><div class="caption">Reference</div>{reference}</div>'
        if reference else ""
    )
    return f"""<div class="qrbill">
  <div class="receipt">
    <h2>Receipt</h2>
    <div class="block"><div class="caption">Account / Payable to</div>
      {esc(account)}<br>{creditor}</div>
    {ref_block}
    <div class="block"><div class="caption">Payable by</div>{debtor}</div>
    <div class="amount-row">
      <div><div class="caption">Currency</div>{currency}</div>
      <div><div class="caption">Amount</div>{amount}</div>
    </div>
    <div class="accept">Acceptance point</div>
  </div>
  <div class="payment">
    <div class="col-left">
      <h2>Payment part</h2>
      {svg}
      <div class="amount-row">
        <div><div class="caption">Currency</div>{currency}</div>
        <div><div class="caption">Amount</div>{amount}</div>
      </div>
    </div>
    <div class="col-right">
      <div class="block"><div class="caption">Account / Payable to</div>
        {esc(account)}<br>{creditor}</div>
      {ref_block}
      <div class="block"><div class="caption">Additional information</div>
        {esc(invoice["number"] or "")}</div>
      <div class="block"><div class="caption">Payable by</div>{debtor}</div>
    </div>
  </div>
</div>"""


def _print_shell(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{esc(title)}</title><style>{PRINT}</style></head>"
        f"<body>{body}</body></html>"
    )
