-- An invoice carries two identifiers, and only one of them reaches the client.
--
-- `number` is the rank: 2026-0001, drawn from the sequence at issue, continuous
-- across clients and exercises. It exists so a control can see that no document
-- was removed. CO art. 957a al. 2 asks for the justification of every entry by a
-- document and the traceability of entries, and a rank is how that question is
-- answered. It stays in the register and is never printed.
--
-- `reference` is what the client quotes: FBG-EXPERTISE. It names the client and
-- the object and says nothing else. A number printed on a document tells its
-- reader how many invoices came before it, which is the issuer's business and
-- not the recipient's, and the first invoice of an exercise announces itself as
-- the first.
--
-- The reference is set when the invoice is drafted, not at issue, because it is
-- how the work is already spoken about before anything is billed. The rank is
-- allocated at issue, because an abandoned draft that had taken one would leave
-- a hole in the series that has to be explained years later.
--
-- A partial unique index rather than a UNIQUE on the column: drafts may be
-- unreferenced while they are being written, and the constraint only has to hold
-- for the rows that carry one. The CHECK closes the other direction, so a row
-- that claims to be issued and carries no reference cannot exist.

ALTER TABLE commercial.invoice ADD COLUMN IF NOT EXISTS reference TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS invoice_reference_idx
    ON commercial.invoice (reference) WHERE reference IS NOT NULL;

ALTER TABLE commercial.invoice DROP CONSTRAINT IF EXISTS invoice_issued_has_reference;
ALTER TABLE commercial.invoice ADD CONSTRAINT invoice_issued_has_reference
    CHECK (status = 'draft' OR reference IS NOT NULL);

COMMENT ON COLUMN commercial.invoice.number IS
    'The rank in the continuous series, allocated at issue. Kept in the register, never printed on the document.';
COMMENT ON COLUMN commercial.invoice.reference IS
    'What the client quotes: the client code and the object, with no date and no rank.';
