-- Arpenteur schema, version 5: keeping the local copy current.
--
-- The register is 183'361 companies here and asking each one whether it has
-- changed is 183'361 requests, against a service that asks users to renounce
-- mass querying. It is also unnecessary. Nothing changes in the commercial
-- register without a FOSC notice, and /api/v1/sogc/bydate/{date} returns a
-- whole day of them for the whole country in one request: 960 notices on
-- 10.08.2026. That is the change log, and it makes the local copy a
-- subscription rather than a poll.
--
-- Idempotency is the property that matters. Running the same range twice must
-- insert nothing, change nothing, and cost the same as running it once, so
-- that a cron job, a retry after a crash and an impatient operator all
-- converge on the same database.

-- Which days have been consumed. The anchor: a day recorded here is done, so
-- a resumed run knows where to start and a repeated run knows to stop.
CREATE TABLE IF NOT EXISTS sync_day (
    sogc_date     TEXT PRIMARY KEY,
    notices       INTEGER NOT NULL DEFAULT 0,
    -- Notices concerning companies already held locally, as opposed to the
    -- other cantons and legal forms this survey does not cover.
    tracked       INTEGER NOT NULL DEFAULT 0,
    inserted      INTEGER NOT NULL DEFAULT 0,
    updated       INTEGER NOT NULL DEFAULT 0,
    deletions     INTEGER NOT NULL DEFAULT 0,
    fetched_at    TEXT
);

-- A digest of the registry fields, provenance and timestamps excluded. Lets a
-- re-read be recognised as a re-read: without it every upsert looks like a
-- change, "companies updated" counts re-reads, and there is no way to tell a
-- quiet day from a broken sync.
ALTER TABLE company ADD COLUMN content_hash TEXT;

-- Set when a notice says a company changed, cleared when the detail call has
-- been made. The work queue: hydration reads it, so a mutation is refetched
-- once and only once, however many times the sync runs.
ALTER TABLE company ADD COLUMN stale_since TEXT;

-- The FOSC date of the most recent notice seen for this company. Cheap
-- recency, and the ordering that decides which stale company to refresh first
-- when a run is capped.
ALTER TABLE company ADD COLUMN last_mutation_date TEXT;

CREATE INDEX IF NOT EXISTS company_stale_idx ON company (stale_since);
