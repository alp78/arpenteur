"""Money: the arithmetic, the documents, and the Swiss formats.

This is the part of the system where being plausibly wrong is expensive and
invisible. A quote that recomputes itself, an invoice number handed out twice,
a VAT rate applied from the wrong year, a QR-bill that scans perfectly and is
refused at the bank: none of these announce themselves, and all of them are
found by somebody else, later, in front of a client.

So the checks below lean on published figures rather than on what the code
happens to produce. The QR reference check digit is verified against the sample
in the SIX implementation guidelines, the creditor reference against the worked
example in ISO 11649, the IBAN check against a real Swiss account and a
deliberately corrupted one, and the VAT rates against the change of 1 January
2024.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.config import IssuerConfig, ServiceEntry, Territory
from arpenteur.console.app import create_app
from arpenteur.kernel.db import Connection
from arpenteur.modules.sales import money, store as sales

pytestmark = pytest.mark.pg

# The QR-IBAN and the ordinary IBAN used throughout. Both are the examples from
# the published guidelines, so a reader can check them against the source.
QR_IBAN = "CH44 3199 9123 0008 8901 2"
PLAIN_IBAN = "CH93 0076 2011 6238 5295 7"

ISSUER = IssuerConfig(
    name="Voirol", street="Chemin du Petit-Flon", building="49",
    postcode="1018", town="Lausanne", iban=PLAIN_IBAN,
    vat_number="CHE-123.456.789 TVA", payment_terms="Payable within 30 days.",
)

CATALOGUE = (
    ServiceEntry(code="diagnostic", name="Diagnostic", unit="fixed",
                 low="3600", high="7200", what="Two to four days on site.",
                 basis="The number of sites and systems.", sort_order=10),
    ServiceEntry(code="day", name="Day rate", unit="day", low="1400",
                 high="1800", sort_order=20),
    ServiceEntry(code="implementation", name="Implementation", unit="fixed",
                 low="8000", high="40000",
                 prerequisite="A completed diagnostic.", sort_order=30),
)


def _client(conn: Connection, name: str = "Fondation Bois-Gentil") -> int:
    company_id = db.upsert_company(
        conn, {"uid": "CHE103895771", "name": name, "canton": "VD"}
    )
    return db.promote_to_client(conn, company_id)


def _draft(conn: Connection, client_id: int, reference: str = "") -> int:
    """A draft invoice with a reference, which issuing now requires.

    The reference is what the client quotes and the number is the register's
    rank, so a document has to carry one before it can be issued. These tests
    are about the rank and the payment, so the reference is derived rather than
    spelt out, which also exercises the suffixing.
    """
    return sales.create_invoice(
        conn, client_id,
        reference=reference or sales.next_invoice_reference(conn, client_id, "work"),
    )


def _card(conn: Connection) -> int:
    card_id, _n = sales.load_catalogue(conn, list(CATALOGUE))
    return card_id


# --- rates and arithmetic --------------------------------------------------------


def test_the_rate_follows_the_date_of_supply_not_today():
    """Work delivered in December 2023 and billed in January 2024 carries the
    old rate. A system holding only the current one bills it wrong and nothing
    downstream would notice."""
    assert money.vat_rate(date(2023, 12, 31)) == Decimal("7.7")
    assert money.vat_rate(date(2024, 1, 1)) == Decimal("8.1")
    assert money.vat_rate(date(2026, 8, 13)) == Decimal("8.1")
    assert money.vat_rate(date(2026, 8, 13), "reduced") == Decimal("2.6")


def test_a_date_the_table_does_not_cover_is_refused():
    """Rather than falling back to today's rate, which is how an invoice for
    2015 quietly gets charged at 2026 prices."""
    with pytest.raises(money.MoneyError, match="no Swiss VAT rate"):
        money.vat_rate(date(2010, 6, 1))


def test_not_being_registered_is_not_a_rate_of_zero():
    """A business under the CHF 100'000 threshold charges no TVA, and its
    invoice says so in words. Zero would be a claim about the supply."""
    found = money.totals([Decimal("3600")], None)
    assert found.vat_rate is None and found.vat == Decimal("0.00")
    assert found.gross == found.net and not found.taxed


def test_vat_is_computed_on_the_total_not_line_by_line():
    """Per-line rounding drifts, and the client's accounts payable system will
    compute it the other way and query the difference."""
    lines = [Decimal("33.33")] * 3
    found = money.totals(lines, Decimal("8.1"))
    assert found.net == Decimal("99.99")
    assert found.vat == Decimal("8.10")
    assert found.gross == Decimal("108.09")


def test_money_never_goes_through_a_float():
    assert money.money(0.1) == Decimal("0.10")
    assert money.line_amount("3", "1400.05") == Decimal("4200.15")


def test_the_total_is_not_rounded_to_five_centimes():
    """That rounding is a cash convention: the smallest coin is 5 centimes, so
    a till rounds. An invoice settled by transfer is paid to the centime, and
    rounding it would make the total disagree with its own lines."""
    found = money.totals([Decimal("100.01"), Decimal("0.01")], None)
    assert found.gross == Decimal("100.02")


def test_amounts_are_written_the_swiss_way():
    assert money.format_chf("1234.5") == "1'234.50"
    assert money.format_chf(1234567.891) == "1'234'567.89"


# --- identifiers ------------------------------------------------------------------


def test_a_real_iban_passes_and_a_corrupted_one_does_not():
    assert money.iban_is_valid(PLAIN_IBAN)
    assert money.iban_is_valid(QR_IBAN)
    # Two digits transposed: still the right shape, still the right length.
    assert not money.iban_is_valid("CH93 0076 2011 6238 5259 7")


def test_a_qr_iban_is_recognised_by_its_institution_number():
    assert money.is_qr_iban(QR_IBAN)
    assert not money.is_qr_iban(PLAIN_IBAN)


def test_the_check_digit_matches_the_published_sample():
    """From the SIX implementation guidelines. If this fails, every reference
    this system produces is refused at the bank."""
    sample = "210000000003139471430009017"
    assert money.mod10_check_digit(sample[:26]) == int(sample[26])


def test_a_creditor_reference_matches_the_iso_example():
    assert money.creditor_reference("539007547034") == "RF18539007547034"


def test_the_same_invoice_always_produces_the_same_reference():
    """So a client paying twice does not create two unmatched payments."""
    assert money.qr_reference("20260001") == money.qr_reference("20260001")


def test_a_reference_is_printed_in_the_blocks_a_person_can_read_back():
    assert money.format_reference("210000000003139471430009017") == \
        "21 00000 00003 13947 14300 09017"
    assert money.format_reference("RF18539007547034") == "RF18 5390 0754 7034"


# --- the QR-bill payload ----------------------------------------------------------


def _creditor() -> money.Party:
    return money.Party(name="Voirol", street="Chemin du Petit-Flon",
                       building="49", postcode="1018", town="Lausanne")


def test_the_payload_has_the_prescribed_shape():
    """Positional and fixed: an omitted optional field is an empty line, never
    a missing one, because the reader counts lines."""
    payload = money.qr_payload(
        iban=QR_IBAN, creditor=_creditor(), amount=Decimal("5405.00"),
        reference=money.qr_reference("20260001"),
    )
    lines = payload.split("\n")
    assert len(lines) == 31
    assert lines[0] == "SPC" and lines[1] == "0200" and lines[2] == "1"
    assert lines[3] == "CH4431999123000889012"
    assert lines[18] == "5405.00" and lines[19] == "CHF"
    assert lines[27] == "QRR"
    assert lines[30] == "EPD"


def test_an_ordinary_iban_takes_an_rf_reference():
    payload = money.qr_payload(
        iban=PLAIN_IBAN, creditor=_creditor(), amount=Decimal("100"),
        reference=money.creditor_reference("20260001"),
    )
    assert payload.split("\n")[27] == "SCOR"


def test_a_qr_reference_with_an_ordinary_iban_is_refused():
    """The mistake that prints perfectly, scans perfectly, and is rejected at
    the bank. Invisible until a client tries to pay."""
    with pytest.raises(money.MoneyError, match="requires a QR-IBAN"):
        money.qr_payload(iban=PLAIN_IBAN, creditor=_creditor(),
                         amount=Decimal("100"),
                         reference=money.qr_reference("20260001"))


def test_a_qr_iban_without_a_qr_reference_is_refused():
    with pytest.raises(money.MoneyError, match="must carry a QR reference"):
        money.qr_payload(iban=QR_IBAN, creditor=_creditor(),
                         amount=Decimal("100"), reference="")


def test_a_wrong_iban_never_reaches_a_document():
    with pytest.raises(money.MoneyError, match="check digits"):
        money.qr_payload(iban="CH93 0076 2011 6238 5259 7", creditor=_creditor(),
                         amount=Decimal("100"))


def test_the_qr_code_reproduces_its_own_matrix():
    """The SVG is built rather than taken from the library, so it is worth
    proving that what is drawn is what was encoded."""
    import xml.etree.ElementTree as ET

    import segno

    payload = money.qr_payload(iban=PLAIN_IBAN, creditor=_creditor(),
                               amount=Decimal("100"))
    svg = money.qr_svg(payload)
    code = segno.make(payload, error="m", encoding="utf-8")
    size = code.symbol_size(border=0)[0]

    drawn = [[0] * size for _ in range(size)]
    for rect in ET.fromstring(svg).findall(".//{*}g/{*}rect"):
        x, y, width = int(rect.get("x")), int(rect.get("y")), int(rect.get("width"))
        for i in range(width):
            drawn[y][x + i] = 1
    assert drawn == [list(row) for row in code.matrix_iter(border=0)]
    assert 'width="46mm"' in svg


# --- the catalogue -----------------------------------------------------------------


def test_loading_the_card_twice_supersedes_rather_than_edits(db_path):
    """A quote sent last month has to go on being explainable by the card it
    cites."""
    conn = db.open_db(db_path)
    first = sales.load_catalogue(conn, list(CATALOGUE),
                                 valid_from=date(2026, 1, 1))[0]
    second = sales.load_catalogue(conn, list(CATALOGUE),
                                  valid_from=date(2026, 8, 1))[0]
    assert first != second
    assert sales.current_card(conn, date(2026, 3, 1))["id"] == first
    assert sales.current_card(conn, date(2026, 8, 13))["id"] == second
    conn.close()


def test_correcting_a_price_the_same_day_supersedes_the_earlier_card(db_path):
    """Reloading within the day is how a corrected price actually arrives.

    The first version of this closed only cards dated strictly earlier, so a
    same-day reload left two cards open with identical `valid_from` and no
    tie-break between them. The console then went on quoting the superseded
    prices, which is the one failure this whole table exists to prevent.
    """
    conn = db.open_db(db_path)
    when = date(2026, 9, 9)
    first = sales.load_catalogue(conn, list(CATALOGUE), valid_from=when)[0]
    corrected = [replace(CATALOGUE[0], low="999", high="999"), *CATALOGUE[1:]]
    second = sales.load_catalogue(conn, corrected, valid_from=when)[0]
    assert first != second

    card = sales.current_card(conn, when)
    assert card["id"] == second
    line = next(
        row for row in sales.card_lines(conn, card["id"])
        if row["code"] == CATALOGUE[0].code
    )
    assert str(line["low"]) == "999.00"
    conn.close()


def test_the_catalogue_reads_back_as_the_prose_the_advice_needs(db_path):
    """The advise stage prices from words, because "quoted only after a
    diagnostic" is a rule a model can respect and a numeric column cannot."""
    conn = db.open_db(db_path)
    _card(conn)
    text = sales.catalogue_text(conn)
    assert "CHF 3'600.00 to 7'200.00" in text
    assert "per day" in text
    assert "Only after: A completed diagnostic." in text
    conn.close()


def test_an_empty_catalogue_yields_no_prices_rather_than_invented_ones(db_path):
    conn = db.open_db(db_path)
    assert sales.catalogue_text(conn) == ""
    conn.close()


# --- quotes ---------------------------------------------------------------------


def test_a_line_is_priced_from_the_card_and_says_which_line(db_path):
    conn = db.open_db(db_path)
    client_id = _client(conn)
    card_id = _card(conn)
    quote_id = sales.create_quote(conn, client_id)
    version_id = sales.add_version(conn, quote_id, rate_card_id=card_id)
    service = sales.service_by_code(conn, "diagnostic")

    sales.line_from_service(conn, version_id, int(service["id"]))
    line = sales.lines_for(conn, version_id)[0]
    assert line["unit_price"] == Decimal("3600.00")
    assert line["basis"] == "The number of sites and systems."
    conn.close()


def test_the_low_end_is_taken_when_nobody_chose(db_path):
    """A number that appears without anyone choosing it should be the one that
    is safe to have quoted."""
    conn = db.open_db(db_path)
    client_id = _client(conn)
    card_id = _card(conn)
    version_id = sales.add_version(
        conn, sales.create_quote(conn, client_id), rate_card_id=card_id
    )
    service = sales.service_by_code(conn, "day")
    sales.line_from_service(conn, version_id, int(service["id"]), quantity=3)
    sales.line_from_service(conn, version_id, int(service["id"]), quantity=1,
                            at="high")
    low, high = sales.lines_for(conn, version_id)
    assert low["unit_price"] == Decimal("1400.00")
    assert low["amount"] == Decimal("4200.00")
    assert high["unit_price"] == Decimal("1800.00")
    conn.close()


def test_a_price_the_card_does_not_cover_is_refused(db_path):
    conn = db.open_db(db_path)
    client_id = _client(conn)
    card_id = _card(conn)
    version_id = sales.add_version(
        conn, sales.create_quote(conn, client_id), rate_card_id=card_id
    )
    orphan = sales.save_service(conn, {"code": "training", "name": "Training"})
    with pytest.raises(ValueError, match="no price on the rate card"):
        sales.line_from_service(conn, version_id, orphan)
    conn.close()


def test_a_sent_version_refuses_to_change(db_path):
    """What the client is holding has to go on saying what it said."""
    conn = db.open_db(db_path)
    client_id = _client(conn)
    quote_id = sales.create_quote(conn, client_id)
    version_id = sales.add_version(conn, quote_id)
    sales.add_line(conn, version_id, {"label": "Diagnostic", "unit_price": 3600})
    sales.recompute(conn, version_id, vat_rate=Decimal("8.1"))
    sales.mark_sent(conn, version_id, to="client@example.ch")

    with pytest.raises(ValueError, match="has been sent"):
        sales.add_line(conn, version_id, {"label": "One more", "unit_price": 100})
    assert sales.quote(conn, quote_id)["status"] == "sent"
    conn.close()


def test_a_sent_version_is_not_recomputed(db_path):
    """Its totals are what was printed, not what the lines would come to now."""
    conn = db.open_db(db_path)
    client_id = _client(conn)
    version_id = sales.add_version(conn, sales.create_quote(conn, client_id))
    sales.add_line(conn, version_id, {"label": "Diagnostic", "unit_price": 3600})
    sales.recompute(conn, version_id, vat_rate=Decimal("8.1"))
    sales.mark_sent(conn, version_id)

    again = sales.recompute(conn, version_id, vat_rate=Decimal("2.6"))
    assert again.vat_rate == Decimal("8.100")
    assert sales.version(conn, version_id)["gross_total"] == Decimal("3891.60")
    conn.close()


def test_a_revision_is_a_new_version_carrying_nothing_by_itself(db_path):
    conn = db.open_db(db_path)
    client_id = _client(conn)
    quote_id = sales.create_quote(conn, client_id)
    first = sales.add_version(conn, quote_id)
    sales.add_line(conn, first, {"label": "Diagnostic", "unit_price": 3600})
    sales.mark_sent(conn, first)

    second = sales.add_version(conn, quote_id)
    assert sales.lines_for(conn, second) == []
    assert sales.copy_lines(conn, first, second) == 1
    assert len(sales.lines_for(conn, second)) == 1
    assert [v["version"] for v in sales.versions_for(conn, quote_id)] == [2, 1]
    conn.close()


def test_a_quote_code_is_readable_and_per_client(db_path):
    conn = db.open_db(db_path)
    client_id = _client(conn)
    first = sales.quote(conn, sales.create_quote(conn, client_id))
    second = sales.quote(conn, sales.create_quote(conn, client_id))
    assert first["code"].endswith("-Q01") and second["code"].endswith("-Q02")
    assert first["code"].startswith("BOIS-GENTIL")
    conn.close()


def test_every_state_change_reaches_the_audit(db_path):
    conn = db.open_db(db_path)
    client_id = _client(conn)
    quote_id = sales.create_quote(conn, client_id)
    sales.set_quote_status(conn, quote_id, "sent")
    sales.set_quote_status(conn, quote_id, "declined", reason="Went elsewhere")
    rows = conn.execute(
        "SELECT from_state, to_state, reason FROM audit.state_change"
        " WHERE entity = 'quote' ORDER BY id"
    ).fetchall()
    assert [r["to_state"] for r in rows] == ["draft", "sent", "declined"]
    assert rows[-1]["reason"] == "Went elsewhere"
    conn.close()


# --- invoices ---------------------------------------------------------------------


def test_a_draft_takes_no_number(db_path):
    """An abandoned draft would otherwise leave a hole in a series the tax
    authority expects to be continuous."""
    conn = db.open_db(db_path)
    invoice_id = _draft(conn, _client(conn))
    assert sales.invoice(conn, invoice_id)["number"] is None
    conn.close()


def test_issuing_allocates_a_number_and_a_reference(db_path):
    conn = db.open_db(db_path)
    invoice_id = _draft(conn, _client(conn))
    sales.add_invoice_line(conn, invoice_id, {"label": "Diagnostic",
                                              "unit_price": 3600})
    sales.recompute_invoice(conn, invoice_id, vat_rate=Decimal("8.1"))
    issued = sales.issue_invoice(conn, invoice_id, issued_on=date(2026, 8, 13),
                                 qr_iban=PLAIN_IBAN)
    assert issued["number"].startswith("2026-")
    assert issued["status"] == "issued"
    # Dates come back from the store as ISO strings, by the kernel's
    # own convention rather than by accident.
    assert issued["due_on"] == "2026-09-12"
    # An ordinary IBAN, so an RF reference rather than a QR one.
    assert issued["qr_reference"].startswith("RF")
    assert issued["gross_total"] == Decimal("3891.60")
    conn.close()


def test_a_qr_iban_gets_a_qr_reference(db_path):
    conn = db.open_db(db_path)
    invoice_id = _draft(conn, _client(conn))
    sales.add_invoice_line(conn, invoice_id, {"label": "Diagnostic", "unit_price": 100})
    issued = sales.issue_invoice(conn, invoice_id, qr_iban=QR_IBAN)
    assert issued["qr_reference"].isdigit() and len(issued["qr_reference"]) == 27
    conn.close()


def test_numbers_are_never_handed_out_twice(db_path):
    conn = db.open_db(db_path)
    client_id = _client(conn)
    numbers = []
    for _ in range(3):
        invoice_id = _draft(conn, client_id)
        sales.add_invoice_line(conn, invoice_id, {"label": "Day", "unit_price": 1400})
        numbers.append(sales.issue_invoice(conn, invoice_id)["number"])
    assert len(set(numbers)) == 3
    conn.close()


def test_an_invoice_with_no_lines_is_not_a_document(db_path):
    conn = db.open_db(db_path)
    invoice_id = _draft(conn, _client(conn))
    with pytest.raises(ValueError, match="no lines"):
        sales.issue_invoice(conn, invoice_id)
    conn.close()


def test_an_issued_invoice_refuses_to_be_edited(db_path):
    conn = db.open_db(db_path)
    invoice_id = _draft(conn, _client(conn))
    sales.add_invoice_line(conn, invoice_id, {"label": "Day", "unit_price": 1400})
    sales.issue_invoice(conn, invoice_id)
    with pytest.raises(ValueError, match="credit note"):
        sales.add_invoice_line(conn, invoice_id, {"label": "More", "unit_price": 1})
    conn.close()


def test_a_correction_is_a_credit_note_carrying_its_own_number(db_path):
    conn = db.open_db(db_path)
    client_id = _client(conn)
    invoice_id = _draft(conn, client_id)
    sales.add_invoice_line(conn, invoice_id, {"label": "Diagnostic",
                                              "unit_price": 3600})
    sales.recompute_invoice(conn, invoice_id, vat_rate=Decimal("8.1"))
    sales.issue_invoice(conn, invoice_id)

    note_id = sales.credit_invoice(conn, invoice_id, reason="Wrong scope")
    note = sales.invoice(conn, note_id)
    assert note["gross_total"] == Decimal("-3891.60")
    assert sales.invoice(conn, invoice_id)["status"] == "credited"
    assert note["credits_id"] == invoice_id
    conn.close()


def test_part_payment_is_a_state_of_its_own(db_path):
    """A client who has settled 90% of a bill should not be chased for all of it."""
    conn = db.open_db(db_path)
    invoice_id = _draft(conn, _client(conn))
    sales.add_invoice_line(conn, invoice_id, {"label": "Diagnostic",
                                              "unit_price": 1000})
    sales.recompute_invoice(conn, invoice_id, vat_rate=None)
    sales.issue_invoice(conn, invoice_id)

    assert sales.record_payment(conn, invoice_id, amount=400) == "part_paid"
    assert sales.record_payment(conn, invoice_id, amount=600) == "paid"
    assert sales.invoice(conn, invoice_id)["paid_total"] == Decimal("1000.00")
    conn.close()


# --- time -------------------------------------------------------------------------


def test_billing_time_marks_it_so_it_cannot_be_billed_twice(db_path):
    conn = db.open_db(db_path)
    client_id = _client(conn)
    first = sales.log_time(conn, client_id, {"occurred_on": date(2026, 8, 10),
                                             "minutes": 240, "note": "On site"})
    second = sales.log_time(conn, client_id, {"occurred_on": date(2026, 8, 11),
                                              "minutes": 180})
    invoice_id = _draft(conn, client_id)
    sales.bill_time(conn, invoice_id, [first, second], label="Day rate",
                    unit_price=175)
    line = sales.invoice_lines(conn, invoice_id)[0]
    assert line["quantity"] == Decimal("7.00")
    assert line["amount"] == Decimal("1225.00")

    with pytest.raises(ValueError, match="already on an invoice"):
        sales.bill_time(conn, invoice_id, [first], label="Again", unit_price=175)
    conn.close()


def test_time_that_is_not_billable_is_still_recorded(db_path):
    """Travel inside the surveyed cantons is not billed, and recording it as
    zero minutes would lose the fact that the time was spent."""
    conn = db.open_db(db_path)
    client_id = _client(conn)
    sales.log_time(conn, client_id, {"occurred_on": date(2026, 8, 10),
                                     "minutes": 120, "billable": False})
    sales.log_time(conn, client_id, {"occurred_on": date(2026, 8, 10),
                                     "minutes": 240})
    found = sales.time_summary(conn, client_id)
    assert (found["minutes"], found["billable"], found["unbilled"]) == (360, 240, 240)
    conn.close()


# --- the pages ---------------------------------------------------------------------


def _app(build_settings, tmp_path, issuer: IssuerConfig = ISSUER):
    return TestClient(create_app(build_settings(
        tmp_path,
        territory=Territory(cantons=("VD",), legal_forms=("0106",), name="test"),
        issuer=issuer,
    )))


def _issued(conn, client_id: int, iban: str = PLAIN_IBAN) -> int:
    invoice_id = _draft(conn, client_id)
    sales.add_invoice_line(conn, invoice_id, {"label": "Diagnostic",
                                              "unit_price": 3600})
    sales.recompute_invoice(conn, invoice_id, vat_rate=Decimal("8.1"))
    sales.issue_invoice(conn, invoice_id, qr_iban=iban)
    return invoice_id


def test_the_printed_invoice_carries_a_payment_part(db_path, build_settings, tmp_path):
    conn = db.open_db(db_path)
    invoice_id = _issued(conn, _client(conn))
    conn.commit()
    conn.close()

    page = _app(build_settings, tmp_path).get(f"/invoice/{invoice_id}/print")
    assert page.status_code == 200
    assert "Payment part" in page.text and "Receipt" in page.text
    assert "Acceptance point" in page.text
    assert 'width="46mm"' in page.text
    # The IBAN in groups of four, which is how somebody checks it against
    # their bank's screen.
    assert "CH93 0076 2011 6238 5295 7" in page.text
    assert "105mm" in page.text


def test_a_draft_prints_without_a_payment_part_and_says_why(db_path, build_settings, tmp_path):
    conn = db.open_db(db_path)
    invoice_id = _draft(conn, _client(conn))
    sales.add_invoice_line(conn, invoice_id, {"label": "Diagnostic", "unit_price": 100})
    conn.commit()
    conn.close()

    page = _app(build_settings, tmp_path).get(f"/invoice/{invoice_id}/print")
    assert "No payment part" in page.text
    assert "Issue it" in page.text
    assert "Payment part" not in page.text.split("No payment part")[0]


def test_no_issuer_means_no_payment_part_rather_than_a_broken_one(db_path, build_settings, tmp_path):
    conn = db.open_db(db_path)
    client_id = _client(conn)
    invoice_id = _draft(conn, client_id)
    sales.add_invoice_line(conn, invoice_id, {"label": "Diagnostic", "unit_price": 100})
    sales.issue_invoice(conn, invoice_id)
    conn.commit()
    conn.close()

    page = _app(build_settings, tmp_path, IssuerConfig()).get(
        f"/invoice/{invoice_id}/print"
    )
    assert "No payment part" in page.text
    assert "needs a name and an IBAN" in page.text


def test_an_invoice_not_subject_to_vat_says_so_in_words(db_path, build_settings, tmp_path):
    conn = db.open_db(db_path)
    invoice_id = _draft(conn, _client(conn))
    sales.add_invoice_line(conn, invoice_id, {"label": "Diagnostic", "unit_price": 3600})
    sales.recompute_invoice(conn, invoice_id, vat_rate=None)
    sales.issue_invoice(conn, invoice_id, qr_iban=PLAIN_IBAN)
    conn.commit()
    conn.close()

    unregistered = IssuerConfig(name="Voirol", iban=PLAIN_IBAN, town="Lausanne")
    page = _app(build_settings, tmp_path, unregistered).get(
        f"/invoice/{invoice_id}/print"
    )
    assert "Not subject to TVA" in page.text
    assert "TVA 0" not in page.text


def test_the_quote_page_refuses_to_edit_what_was_sent(db_path, build_settings, tmp_path):
    conn = db.open_db(db_path)
    client_id = _client(conn)
    quote_id = sales.create_quote(conn, client_id)
    version_id = sales.add_version(conn, quote_id)
    sales.add_line(conn, version_id, {"label": "Diagnostic", "unit_price": 3600})
    sales.recompute(conn, version_id, vat_rate=Decimal("8.1"))
    sales.mark_sent(conn, version_id, to="nils@example.ch")
    conn.commit()
    conn.close()

    client = _app(build_settings, tmp_path)
    page = client.get(f"/quote/{quote_id}")
    assert "cannot be changed" in page.text
    assert "Add</button>" not in page.text

    # And the route enforces it, not only the page.
    client.post(f"/quote-version/{version_id}/lines",
                data={"label": "Snuck in", "quantity": "1", "unit_price": "1"},
                follow_redirects=False)
    conn = db.open_db(db_path)
    assert len(sales.lines_for(conn, version_id)) == 1
    conn.close()


def test_a_whole_quote_can_be_built_and_billed_through_the_console(db_path, build_settings, tmp_path):
    """The path the money actually takes: catalogue to quote to acceptance to
    invoice, without a figure being retyped."""
    conn = db.open_db(db_path)
    client_id = _client(conn)
    _card(conn)
    service = sales.service_by_code(conn, "diagnostic")
    conn.commit()
    conn.close()

    client = _app(build_settings, tmp_path)
    client.post(f"/client/{client_id}/quotes", follow_redirects=False)

    conn = db.open_db(db_path)
    quote_id = sales.quotes_for(conn, client_id)[0]["id"]
    version_id = sales.latest_version(conn, quote_id)["id"]
    conn.close()

    client.post(f"/quote-version/{version_id}/lines",
                data={"service_id": str(service["id"]), "quantity": "1", "at": "low"},
                follow_redirects=False)
    client.post(f"/quote-version/{version_id}/sent", data={"to": "nils@example.ch"},
                follow_redirects=False)
    client.post(f"/quote/{quote_id}/status", data={"status": "accepted"},
                follow_redirects=False)
    client.post(f"/quote/{quote_id}/invoice", follow_redirects=False)

    conn = db.open_db(db_path)
    invoice = sales.invoices_for(conn, client_id)[0]
    lines = sales.invoice_lines(conn, int(invoice["id"]))
    assert [line["unit_price"] for line in lines] == [Decimal("3600.00")]
    assert invoice["gross_total"] == Decimal("3891.60")
    assert sales.quote(conn, quote_id)["status"] == "accepted"
    conn.close()


def test_the_money_pages_still_cannot_send(db_path, build_settings, tmp_path):
    """"Mark as sent" records that a person sent it. It is not a send button,
    and the freeze it applies is the whole reason it exists."""
    client = _app(build_settings, tmp_path)
    routes = {getattr(r, "path", "") for r in client.app.routes}
    assert "/quote-version/{version_id}/sent" in routes
    from arpenteur.console.app import TRANSMITTING
    assert not [p for p in routes
                if ("send" in p or "smtp" in p) and p not in TRANSMITTING]


# --- the register --------------------------------------------------------------------
#
# Two identifiers, and the whole point is which one goes where. The rank is the
# register's and proves the series is whole; the reference is the client's and
# says nothing about how many invoices came before theirs.


def test_a_draft_takes_a_reference_although_it_takes_no_rank(db_path):
    """The reference is how the work is spoken about before it is billed.

    A draft that could not be named would have to be found by its database id,
    which is the one thing nobody says out loud."""
    conn = db.open_db(db_path)
    invoice_id = sales.create_invoice(conn, _client(conn), reference="ABC-EXPERTISE")
    held = sales.invoice(conn, invoice_id)
    assert held["reference"] == "ABC-EXPERTISE"
    assert held["number"] is None
    conn.close()


def test_an_invoice_with_no_reference_is_not_a_document(db_path):
    """Issuing one would print a rank at the client, which is the register's own
    count of how its year is going and none of their business."""
    conn = db.open_db(db_path)
    invoice_id = sales.create_invoice(conn, _client(conn))
    sales.add_invoice_line(conn, invoice_id, {"label": "Day", "unit_price": 1400})
    with pytest.raises(ValueError, match="no reference"):
        sales.issue_invoice(conn, invoice_id)
    conn.close()


def test_two_invoices_cannot_carry_the_same_reference(db_path):
    """A client quoting it would be naming two documents, and a payment against
    it could be matched to either."""
    conn = db.open_db(db_path)
    client_id = _client(conn)
    sales.create_invoice(conn, client_id, reference="ABC-EXPERTISE")
    with pytest.raises(Exception):
        sales.create_invoice(conn, client_id, reference="ABC-EXPERTISE")
        conn.execute("SELECT 1")
    conn.close()


def test_a_reference_is_built_from_the_client_code_and_the_object(db_path):
    """Derived rather than typed, so it cannot drift from the account it belongs
    to, and folded because the same string is read down a telephone."""
    conn = db.open_db(db_path)
    client_id = _client(conn)
    conn.execute("UPDATE crm.client SET code = 'ABC' WHERE id = %s", (client_id,))
    assert sales.next_invoice_reference(conn, client_id, "expertise") == "ABC-EXPERTISE"
    assert sales.next_invoice_reference(conn, client_id, "étape 1") == "ABC-ETAPE-1"
    conn.close()


def test_a_reference_carries_neither_a_date_nor_a_rank(db_path):
    """The owner's reason for the whole split: 2026-001 tells the reader they are
    the first invoice of the exercise."""
    conn = db.open_db(db_path)
    client_id = _client(conn)
    conn.execute("UPDATE crm.client SET code = 'ABC' WHERE id = %s", (client_id,))
    reference = sales.next_invoice_reference(conn, client_id, "expertise")
    assert not any(c.isdigit() for c in reference)
    conn.close()


def test_a_second_invoice_for_the_same_object_is_named_rather_than_refused(db_path):
    conn = db.open_db(db_path)
    client_id = _client(conn)
    first = sales.next_invoice_reference(conn, client_id, "expertise")
    sales.create_invoice(conn, client_id, reference=first)
    assert sales.next_invoice_reference(conn, client_id, "expertise") == f"{first}-2"
    conn.close()


def test_an_issued_invoice_refuses_to_be_renamed(db_path):
    """The reference is what the client holds and what a payment will quote."""
    conn = db.open_db(db_path)
    invoice_id = _issued(conn, _client(conn))
    with pytest.raises(ValueError, match="has been issued"):
        sales.set_invoice_reference(conn, invoice_id, "SOMETHING-ELSE")
    conn.close()


def test_a_bank_made_payment_part_records_the_account_and_no_reference(db_path):
    """BCV-net emits the payment part with reference type NON. Storing a derived
    RF reference beside it would put a number in the register that is on no
    document, and a reconciliation would chase it."""
    conn = db.open_db(db_path)
    invoice_id = sales.create_invoice(conn, _client(conn), reference="ABC-EXPERTISE")
    sales.add_invoice_line(conn, invoice_id, {"label": "Forfait", "unit_price": 3200})
    sales.recompute_invoice(conn, invoice_id, vat_rate=None)
    issued = sales.issue_invoice(conn, invoice_id, qr_iban=PLAIN_IBAN,
                                 with_reference=False)
    assert issued["qr_iban"] == money.clean_iban(PLAIN_IBAN)
    assert issued["qr_reference"] is None
    conn.close()


def test_a_credit_note_is_named_after_what_it_reverses(db_path):
    """Its own rank, because it is its own document, and a reference that says
    which invoice it undoes, because that is all the client can match it on."""
    conn = db.open_db(db_path)
    invoice_id = sales.create_invoice(conn, _client(conn), reference="ABC-EXPERTISE")
    sales.add_invoice_line(conn, invoice_id, {"label": "Forfait", "unit_price": 3200})
    sales.recompute_invoice(conn, invoice_id, vat_rate=None)
    sales.issue_invoice(conn, invoice_id)
    note_id = sales.credit_invoice(conn, invoice_id, reason="Wrong scope")
    assert sales.invoice(conn, note_id)["reference"] == "ABC-EXPERTISE-CREDIT"
    conn.close()


def test_the_register_runs_by_rank_and_not_by_date(db_path):
    """Ordered by the date, a backdated invoice hides between two others and the
    series stops being readable as a series."""
    conn = db.open_db(db_path)
    client_id = _client(conn)
    for n, when in enumerate(((2026, 9, 1), (2026, 7, 1), (2026, 8, 1)), start=1):
        invoice_id = sales.create_invoice(conn, client_id, reference=f"ABC-W{n}")
        sales.add_invoice_line(conn, invoice_id, {"label": "Day", "unit_price": 100})
        sales.recompute_invoice(conn, invoice_id, vat_rate=None)
        sales.issue_invoice(conn, invoice_id, issued_on=date(*when))
    ranks = [row["number"] for row in sales.register(conn)]
    assert ranks == sorted(ranks)
    assert [row["reference"] for row in sales.register(conn)] == \
        ["ABC-W1", "ABC-W2", "ABC-W3"]
    conn.close()


def test_a_draft_is_not_in_the_register(db_path):
    """It has no rank, so it is not in the series and cannot be missing from it."""
    conn = db.open_db(db_path)
    client_id = _client(conn)
    sales.create_invoice(conn, client_id, reference="ABC-DRAFT")
    assert sales.register(conn) == []
    conn.close()


def test_the_register_names_a_rank_no_document_carries(db_path):
    """A number is drawn outside the transaction, so an issue that failed burns
    one. A register that quietly skips it proves nothing about the series."""
    conn = db.open_db(db_path)
    client_id = _client(conn)
    for n in (1, 2):
        invoice_id = sales.create_invoice(conn, client_id, reference=f"ABC-W{n}")
        sales.add_invoice_line(conn, invoice_id, {"label": "Day", "unit_price": 100})
        sales.recompute_invoice(conn, invoice_id, vat_rate=None)
        sales.issue_invoice(conn, invoice_id, issued_on=date(2026, 8, 1))
        if n == 1:
            conn.execute("SELECT nextval('commercial.invoice_number_seq')")
    rows = sales.register(conn)
    # The sequence is not reset between tests, so the ranks are read rather than
    # spelt out. What is asserted is the shape: two documents, one rank between
    # them that nothing carries.
    first, second = (r["number"] for r in rows)
    assert sales.register_gaps(rows) == [f"{first[:4]}-{int(first[5:]) + 1:04d}"]
    assert int(second[5:]) == int(first[5:]) + 2
    conn.close()


def test_a_whole_series_reports_no_gap(db_path):
    conn = db.open_db(db_path)
    client_id = _client(conn)
    for n in (1, 2, 3):
        invoice_id = sales.create_invoice(conn, client_id, reference=f"ABC-W{n}")
        sales.add_invoice_line(conn, invoice_id, {"label": "Day", "unit_price": 100})
        sales.recompute_invoice(conn, invoice_id, vat_rate=None)
        sales.issue_invoice(conn, invoice_id, issued_on=date(2026, 8, 1))
    assert sales.register_gaps(sales.register(conn)) == []
    conn.close()


def test_the_register_page_shows_the_rank_beside_the_reference(
    db_path, build_settings, tmp_path
):
    """Both identifiers in one view is the point: the rank proves the series is
    whole, the reference is what a client telephoning would quote."""
    conn = db.open_db(db_path)
    client_id = _client(conn)
    conn.execute("UPDATE crm.client SET code = 'ABC' WHERE id = %s", (client_id,))
    invoice_id = sales.create_invoice(conn, client_id, reference="ABC-EXPERTISE")
    sales.add_invoice_line(conn, invoice_id, {"label": "Forfait", "unit_price": 3200})
    sales.recompute_invoice(conn, invoice_id, vat_rate=None)
    rank = sales.issue_invoice(conn, invoice_id, issued_on=date(2026, 10, 5))["number"]
    conn.commit()
    conn.close()

    client = _app(build_settings, tmp_path)
    body = client.get("/invoices/register").text
    assert "ABC-EXPERTISE" in body and rank in body
    csv = client.get("/invoices/register.csv")
    assert csv.status_code == 200
    assert "ABC-EXPERTISE" in csv.text and rank in csv.text


def test_the_ledger_leads_on_the_reference_not_the_rank(
    db_path, build_settings, tmp_path
):
    """The ledger is read while talking to a client, so it shows what they hold."""
    conn = db.open_db(db_path)
    client_id = _client(conn)
    invoice_id = sales.create_invoice(conn, client_id, reference="ABC-EXPERTISE")
    sales.add_invoice_line(conn, invoice_id, {"label": "Forfait", "unit_price": 3200})
    sales.recompute_invoice(conn, invoice_id, vat_rate=None)
    sales.issue_invoice(conn, invoice_id)
    conn.commit()
    conn.close()

    body = _app(build_settings, tmp_path).get("/invoices").text
    assert "ABC-EXPERTISE" in body


def test_an_invoice_started_from_the_console_can_be_issued(
    db_path, build_settings, tmp_path
):
    """Making the reference compulsory could have left the console able to create
    a draft that nothing could issue, which is a dead end discovered at the worst
    moment. The form names it on the way in."""
    conn = db.open_db(db_path)
    client_id = _client(conn)
    conn.execute("UPDATE crm.client SET code = 'ABC' WHERE id = %s", (client_id,))
    conn.commit()
    conn.close()

    client = _app(build_settings, tmp_path)
    reply = client.post(f"/client/{client_id}/invoices", data={"what": "expertise"},
                        follow_redirects=False)
    assert reply.status_code == 303
    invoice_id = int(reply.headers["location"].rsplit("/", 1)[1])

    conn = db.open_db(db_path)
    assert sales.invoice(conn, invoice_id)["reference"] == "ABC-EXPERTISE"
    sales.add_invoice_line(conn, invoice_id, {"label": "Day", "unit_price": 80})
    sales.recompute_invoice(conn, invoice_id, vat_rate=None)
    assert sales.issue_invoice(conn, invoice_id)["status"] == "issued"
    conn.close()


def test_a_draft_can_be_renamed_from_its_page(db_path, build_settings, tmp_path):
    conn = db.open_db(db_path)
    client_id = _client(conn)
    invoice_id = sales.create_invoice(conn, client_id, reference="ABC-WRONG")
    conn.commit()
    conn.close()

    client = _app(build_settings, tmp_path)
    client.post(f"/invoice/{invoice_id}/reference", data={"reference": "ABC-RIGHT"},
                follow_redirects=False)
    conn = db.open_db(db_path)
    assert sales.invoice(conn, invoice_id)["reference"] == "ABC-RIGHT"
    conn.close()


def test_work_that_recurs_is_numbered_from_the_first_one(db_path):
    """A client receiving several sessions has to see which one they are paying.
    ABC-SEANCE followed by ABC-SEANCE-2 reads as a correction of the first."""
    conn = db.open_db(db_path)
    client_id = _client(conn)
    conn.execute("UPDATE crm.client SET code = 'ABC' WHERE id = %s", (client_id,))
    premiere = sales.next_invoice_reference(conn, client_id, "séance", series=True)
    assert premiere == "ABC-SEANCE-01"
    sales.create_invoice(conn, client_id, reference=premiere)
    assert sales.next_invoice_reference(conn, client_id, "séance", series=True) == "ABC-SEANCE-02"
    conn.close()


def test_a_one_off_is_not_numbered(db_path):
    """A number on a thing that happens once invites the reader to look for the
    others."""
    conn = db.open_db(db_path)
    client_id = _client(conn)
    conn.execute("UPDATE crm.client SET code = 'ABC' WHERE id = %s", (client_id,))
    assert sales.next_invoice_reference(conn, client_id, "expertise") == "ABC-EXPERTISE"
    conn.close()


def test_a_numbered_series_survives_a_gap_in_its_own_count(db_path):
    """Taking max + 1 rather than count + 1: a cancelled second session must not
    make the third reuse its reference."""
    conn = db.open_db(db_path)
    client_id = _client(conn)
    conn.execute("UPDATE crm.client SET code = 'ABC' WHERE id = %s", (client_id,))
    for ref in ("ABC-SEANCE-01", "ABC-SEANCE-03"):
        sales.create_invoice(conn, client_id, reference=ref)
    assert sales.next_invoice_reference(conn, client_id, "seance", series=True) == "ABC-SEANCE-04"
    conn.close()


def test_the_console_can_start_a_numbered_series(db_path, build_settings, tmp_path):
    conn = db.open_db(db_path)
    client_id = _client(conn)
    conn.execute("UPDATE crm.client SET code = 'ABC' WHERE id = %s", (client_id,))
    conn.commit()
    conn.close()

    client = _app(build_settings, tmp_path)
    reply = client.post(f"/client/{client_id}/invoices",
                        data={"what": "séance", "series": "1"},
                        follow_redirects=False)
    invoice_id = int(reply.headers["location"].rsplit("/", 1)[1])
    conn = db.open_db(db_path)
    assert sales.invoice(conn, invoice_id)["reference"] == "ABC-SEANCE-01"
    conn.close()
