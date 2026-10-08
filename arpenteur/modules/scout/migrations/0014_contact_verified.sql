-- When an address was last checked, and what the check found.
--
-- `contact.status` has existed since the table did and was NULL on all 12'111
-- rows: `mailcheck.verify` and its `Verdict` were written to fill it and never
-- called from anywhere. So the column is not new, but nothing has ever read a
-- verdict out of it, and the two columns here are what make one trustworthy.
--
-- WHY A DATE IS PART OF THE VERDICT. A domain lapses. `info@livy-immobilien.ch`
-- resolved when it was found and does not resolve now, and nothing in the row
-- distinguished "checked, and it answered" from "never checked". `fetched_at`
-- cannot stand in: it says when the address was observed on a page, which is a
-- different fact and is not refreshed by a check.
--
-- WHY A NOTE RATHER THAN MORE COLUMNS. The useful distinctions are few and they
-- change as the checking does: today MX, A-only, NXDOMAIN and a resolver that
-- did not answer; tomorrow a bounce code from a real send. A column per outcome
-- would be a migration per outcome, and the status itself is what queries read.
--
-- `status` is deliberately not constrained to a value list. A bounce verdict
-- from a send is the next thing to land here, and its vocabulary is not known
-- yet; a CHECK written now would be a migration to undo later.

ALTER TABLE core.contact
    ADD COLUMN IF NOT EXISTS status_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS status_note TEXT;

-- The common read is the send list: everything deliverable and not suppressed.
-- The predicate rides in the index because `suppressed` is false on all but
-- 130 rows, so a plain column index would be scanned almost entirely.
CREATE INDEX IF NOT EXISTS contact_sendable_idx
    ON core.contact (kind, status)
 WHERE NOT suppressed;

-- The other read is "what has gone stale", which orders by the date and wants
-- the rows never checked at all to come first.
CREATE INDEX IF NOT EXISTS contact_status_age_idx
    ON core.contact (status_at NULLS FIRST)
 WHERE kind = 'email';
