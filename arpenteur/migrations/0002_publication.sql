-- Arpenteur schema, version 2: the SHAB publication record.
--
-- Zefix carries no officer field and no email. It does carry sogcPub, the
-- Feuille officielle suisse du commerce notices filed on every mutation, and
-- those name people: "Associé-gérant: Turbé Christian, de France, à Genève,
-- avec signature individuelle". Measured on 11.08.2026, 22 of 25 sampled
-- companies had officer vocabulary in this text. It is the only officer
-- source that arrives with the data we already fetch.
--
-- The raw message is stored rather than only the parsed result. The parser
-- will get better, and reparsing 183k stored strings costs nothing while
-- re-fetching them costs a day of polite requests.

CREATE TABLE IF NOT EXISTS publication (
    id            INTEGER PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    -- The SHAB publication id. Unique per notice and stable, so a re-hydrate
    -- updates a row rather than duplicating it.
    sogc_id       INTEGER,
    sogc_date     TEXT,
    -- The cantonal register journal behind the notice.
    journal_id    INTEGER,
    journal_date  TEXT,
    canton        TEXT,
    -- The notice itself, as delivered: HTML tags and all. Parsing happens on
    -- read, so a better parser never needs a new fetch.
    message       TEXT NOT NULL,
    -- Provenance.
    source_url    TEXT,
    method        TEXT,
    fetched_at    TEXT,
    UNIQUE (company_id, sogc_id)
);

CREATE INDEX IF NOT EXISTS publication_company_idx ON publication (company_id);
CREATE INDEX IF NOT EXISTS publication_date_idx ON publication (sogc_date);

-- The people stage reads publications and writes person rows. `person` already
-- exists from version 1 and needs no change, but it gains two columns that the
-- SHAB grammar supplies and a cantonal excerpt would not.
ALTER TABLE person ADD COLUMN origin TEXT;
ALTER TABLE person ADD COLUMN domicile TEXT;
-- "signature individuelle", "signature collective à deux", or null. Who can
-- bind the company is exactly the question when deciding whom to approach.
ALTER TABLE person ADD COLUMN signature TEXT;
-- The publication a person was last seen in. Officers change; the newest
-- notice naming someone is the evidence they still hold the role.
ALTER TABLE person ADD COLUMN last_seen_date TEXT;
