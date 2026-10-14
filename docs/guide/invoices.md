# Invoices

Issued, paid, credited. An invoice is a draft until it is issued; issuing gives it the next rank in the register and freezes it. A correction to an issued invoice is a credit note, never an edit.

## The ledger

The page opens on what is still owed and on the total shown. The table lists each invoice with its reference, the client, the state, the total, what is owed, the issue date and the due date, with the days late once overdue. The filters take a search over the reference, the rank or the client, and a state.

## The register

The second tab is the register the law asks for: every issued document in rank order, with what was billed, what was received and what is owed. A rank nobody carries is listed as a gap, to be explained. The register can be downloaded as CSV, with a year filter.

## The invoice's page

**A draft**

Lines are added from the catalogue or with a label, a quantity and a price, and can be removed and reordered. A draft's reference can be changed. Issue takes the next rank and freezes the document; it is refused while the invoice has no line, no reference, or while the identity under Settings has no name or no valid IBAN, because the payment part cannot be drawn without them. A draft that is not wanted is deleted.

**An issued invoice**

The page states the rank and the issue and due dates. The rank never appears on the document; the client sees the reference.

- Record a payment takes the amount received, which must be positive, and moves the invoice to part paid or paid.
- Credit note, with a reason, issues a credit note reversing the invoice. The note takes its own rank, and the invoice is marked credited. An invoice can be credited once, and a credit note cannot be credited.

**The printed document**

The print page carries the identity, the client's address, the reference, the dates, the lines, the terms, and the Swiss QR payment part with its receipt. A credit note prints without a payment part, since it owes nothing.

## The figure for what is owed

What is owed is the issued total less the payments received, and a credit note nets against the invoice it reverses. The ledger, the register and the Reports page agree on the figure because they read the same view.

## Related pages

- [Quotes](quotes.md), from which an invoice can be raised
- [Settings](settings.md), under Identity, for the name and the IBAN the payment part needs
- [Reports](reports.md), for what is billed and received by month
