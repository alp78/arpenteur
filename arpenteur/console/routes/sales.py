"""Quotes and invoices: versioned offers, the register, payments and credit notes.

Routes of the sales module, mounted when it is enabled. Moved out of
`app.py` verbatim: the bodies close over `ctx` where they closed over the
factory's locals, and read `ctx.settings` at request time so a saved
setting reaches the next request.
"""

from __future__ import annotations

from datetime import date




from fastapi import Form, Request
from fastapi.responses import (
    HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse,
)

from ...modules.clients import store as clients_store
from ...modules.sales import money as money_lib
from ...modules.sales import store as sales_store
from ...modules.scout import store as scout_store
from .. import money_page as money_view
from .. import render
from fastapi import APIRouter

from ...kernel import figures
from ...kernel.db import Connection
from ...kernel.modules import Contribution
from .. import home_page as home_view
from ..context import Console


# --- what this module puts on other modules' pages ---------------------------


def _money(conn: Connection, client_id: int, company_id: int) -> str:
    """A client's quotes and invoices, each with the way to start one."""
    return (
        money_view.quotes_panel(sales_store.quotes_for(conn, client_id), client_id)
        + money_view.invoices_panel(sales_store.invoices_for(conn, client_id), client_id)
    )


def _tiles(conn: Connection) -> str:
    money = sales_store.home_counts(conn)
    return (
        home_view.tile("open quote", "open quotes", money["open_quotes"], "/quotes")
        + home_view.tile("unpaid invoice", "unpaid invoices", money["unpaid_invoices"],
                         "/invoices")
    )


def _time(conn: Connection, project_id: int, client_id: int) -> str:
    """The time logged on a project, with the way to add to it and bill it."""
    return money_view.time_panel(
        project_id, sales_store.time_for(conn, client_id, project_id=project_id),
        today=str(date.today()),
    )


CONTRIBUTIONS = (
    Contribution("client.panels", _money, order=60),
    Contribution("project.panels", _time, order=40),
    Contribution("home.tiles", _tiles, order=30),
)


def router(ctx: Console) -> APIRouter:
    router = APIRouter()
    connect = ctx.connect
    at = ctx.at
    shell = ctx.shell
    _back = ctx.back

    def _address_of(conn, company_id: int):
        row = scout_store.company(conn, company_id)
        return (row["address_json"] if row else None) or None

    def _vat_for(conn, when=None):
        """The rate to apply, or None when the issuer is not registered.

        Not being subject to TVA is the ordinary state of a business under the
        CHF 100'000 threshold, and it is a different thing from a rate of zero:
        the document has to say so in words rather than print 0.00 beside a
        rate, which reads as an exemption claim.
        """
        from datetime import date as _date

        if not ctx.settings.issuer.subject_to_vat:
            return None
        return money_lib.vat_rate(when or _date.today())

    @router.post("/client/{client_id}/quotes")
    def start_quote(client_id: int) -> RedirectResponse:
        conn = connect()
        try:
            quote_id = sales_store.create_quote(conn, client_id)
            sales_store.add_version(conn, quote_id)
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/quote/{quote_id}", status_code=303)

    @router.get("/quotes", response_class=HTMLResponse)
    def quotes_ledger(request: Request) -> HTMLResponse:
        """What is out, and who has not come back.

        A quote used to be reachable only from the client it belonged to, so
        this question could be answered by opening every client in turn or by
        reading an aggregate on the Reports page. Neither is a list you can act
        on: a total says there is money out and not whose.
        """
        args = request.query_params
        status, q = args.get("status", ""), args.get("q", "")
        conn = connect()
        try:
            rows = sales_store.all_quotes(conn, status=status, q=q)
            if request.headers.get("HX-Request"):
                return HTMLResponse(
                    '<div id="ledger">'
                    + money_view._quote_table([money_view.quote_row(r) for r in rows])
                    + "</div>"
                )
            return HTMLResponse(money_view.quotes_page(
                rows, sales_store.pipeline_value(conn),
                status=status, q=q, chrome=at("/quotes"),
            ))
        finally:
            conn.close()

    @router.get("/invoices", response_class=HTMLResponse)
    def invoices_ledger(request: Request) -> HTMLResponse:
        args = request.query_params
        status, q = args.get("status", ""), args.get("q", "")
        conn = connect()
        try:
            rows = sales_store.all_invoices(conn, status=status, q=q)
            if request.headers.get("HX-Request"):
                return HTMLResponse(
                    '<div id="ledger">'
                    + money_view._invoice_table(
                        [money_view.invoice_row(r) for r in rows])
                    + "</div>"
                )
            return HTMLResponse(money_view.invoices_page(
                rows, status=status, q=q, chrome=at("/invoices"),
            ))
        finally:
            conn.close()

    # Declared before /invoice/{invoice_id} is reached and before the .csv
    # sibling, because routes match in order and "register.csv" is not an int.
    @router.get("/invoices/register.csv", response_class=PlainTextResponse)
    def invoice_register_csv(request: Request) -> PlainTextResponse:
        conn = connect()
        try:
            rows = sales_store.register(
                conn, year=request.query_params.get("year", "")
            )
            return PlainTextResponse(
                money_view.register_csv(rows), media_type="text/csv",
                headers={"Content-Disposition":
                         'attachment; filename="register.csv"'},
            )
        finally:
            conn.close()

    @router.get("/invoices/register", response_class=HTMLResponse)
    def invoice_register(request: Request) -> HTMLResponse:
        year = request.query_params.get("year", "")
        conn = connect()
        try:
            rows = sales_store.register(conn, year=year)
            return HTMLResponse(money_view.register_page(
                rows, sales_store.register_gaps(rows), year=year,
                years=sales_store.register_years(conn), chrome=at("/invoices"),
            ))
        finally:
            conn.close()

    @router.get("/quote/{quote_id}", response_class=HTMLResponse)
    def one_quote(quote_id: int, request: Request) -> HTMLResponse:
        conn = connect()
        try:
            quote = sales_store.quote(conn, quote_id)
            if quote is None:
                return shell(render.not_found(), status=404)
            versions = sales_store.versions_for(conn, quote_id)
            if not versions:
                return shell(render.not_found("version of this quote"), status=404)
            wanted = request.query_params.get("v", "")
            shown = next(
                (v for v in versions if str(v["version"]) == wanted), versions[0]
            )
            full = sales_store.version(conn, int(shown["id"]))
            return HTMLResponse(money_view.quote_page(
                quote, versions, full,
                sales_store.lines_for(conn, int(shown["id"])),
                sales_store.services(conn),
                chrome=at("/quotes"),
            ))
        finally:
            conn.close()

    @router.post("/quote/{quote_id}/versions")
    def new_version(quote_id: int) -> RedirectResponse:
        conn = connect()
        try:
            previous = sales_store.latest_version(conn, quote_id)
            version_id = sales_store.add_version(conn, quote_id)
            if previous is not None:
                sales_store.copy_lines(conn, int(previous["id"]), version_id)
                sales_store.recompute(conn, version_id, vat_rate=_vat_for(conn))
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/quote/{quote_id}", status_code=303)

    @router.post("/quote-version/{version_id}/lines")
    def add_quote_line(
        version_id: int, service_id: str = Form(""), label: str = Form(""),
        quantity: str = Form("1"), unit_price: str = Form(""), at: str = Form("low"),
    ) -> RedirectResponse:
        conn = connect()
        try:
            held = sales_store.version(conn, version_id)
            if held is None:
                return RedirectResponse("/clients", status_code=303)
            try:
                if service_id:
                    sales_store.line_from_service(
                        conn, version_id, int(service_id), quantity=quantity or 1,
                        unit_price=unit_price or None, at=at,
                    )
                elif label.strip():
                    sales_store.add_line(conn, version_id, {
                        "label": label.strip(), "quantity": quantity or 1,
                        "unit_price": unit_price or 0,
                    })
                else:
                    raise ValueError("A line needs a catalogue entry or a label.")
                sales_store.recompute(conn, version_id, vat_rate=_vat_for(conn))
                conn.commit()
            except ValueError as refusal:
                # A price the card does not cover, a sent version, an amount
                # that is not one. Refusals the store makes on purpose, shown
                # on the page rather than swallowed.
                conn.rollback()
                return _back(f"/quote/{held['quote_id']}", error=str(refusal))
            return RedirectResponse(f"/quote/{held['quote_id']}", status_code=303)
        finally:
            conn.close()

    @router.post("/quote-line/{line_id}/delete")
    def drop_quote_line(line_id: int) -> RedirectResponse:
        conn = connect()
        try:
            try:
                held = sales_store.delete_line(conn, line_id)
            except ValueError as refusal:
                conn.rollback()
                owner = conn.execute(
                    "SELECT v.quote_id FROM commercial.quote_line l"
                    " JOIN commercial.quote_version v ON v.id = l.quote_version_id"
                    " WHERE l.id = %s", (line_id,)
                ).fetchone()
                if owner is None:
                    return RedirectResponse("/quotes", status_code=303)
                return _back(f"/quote/{owner['quote_id']}", error=str(refusal))
            sales_store.recompute(conn, int(held["version_id"]), vat_rate=_vat_for(conn))
            conn.commit()
            return RedirectResponse(f"/quote/{held['quote_id']}", status_code=303)
        finally:
            conn.close()

    @router.post("/quote-version/{version_id}/order")
    def order_quote_lines(
        version_id: int, order: list[str] = Form(default=[]),
    ) -> JSONResponse:
        """The lines in the order somebody put them in.

        Answers with a status rather than a page, because all three ways of
        moving a line have already moved it on screen: the list waits for
        nothing, and a page swapped in behind a drag would fight the drag.

        A sent version is refused here and not only in the interface. The
        buttons are absent and the rows are not draggable, but a refusal that
        exists only in the markup is a refusal anybody can post around.
        """
        conn = connect()
        try:
            held = sales_store.version(conn, version_id)
            if held is None:
                return JSONResponse({"error": "no such version"}, status_code=404)
            if held["sent_at"]:
                return JSONResponse(
                    {"error": "This version was sent. What the client is "
                              "holding has to go on saying what it said."},
                    status_code=409,
                )
            sales_store.reorder_lines(conn, version_id, order)
            conn.commit()
            return JSONResponse({"ok": True, "lines": len(order)})
        finally:
            conn.close()

    @router.post("/invoice/{invoice_id}/order")
    def order_invoice_lines(
        invoice_id: int, order: list[str] = Form(default=[]),
    ) -> JSONResponse:
        conn = connect()
        try:
            held = sales_store.invoice(conn, invoice_id)
            if held is None:
                return JSONResponse({"error": "no such invoice"}, status_code=404)
            if held["status"] != "draft":
                return JSONResponse(
                    {"error": "An issued invoice is never edited. A correction "
                              "is a credit note."},
                    status_code=409,
                )
            sales_store.reorder_invoice_lines(conn, invoice_id, order)
            conn.commit()
            return JSONResponse({"ok": True, "lines": len(order)})
        finally:
            conn.close()

    @router.post("/quote-version/{version_id}/sent")
    def mark_quote_sent(version_id: int, to: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            held = sales_store.version(conn, version_id)
            if held is None:
                return RedirectResponse("/clients", status_code=303)
            try:
                sales_store.recompute(conn, version_id, vat_rate=_vat_for(conn))
                sales_store.mark_sent(conn, version_id, to=to)
                conn.commit()
            except ValueError as refusal:
                conn.rollback()
                return _back(f"/quote/{held['quote_id']}", error=str(refusal))
            return _back(f"/quote/{held['quote_id']}",
                         note=f"Version {held['version']} is marked as sent and frozen.")
        finally:
            conn.close()

    @router.post("/quote/{quote_id}/status")
    def set_quote_state(quote_id: int, status: str = Form(...),
                        reason: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            sales_store.set_quote_status(conn, quote_id, status, reason=reason,
                                         by="console")
            conn.commit()
        except ValueError as refusal:
            conn.rollback()
            return _back(f"/quote/{quote_id}", error=str(refusal))
        finally:
            conn.close()
        return RedirectResponse(f"/quote/{quote_id}", status_code=303)

    @router.get("/quote/{quote_id}/print", response_class=HTMLResponse)
    def print_quote(quote_id: int, request: Request) -> HTMLResponse:
        conn = connect()
        try:
            quote = sales_store.quote(conn, quote_id)
            versions = sales_store.versions_for(conn, quote_id)
            if quote is None or not versions:
                return shell(render.not_found(), status=404)
            wanted = request.query_params.get("v", "")
            shown = next(
                (v for v in versions if str(v["version"]) == wanted), versions[0]
            )
            client = clients_store.client(conn, int(quote["client_id"]))
            if client is None:
                return shell(render.not_found("client of this quote"), status=404)
            return HTMLResponse(money_view.quote_print(
                quote, sales_store.version(conn, int(shown["id"])),
                sales_store.lines_for(conn, int(shown["id"])),
                _address_of(conn, int(client["company_id"])),
                issuer=ctx.settings.issuer,
            ))
        finally:
            conn.close()

    @router.post("/client/{client_id}/invoices")
    def start_invoice(
        client_id: int, what: str = Form(""), series: str = Form(""),
    ) -> RedirectResponse:
        """A draft, named for the work it bills.

        The reference is asked for here rather than at issue because it is how
        the work is already spoken about, and because an invoice that reached
        the issue button without one would stop there.
        """
        conn = connect()
        try:
            invoice_id = sales_store.create_invoice(
                conn, client_id,
                reference=sales_store.next_invoice_reference(
                    conn, client_id, what or "work", series=bool(series)),
            )
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/invoice/{invoice_id}", status_code=303)

    @router.post("/invoice/{invoice_id}/reference")
    def rename_invoice(invoice_id: int, reference: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            try:
                sales_store.set_invoice_reference(conn, invoice_id, reference)
                conn.commit()
            except ValueError as refusal:
                conn.rollback()
                return _back(f"/invoice/{invoice_id}", error=str(refusal))
        finally:
            conn.close()
        return RedirectResponse(f"/invoice/{invoice_id}", status_code=303)

    @router.post("/invoice/{invoice_id}/delete")
    def delete_invoice(invoice_id: int) -> RedirectResponse:
        conn = connect()
        try:
            try:
                client_id = sales_store.delete_invoice(conn, invoice_id, by="console")
                conn.commit()
            except ValueError as refusal:
                conn.rollback()
                return _back(f"/invoice/{invoice_id}", error=str(refusal))
        finally:
            conn.close()
        return _back(f"/client/{client_id}", note="The draft invoice was deleted.")

    @router.post("/quote/{quote_id}/invoice")
    def invoice_from_quote(quote_id: int) -> RedirectResponse:
        """Bill an accepted quote, line for line.

        Copied rather than referenced: an invoice is its own document and must
        go on saying what it said even if the quote is later revised.
        """
        conn = connect()
        try:
            quote = sales_store.quote(conn, quote_id)
            latest = sales_store.latest_version(conn, quote_id)
            if quote is None or latest is None:
                return RedirectResponse("/clients", status_code=303)
            invoice_id = sales_store.create_invoice(
                conn, int(quote["client_id"]), project_id=quote["project_id"],
                quote_id=quote_id,
                # Named after the quote it bills, which is what the client has
                # been reading the whole time.
                reference=sales_store.next_invoice_reference(
                    conn, int(quote["client_id"]), str(quote["code"]).split("-")[-1]),
            )
            for line in sales_store.lines_for(conn, int(latest["id"])):
                sales_store.add_invoice_line(conn, invoice_id, {
                    "service_id": line["service_id"], "label": line["label"],
                    "detail": line["detail"], "unit": line["unit"],
                    "quantity": line["quantity"], "unit_price": line["unit_price"],
                })
            sales_store.recompute_invoice(conn, invoice_id, vat_rate=_vat_for(conn))
            conn.commit()
            return RedirectResponse(f"/invoice/{invoice_id}", status_code=303)
        finally:
            conn.close()

    @router.get("/invoice/{invoice_id}", response_class=HTMLResponse)
    def one_invoice(invoice_id: int, request: Request) -> HTMLResponse:
        conn = connect()
        try:
            invoice = sales_store.invoice(conn, invoice_id)
            if invoice is None:
                return shell(render.not_found(), status=404)
            return HTMLResponse(money_view.invoice_page(
                invoice, sales_store.invoice_lines(conn, invoice_id),
                sales_store.services(conn), issuer=ctx.settings.issuer,
                chrome=at("/invoices"),
                credit_note=sales_store.credit_note_for(conn, invoice_id),
            ))
        finally:
            conn.close()

    @router.post("/invoice/{invoice_id}/lines")
    def add_bill_line(
        invoice_id: int, service_id: str = Form(""), label: str = Form(""),
        quantity: str = Form("1"), unit_price: str = Form("0"),
    ) -> RedirectResponse:
        conn = connect()
        try:
            name = label.strip()
            if service_id and not name:
                found = sales_store.service(conn, int(service_id))
                name = found["name"] if found else ""
            if not name:
                raise ValueError("A line needs a catalogue entry or a label.")
            sales_store.add_invoice_line(conn, invoice_id, {
                "service_id": int(service_id) if service_id else None,
                "label": name, "quantity": quantity or 1,
                "unit_price": unit_price or 0,
            })
            sales_store.recompute_invoice(conn, invoice_id, vat_rate=_vat_for(conn))
            conn.commit()
        except ValueError as refusal:
            conn.rollback()
            return _back(f"/invoice/{invoice_id}", error=str(refusal))
        finally:
            conn.close()
        return RedirectResponse(f"/invoice/{invoice_id}", status_code=303)

    @router.post("/invoice-line/{line_id}/delete")
    def drop_bill_line(line_id: int) -> RedirectResponse:
        conn = connect()
        try:
            try:
                invoice_id = sales_store.delete_invoice_line(conn, line_id)
            except ValueError as refusal:
                conn.rollback()
                owner = conn.execute(
                    "SELECT invoice_id FROM commercial.invoice_line WHERE id = %s",
                    (line_id,)
                ).fetchone()
                if owner is None:
                    return RedirectResponse("/invoices", status_code=303)
                return _back(f"/invoice/{owner['invoice_id']}", error=str(refusal))
            sales_store.recompute_invoice(conn, invoice_id, vat_rate=_vat_for(conn))
            conn.commit()
            return RedirectResponse(f"/invoice/{invoice_id}", status_code=303)
        finally:
            conn.close()

    @router.post("/invoice/{invoice_id}/issue")
    def issue(invoice_id: int) -> RedirectResponse:
        conn = connect()
        try:
            sales_store.recompute_invoice(conn, invoice_id, vat_rate=_vat_for(conn))
            issued = sales_store.issue_invoice(
                conn, invoice_id, days=ctx.settings.issuer.payment_days,
                qr_iban=ctx.settings.issuer.iban, by="console",
            )
            conn.commit()
        except ValueError as refusal:
            conn.rollback()
            return _back(f"/invoice/{invoice_id}", error=str(refusal))
        finally:
            conn.close()
        return _back(f"/invoice/{invoice_id}",
                     note=f"Issued: {issued['reference']} takes rank {issued['number']} "
                          f"in the register, due {str(issued['due_on'])[:10]}.")

    @router.post("/invoice/{invoice_id}/payment")
    def pay(invoice_id: int, amount: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            status = sales_store.record_payment(conn, invoice_id, amount=amount,
                                                by="console")
            conn.commit()
        except ValueError as refusal:
            conn.rollback()
            return _back(f"/invoice/{invoice_id}", error=str(refusal))
        finally:
            conn.close()
        return _back(f"/invoice/{invoice_id}",
                     note=f"Payment recorded; the invoice is {status.replace('_', ' ')}.")

    @router.post("/invoice/{invoice_id}/credit")
    def credit(invoice_id: int, reason: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            note_id = sales_store.credit_invoice(
                conn, invoice_id, reason=reason, by="console",
                days=ctx.settings.issuer.payment_days,
            )
            conn.commit()
            issued = sales_store.invoice(conn, note_id)
            return _back(f"/invoice/{note_id}",
                         note=f"Credit note {issued['reference']} issued at rank "
                              f"{issued['number']}; the invoice is marked credited.")
        except ValueError as refusal:
            conn.rollback()
            return _back(f"/invoice/{invoice_id}", error=str(refusal))
        finally:
            conn.close()

    @router.get("/invoice/{invoice_id}/print", response_class=HTMLResponse)
    def print_invoice(invoice_id: int) -> HTMLResponse:
        """The document, with a payment part when one can honestly be made.

        A QR-bill that is malformed prints perfectly and is refused at the
        bank, which is the worst failure available here because it is invisible
        until a client tries to pay. So the payload is built inside a try and a
        refusal becomes words on the page rather than a payment part nobody can
        use.
        """
        conn = connect()
        try:
            invoice = sales_store.invoice(conn, invoice_id)
            if invoice is None:
                return shell(render.not_found(), status=404)
            client = clients_store.client(conn, int(invoice["client_id"]))
            if client is None:
                return shell(render.not_found("client of this invoice"), status=404)
            address = _address_of(conn, int(client["company_id"]))
            issuer = ctx.settings.issuer
            qr, problem = "", ""
            if invoice["credits_id"] is not None:
                problem = ("A credit note carries no payment part: nothing is "
                           "owed on it.")
            elif invoice["status"] == "draft":
                problem = ("A draft has no number and no payment reference. "
                           "Issue it to produce a payment part.")
            elif not issuer.can_bill:
                problem = ("The [issuer] section of the configuration needs a "
                           "name and an IBAN before a payment part can be "
                           "produced.")
            else:
                # The register writes `city`; a client typed by hand may say
                # `town`. A debtor the standard would refuse (no postcode or
                # town) is left off the payment part, which the standard
                # allows, rather than encoded wrong, which it does not.
                where = address or {}
                town = str(where.get("town") or where.get("city") or "")
                postcode = str(where.get("swissZipCode") or "")
                debtor = money_lib.EMPTY
                if town and postcode:
                    debtor = money_lib.Party(
                        name=invoice["company_name"],
                        street=str(where.get("street") or ""),
                        building=str(where.get("houseNumber") or ""),
                        postcode=postcode, town=town,
                    )
                try:
                    payload = money_lib.qr_payload(
                        iban=invoice["qr_iban"] or issuer.iban,
                        creditor=money_lib.Party(
                            name=issuer.name, street=issuer.street,
                            building=issuer.building, postcode=issuer.postcode,
                            town=issuer.town, country=issuer.country,
                        ),
                        amount=invoice["gross_total"],
                        currency=invoice["currency"],
                        debtor=debtor,
                        reference=invoice["qr_reference"] or "",
                        message=f"Invoice {invoice['reference']}",
                    )
                    qr = money_view.qr_bill(
                        invoice, address, issuer=issuer,
                        svg=money_lib.qr_svg(payload), debtor=debtor,
                    )
                except money_lib.MoneyError as exc:
                    problem = str(exc)
            return HTMLResponse(money_view.invoice_print(
                invoice, sales_store.invoice_lines(conn, invoice_id), address,
                issuer=issuer, qr=qr, payload_error=problem,
            ))
        finally:
            conn.close()


    # --- time on a project ------------------------------------------------------

    @router.post("/project/{project_id}/time")
    def log_project_time(
        project_id: int, occurred_on: str = Form(""), hours: str = Form(""),
        note: str = Form(""), billable: str = Form(""),
    ) -> RedirectResponse:
        conn = connect()
        try:
            project = clients_store.project(conn, project_id)
            if project is None:
                return shell(render.not_found(), status=404)
            try:
                when = date.fromisoformat(occurred_on.strip())
                amount = figures.parse_decimal(hours)
                if amount <= 0:
                    raise ValueError("The hours must be above zero.")
            except ValueError as refusal:
                return _back(f"/project/{project_id}#time",
                             error=f"Nothing logged: {refusal}")
            sales_store.log_time(conn, int(project["client_id"]), {
                "project_id": project_id, "occurred_on": when,
                "minutes": int((amount * 60).to_integral_value()),
                "note": note.strip() or None, "billable": bool(billable),
                "author": "console",
            })
            conn.commit()
        finally:
            conn.close()
        return _back(f"/project/{project_id}#time", note=f"{hours.strip()} h logged.")

    @router.post("/project/{project_id}/bill-time")
    def bill_project_time(project_id: int, rate: str = Form("")) -> RedirectResponse:
        """Every billable hour not yet billed, as one line of a new draft."""
        conn = connect()
        try:
            project = clients_store.project(conn, project_id)
            if project is None:
                return shell(render.not_found(), status=404)
            client_id = int(project["client_id"])
            try:
                price = figures.parse_decimal(rate)
                if price <= 0:
                    raise ValueError("The hourly rate must be above zero.")
                entries = sales_store.time_for(
                    conn, client_id, project_id=project_id, unbilled_only=True
                )
                if not entries:
                    raise ValueError("No billable time is waiting on this project.")
                invoice_id = sales_store.create_invoice(
                    conn, client_id, project_id=project_id,
                    reference=sales_store.next_invoice_reference(conn, client_id, "time"),
                )
                sales_store.bill_time(
                    conn, invoice_id, [int(e["id"]) for e in entries],
                    label=f"Time on {project['name']}", unit_price=price,
                )
                sales_store.recompute_invoice(conn, invoice_id, vat_rate=_vat_for(conn))
                conn.commit()
            except ValueError as refusal:
                conn.rollback()
                return _back(f"/project/{project_id}#time", error=str(refusal))
        finally:
            conn.close()
        return _back(
            f"/invoice/{invoice_id}",
            note=f"{len(entries)} time entr{'y' if len(entries) == 1 else 'ies'} billed "
                 "into this draft. Issue it when it is right.",
        )

    return router
