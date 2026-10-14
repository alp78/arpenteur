-- Whether a gazette day was read after it had ended.
--
-- The cantonal registers file through the working day, so a copy of today's
-- gazette taken this morning misses the afternoon. The sync re-asks for today
-- within the day, but a day recorded on the day itself was never re-asked
-- once it had ended: the next run resumed from the day after. This column
-- says which days were read complete; the others are read again, bypassing
-- the cached copy, which was the morning's.
ALTER TABLE intel.sync_day ADD COLUMN IF NOT EXISTS complete BOOLEAN NOT NULL DEFAULT TRUE;
UPDATE intel.sync_day SET complete = (fetched_at::date > sogc_date::date);
