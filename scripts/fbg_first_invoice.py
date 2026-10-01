"""Put the first invoice of the practice into the register.

Written as a script and not typed into a form, so the row that opens the series
is reproducible and says where each of its values came from. Idempotent: run it
twice and the second run reports what it found rather than raising a second rank.

    uv run python scripts/fbg_first_invoice.py [--commit]

Everything here comes from two documents. The adjudication of 18.09.2026, held at
FBG/00-documents-fbg/correspondance, names the lot, the amount and the billing
address. The invoice itself, FACTURE-FBG-EXPERTISE-2026-10-05.pdf, fixes the
reference, the issue date and the thirty-day term.
"""
from __future__ import annotations

import sys
from datetime import date

from arpenteur import db
from arpenteur.modules.clients import store as clients
from arpenteur.modules.sales import store as sales

REFERENCE = "FBG-EXPERTISE"
CODE = "FBG"
ISSUED_ON = date(2026, 10, 5)
DAYS = 30
AMOUNT = 3200
# The BCV account the QR-facture is drawn on. An ordinary IBAN, institution
# 00767, so the payment part takes no structured reference.
IBAN = "CH25 0076 7000 R570 7595 3"


def main() -> int:
    commit = "--commit" in sys.argv
    conn = db.open_db(None)

    client = conn.execute(
        "SELECT id, code, status FROM crm.client WHERE code IN ('FBG', 'BOIS-GENTIL')"
    ).fetchone()
    if client is None:
        print("no client for the Fondation. Import it first: arpenteur import <UID> --client")
        return 1
    client_id = int(client["id"])

    if client["code"] != CODE or client["status"] != "active":
        clients.update_client(conn, client_id, code=CODE, status="active")
        print(f"client {client['code']}/{client['status']} -> {CODE}/active")
    else:
        print(f"client {CODE} already active")

    project = conn.execute(
        "SELECT id, code FROM crm.project WHERE client_id = %s AND code LIKE %s",
        (client_id, f"{CODE}-%"),
    ).fetchone()
    if project is None:
        project_id = clients.add_project(
            conn, client_id, "Digital 2030, mandat d'expertise indépendante",
            status="active", billing="fixed", budget=AMOUNT,
            summary=(
                "Avis indépendant sur la refonte du système d'information, "
                "étapes 1 à 4. Offre PER-2026-FBG-AMO du 15.09.2026, adjugée "
                "le 18.09.2026."
            ),
            started_at="2026-09-18",
        )
        made = conn.execute(
            "SELECT code FROM crm.project WHERE id = %s", (project_id,)
        ).fetchone()
        print(f"project created: {made['code']}")
    else:
        project_id = int(project["id"])
        print(f"project {project['code']} already there")

    held = conn.execute(
        "SELECT id, number, status FROM commercial.invoice WHERE reference = %s",
        (REFERENCE,),
    ).fetchone()
    if held is not None:
        print(f"invoice {REFERENCE} already in the register: "
              f"rank {held['number']}, {held['status']}")
        conn.rollback() if not commit else conn.commit()
        conn.close()
        return 0

    invoice_id = sales.create_invoice(
        conn, client_id, project_id=project_id, reference=REFERENCE,
        note="Partie paiement produite par BCV-net, type de référence NON.",
    )
    sales.add_invoice_line(conn, invoice_id, {
        # Les mots de l'offre PER-2026-FBG-AMO : son sous-titre nomme le travail,
        # son chapitre « Livrables » énumère ce qui a été remis.
        "label": "Revue des travaux préparatoires à la refonte du système d'information",
        "detail": ("Analyse détaillée de l'état des lieux du système d'information\n"
                   "Analyse du cahier des charges stratégique\n"
                   "Avis motivé sur les six chantiers et les cinq principes directeurs\n"
                   "Revue et correction du cahier des charges de consultation"),
        "unit": "fixed", "quantity": 1, "unit_price": AMOUNT,
    })
    # No VAT rate rather than a rate of zero: not registered, and the invoice
    # says so in words.
    sales.recompute_invoice(conn, invoice_id, vat_rate=None)
    issued = sales.issue_invoice(
        conn, invoice_id, issued_on=ISSUED_ON, days=DAYS, qr_iban=IBAN,
        # BCV-net produces the payment part with reference type NON, so there is
        # no structured reference to record.
        with_reference=False, by="script",
    )
    print(f"issued: rank {issued['number']}, reference {issued['reference']}, "
          f"{issued['currency']} {issued['gross_total']}, "
          f"due {issued['due_on']}, account {issued['qr_iban']}, "
          f"payment reference {issued['qr_reference'] or 'none, the bank draws it'}")

    if commit:
        conn.commit()
        print("committed")
    else:
        conn.rollback()
        print("rolled back. Pass --commit to keep it.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
