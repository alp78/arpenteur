"""Swiss money: TVA, rounding, the QR-bill payload and its reference.

Nothing in this file talks to a database and nothing in it guesses. It is the
arithmetic and the formats, kept apart from the store so both can be tested
against published figures rather than against whatever the application happened
to write.

Four things worth stating before the code, because each is a place where a
plausible implementation is wrong.

**A rate is not a constant.** Swiss TVA moved on 1 January 2024 to 8.1%, and
before that it was 7.7% for fifteen years. An invoice raised for work done in
2023 carries the old rate. So the rate is chosen by date and the table keeps
what expired, rather than being a single number that silently rewrites
history.

**Not being registered is a normal state, not a missing value.** A consultancy
under the CHF 100'000 threshold charges no TVA at all, and its invoices must
say why rather than showing 0.00 next to a rate. `None` means not subject; zero
would be a claim.

**The cents are not rounded to five.** The 5-centime rounding everyone
associates with Swiss francs is a cash-payment convention: the smallest coin is
5 centimes, so a till rounds. An invoice paid by transfer is settled to the
centime, and the QR-bill carries an amount to two decimals. Rounding the total
would make the invoice disagree with the sum of its own lines, which is the
kind of error an auditor finds in a minute.

**A QR reference is not free text.** It is 27 digits ending in a check digit
computed by the recursive mod-10 the Swiss payment system has used since the
orange ESR slips, and it requires a QR-IBAN, which is an IBAN whose institution
number falls in a reserved range. Using an ordinary IBAN with a QR reference
produces a bill the bank rejects, and the two mistakes look identical on paper.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

from ...kernel import figures

# Swiss VAT, by the date the service was supplied rather than the date of the
# invoice. Each entry is (from, to or None, standard, reduced, lodging).
#
# The 2024 change is the one that matters in practice: work delivered in
# December 2023 and billed in January 2024 is charged at 7.7%, and a system
# holding only the current rate bills it at 8.1% and is wrong by a few hundred
# francs on a large invoice.
VAT_TABLE: tuple[tuple[date, date | None, Decimal, Decimal, Decimal], ...] = (
    (date(2018, 1, 1), date(2023, 12, 31), Decimal("7.7"), Decimal("2.5"), Decimal("3.7")),
    (date(2024, 1, 1), None, Decimal("8.1"), Decimal("2.6"), Decimal("3.8")),
)

# Consulting, analysis and implementation are all standard-rated. The reduced
# and lodging rates are here because the table would be a lie without them, not
# because this tool expects to bill a hotel night.
KINDS = ("standard", "reduced", "lodging")

CENT = Decimal("0.01")

# An IBAN whose institution identifier is in this range is a QR-IBAN, and only
# a QR-IBAN may carry a QR reference. The range is reserved by SIX for exactly
# this purpose.
QR_IID = range(30000, 32000)

# The recursive mod-10 table, from the Swiss payment standard. It is written
# out rather than computed because it is a published constant and a clever
# derivation of it would be one more thing to get wrong.
_MOD10 = (
    (0, 9, 4, 6, 8, 2, 7, 1, 3, 5),
    (9, 4, 6, 8, 2, 7, 1, 3, 5, 0),
    (4, 6, 8, 2, 7, 1, 3, 5, 0, 9),
    (6, 8, 2, 7, 1, 3, 5, 0, 9, 4),
    (8, 2, 7, 1, 3, 5, 0, 9, 4, 6),
    (2, 7, 1, 3, 5, 0, 9, 4, 6, 8),
    (7, 1, 3, 5, 0, 9, 4, 6, 8, 2),
    (1, 3, 5, 0, 9, 4, 6, 8, 2, 7),
    (3, 5, 0, 9, 4, 6, 8, 2, 7, 1),
    (5, 0, 9, 4, 6, 8, 2, 7, 1, 3),
)


class MoneyError(ValueError):
    """A figure or an identifier that must not be allowed onto a document."""


# --- rates ---------------------------------------------------------------------


def vat_rate(on: date, kind: str = "standard") -> Decimal:
    """The rate in force on a date, as a percentage.

    Raises rather than falling back to the current rate for a date the table
    does not cover. A silent fallback is how an invoice for 2015 gets charged
    at today's rate, and nothing downstream would notice.
    """
    if kind not in KINDS:
        raise MoneyError(f"unknown VAT kind {kind!r}")
    index = {"standard": 2, "reduced": 3, "lodging": 4}[kind]
    for start, end, *rates in VAT_TABLE:
        if start <= on and (end is None or on <= end):
            return rates[index - 2]
    raise MoneyError(
        f"no Swiss VAT rate recorded for {on:%d.%m.%Y}. The table starts at "
        f"{VAT_TABLE[0][0]:%d.%m.%Y}; add the period rather than assuming today's rate."
    )


def parse_decimal(value: object) -> Decimal:
    """A figure as a person types it, or a MoneyError naming what was typed.

    The parsing lives in the kernel, because a budget on a project is typed
    the same way as a quote line; this keeps the sales module's own error.
    """
    try:
        return figures.parse_decimal(value)
    except ValueError as exc:
        raise MoneyError(str(exc)) from None


def money(value: object) -> Decimal:
    """A figure as a two-decimal Decimal, never a float.

    Floats cannot represent 0.1 and an invoice built from them is out by a
    centime often enough to be noticed by exactly the person who should not
    have to notice it.
    """
    return parse_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def parse_amount(value: object) -> Decimal:
    """What a money form typed, as cents."""
    return money(value)


def line_amount(quantity: object, unit_price: object) -> Decimal:
    return money(parse_decimal(quantity) * parse_decimal(unit_price))


@dataclass(frozen=True)
class Totals:
    net: Decimal
    vat_rate: Decimal | None
    vat: Decimal
    gross: Decimal

    @property
    def taxed(self) -> bool:
        return self.vat_rate is not None


def totals(amounts: Iterable[object], rate: Decimal | None) -> Totals:
    """Add the lines up and apply the rate, or state that none applies.

    `rate` of None is a business not subject to TVA, which is the ordinary
    state of a consultancy under the CHF 100'000 threshold. It is not the same
    as a rate of zero: zero says the supply is exempt or zero-rated, and the
    two require different words on the document.

    VAT is computed on the summed net rather than per line and then added up.
    Per-line rounding drifts: twelve lines each half a centime out is six
    centimes on the total, and the client's accounts payable system will
    compute it the other way and query the difference.
    """
    net = money(sum((Decimal(str(a)) for a in amounts), Decimal(0)))
    if rate is None:
        return Totals(net=net, vat_rate=None, vat=Decimal("0.00"), gross=net)
    rate = Decimal(str(rate))
    vat = money(net * rate / Decimal(100))
    return Totals(net=net, vat_rate=rate, vat=vat, gross=money(net + vat))


def format_chf(value: object) -> str:
    """1234.5 as 1'234.50, the way it is written in Switzerland."""
    amount = money(value)
    sign = "-" if amount < 0 else ""
    whole, _, fraction = f"{abs(amount):.2f}".partition(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"{sign}{"'".join(groups)}.{fraction}"


# --- identifiers ---------------------------------------------------------------


def clean_iban(iban: str) -> str:
    return re.sub(r"\s+", "", (iban or "")).upper()


def iban_is_valid(iban: str) -> bool:
    """The mod-97 check every IBAN carries.

    Worth doing at the point an invoice is raised rather than trusting the
    settings field: a transposed pair of digits in an IBAN produces a document
    that looks right, prints right, and is never paid.
    """
    value = clean_iban(iban)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", value):
        return False
    rotated = value[4:] + value[:4]
    digits = "".join(
        str(ord(c) - 55) if c.isalpha() else c for c in rotated
    )
    return int(digits) % 97 == 1


def is_qr_iban(iban: str) -> bool:
    """Whether this IBAN may carry a QR reference.

    The institution identifier is positions 5 to 9 of a Swiss IBAN. A QR-IBAN
    is issued specifically so the bank can route a structured reference; using
    a QR reference with an ordinary IBAN is rejected, and the two look the same
    to a person reading the printed bill.
    """
    value = clean_iban(iban)
    if len(value) < 9 or value[:2] not in ("CH", "LI"):
        return False
    try:
        return int(value[4:9]) in QR_IID
    except ValueError:
        return False


def mod10_check_digit(digits: str) -> int:
    """The recursive mod-10 carried by every Swiss reference since the ESR."""
    carry = 0
    for char in digits:
        if not char.isdigit():
            raise MoneyError(f"a reference is digits only, got {char!r}")
        carry = _MOD10[carry][int(char)]
    return (10 - carry) % 10


def qr_reference(number: str, *, customer: str = "") -> str:
    """A 27-digit QR reference encoding an invoice number.

    Right-aligned and zero-filled, which is what the standard requires and also
    what makes the reference stable: the same invoice always produces the same
    reference, so a client paying twice does not create two unmatched payments.
    """
    body = re.sub(r"\D", "", f"{customer}{number}")
    if not body:
        raise MoneyError("a QR reference needs at least one digit to encode")
    if len(body) > 26:
        raise MoneyError(
            f"{len(body)} digits will not fit a QR reference, which is 26 plus "
            "a check digit"
        )
    body = body.rjust(26, "0")
    return body + str(mod10_check_digit(body))


def creditor_reference(number: str) -> str:
    """An ISO 11649 reference (RF…), for use with an ordinary IBAN.

    The alternative to a QR reference when the account is not a QR-IBAN, which
    is the common case for a small company that has never asked its bank for
    one. Same purpose: the payment arrives already matched to the invoice.
    """
    body = re.sub(r"[^A-Za-z0-9]", "", number).upper()
    if not body:
        raise MoneyError("a creditor reference needs an invoice number")
    if len(body) > 21:
        raise MoneyError("an ISO 11649 reference carries at most 21 characters")
    rotated = body + "RF00"
    digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rotated)
    check = 98 - int(digits) % 97
    return f"RF{check:02d}{body}"


def format_reference(reference: str) -> str:
    """A reference as it is printed: QR in blocks of five from the right, RF in
    blocks of four from the left. Both are specified, and both exist so a human
    can read a number back over the telephone without losing their place."""
    value = re.sub(r"\s+", "", reference or "")
    if value.startswith("RF"):
        return " ".join(value[i:i + 4] for i in range(0, len(value), 4))
    padded = value.rjust(27, "0") if value else ""
    head = padded[:2]
    rest = padded[2:]
    return " ".join([head] + [rest[i:i + 5] for i in range(0, len(rest), 5)]).strip()


# --- the QR-bill payload --------------------------------------------------------


@dataclass(frozen=True)
class Party:
    """A creditor or a debtor, in the structured form the payload wants."""

    name: str
    street: str = ""
    building: str = ""
    postcode: str = ""
    town: str = ""
    country: str = "CH"

    def lines(self) -> list[str]:
        """Seven lines: address type, name, and the five address fields.

        Structured ('S') rather than combined ('K') throughout. Combined is
        allowed and is being withdrawn; structured is what a bank's own
        validator prefers and what survives a client pasting the address into
        their accounting system.
        """
        if not self.name:
            return ["", "", "", "", "", "", ""]
        if not (self.postcode.strip() and self.town.strip()):
            # The standard makes both mandatory once a party is named. A bill
            # that leaves them out prints perfectly and is refused at the
            # bank, so the refusal is made here, where somebody can see it.
            raise MoneyError(
                f"{self.name} is named on the payment part without a postcode "
                "and a town, which the standard requires. Leave the party out "
                "or complete the address."
            )
        return [
            "S", self.name[:70], self.street[:70], self.building[:16],
            self.postcode[:16], self.town[:35], self.country[:2].upper(),
        ]

    @property
    def empty(self) -> bool:
        return not self.name


EMPTY = Party(name="")


def qr_payload(
    *,
    iban: str,
    creditor: Party,
    amount: Decimal | None,
    currency: str = "CHF",
    debtor: Party = EMPTY,
    reference: str = "",
    message: str = "",
    billing_information: str = "",
) -> str:
    """The text encoded in the Swiss QR Code, as 31 lines.

    The structure is fixed and positional: a missing optional field is an empty
    line, never an omitted one, because the reader counts lines. Getting this
    wrong produces a QR code that scans perfectly and is refused at the bank,
    which is the worst failure available here since it is invisible until a
    client tries to pay.

    The reference type is derived rather than asked for. It is decided entirely
    by the shape of the reference and the kind of IBAN, so making it a
    parameter would only create the opportunity for the two to disagree.
    """
    account = clean_iban(iban)
    if not iban_is_valid(account):
        raise MoneyError(
            f"{iban!r} does not pass the IBAN check digits. A QR-bill with a "
            "wrong IBAN prints correctly and is never paid."
        )
    if currency not in ("CHF", "EUR"):
        raise MoneyError("a Swiss QR-bill is in CHF or EUR")
    if creditor.empty:
        raise MoneyError("a QR-bill needs a creditor")

    reference = re.sub(r"\s+", "", reference or "")
    if not reference:
        kind = "NON"
    elif reference.startswith("RF"):
        kind = "SCOR"
    else:
        kind = "QRR"

    if kind == "QRR" and not is_qr_iban(account):
        raise MoneyError(
            "a QR reference requires a QR-IBAN. This account is an ordinary "
            "IBAN, which takes an RF creditor reference or none at all."
        )
    if kind != "QRR" and is_qr_iban(account):
        raise MoneyError(
            "a QR-IBAN must carry a QR reference. The bank will not accept it "
            "with an RF reference or without one."
        )
    if kind == "QRR":
        if len(reference) != 27 or not reference.isdigit():
            raise MoneyError("a QR reference is exactly 27 digits")
        if mod10_check_digit(reference[:26]) != int(reference[26]):
            raise MoneyError("the QR reference check digit is wrong")

    if amount is not None:
        amount = money(amount)
        if not (Decimal("0.01") <= amount <= Decimal("999999999.99")):
            raise MoneyError(f"{amount} is outside what a QR-bill can carry")

    lines = [
        "SPC",                    # 1  QRType
        "0200",                   # 2  Version 2.0
        "1",                      # 3  Coding type: UTF-8
        account,                  # 4  IBAN
    ]
    lines += creditor.lines()     # 5-11  Creditor
    lines += EMPTY.lines()        # 12-18 Ultimate creditor: reserved, empty
    lines += [
        f"{amount:.2f}" if amount is not None else "",   # 19 Amount
        currency,                                        # 20 Currency
    ]
    lines += debtor.lines()       # 21-27 Ultimate debtor
    lines += [
        kind,                     # 28 Reference type
        reference,                # 29 Reference
        message[:140],            # 30 Unstructured message
        "EPD",                    # 31 Trailer
    ]
    if billing_information:
        lines.append(billing_information[:140])
    return "\n".join(lines)


def qr_svg(payload: str, *, size: int = 46) -> str:
    """The Swiss QR Code as inline SVG, cross and all.

    Error correction level M and the Swiss cross in the middle are both
    prescribed; the cross is what distinguishes a payment QR from any other and
    a reader uses it to decide how to parse what it found. `size` is in
    millimetres and the standard says 46 by 46, which is why it is the default
    rather than something the caller has to know.

    Kept as SVG rather than a raster: it goes into an HTML page that a browser
    prints, and a bitmap scaled to 46 mm by a print stylesheet is a QR code
    that a phone camera struggles with at exactly the moment it matters.
    """
    import segno

    code = segno.make(payload, error="m", encoding="utf-8")
    modules = code.symbol_size(border=0)[0]

    # The dark modules as one rect per horizontal run, in module units, so the
    # whole thing is one coordinate system: the viewBox is the module grid and
    # the millimetres are declared once on the root. Building the geometry
    # rather than wrapping segno's own <svg> is what makes the cross placeable:
    # a nested svg carries its own viewBox and cannot be drawn on top of.
    rects: list[str] = []
    for y, row in enumerate(code.matrix_iter(border=0)):
        run = 0
        for x, dark in enumerate(list(row) + [0]):
            if dark:
                run += 1
                continue
            if run:
                rects.append(
                    f'<rect x="{x - run}" y="{y}" width="{run}" height="1"/>'
                )
                run = 0

    # The Swiss cross, 7 mm on a 46 mm code, centred: a white square, a black
    # square inside it, and a white cross in Swiss flag proportions. It covers
    # modules on purpose, which is why the code is made at error correction M:
    # the standard prescribes both, and the cross is how a reader knows it has
    # found a payment code rather than a URL.
    side = modules * Decimal(7) / Decimal(46)
    mid = Decimal(modules) / 2
    inner = side * Decimal("0.92")
    arm = side * Decimal("0.60")
    thick = side * Decimal("0.20")

    def box(width: Decimal, height: Decimal, fill: str) -> str:
        return (
            f'<rect x="{mid - width / 2:.3f}" y="{mid - height / 2:.3f}" '
            f'width="{width:.3f}" height="{height:.3f}" fill="{fill}"/>'
        )

    cross = (
        box(side, side, "#fff") + box(inner, inner, "#000")
        + box(arm, thick, "#fff") + box(thick, arm, "#fff")
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" class="qr" '
        f'viewBox="0 0 {modules} {modules}" '
        f'width="{size}mm" height="{size}mm" shape-rendering="crispEdges">'
        f'<rect width="{modules}" height="{modules}" fill="#fff"/>'
        f'<g fill="#000">{"".join(rects)}</g>{cross}</svg>'
    )
