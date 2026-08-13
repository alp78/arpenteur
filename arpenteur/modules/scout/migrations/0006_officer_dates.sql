-- When an officer arrived, and when they left.
--
-- `person` held one date, `last_seen_date`, which answered "how current is
-- this" and not "how long have they been there". Both questions matter and the
-- second is the more useful one commercially: a director appointed six weeks
-- ago is a different conversation from one who has been there since 2011.
--
-- WHY DEPARTED OFFICERS ARE KEPT NOW. They were dropped, on the grounds that
-- recording someone who left is worse than missing them: a draft would be
-- addressed to the wrong person with the federal register cited as the
-- authority. That reasoning is intact and is why `left_date` is a column
-- rather than a deletion: `people_for` returns current officers unless asked
-- otherwise, so nothing that addresses a human sees a departure by accident.
-- What changes is that board history stops being thrown away. "They worked
-- together at a company neither is at now" is a real link, and it was being
-- discarded at parse time.

ALTER TABLE core.person
    ADD COLUMN IF NOT EXISTS first_seen_date DATE,
    ADD COLUMN IF NOT EXISTS left_date DATE;

-- The common read is "who is there now", so the index carries the predicate
-- rather than the column.
CREATE INDEX IF NOT EXISTS person_current_idx
    ON core.person (company_id) WHERE left_date IS NULL;
