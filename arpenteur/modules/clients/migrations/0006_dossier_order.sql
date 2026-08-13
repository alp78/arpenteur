-- The dossier in the order it should be read, not the order it was written.
--
-- A dossier is a stack of sourced sections that an approach is built from and
-- that a person reads before walking into a room. It has been coming back in
-- whatever order Postgres felt like, which for a heap table is roughly the
-- order the rows were written and changes the moment one is updated. So the
-- narrative reordered itself when somebody corrected a typo.
--
-- 100 as the default rather than 0, so a section added later lands at the end
-- among the others rather than fighting for first place, and so there is room
-- to insert between existing values without renumbering everything. The
-- reorder writes dense positions from 1 anyway; the default is for rows that
-- arrive without one.

ALTER TABLE crm.dossier ADD COLUMN IF NOT EXISTS position INTEGER NOT NULL DEFAULT 100;

-- Existing rows keep the order they were being shown in, which is the order
-- they were inserted. Doing this as one statement rather than leaving them all
-- at 100 matters: with every position equal, the tie is broken by whatever the
-- planner does that day, and the first reorder would appear to shuffle
-- sections nobody touched.
WITH ranked AS (
    SELECT id, ROW_NUMBER() OVER (PARTITION BY client_id ORDER BY id) AS n
      FROM crm.dossier
)
UPDATE crm.dossier d SET position = ranked.n
  FROM ranked WHERE ranked.id = d.id;

CREATE INDEX IF NOT EXISTS dossier_order_idx ON crm.dossier (client_id, position);
