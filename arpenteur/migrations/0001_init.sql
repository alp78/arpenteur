-- Arpenteur schema, version 1.
--
-- Every table that holds a fact about the outside world carries its own
-- provenance columns: source_url, method, fetched_at. Not a separate
-- provenance table, because the question "where did this come from and when"
-- is asked of single rows, and a join would let a row exist without an answer.
-- Under the nLPD that answer is the difference between a defensible dataset
-- and an indefensible one.

CREATE TABLE IF NOT EXISTS company (
    id                   INTEGER PRIMARY KEY,
    -- The UID (CHE-...) is the federal identifier and the natural key.
    uid                  TEXT NOT NULL UNIQUE,
    chid                 TEXT,
    ehraid               INTEGER,
    name                 TEXT NOT NULL,
    legal_form_uid       TEXT,
    legal_form_label     TEXT,
    legal_seat           TEXT,
    legal_seat_id        INTEGER,
    canton               TEXT,
    status               TEXT,
    -- The registered purpose. The profile stage reads nothing else, so a
    -- company with no purpose text is simply not classifiable yet.
    purpose              TEXT,
    address_json         TEXT,
    capital              REAL,
    capital_currency     TEXT,
    cantonal_excerpt_url TEXT,
    zefix_detail_url     TEXT,
    -- Provenance.
    source_url           TEXT,
    method               TEXT,
    fetched_at           TEXT,
    -- Set once, on first insert: lets a later run tell new registrations from
    -- companies that were simply re-swept.
    first_seen           TEXT NOT NULL,
    -- Null until the CompanyFull hydration succeeds; the sweep uses this to
    -- know what still needs a detail call.
    hydrated_at          TEXT
);

CREATE INDEX IF NOT EXISTS company_canton_idx ON company (canton);
CREATE INDEX IF NOT EXISTS company_form_idx ON company (legal_form_uid);
CREATE INDEX IF NOT EXISTS company_hydrated_idx ON company (hydrated_at);

-- A company can sit in several segments at once: a regulated laboratory is
-- both compliance-sector and data-driven, and pretending otherwise would
-- force a false choice at classification time.
CREATE TABLE IF NOT EXISTS segment (
    company_id    INTEGER NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    segment       TEXT NOT NULL,
    confidence    REAL,
    rationale     TEXT,
    model         TEXT,
    classified_at TEXT NOT NULL,
    PRIMARY KEY (company_id, segment)
);

CREATE INDEX IF NOT EXISTS segment_name_idx ON segment (segment);

-- One row per completed (canton, legal form, prefix) search. This is what
-- makes a sweep resumable: an interrupted run re-reads this table and skips
-- the ground it has already covered, which matters because a full romand
-- sweep is tens of thousands of requests.
CREATE TABLE IF NOT EXISTS sweep (
    canton         TEXT NOT NULL,
    legal_form_uid TEXT NOT NULL,
    prefix         TEXT NOT NULL,
    result_count   INTEGER NOT NULL,
    -- True when the response hit the API cap and the prefix was deepened;
    -- the children carry the actual results.
    deepened       INTEGER NOT NULL DEFAULT 0,
    completed_at   TEXT NOT NULL,
    PRIMARY KEY (canton, legal_form_uid, prefix)
);

-- Stage runs, for "when did this last happen and what did it do".
CREATE TABLE IF NOT EXISTS run (
    id          INTEGER PRIMARY KEY,
    stage       TEXT NOT NULL,
    args_json   TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    stats_json  TEXT,
    error       TEXT
);

-- The tables below are empty in this phase. They are created now so the
-- schema states the whole shape of the pipeline rather than growing it in
-- surprises, and so the provenance discipline is fixed before any personal
-- data is written into them.

-- People come from public sources only: cantonal registry excerpts, which
-- name the officers of a company, and published team or impressum pages.
CREATE TABLE IF NOT EXISTS person (
    id         INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    full_name  TEXT NOT NULL,
    role       TEXT,
    -- Provenance: which excerpt or page, fetched when, by what means.
    source_url TEXT,
    method     TEXT,
    fetched_at TEXT,
    UNIQUE (company_id, full_name, role)
);

-- A resolved point of contact. `method` records how it was obtained
-- (published, pattern, provider) and `status` whether it verified.
CREATE TABLE IF NOT EXISTS contact (
    id          INTEGER PRIMARY KEY,
    person_id   INTEGER REFERENCES person (id) ON DELETE CASCADE,
    company_id  INTEGER NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    value       TEXT NOT NULL,
    status      TEXT,
    source_url  TEXT,
    method      TEXT,
    fetched_at  TEXT,
    -- Honoured before anything is ever drafted or sent.
    suppressed  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (company_id, kind, value)
);

-- Drafts are proposals for a human to read, edit and decide on. `sent_at`
-- stays null unless a person acts; nothing in this pipeline sends by itself.
CREATE TABLE IF NOT EXISTS draft (
    id         INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    person_id  INTEGER REFERENCES person (id) ON DELETE SET NULL,
    language   TEXT,
    subject    TEXT,
    body       TEXT,
    model      TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    sent_at    TEXT
);
