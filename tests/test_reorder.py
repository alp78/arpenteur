"""Order as part of the record, and the three ways to change it.

A quote is read top to bottom and the order is part of what it says. The
schema has carried `position` on both line tables since the first commercial
migration and nothing ever set it, so the order was insertion order: whatever
somebody happened to type at half past six.

Two properties are worth more than the rest.

**A document somebody else is holding cannot be rearranged.** A sent quote
version and an issued invoice are refused, and refused on the server. The
buttons are absent and the rows are not draggable, but a refusal that exists
only in the markup is a refusal anybody can post around.

**An id from somewhere else changes nothing.** The order arrives from a
browser as a list of ids, and the scoping is in the WHERE clause rather than in
a check beforehand, so a forged id is inert rather than caught by something
somebody can forget to write next time.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from arpenteur import db, dossier
from arpenteur.config import Territory
from arpenteur.console import create_app
from arpenteur.kernel.db import Connection
from arpenteur.modules.clients import store as crm
from arpenteur.modules.sales import store as sales


@pytest.fixture
def settings(db_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        database_url=db_path,
        territory=Territory(cantons=("GE",), legal_forms=("0107",), name="geneve"),
        segments={"sme-owner": "A small owner-run business."},
        sectors={"legal-accounting": "Fiduciary and accounting work."},
        mail=SimpleNamespace(configured=False),
    )


@pytest.fixture
def conn(settings) -> Connection:
    connection = db.open_db(settings.database_url)
    db.upsert_company(
        connection,
        {"uid": "CHE1", "name": "Bois-Gentil SA", "canton": "GE",
         "legal_seat": "Genève", "status": "ACTIVE", "method": "zefix"},
    )
    db.promote_to_client(connection, 1)
    connection.commit()
    return connection


@pytest.fixture
def client(settings, conn) -> TestClient:
    return TestClient(create_app(settings))


def _quote_with_lines(conn: Connection, labels: list[str]) -> tuple[int, int]:
    quote_id = sales.create_quote(conn, 1)
    version_id = sales.add_version(conn, quote_id)
    for label in labels:
        sales.add_line(conn, version_id, {"label": label, "unit_price": 100})
    conn.commit()
    return quote_id, version_id


def _labels(conn: Connection, version_id: int) -> list[str]:
    return [row["label"] for row in sales.lines_for(conn, version_id)]


# --- quote lines --------------------------------------------------------------


def test_lines_come_back_in_the_order_they_were_put_in(conn) -> None:
    _quote, version_id = _quote_with_lines(conn, ["Relevé", "Plan", "Déplacements"])
    ids = [row["id"] for row in sales.lines_for(conn, version_id)]
    sales.reorder_lines(conn, version_id, [ids[2], ids[0], ids[1]])
    conn.commit()
    assert _labels(conn, version_id) == ["Déplacements", "Relevé", "Plan"]


def test_positions_are_dense_from_one_rather_than_patched(conn) -> None:
    """Half an order is not a state worth being able to reach."""
    _quote, version_id = _quote_with_lines(conn, ["a", "b", "c"])
    ids = [row["id"] for row in sales.lines_for(conn, version_id)]
    sales.reorder_lines(conn, version_id, list(reversed(ids)))
    conn.commit()
    assert [row["position"] for row in sales.lines_for(conn, version_id)] == [1, 2, 3]


def test_a_line_from_another_quote_cannot_renumber_this_one(conn) -> None:
    _one, first = _quote_with_lines(conn, ["a", "b"])
    _two, second = _quote_with_lines(conn, ["x", "y"])
    stranger = sales.lines_for(conn, second)[0]["id"]
    moved = sales.reorder_lines(conn, first, [stranger])
    conn.commit()
    assert moved == 0
    assert _labels(conn, second) == ["x", "y"]


def test_rubbish_in_the_order_is_dropped_rather_than_raising(conn) -> None:
    _quote, version_id = _quote_with_lines(conn, ["a", "b"])
    ids = [row["id"] for row in sales.lines_for(conn, version_id)]
    assert sales.reorder_lines(conn, version_id, ["not-a-number", ids[1]]) == 1


# --- through the console ------------------------------------------------------


def test_the_route_reorders_and_the_page_shows_it(client, conn) -> None:
    quote_id, version_id = _quote_with_lines(conn, ["Relevé", "Plan", "Frais"])
    ids = [row["id"] for row in sales.lines_for(conn, version_id)]
    reply = client.post(
        f"/quote-version/{version_id}/order",
        data={"order": [str(ids[1]), str(ids[2]), str(ids[0])]},
    )
    assert reply.status_code == 200
    page = client.get(f"/quote/{quote_id}").text
    assert page.index("Plan") < page.index("Frais") < page.index("Relevé")


def test_a_sent_version_refuses_to_be_rearranged(client, conn) -> None:
    """What the client is holding has to go on saying what it said."""
    quote_id, version_id = _quote_with_lines(conn, ["Relevé", "Plan"])
    sales.mark_sent(conn, version_id, to="direction@bois-gentil.ch")
    conn.commit()
    ids = [row["id"] for row in sales.lines_for(conn, version_id)]

    reply = client.post(
        f"/quote-version/{version_id}/order",
        data={"order": [str(ids[1]), str(ids[0])]},
    )
    assert reply.status_code == 409
    assert "holding" in reply.json()["error"]
    assert _labels(conn, version_id) == ["Relevé", "Plan"]


def test_a_sent_version_offers_no_control_that_would_rearrange_it(
    client, conn
) -> None:
    quote_id, version_id = _quote_with_lines(conn, ["Relevé", "Plan"])
    sales.mark_sent(conn, version_id, to="x@y.ch")
    conn.commit()
    page = client.get(f"/quote/{quote_id}").text
    assert "data-sort-up" not in page
    assert 'draggable="true"' not in page
    assert "keep the order the client received them in" in page


def test_an_issued_invoice_refuses_to_be_rearranged(client, conn) -> None:
    invoice_id = sales.create_invoice(conn, 1, reference=sales.next_invoice_reference(conn, 1, "work"))
    for label in ("Relevé", "Plan"):
        sales.add_invoice_line(conn, invoice_id, {"label": label, "unit_price": 100})
    sales.recompute_invoice(conn, invoice_id, vat_rate=8.1)
    sales.issue_invoice(conn, invoice_id, issued_on=date(2026, 8, 13),
                        qr_iban="CH4431999123000889012")
    conn.commit()
    ids = [r["id"] for r in sales.invoice_lines(conn, invoice_id)]

    reply = client.post(f"/invoice/{invoice_id}/order",
                        data={"order": [str(ids[1]), str(ids[0])]})
    assert reply.status_code == 409
    assert "credit note" in reply.json()["error"]


def test_a_draft_invoice_can_be_rearranged(client, conn) -> None:
    invoice_id = sales.create_invoice(conn, 1, reference=sales.next_invoice_reference(conn, 1, "work"))
    for label in ("Relevé", "Plan"):
        sales.add_invoice_line(conn, invoice_id, {"label": label, "unit_price": 100})
    conn.commit()
    ids = [r["id"] for r in sales.invoice_lines(conn, invoice_id)]
    reply = client.post(f"/invoice/{invoice_id}/order",
                        data={"order": [str(ids[1]), str(ids[0])]})
    assert reply.status_code == 200
    assert [r["label"] for r in sales.invoice_lines(conn, invoice_id)] == [
        "Plan", "Relevé",
    ]


def test_reordering_a_quote_that_does_not_exist_is_a_404(client) -> None:
    assert client.post("/quote-version/999/order", data={"order": []}).status_code == 404


# --- dossier sections ---------------------------------------------------------


def _group(name: str) -> list[str]:
    return [s.name for s in dossier.SECTIONS if s.group == name]


def test_a_section_that_was_never_filled_in_can_still_be_moved(conn) -> None:
    """It is on the page and it is draggable, so it has to have a position.

    Keyed by name rather than by row id for exactly this reason: an empty
    section has no row, and a control that worked only after you had typed
    something into the box would be a control nobody could find a rule for.
    """
    names = _group("organisation")
    moved = crm.reorder_dossier(
        conn, 1, list(reversed(names)), known=[s.name for s in dossier.SECTIONS]
    )
    conn.commit()
    assert moved == len(names)
    held = crm.dossier_for(conn, 1)
    assert [s.name for s in dossier.group_sections("organisation", held)] == \
        list(reversed(names))


def test_a_position_alone_does_not_make_a_section_look_filled_in(conn) -> None:
    crm.reorder_dossier(conn, 1, _group("organisation"),
                        known=[s.name for s in dossier.SECTIONS])
    conn.commit()
    assert dossier.filled(crm.dossier_for(conn, 1)) == []


def test_a_name_that_is_not_a_section_stores_nothing(conn) -> None:
    moved = crm.reorder_dossier(
        conn, 1, ["situation", "not-a-section"],
        known=[s.name for s in dossier.SECTIONS],
    )
    conn.commit()
    assert moved == 1
    assert set(crm.dossier_for(conn, 1)) == {"situation"}


def test_the_order_reaches_what_the_model_reads(conn) -> None:
    """The point of the whole thing. If it only moved boxes on a page it would
    be decoration."""
    names = _group("organisation")
    crm.reorder_dossier(conn, 1, list(reversed(names)),
                        known=[s.name for s in dossier.SECTIONS])
    conn.commit()
    text = dossier.as_text(crm.dossier_for(conn, 1))
    titles = [line[3:] for line in text.splitlines() if line.startswith("## ")]
    first_group = [dossier.BY_NAME[n].title for n in reversed(names)]
    assert titles[: len(first_group)] == first_group


def test_sections_never_leave_the_group_that_explains_them(conn) -> None:
    """The board refuses drops across a swimlane for the same reason."""
    crm.reorder_dossier(conn, 1, list(reversed(_group("organisation"))),
                        known=[s.name for s in dossier.SECTIONS])
    conn.commit()
    held = crm.dossier_for(conn, 1)
    seen = [s.group for s in dossier.ordered_sections(held)]
    assert seen == sorted(seen, key=lambda g: [n for n, _t, _w in dossier.GROUPS].index(g))


def test_an_untouched_dossier_reads_in_the_order_the_page_shows_it(conn) -> None:
    """The text and the form now agree, which they did not before.

    `SECTIONS` is written with the groups interleaved, and `as_text` walked it
    in file order while the form has always drawn the three groups in turn. So
    the dossier an agent read and the dossier a person filled in were in
    different orders, and nothing said so. Both are grouped now.
    """
    grouped = [s.name for s in dossier.ordered_sections({})]
    assert grouped == [
        s.name for name, _t, _w in dossier.GROUPS
        for s in dossier.SECTIONS if s.group == name
    ]
    assert sorted(grouped) == sorted(s.name for s in dossier.SECTIONS)


def test_the_route_reorders_a_group(client, conn) -> None:
    names = _group("organisation")
    reply = client.post(f"/dossier/1/order", data={"order": list(reversed(names))})
    assert reply.status_code == 200
    assert reply.json()["sections"] == len(names)
