-- Which mailbox a draft is for, and which brief it was written from.
--
-- `intel.draft` has existed since the schema did, unreached by any code: the
-- stage printed "not implemented" and nothing read or wrote the table. Two
-- columns are missing before it can be.
--
-- WHY THE ADDRESS AND NOT ONLY THE COMPANY. A firm in this survey holds 2.3
-- addresses on average and the send list picks one of them, preferring a desk
-- over a named person. A draft written for `info@` and sent to
-- `marc.burri@` is a different act under the nLPD, so the row has to name the
-- mailbox it was composed for rather than leave the choice to whoever sends.
--
-- WHY THE BRIEF DIGEST. The same reasoning `match` applies to campaign.md: the
-- message is only as good as the brief, and rewriting the brief invalidates
-- the drafts taken against the old text. Without the digest an old draft and a
-- new one sit together looking equally current.

ALTER TABLE intel.draft
    ADD COLUMN IF NOT EXISTS address TEXT,
    ADD COLUMN IF NOT EXISTS brief_digest TEXT;

-- One draft per company and mailbox, so a second run proposes rather than
-- duplicates. Partial, because a draft already sent is history and a later
-- proposal to the same mailbox is a new act, not a replacement.
CREATE UNIQUE INDEX IF NOT EXISTS draft_one_per_mailbox_idx
    ON intel.draft (company_id, address)
 WHERE sent_at IS NULL AND address IS NOT NULL;

-- The review queue: what is proposed and not yet judged, oldest first.
CREATE INDEX IF NOT EXISTS draft_awaiting_review_idx
    ON intel.draft (created_at)
 WHERE reviewed_at IS NULL AND sent_at IS NULL;
