-- The cantons where a campaign writes every firm in each official language at
-- once. In Fribourg and Valais an agency may work in French or in German, and
-- the guess from its name and canton is the weakest there; one message
-- carrying both versions, the firm's own first, cannot be sent in the wrong
-- one. Empty for a campaign that writes each firm in its own language only.
ALTER TABLE crm.campaign
    ADD COLUMN IF NOT EXISTS bilingual TEXT[] NOT NULL DEFAULT '{}';
