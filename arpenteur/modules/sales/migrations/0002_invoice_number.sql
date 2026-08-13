-- An invoice number is allocated when the invoice is issued, not before.
--
-- `number` was NOT NULL, which forces every draft to take a number from the
-- sequence. A draft is a working document: it gets reread, corrected, and
-- sometimes abandoned, and each abandoned one would leave a hole in a series
-- the tax authority expects to be continuous. Explaining a gap years later
-- means explaining a document that no longer exists.
--
-- So the number is null until `issue_invoice` allocates it, and uniqueness is
-- enforced by a partial index over the rows that have one. The check keeps the
-- pair honest in the other direction: a row that claims to be issued and
-- carries no number is a document somebody could send.
--
-- This is a new file rather than an edit to 0001 because a migration that has
-- been applied is immutable: `migrate()` compares checksums and refuses a file
-- that has changed underneath it, which is the property that makes a database
-- and a repository trustworthy about each other.

ALTER TABLE commercial.invoice ALTER COLUMN number DROP NOT NULL;

-- The UNIQUE on the column came with an implicit index covering nulls; a
-- partial one says what is actually meant, which is that issued numbers are
-- unique and drafts are not numbered at all.
ALTER TABLE commercial.invoice DROP CONSTRAINT IF EXISTS invoice_number_key;

CREATE UNIQUE INDEX IF NOT EXISTS invoice_number_idx
    ON commercial.invoice (number) WHERE number IS NOT NULL;

ALTER TABLE commercial.invoice DROP CONSTRAINT IF EXISTS invoice_issued_has_number;
ALTER TABLE commercial.invoice ADD CONSTRAINT invoice_issued_has_number
    CHECK (status = 'draft' OR number IS NOT NULL);

-- What is billed from, printed on the document. Null is a legitimate and
-- common state: a business under the CHF 100'000 threshold is not subject to
-- TVA and its invoices must say so in words rather than showing 0.00 beside a
-- rate, which reads as an error or an exemption claim.
COMMENT ON COLUMN commercial.invoice.vat_rate IS
    'Percentage in force when the work was supplied. NULL means not subject to TVA.';
