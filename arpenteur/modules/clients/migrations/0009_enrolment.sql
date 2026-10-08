-- What a member answered, where that answer is, and what it enrols them in.
--
-- The programme already holds the stage, which says where a firm stands. It
-- does not hold what the firm said, and for this programme the content of the
-- answer is the whole point: a régie that replies « Android, jean@gmail.com »
-- has given the two facts needed to open a closed test track to them, and a
-- stage of 'replied' records neither.
--
-- THESE ARE THE MEMBER'S ATTRIBUTES, NOT A FEEDBACK ENTRY.
-- `crm.programme_feedback` was kept thin on purpose, so that it could not grow
-- into an issue tracker. The platform and the tester's account are read again
-- at every later step, by whoever adds that tester to TestFlight or to the
-- Play track, so they belong where the member is and not in a log that has to
-- be searched for the latest line.
--
-- THE TESTER'S ACCOUNT NEVER ENTERS `core.contact`.
-- The survey bank holds addresses taken from public registers and public
-- websites. This one is different in kind: a named person volunteered it, for
-- one purpose, in a reply. Holding it on the member means three things that
-- holding it in the bank would not. It is deleted with the programme, since
-- the member cascades. It can never be read by the send list, which selects
-- from `core.contact` alone. Nothing re-harvests it, because no stage writes
-- here.
--
-- `answer_message_id` cites the message rather than copying it. The text is
-- already in `crm.email_message`, imported from the operator's own mailbox and
-- accepted by hand, and a second copy would be the one that drifts.
--
-- `window_ok` is nullable with intent. NULL means nobody has decided whether
-- this tester accepted the dated window, which is the state every answer
-- starts in: the reply names the platform and the account in a form a pattern
-- reads, and accepts or declines the dates in a sentence that it does not.

ALTER TABLE crm.programme_member
    ADD COLUMN platform TEXT
        CHECK (platform IS NULL OR platform IN ('ios', 'android')),
    ADD COLUMN tester_account TEXT,
    ADD COLUMN window_ok BOOLEAN,
    ADD COLUMN answered_at TIMESTAMPTZ,
    ADD COLUMN answer_message_id BIGINT
        REFERENCES crm.email_message (id) ON DELETE SET NULL;

-- Who is ready to be enrolled on a cohort, which is the question asked once a
-- fortnight while twelve Android testers are being gathered.
CREATE INDEX programme_member_enrolled_idx
    ON crm.programme_member (programme_id, platform)
 WHERE platform IS NOT NULL;

COMMENT ON COLUMN crm.programme_member.tester_account IS
    'Volunteered by the tester for enrolment on a store test track. Never '
    'harvested, never copied into core.contact, deleted with the programme.';
