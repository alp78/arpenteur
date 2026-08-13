"""Reports: the whole thing read back, in one page.

Rendered generically from whatever columns a view returns rather than with a
hand-built layout per report. That is a deliberate limit: these are tables of
numbers to be read, exported or queried further, and a page that dressed each
one differently would be a page nobody could add a report to without also
writing a renderer.

Two things it does say in words rather than leaving to the reader.

**Which half of the page is stale.** The materialised views are refreshed on
demand, so the numbers in them are as old as the last refresh. A figure with no
date is a figure somebody will quote in a meeting.

**Why a section is missing.** A deployment without the sales module has no
pipeline report, and an absent section that says nothing looks identical to a
broken one.
"""

from __future__ import annotations

from typing import Any

from ..kernel.db import Row
from ..modules.analytics import store as analytics
from .render import esc, page

STYLES = """
.report { margin:0 0 1.8rem; }
.report h2 { margin-bottom:.15rem; }
.report .lede { color:var(--dim); font-size:.84rem; margin:0 0 .5rem; }
.report table { font-variant-numeric:tabular-nums; }
.report td.n, .report th.n { text-align:right; white-space:nowrap; }
.report .empty { color:var(--dim); font-size:.86rem; background:#fff;
                 border:1px solid var(--line); padding:.6rem .9rem; }
.refresh-bar { display:flex; gap:.8rem; align-items:center; flex-wrap:wrap;
               background:#fff; border:1px solid var(--line); padding:.7rem .9rem;
               margin:0 0 1.4rem; }
.refresh-bar form { margin:0; }
.refresh-bar .note { color:var(--dim); font-size:.82rem; max-width:60ch; }
.duck { background:#fff; border:1px solid var(--line); padding:.8rem 1rem;
        margin:1.5rem 0 0; font-size:.86rem; }
.duck pre { background:var(--bg); border:1px solid var(--line); padding:.6rem .8rem;
            overflow:auto; font-size:.8rem; margin:.5rem 0 0; }
"""

# Columns that are numbers and should be set to the right. Matched by name
# rather than by inspecting the value, so a column of nulls still lines up with
# the one above it.
NUMERIC = (
    "companies", "hydrated", "with_address", "profiled", "researched", "scored",
    "reachable", "people", "on_the_board", "runs", "failed", "model_calls",
    "input_tokens", "output_tokens", "searches", "mean_score", "set_by_hand",
    "high", "reached", "mean_days_from_previous", "median_days_from_previous",
    "picked", "mean_propensity", "won", "lost", "mean_score_when_picked",
    "days_still", "messages", "matched", "by_thread", "active_projects",
    "dossier_sections", "documents", "terms", "terms_known", "interactions",
    "quotes", "value", "mean_value", "invoices", "net", "vat", "gross",
    "received", "outstanding", "owed", "days_late", "minutes",
    "billable_minutes", "unbilled_minutes",
)


def _cell(column: str, value: Any) -> str:
    if value is None:
        return '<td class="dim">-</td>'
    text = str(value)
    if len(text) > 19 and text[4] == "-" and "T" in text:
        text = text[:16].replace("T", " ")
    css = ' class="n"' if column in NUMERIC else ""
    return f"<td{css}>{esc(text)}</td>"


def table(rows: list[Row]) -> str:
    if not rows:
        return ""
    columns = list(rows[0].keys())
    head = "".join(
        f'<th{" class=\"n\"" if c in NUMERIC else ""}>{esc(c.replace("_", " "))}</th>'
        for c in columns
    )
    body = "".join(
        "<tr>" + "".join(_cell(c, row[c]) for c in columns) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def reports_page(
    sections: list[tuple[str, str, list[Row]]], *, missing: list[tuple[str, str]],
    fresh: dict[str, Any], territory: str = "", note: str = "",
) -> str:
    body = [
        "<h1>Reports</h1>",
        '<p class="lede">Views over everything else. Nothing on this page is '
        "written to by the application, and dropping the whole schema would "
        "lose no data at all.</p>",
        '<div class="refresh-bar">'
        '<form method="post" action="/reports/refresh">'
        '<button type="submit">Refresh the aggregates</button></form>'
        f'<span class="note">{fresh["views"]} materialised view(s), refreshed '
        "on demand rather than on a timer, so the survey-wide figures are as "
        "old as the last refresh."
        + (" Never populated: " + esc(", ".join(fresh["unpopulated"])) + "."
           if fresh["unpopulated"] else "")
        + "</span></div>",
    ]
    if note:
        body.append(f'<p class="lede">{esc(note)}</p>')

    for name, title, rows in sections:
        body.append(f'<section class="report"><h2>{esc(title)}</h2>')
        body.append(f'<p class="lede"><code>analytics.{esc(name)}</code></p>')
        body.append(
            table(rows) if rows
            else '<p class="empty">Nothing to report yet.</p>'
        )
        body.append("</section>")

    if missing:
        body.append(
            '<section class="report"><h2>Not installed</h2>'
            '<p class="lede">These reports read schemas this deployment does '
            "not have. That is what an absent module should look like.</p><ul>"
            + "".join(
                f"<li><code>analytics.{esc(name)}</code> needs the "
                f"<b>{esc(module)}</b> module</li>"
                for name, module in missing
            )
            + "</ul></section>"
        )

    body.append(_duckdb())
    return page("Reports", "".join(body), territory=territory, css=STYLES)


def _duckdb() -> str:
    """How to ask a question this page does not answer.

    Documented on the page rather than in a file nobody opens. The point of
    keeping analytics in the same database as everything else is that ad-hoc
    work needs no export, and an export is exactly what somebody will build if
    they are not told this.
    """
    return (
        '<div class="duck"><b>Asking something this page does not answer.</b> '
        "The analytics schema is in the same database as the rest, so nothing "
        "has to be exported. DuckDB attaches to it read-only and speaks its "
        "own dialect over the top, which is the quickest way to a question "
        "that only needs asking once:"
        "<pre>duckdb\n"
        "INSTALL postgres; LOAD postgres;\n"
        "ATTACH 'postgresql://arpenteur@127.0.0.1:5433/arpenteur' AS a "
        "(TYPE postgres, READ_ONLY);\n"
        "SELECT * FROM a.analytics.funnel;</pre>"
        '<p class="dim small" style="margin:.5rem 0 0">Read-only is not '
        "decoration: this is the production database, and an exploratory "
        "query that writes is a category of accident worth making impossible "
        "rather than avoiding by hand.</p></div>"
    )
