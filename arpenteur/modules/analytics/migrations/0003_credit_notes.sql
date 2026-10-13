-- What is still owed, with credit notes netted against their invoices.
--
-- `revenue.outstanding` counted an issued credit note, whose gross is
-- negative, and dropped the invoice it reversed, whose state is `credited`.
-- Crediting an unpaid invoice of 1'000 therefore moved "still owed" from
-- 1'000 to -1'000, a swing of twice the amount. A credit note is never owed:
-- it is left out, and the credited invoice was already out.
--
-- Revenue itself keeps counting the note: a month in which an invoice was
-- reversed earned less, and the negative gross is how the books say so.
--
-- The same conditional as 0001: the view exists only where the money half is
-- installed.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.schemata
                WHERE schema_name = 'commercial') THEN
        EXECUTE $view$
        CREATE OR REPLACE VIEW analytics.revenue AS
        SELECT
            DATE_TRUNC('month', i.issued_on)::DATE         AS month,
            COUNT(*)                                       AS invoices,
            SUM(i.net_total)                               AS net,
            SUM(i.vat_total)                               AS vat,
            SUM(i.gross_total)                             AS gross,
            SUM(i.paid_total)                              AS received,
            COALESCE(SUM(i.gross_total - i.paid_total)
                FILTER (WHERE i.status IN ('issued', 'part_paid', 'overdue')
                          AND i.credits_id IS NULL), 0)   AS outstanding
          FROM commercial.invoice i
         WHERE i.issued_on IS NOT NULL AND i.status <> 'cancelled'
         GROUP BY 1
        $view$;
    END IF;
END $$;
