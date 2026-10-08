-- A draft somebody turned down, kept rather than deleted.
--
-- Discarding one used to delete the row. Two things were lost with it: why it
-- was rejected, and the fact that it was. The second is the expensive one,
-- because `draft` selects from the send list and a deleted proposal is
-- indistinguishable from one never written, so the next run proposes the same
-- message to the same mailbox and the reviewer rejects it again.
--
-- WHY NOT A STATUS COLUMN. The table already carries its state in timestamps:
-- `created_at` that it was written, `reviewed_at` that somebody read it,
-- `sent_at` that it left. A fourth state belongs in the same vocabulary, so
-- `refused_at` joins them rather than introducing a parallel status that can
-- disagree with the dates.

ALTER TABLE intel.draft
    ADD COLUMN IF NOT EXISTS refused_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS refused_reason TEXT;

-- The one-per-mailbox index covered unsent drafts, which now includes refused
-- ones: a refusal must block a second identical proposal, not be replaced by
-- it. Rebuilt rather than added to, since a partial index cannot be altered.
DROP INDEX IF EXISTS intel.draft_one_per_mailbox_idx;

CREATE UNIQUE INDEX IF NOT EXISTS draft_one_per_mailbox_idx
    ON intel.draft (company_id, address)
 WHERE sent_at IS NULL AND refused_at IS NULL AND address IS NOT NULL;

-- A refused proposal still has to be findable, or the reason it was refused is
-- as lost as the deleted row was.
CREATE INDEX IF NOT EXISTS draft_refused_idx
    ON intel.draft (refused_at)
 WHERE refused_at IS NOT NULL;
