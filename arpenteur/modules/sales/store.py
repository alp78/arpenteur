"""Reading and writing the money.

Every statement touching `commercial` lives here. This module may reach into
`crm` and `core` by id and nothing there may reach back, which is what makes
the sales half removable: dropping this schema leaves the survey, the clients
and their timelines exactly as they were.

The arithmetic is in `money.py` and deliberately not here, so the rates, the
rounding and the QR-bill can be checked against published figures without a
database in the way.

Three rules the functions below enforce rather than document.

**A sent quote is never edited.** `add_version` is the only way to change one,
and a version that has been sent refuses further lines. What a client is
holding in their inbox must go on saying what it said.

**Totals are stored, not recomputed.** A quote is a document that existed at a
moment. Recomputing it later from lines, or from a rate card that has since
been superseded, would make the archive disagree with the paper.

**A number is allocated at issue and never reused.** From a Postgres sequence
rather than by counting rows, because counting hands the same number to two
transactions and a rollback leaves a hole nobody can explain to an auditor.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Any, Sequence

from ...kernel.db import Connection, Row, now
from ..scout.store import record_state_change
from . import money

QUOTE_STATES = ("draft", "sent", "accepted", "declined", "expired", "withdrawn")
INVOICE_STATES = ("draft", "issued", "paid", "part_paid", "overdue", "credited",
                  "cancelled")
UNITS = ("fixed", "day", "month", "hour")


# --- the catalogue --------------------------------------------------------------


def save_service(conn: Connection, row: dict[str, Any]) -> int:
    """Add or update one offering, keyed by its code."""
    if row.get("unit", "fixed") not in UNITS:
        raise ValueError(f"unknown unit {row.get('unit')!r}")
    found = conn.execute(
        "SELECT id FROM commercial.service WHERE code = %s", (row["code"],)
    ).fetchone()
    if found:
        conn.execute(
            "UPDATE commercial.service SET name = %s, what = %s,"
            " prerequisite = %s, unit = %s, active = %s, sort_order = %s,"
            " updated_at = %s WHERE id = %s",
            (row["name"], row.get("what"), row.get("prerequisite"),
             row.get("unit", "fixed"), row.get("active", True),
             row.get("sort_order", 100), now(), found["id"]),
        )
        return int(found["id"])
    made = conn.execute(
        "INSERT INTO commercial.service (code, name, what, prerequisite, unit,"
        " active, sort_order, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (row["code"], row["name"], row.get("what"), row.get("prerequisite"),
         row.get("unit", "fixed"), row.get("active", True),
         row.get("sort_order", 100), now()),
    ).fetchone()
    return int(made["id"])


def services(conn: Connection, *, active_only: bool = True) -> list[Row]:
    where = "WHERE active" if active_only else ""
    return conn.execute(
        f"SELECT * FROM commercial.service {where} ORDER BY sort_order, code"
    ).fetchall()


def service(conn: Connection, service_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM commercial.service WHERE id = %s", (service_id,)
    ).fetchone()


def service_by_code(conn: Connection, code: str) -> Row | None:
    return conn.execute(
        "SELECT * FROM commercial.service WHERE code = %s", (code,)
    ).fetchone()


def save_rate_card(conn: Connection, row: dict[str, Any]) -> int:
    """Open a dated set of prices, closing the one it supersedes.

    Closing rather than deleting: a quote sent in March was computed against
    March's card, and the only way to explain that document a year later is to
    still have the card it was built from.
    """
    valid_from = row["valid_from"]
    # `<=` rather than `<`, so a card opened earlier the same day is closed
    # too. Correcting a price and reloading within the day is the ordinary
    # way this is used, and the strict comparison left both cards open with
    # nothing to choose between them.
    conn.execute(
        "UPDATE commercial.rate_card SET valid_to = %s"
        " WHERE valid_to IS NULL AND valid_from <= %s",
        (valid_from, valid_from),
    )
    made = conn.execute(
        "INSERT INTO commercial.rate_card (name, valid_from, valid_to,"
        " currency, note, created_at) VALUES (%s, %s, %s, %s, %s, %s)"
        " RETURNING id",
        (row["name"], valid_from, row.get("valid_to"),
         row.get("currency", "CHF"), row.get("note"), now()),
    ).fetchone()
    return int(made["id"])


def save_rate_line(
    conn: Connection, rate_card_id: int, service_id: int, *,
    low: Any = None, high: Any = None, basis: str | None = None,
) -> int:
    made = conn.execute(
        "INSERT INTO commercial.rate_card_line (rate_card_id, service_id, low,"
        " high, basis) VALUES (%s, %s, %s, %s, %s)"
        " ON CONFLICT (rate_card_id, service_id) DO UPDATE SET"
        " low = EXCLUDED.low, high = EXCLUDED.high, basis = EXCLUDED.basis"
        " RETURNING id",
        (rate_card_id, service_id,
         money.money(low) if low is not None else None,
         money.money(high) if high is not None else None, basis),
    ).fetchone()
    return int(made["id"])


def current_card(conn: Connection, on: date | None = None) -> Row | None:
    """The card in force on a date, which is today unless asked otherwise.

    The tie-break on id is not decoration. Two cards can share a `valid_from`,
    and without it Postgres is free to return either, so a corrected price
    would sometimes be quoted and sometimes not, which is the worst shape a
    pricing bug can take.
    """
    when = on or date.today()
    return conn.execute(
        "SELECT * FROM commercial.rate_card"
        " WHERE valid_from <= %s AND (valid_to IS NULL OR valid_to > %s)"
        " ORDER BY valid_from DESC, id DESC LIMIT 1",
        (when, when),
    ).fetchone()


def card_lines(conn: Connection, rate_card_id: int) -> list[Row]:
    return conn.execute(
        "SELECT l.*, s.code, s.name, s.what, s.prerequisite, s.unit"
        "  FROM commercial.rate_card_line l"
        "  JOIN commercial.service s ON s.id = l.service_id"
        " WHERE l.rate_card_id = %s ORDER BY s.sort_order, s.code",
        (rate_card_id,),
    ).fetchall()


def rate_for(conn: Connection, rate_card_id: int, service_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM commercial.rate_card_line"
        " WHERE rate_card_id = %s AND service_id = %s",
        (rate_card_id, service_id),
    ).fetchone()


def catalogue_text(conn: Connection, on: date | None = None) -> str:
    """The card as prose, for the stage that suggests an approach.

    The advise stage is forbidden to price anything the card does not cover,
    and it reads text rather than rows because that constraint is expressed in
    words: "quoted only after a diagnostic" is a rule a model can respect and a
    numeric column cannot carry. Generating the prose from the rows keeps one
    source of truth while giving the prompt the shape it needs.

    An empty catalogue returns an empty string, and the advice then comes back
    with the work identified and no prices on it, which is honest.
    """
    card = current_card(conn, on)
    if card is None:
        return ""
    out = []
    for line in card_lines(conn, card["id"]):
        price = _price_phrase(line, card["currency"])
        parts = [f"{line['name']}{price}."]
        if line["what"]:
            parts.append(str(line["what"]).strip())
        if line["prerequisite"]:
            parts.append(f"Only after: {str(line['prerequisite']).strip()}")
        if line["basis"]:
            parts.append(f"What moves the price: {str(line['basis']).strip()}")
        out.append(" ".join(parts))
    if card["note"]:
        out.append(str(card["note"]).strip())
    return "\n".join(out)


def _price_phrase(line: Row, currency: str) -> str:
    unit = {"fixed": "", "day": " per day", "month": " per month",
            "hour": " per hour"}.get(line["unit"], "")
    if line["low"] is None and line["high"] is None:
        return " (price on application)"
    if line["high"] is None or line["low"] == line["high"]:
        return f": {currency} {money.format_chf(line['low'])}{unit}"
    return (f": {currency} {money.format_chf(line['low'])} to "
            f"{money.format_chf(line['high'])}{unit}")


# --- quotes ---------------------------------------------------------------------


def next_quote_code(conn: Connection, client_id: int) -> str:
    """BOIS-GENTIL-Q03. Quoted on the telephone, so it is readable."""
    client = conn.execute(
        "SELECT code FROM crm.client WHERE id = %s", (client_id,)
    ).fetchone()
    if client is None:
        raise ValueError(f"no client {client_id}")
    taken = conn.execute(
        "SELECT COUNT(*) AS n FROM commercial.quote WHERE client_id = %s",
        (client_id,),
    ).fetchone()
    return f"{client['code']}-Q{int(taken['n']) + 1:02d}"


def create_quote(
    conn: Connection, client_id: int, *, project_id: int | None = None,
    code: str = "", currency: str = "CHF",
) -> int:
    made = conn.execute(
        "INSERT INTO commercial.quote (client_id, project_id, code, status,"
        " currency, created_at) VALUES (%s, %s, %s, 'draft', %s, %s)"
        " RETURNING id",
        (client_id, project_id, code or next_quote_code(conn, client_id),
         currency, now()),
    ).fetchone()
    quote_id = int(made["id"])
    record_state_change(
        conn, entity="quote", entity_id=quote_id, to_state="draft",
        company_id=_company_of_client(conn, client_id),
    )
    return quote_id


def add_version(
    conn: Connection, quote_id: int, *, rate_card_id: int | None = None,
    intro: str = "", terms: str = "", valid_until: date | None = None,
) -> int:
    """Open a new version of a quote, carrying nothing over by itself.

    A revision is a document in its own right. Copying the previous version's
    lines automatically is tempting and wrong: the reason a quote is being
    revised is that something in it changed, and a silent copy makes the
    unchanged lines look reviewed when nobody looked at them.
    """
    highest = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM commercial.quote_version"
        " WHERE quote_id = %s",
        (quote_id,),
    ).fetchone()
    if rate_card_id is None:
        card = current_card(conn)
        rate_card_id = int(card["id"]) if card else None
    made = conn.execute(
        "INSERT INTO commercial.quote_version (quote_id, version, rate_card_id,"
        " intro, terms, valid_until, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (quote_id, int(highest["v"]) + 1, rate_card_id, intro or None,
         terms or None, valid_until, now()),
    ).fetchone()
    return int(made["id"])


def copy_lines(conn: Connection, from_version_id: int, to_version_id: int) -> int:
    """Carry a previous version's lines across, when a person asks for it.

    Explicit rather than automatic, for the reason in `add_version`: a revision
    that quietly reproduces what was there hides which parts were reconsidered.
    """
    rows = lines_for(conn, from_version_id)
    for row in rows:
        add_line(conn, to_version_id, {
            "service_id": row["service_id"], "label": row["label"],
            "detail": row["detail"], "unit": row["unit"],
            "quantity": row["quantity"], "unit_price": row["unit_price"],
            "basis": row["basis"], "position": row["position"],
        })
    return len(rows)


def version(conn: Connection, version_id: int) -> Row | None:
    return conn.execute(
        "SELECT v.*, q.code, q.client_id, q.project_id, q.status, q.currency"
        "  FROM commercial.quote_version v"
        "  JOIN commercial.quote q ON q.id = v.quote_id"
        " WHERE v.id = %s",
        (version_id,),
    ).fetchone()


def versions_for(conn: Connection, quote_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM commercial.quote_version WHERE quote_id = %s"
        " ORDER BY version DESC",
        (quote_id,),
    ).fetchall()


def latest_version(conn: Connection, quote_id: int) -> Row | None:
    rows = versions_for(conn, quote_id)
    return rows[0] if rows else None


def add_line(conn: Connection, version_id: int, row: dict[str, Any]) -> int:
    """One line on a quote. Refuses once the version has been sent.

    The refusal is the point. A sent version is what a client is holding, and
    an editable one turns a disagreement about what was quoted into an argument
    nobody can settle.
    """
    held = conn.execute(
        "SELECT sent_at FROM commercial.quote_version WHERE id = %s",
        (version_id,),
    ).fetchone()
    if held is None:
        raise ValueError(f"no quote version {version_id}")
    if held["sent_at"] is not None:
        raise ValueError(
            "this version has been sent. Add a new version rather than "
            "changing what the client is holding."
        )
    quantity = Decimal(str(row.get("quantity", 1)))
    unit_price = money.money(row.get("unit_price", 0))
    position = row.get("position")
    if position is None:
        last = conn.execute(
            "SELECT COALESCE(MAX(position), 0) AS p FROM commercial.quote_line"
            " WHERE quote_version_id = %s",
            (version_id,),
        ).fetchone()
        position = int(last["p"]) + 1
    made = conn.execute(
        "INSERT INTO commercial.quote_line (quote_version_id, service_id,"
        " position, label, detail, unit, quantity, unit_price, amount, basis)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (version_id, row.get("service_id"), position, row["label"],
         row.get("detail"), row.get("unit", "fixed"), quantity, unit_price,
         money.line_amount(quantity, unit_price), row.get("basis")),
    ).fetchone()
    return int(made["id"])


def line_from_service(
    conn: Connection, version_id: int, service_id: int, *,
    quantity: Any = 1, unit_price: Any = None, at: str = "low",
) -> int:
    """A line built from the catalogue, priced from the card the version cites.

    The price comes from the rate card unless one is given, and where the card
    holds a range the low end is taken by default. Defaulting to the low end
    rather than the middle is deliberate: a number that appears without anyone
    choosing it should be the one that is safe to have quoted.
    """
    row = service(conn, service_id)
    if row is None:
        raise ValueError(f"no service {service_id}")
    header = conn.execute(
        "SELECT rate_card_id FROM commercial.quote_version WHERE id = %s",
        (version_id,),
    ).fetchone()
    basis = None
    if unit_price is None and header and header["rate_card_id"]:
        rate = rate_for(conn, int(header["rate_card_id"]), service_id)
        if rate is not None:
            unit_price = rate["high"] if at == "high" else rate["low"]
            basis = rate["basis"]
    if unit_price is None:
        raise ValueError(
            f"{row['code']} has no price on the rate card in force. Put one on "
            "the card, or give the price explicitly; it is not guessed at."
        )
    return add_line(conn, version_id, {
        "service_id": service_id, "label": row["name"], "detail": row["what"],
        "unit": row["unit"], "quantity": quantity, "unit_price": unit_price,
        "basis": basis,
    })


def lines_for(conn: Connection, version_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM commercial.quote_line WHERE quote_version_id = %s"
        " ORDER BY position, id",
        (version_id,),
    ).fetchall()


def reorder_lines(conn: Connection, version_id: int, ids: Sequence[Any]) -> int:
    """Put a version's lines in the order given. Returns how many moved.

    A quote is read top to bottom and the order is part of what it says: the
    survey before the plan before the travel, not whatever order somebody
    happened to add them in at half past six.

    Scoped to the version in the statement rather than checked beforehand, so
    an id belonging to another quote renumbers nothing instead of relying on a
    guard somebody remembers to write. Refusing a sent version is the caller's
    job, because that refusal has to be explained to a person and this cannot
    do that.
    """
    return _reorder(conn, "commercial.quote_line", "quote_version_id",
                    version_id, ids)


def reorder_invoice_lines(conn: Connection, invoice_id: int, ids: Sequence[Any]) -> int:
    return _reorder(conn, "commercial.invoice_line", "invoice_id", invoice_id, ids)


def _reorder(
    conn: Connection, table: str, parent: str, parent_id: int, ids: Sequence[Any],
) -> int:
    """Dense positions from 1, in the order given.

    The table and column names are literals from the two callers above and
    never touch anything a request supplied, which is the only reason they can
    be interpolated. The ids, which do come from a request, are parameters.
    """
    moved = 0
    for index, item in enumerate(ids, 1):
        try:
            row_id = int(item)
        except (TypeError, ValueError):
            continue
        moved += conn.execute(
            f"UPDATE {table} SET position = %s WHERE id = %s AND {parent} = %s",
            (index, row_id, parent_id),
        ).rowcount
    return moved


def delete_line(conn: Connection, line_id: int) -> None:
    conn.execute("DELETE FROM commercial.quote_line WHERE id = %s", (line_id,))


def recompute(
    conn: Connection, version_id: int, *, vat_rate: Decimal | None,
) -> money.Totals:
    """Add the version up and store what it came to.

    Stored rather than derived on read: see the module docstring. A version
    that has been sent is not recomputed at all, because its totals are what
    was printed.
    """
    held = version(conn, version_id)
    if held is None:
        raise ValueError(f"no quote version {version_id}")
    if held["sent_at"] is not None:
        return money.Totals(
            net=held["net_total"], vat_rate=held["vat_rate"],
            vat=held["vat_total"], gross=held["gross_total"],
        )
    lines = lines_for(conn, version_id)
    found = money.totals([line["amount"] for line in lines], vat_rate)
    conn.execute(
        "UPDATE commercial.quote_version SET net_total = %s, vat_rate = %s,"
        " vat_total = %s, gross_total = %s WHERE id = %s",
        (found.net, found.vat_rate, found.vat, found.gross, version_id),
    )
    return found


def mark_sent(
    conn: Connection, version_id: int, *, to: str = "", when: str | None = None,
) -> None:
    """Record that a version went out. Arpenteur does not send it.

    The whole codebase has no send path, by design and by test. This records
    that a person sent it, which is what freezes the version and moves the
    quote's own state.
    """
    held = version(conn, version_id)
    if held is None:
        raise ValueError(f"no quote version {version_id}")
    conn.execute(
        "UPDATE commercial.quote_version SET sent_at = %s, sent_to = %s"
        " WHERE id = %s",
        (when or now(), to or None, version_id),
    )
    set_quote_status(conn, int(held["quote_id"]), "sent")


def set_quote_status(
    conn: Connection, quote_id: int, status: str, *, reason: str = "",
    by: str | None = None,
) -> None:
    if status not in QUOTE_STATES:
        raise ValueError(f"unknown quote state {status!r}")
    before = conn.execute(
        "SELECT status, client_id FROM commercial.quote WHERE id = %s",
        (quote_id,),
    ).fetchone()
    if before is None:
        raise ValueError(f"no quote {quote_id}")
    conn.execute(
        "UPDATE commercial.quote SET status = %s,"
        " accepted_at = CASE WHEN %s = 'accepted' THEN %s ELSE accepted_at END,"
        " lost_reason = COALESCE(%s, lost_reason) WHERE id = %s",
        (status, status, now(), reason or None, quote_id),
    )
    record_state_change(
        conn, entity="quote", entity_id=quote_id,
        company_id=_company_of_client(conn, int(before["client_id"])),
        from_state=before["status"], to_state=status, by=by,
        reason=reason or None,
    )


def quote(conn: Connection, quote_id: int) -> Row | None:
    return conn.execute(
        "SELECT q.*, c.code AS client_code, co.name AS company_name"
        "  FROM commercial.quote q"
        "  JOIN crm.client c ON c.id = q.client_id"
        "  JOIN core.company co ON co.id = c.company_id"
        " WHERE q.id = %s",
        (quote_id,),
    ).fetchone()


def quotes_for(conn: Connection, client_id: int) -> list[Row]:
    return conn.execute(
        "SELECT q.*, (SELECT MAX(version) FROM commercial.quote_version v"
        "              WHERE v.quote_id = q.id) AS versions,"
        "       (SELECT v.gross_total FROM commercial.quote_version v"
        "         WHERE v.quote_id = q.id ORDER BY v.version DESC LIMIT 1) AS total"
        "  FROM commercial.quote q WHERE q.client_id = %s"
        " ORDER BY q.created_at DESC",
        (client_id,),
    ).fetchall()


def all_quotes(
    conn: Connection, *, status: str = "", q: str = "", limit: int = 200,
) -> list[Row]:
    """Every quote, newest first, whoever it is for.

    `quotes_for` answers "what has this client been sent", which is the
    question the client page asks. This answers the other one, the one nobody
    could ask at all: what is out, and who has not come back. A quote was
    reachable only from the client it belonged to, so finding the unanswered
    ones meant opening every client in turn and remembering.
    """
    where = ["TRUE"]
    args: list[Any] = []
    if status:
        where.append("q.status = %s")
        args.append(status)
    if q:
        where.append("(q.code ILIKE %s OR co.name ILIKE %s OR c.code ILIKE %s)")
        args += [f"%{q}%"] * 3
    args.append(limit)
    return conn.execute(
        "SELECT q.*, c.code AS client_code, co.name AS company_name,"
        "       (SELECT MAX(version) FROM commercial.quote_version v"
        "         WHERE v.quote_id = q.id) AS versions,"
        "       (SELECT v.gross_total FROM commercial.quote_version v"
        "         WHERE v.quote_id = q.id ORDER BY v.version DESC LIMIT 1) AS total,"
        "       (SELECT v.sent_at FROM commercial.quote_version v"
        "         WHERE v.quote_id = q.id AND v.sent_at IS NOT NULL"
        "         ORDER BY v.version DESC LIMIT 1) AS sent_at,"
        "       (SELECT v.valid_until FROM commercial.quote_version v"
        "         WHERE v.quote_id = q.id ORDER BY v.version DESC LIMIT 1) AS valid_until"
        "  FROM commercial.quote q"
        "  JOIN crm.client c ON c.id = q.client_id"
        "  JOIN core.company co ON co.id = c.company_id"
        f" WHERE {' AND '.join(where)}"
        " ORDER BY q.created_at DESC, q.id DESC LIMIT %s",
        tuple(args),
    ).fetchall()


def all_invoices(
    conn: Connection, *, status: str = "", q: str = "", limit: int = 200,
) -> list[Row]:
    """Every invoice, newest first, with what is still owed on it.

    `outstanding` answers the Friday afternoon question and deliberately shows
    only what is unsettled. This is the ledger: drafts, credit notes and
    settled ones included, because "was that ever raised" is asked at least as
    often as "who has not paid".
    """
    where = ["TRUE"]
    args: list[Any] = []
    if status:
        where.append("i.status = %s")
        args.append(status)
    if q:
        where.append(
            "(i.number ILIKE %s OR i.reference ILIKE %s OR co.name ILIKE %s"
            " OR c.code ILIKE %s)"
        )
        args += [f"%{q}%"] * 4
    args.append(limit)
    return conn.execute(
        "SELECT i.*, c.code AS client_code, co.name AS company_name,"
        "       (i.gross_total - i.paid_total) AS owed,"
        "       (CURRENT_DATE - i.due_on) AS days_late"
        "  FROM commercial.invoice i"
        "  JOIN crm.client c ON c.id = i.client_id"
        "  JOIN core.company co ON co.id = c.company_id"
        f" WHERE {' AND '.join(where)}"
        " ORDER BY COALESCE(i.issued_on, i.created_at::date) DESC, i.id DESC"
        " LIMIT %s",
        tuple(args),
    ).fetchall()


def register(conn: Connection, *, year: str = "") -> list[Row]:
    """The series in rank order, oldest first, drafts excluded.

    A register answers one question that the ledger does not: whether anything
    is missing. So it is ordered by the rank and never by the date, it leaves
    out drafts because an unnumbered document is not in the series, and the
    caller is expected to read the ranks as a run rather than as a set.
    """
    where = ["i.number IS NOT NULL"]
    args: list[Any] = []
    if year:
        where.append("i.number LIKE %s")
        args.append(f"{year}-%")
    return conn.execute(
        "SELECT i.id, i.client_id, i.number, i.reference, i.status, i.issued_on, i.due_on,"
        "       i.paid_on, i.currency, i.net_total, i.vat_total, i.gross_total,"
        "       i.paid_total, (i.gross_total - i.paid_total) AS owed,"
        "       c.code AS client_code, co.name AS company_name,"
        "       p.code AS project_code, p.name AS project_name"
        "  FROM commercial.invoice i"
        "  JOIN crm.client c ON c.id = i.client_id"
        "  JOIN core.company co ON co.id = c.company_id"
        "  LEFT JOIN crm.project p ON p.id = i.project_id"
        f" WHERE {' AND '.join(where)}"
        " ORDER BY i.number, i.id",
        tuple(args),
    ).fetchall()


def register_gaps(rows: Sequence[Row]) -> list[str]:
    """The ranks the series skips, which is the only thing a register is for.

    A burnt number is not an error: `nextval` stands outside the transaction, so
    an issue that rolled back consumed one. It still has to be visible, because
    the alternative is a reader who cannot tell a rollback from a withdrawal.
    """
    seen: dict[str, set[int]] = {}
    for row in rows:
        year, _, rank = str(row["number"]).partition("-")
        if rank.isdigit():
            seen.setdefault(year, set()).add(int(rank))
    missing: list[str] = []
    for year in sorted(seen):
        ranks = seen[year]
        for n in range(min(ranks), max(ranks)):
            if n not in ranks:
                missing.append(f"{year}-{n:04d}")
    return missing


def pipeline_value(conn: Connection) -> list[Row]:
    """What is out, by state, with its money. The one number a quote table owes."""
    return conn.execute(
        "SELECT q.status, COUNT(*) AS n, COALESCE(SUM(v.gross_total), 0) AS total"
        "  FROM commercial.quote q"
        "  LEFT JOIN LATERAL ("
        "    SELECT gross_total FROM commercial.quote_version"
        "     WHERE quote_id = q.id ORDER BY version DESC LIMIT 1) v ON TRUE"
        " GROUP BY q.status ORDER BY q.status"
    ).fetchall()


# --- invoices --------------------------------------------------------------------


def create_invoice(
    conn: Connection, client_id: int, *, project_id: int | None = None,
    quote_id: int | None = None, currency: str = "CHF", note: str = "",
    reference: str = "",
) -> int:
    """A draft invoice. It has no number until it is issued.

    The reference may be set here and the number may not, which is the whole
    difference between them: the reference is how the work is already spoken
    about, and the number is the rank in a series that a draft has not joined.
    """
    made = conn.execute(
        "INSERT INTO commercial.invoice (client_id, project_id, quote_id,"
        " status, currency, note, reference, created_at)"
        " VALUES (%s, %s, %s, 'draft', %s, %s, %s, %s) RETURNING id",
        (client_id, project_id, quote_id, currency, note or None,
         reference.strip() or None, now()),
    ).fetchone()
    return int(made["id"])


def next_invoice_reference(
    conn: Connection, client_id: int, slug: str, *, series: bool = False,
) -> str:
    """A reference for a client's invoice: FBG-EXPERTISE, or FBG-SEANCE-01.

    The client code and the object, folded the way `make_code` folds a company
    name, because the same string is read down a telephone and typed into a
    bank form. No date and no rank in the issuer's series: those say how the
    issuer's year is going, which is not what the recipient is being told.

    `series=True` numbers it from the first one, for work that recurs: a client
    who will receive several sessions needs to see which one they are paying,
    and `FBG-SEANCE` followed by `FBG-SEANCE-2` reads as a correction of the
    first rather than as the second of a set. The count is of this client's
    invoices for this object, which they already know, and says nothing about
    the practice as a whole.

    Without `series`, a suffix on collision rather than a refusal, so an
    unforeseen second invoice for the same object is named rather than blocked.
    The uniqueness that counts is the index, since nothing here holds a lock.
    """
    held = conn.execute(
        "SELECT code FROM crm.client WHERE id = %s", (client_id,)
    ).fetchone()
    if held is None:
        raise ValueError(f"no client {client_id}")
    folded = unicodedata.normalize("NFKD", slug)
    ascii_only = "".join(c for c in folded if not unicodedata.combining(c))
    words = [w for w in re.split(r"[^A-Za-z0-9]+", ascii_only) if w]
    base = f"{held['code']}-{'-'.join(words).upper()}" if words else str(held["code"])
    if not series:
        return _free_reference(conn, base)
    rangs = [
        int(m.group(1))
        for row in conn.execute(
            "SELECT reference FROM commercial.invoice WHERE reference LIKE %s",
            (f"{base}-%",),
        )
        if (m := re.fullmatch(rf"{re.escape(base)}-(\d+)", row["reference"] or ""))
    ]
    return f"{base}-{max(rangs, default=0) + 1:02d}"


def _free_reference(conn: Connection, base: str) -> str:
    """`base`, or the first free suffix of it."""
    taken = {
        row["reference"]
        for row in conn.execute(
            "SELECT reference FROM commercial.invoice WHERE reference IS NOT NULL"
        )
    }
    if base not in taken:
        return base
    for n in range(2, 100):
        candidate = f"{base}-{n}"
        if candidate not in taken:
            return candidate
    raise RuntimeError(f"cannot find a free reference from {base!r}")


def set_invoice_reference(conn: Connection, invoice_id: int, reference: str) -> None:
    """Rename a draft. An issued invoice keeps the reference the client holds."""
    held = conn.execute(
        "SELECT status FROM commercial.invoice WHERE id = %s", (invoice_id,)
    ).fetchone()
    if held is None:
        raise ValueError(f"no invoice {invoice_id}")
    if held["status"] != "draft":
        raise ValueError(
            "this invoice has been issued. Its reference is what the client "
            "quotes and what the payment will carry, so it cannot be renamed."
        )
    conn.execute(
        "UPDATE commercial.invoice SET reference = %s WHERE id = %s",
        (reference.strip() or None, invoice_id),
    )


def add_invoice_line(conn: Connection, invoice_id: int, row: dict[str, Any]) -> int:
    held = conn.execute(
        "SELECT status FROM commercial.invoice WHERE id = %s", (invoice_id,)
    ).fetchone()
    if held is None:
        raise ValueError(f"no invoice {invoice_id}")
    if held["status"] != "draft":
        raise ValueError(
            "this invoice has been issued. A correction is a credit note, not "
            "an edit: the client and the accounts hold the document as sent."
        )
    quantity = Decimal(str(row.get("quantity", 1)))
    unit_price = money.money(row.get("unit_price", 0))
    last = conn.execute(
        "SELECT COALESCE(MAX(position), 0) AS p FROM commercial.invoice_line"
        " WHERE invoice_id = %s",
        (invoice_id,),
    ).fetchone()
    made = conn.execute(
        "INSERT INTO commercial.invoice_line (invoice_id, service_id, position,"
        " label, detail, unit, quantity, unit_price, amount, vat_rate)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (invoice_id, row.get("service_id"),
         row.get("position") or int(last["p"]) + 1, row["label"],
         row.get("detail"), row.get("unit", "fixed"), quantity, unit_price,
         money.line_amount(quantity, unit_price), row.get("vat_rate")),
    ).fetchone()
    return int(made["id"])


def invoice_lines(conn: Connection, invoice_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM commercial.invoice_line WHERE invoice_id = %s"
        " ORDER BY position, id",
        (invoice_id,),
    ).fetchall()


def recompute_invoice(
    conn: Connection, invoice_id: int, *, vat_rate: Decimal | None,
) -> money.Totals:
    lines = invoice_lines(conn, invoice_id)
    found = money.totals([line["amount"] for line in lines], vat_rate)
    conn.execute(
        "UPDATE commercial.invoice SET net_total = %s, vat_rate = %s,"
        " vat_total = %s, gross_total = %s WHERE id = %s",
        (found.net, found.vat_rate, found.vat, found.gross, invoice_id),
    )
    return found


def next_invoice_number(conn: Connection, *, on: date | None = None) -> str:
    """The next number in the series, from a sequence.

    A sequence and not `MAX(number) + 1`: two invoices raised in the same
    second by counting rows take the same number, and the second one fails a
    unique constraint at best or overwrites at worst. A sequence is also
    immune to a deleted draft, which is the case that makes counting look
    fine right up until it does not.

    The year is a prefix rather than a reset. Restarting at 1 each January
    means the number alone no longer identifies the invoice, and every system
    that has done it has ended up with a compound key nobody remembers.
    """
    when = on or date.today()
    seq = conn.execute("SELECT nextval('commercial.invoice_number_seq') AS n").fetchone()
    return f"{when:%Y}-{int(seq['n']):04d}"


def issue_invoice(
    conn: Connection, invoice_id: int, *, issued_on: date | None = None,
    days: int = 30, qr_iban: str = "", customer_ref: str = "",
    with_reference: bool = True, by: str | None = None,
) -> Row:
    """Allocate a number, fix the dates, and compute the payment reference.

    The payment reference is derived from the number and the account: a QR-IBAN
    takes a QR reference, an ordinary IBAN an RF creditor reference.
    `money.qr_payload` refuses the mismatched combinations, and this computes
    the right one so the refusal is never reached in normal use.

    `with_reference=False` records the account and leaves the payment reference
    null, for an invoice whose payment part is produced by the bank rather than
    here. BCV-net emits one with reference type NON, and a derived RF reference
    stored beside it would be a number the register holds and no document
    carries, which is the kind of disagreement a reconciliation is built on.
    """
    held = conn.execute(
        "SELECT * FROM commercial.invoice WHERE id = %s", (invoice_id,)
    ).fetchone()
    if held is None:
        raise ValueError(f"no invoice {invoice_id}")
    if held["status"] != "draft":
        raise ValueError(f"invoice {held['number']} has already been issued")
    if not invoice_lines(conn, invoice_id):
        raise ValueError("an invoice with no lines is not a document")
    if not held["reference"]:
        raise ValueError(
            "an invoice with no reference is not a document: the client has "
            "nothing to quote, and the number is the register's, not theirs."
        )

    when = issued_on or date.today()
    number = next_invoice_number(conn, on=when)
    reference = ""
    if qr_iban and with_reference:
        if not money.iban_is_valid(qr_iban):
            raise ValueError(
                f"{qr_iban} does not pass the IBAN check digits. An invoice "
                "with a wrong IBAN prints correctly and is never paid."
            )
        digits = number.replace("-", "")
        reference = (money.qr_reference(digits, customer=customer_ref)
                     if money.is_qr_iban(qr_iban)
                     else money.creditor_reference(digits))
    conn.execute(
        "UPDATE commercial.invoice SET number = %s, status = 'issued',"
        " issued_on = %s, due_on = %s, qr_iban = %s, qr_reference = %s"
        " WHERE id = %s",
        (number, when, _plus_days(when, days),
         money.clean_iban(qr_iban) or None, reference or None, invoice_id),
    )
    record_state_change(
        conn, entity="invoice", entity_id=invoice_id,
        company_id=_company_of_client(conn, int(held["client_id"])),
        from_state="draft", to_state="issued", by=by,
    )
    return invoice(conn, invoice_id)


def _plus_days(when: date, days: int) -> date:
    from datetime import timedelta

    return when + timedelta(days=days)


def record_payment(
    conn: Connection, invoice_id: int, *, amount: Any, on: date | None = None,
    by: str | None = None,
) -> str:
    """Record what arrived, and let the amount decide the state.

    Part payment is a real and common state, and treating anything short of the
    full amount as unpaid is how a client who has settled 90% of a bill gets a
    reminder for the whole of it.
    """
    held = conn.execute(
        "SELECT * FROM commercial.invoice WHERE id = %s", (invoice_id,)
    ).fetchone()
    if held is None:
        raise ValueError(f"no invoice {invoice_id}")
    paid = money.money(held["paid_total"] or 0) + money.money(amount)
    status = "paid" if paid >= money.money(held["gross_total"]) else "part_paid"
    conn.execute(
        "UPDATE commercial.invoice SET paid_total = %s, status = %s,"
        " paid_on = CASE WHEN %s = 'paid' THEN %s ELSE paid_on END"
        " WHERE id = %s",
        (paid, status, status, on or date.today(), invoice_id),
    )
    record_state_change(
        conn, entity="invoice", entity_id=invoice_id,
        company_id=_company_of_client(conn, int(held["client_id"])),
        from_state=held["status"], to_state=status, by=by,
    )
    return status


def credit_invoice(
    conn: Connection, invoice_id: int, *, reason: str = "",
    by: str | None = None,
) -> int:
    """Reverse an issued invoice with a credit note.

    An issued invoice is never edited and never deleted. The tax authority's
    view is that a document that left the building exists; what corrects it is
    another document that says so and carries its own number.
    """
    held = conn.execute(
        "SELECT * FROM commercial.invoice WHERE id = %s", (invoice_id,)
    ).fetchone()
    if held is None:
        raise ValueError(f"no invoice {invoice_id}")
    if held["status"] == "draft":
        raise ValueError("a draft is deleted, not credited")
    note_id = create_invoice(
        conn, int(held["client_id"]), project_id=held["project_id"],
        quote_id=held["quote_id"], currency=held["currency"],
        note=reason or f"Credit note for {held['number']}",
        # Named after what it reverses, because that is the only thing the
        # client has to match it against. Without one it could not be issued.
        reference=_free_reference(conn, f"{held['reference']}-CREDIT"),
    )
    for line in invoice_lines(conn, invoice_id):
        add_invoice_line(conn, note_id, {
            "service_id": line["service_id"], "label": line["label"],
            "detail": line["detail"], "unit": line["unit"],
            "quantity": -Decimal(str(line["quantity"])),
            "unit_price": line["unit_price"], "vat_rate": line["vat_rate"],
        })
    recompute_invoice(conn, note_id, vat_rate=held["vat_rate"])
    conn.execute(
        "UPDATE commercial.invoice SET credits_id = %s WHERE id = %s",
        (invoice_id, note_id),
    )
    conn.execute(
        "UPDATE commercial.invoice SET status = 'credited' WHERE id = %s",
        (invoice_id,),
    )
    record_state_change(
        conn, entity="invoice", entity_id=invoice_id,
        company_id=_company_of_client(conn, int(held["client_id"])),
        from_state=held["status"], to_state="credited", by=by,
        reason=reason or None,
    )
    return note_id


def invoice(conn: Connection, invoice_id: int) -> Row | None:
    return conn.execute(
        "SELECT i.*, c.code AS client_code, co.name AS company_name,"
        "       co.address_json"
        "  FROM commercial.invoice i"
        "  JOIN crm.client c ON c.id = i.client_id"
        "  JOIN core.company co ON co.id = c.company_id"
        " WHERE i.id = %s",
        (invoice_id,),
    ).fetchone()


def invoices_for(conn: Connection, client_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM commercial.invoice WHERE client_id = %s"
        " ORDER BY COALESCE(issued_on, created_at::date) DESC, id DESC",
        (client_id,),
    ).fetchall()


def outstanding(conn: Connection, *, on: date | None = None) -> list[Row]:
    """Issued, not settled, oldest first. The list a Friday afternoon is for."""
    when = on or date.today()
    return conn.execute(
        "SELECT i.*, c.code AS client_code, co.name AS company_name,"
        "       (%s - i.due_on) AS days_late"
        "  FROM commercial.invoice i"
        "  JOIN crm.client c ON c.id = i.client_id"
        "  JOIN core.company co ON co.id = c.company_id"
        " WHERE i.status IN ('issued', 'part_paid', 'overdue')"
        " ORDER BY i.due_on NULLS LAST, i.id",
        (when,),
    ).fetchall()


# --- time ------------------------------------------------------------------------


def log_time(conn: Connection, client_id: int, row: dict[str, Any]) -> int:
    made = conn.execute(
        "INSERT INTO commercial.time_entry (client_id, project_id, service_id,"
        " occurred_on, minutes, note, billable, author, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (client_id, row.get("project_id"), row.get("service_id"),
         row["occurred_on"], int(row["minutes"]), row.get("note"),
         row.get("billable", True), row.get("author"), now()),
    ).fetchone()
    return int(made["id"])


def time_for(
    conn: Connection, client_id: int, *, project_id: int | None = None,
    unbilled_only: bool = False,
) -> list[Row]:
    clauses = ["client_id = %s"]
    params: list[Any] = [client_id]
    if project_id is not None:
        clauses.append("project_id = %s")
        params.append(project_id)
    if unbilled_only:
        clauses.append("invoice_line_id IS NULL AND billable")
    return conn.execute(
        f"SELECT * FROM commercial.time_entry WHERE {' AND '.join(clauses)}"
        " ORDER BY occurred_on DESC, id DESC",
        tuple(params),
    ).fetchall()


def time_summary(conn: Connection, client_id: int) -> Row | None:
    """Hours logged, hours billable, hours not yet billed.

    The three are different numbers and a system that reports one of them
    cannot answer whether a fixed fee was worth taking.
    """
    return conn.execute(
        "SELECT COALESCE(SUM(minutes), 0) AS minutes,"
        "       COALESCE(SUM(minutes) FILTER (WHERE billable), 0) AS billable,"
        "       COALESCE(SUM(minutes) FILTER (WHERE billable"
        "            AND invoice_line_id IS NULL), 0) AS unbilled"
        "  FROM commercial.time_entry WHERE client_id = %s",
        (client_id,),
    ).fetchone()


def bill_time(
    conn: Connection, invoice_id: int, entry_ids: list[int], *,
    label: str, unit_price: Any, service_id: int | None = None,
) -> int:
    """Turn logged minutes into one invoice line, and mark them billed.

    The entries carry the line id afterwards, which is what stops the same
    afternoon being billed twice. Already-billed entries are refused rather
    than skipped: silently dropping them would produce an invoice quietly
    short of what the caller asked for.
    """
    if not entry_ids:
        raise ValueError("no time entries given")
    rows = conn.execute(
        "SELECT * FROM commercial.time_entry WHERE id = ANY(%s)", (entry_ids,)
    ).fetchall()
    if len(rows) != len(set(entry_ids)):
        raise ValueError("some of those time entries do not exist")
    already = [r["id"] for r in rows if r["invoice_line_id"] is not None]
    if already:
        raise ValueError(f"time entries {already} are already on an invoice")
    minutes = sum(int(r["minutes"]) for r in rows)
    hours = (Decimal(minutes) / Decimal(60)).quantize(Decimal("0.01"))
    line_id = add_invoice_line(conn, invoice_id, {
        "service_id": service_id, "label": label, "unit": "hour",
        "quantity": hours, "unit_price": unit_price,
        "detail": f"{len(rows)} entries, {minutes} minutes",
    })
    conn.execute(
        "UPDATE commercial.time_entry SET invoice_line_id = %s WHERE id = ANY(%s)",
        (line_id, entry_ids),
    )
    return line_id


def _company_of_client(conn: Connection, client_id: int) -> int | None:
    row = conn.execute(
        "SELECT company_id FROM crm.client WHERE id = %s", (client_id,)
    ).fetchone()
    return int(row["company_id"]) if row else None


def load_catalogue(
    conn: Connection, entries: list[Any], *, note: str = "",
    valid_from: date | None = None, name: str = "",
) -> tuple[int, int]:
    """Put the configured catalogue into the database as a new rate card.

    Returns the card id and how many lines it carries. A new card each time
    rather than an edit of the current one: the prices in the committed file
    changed, which is exactly the event the versioning exists for, and a quote
    sent last month must go on being explainable by the card it cites.

    Loading the same file twice on the same day is idempotent in the way that
    matters. It opens a card dated today, which supersedes the one opened
    earlier today, and the superseded card keeps whatever quotes cited it.
    """
    if not entries:
        return 0, 0
    when = valid_from or date.today()
    card_id = save_rate_card(conn, {
        "name": name or f"Rate card from {when:%d.%m.%Y}",
        "valid_from": when,
        "note": note or None,
    })
    for entry in entries:
        service_id = save_service(conn, {
            "code": entry.code, "name": entry.name, "unit": entry.unit,
            "what": entry.what or None, "prerequisite": entry.prerequisite or None,
            "sort_order": entry.sort_order,
        })
        save_rate_line(
            conn, card_id, service_id, low=entry.low, high=entry.high,
            basis=entry.basis or None,
        )
    return card_id, len(entries)
