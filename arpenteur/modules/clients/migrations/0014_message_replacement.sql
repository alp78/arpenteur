-- The message a wave sent again, to an address a person accepted after the
-- first one bounced. The wave's own draft stays what it was, the record of
-- what was sent and refused; the replacement is a second draft, with the same
-- words, to the accepted address, and at most one per message.
ALTER TABLE crm.campaign_message
    ADD COLUMN IF NOT EXISTS replacement_draft_id BIGINT
        REFERENCES intel.draft (id) ON DELETE SET NULL;
