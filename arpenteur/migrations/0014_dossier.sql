-- Arpenteur schema, version 14: what is known about a client, what to propose
-- to them, and the papers they send.
--
-- The register says what a company is. The dossier says what it runs, who
-- decides, what is exposed and what has already been tried, and none of that
-- is in any register: it is assembled from public evidence, from the client's
-- own documents and from what was said in the room.
--
-- Four decisions, each taken against the obvious alternative.
--
-- SECTIONS, NOT COLUMNS. A fixed schema of forty fields fits the first client
-- and is wrong for the second: a care foundation has a mail estate and a
-- resident record, a manufacturer has a shop floor and a stock system. So the
-- dossier is one row per named section, with a fixed vocabulary of sections in
-- `arpenteur/dossier.py` and free text inside each. The vocabulary can grow
-- without a migration and an old section survives being retired.
--
-- PROVENANCE PER SECTION. Every section carries where it came from, in the
-- operator's own words: "public DNS, 12.08.2026", "said on the call", "rapport
-- annuel p. 12". The distinction between observed, reported and inferred is
-- the difference between a diagnostic and a guess, and it is exactly what gets
-- lost when notes are one long textarea.
--
-- ADVICE IS VERSIONED, NEVER OVERWRITTEN. A proposal is generated against a
-- dossier at a moment. When the client asks in September why March said
-- CHF 4'800, the answer has to still exist, along with what it was computed
-- from. So a new suggestion is a new `approach` row, carrying a digest of its
-- inputs the way a `fit` row carries the brief it was scored against.
--
-- DOCUMENTS ARE CONTENT-ADDRESSED, AND EXTRACTED ONCE. The file on disk is
-- named by its sha256, so the same document sent twice is one file and a
-- renamed copy is visibly the same document rather than a second one. The text
-- is extracted at upload and stored, rather than re-extracted per read:
-- extraction fails on scanned paper, and a failure recorded once is answerable
-- where one that happens silently every time an agent reads is not.

-- One named part of what is known about a client.
CREATE TABLE IF NOT EXISTS dossier (
    id         INTEGER PRIMARY KEY,
    client_id  INTEGER NOT NULL REFERENCES client (id) ON DELETE CASCADE,
    -- From dossier.SECTIONS. Not constrained here on purpose: retiring a
    -- section from the vocabulary must not make old rows unreadable.
    section    TEXT NOT NULL,
    body       TEXT,
    -- Where it came from, and when. Free text because the honest answer is
    -- sometimes "the receptionist mentioned it" and no enum has that.
    source     TEXT,
    author     TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (client_id, section)
);

CREATE INDEX IF NOT EXISTS ix_dossier_client ON dossier (client_id);

-- A suggested approach: what to discuss, what to offer, what it costs.
CREATE TABLE IF NOT EXISTS approach (
    id             INTEGER PRIMARY KEY,
    client_id      INTEGER NOT NULL REFERENCES client (id) ON DELETE CASCADE,
    created_at     TEXT NOT NULL,
    -- model | manual. A suggestion written by hand is a first-class row, not
    -- an edit of a generated one, for the same reason a manual segment label
    -- is: a person's judgement must be distinguishable from a model's
    -- afterwards, and permanently.
    source         TEXT NOT NULL DEFAULT 'model',
    model          TEXT,
    -- What it was computed from, so a proposal made against a dossier that has
    -- since moved is findable rather than merely old. Same device as
    -- fit.brief_hash.
    inputs_hash    TEXT,
    -- The named inputs themselves: which sections were filled, which documents
    -- were read, whether a rate card was present. An approach that priced
    -- three days of work having read nothing is a different object from one
    -- that read the accounts, and the difference must be visible.
    read_json      TEXT,
    situation      TEXT,
    opening        TEXT,
    points_json    TEXT,
    services_json  TEXT,
    pricing_json   TEXT,
    risks_json     TEXT,
    questions_json TEXT,
    -- A human took this and used it. Generated advice nobody acted on is not
    -- the same thing as advice that went into a proposal, and only one of the
    -- two should be quoted back later.
    accepted_at    TEXT,
    note           TEXT
);

CREATE INDEX IF NOT EXISTS ix_approach_client ON approach (client_id, created_at DESC);

-- A document the client sent, or one gathered about them.
CREATE TABLE IF NOT EXISTS attachment (
    id            INTEGER PRIMARY KEY,
    client_id     INTEGER NOT NULL REFERENCES client (id) ON DELETE CASCADE,
    -- Nullable: a document can belong to the relationship rather than to any
    -- one project, and forcing a project on it would invent an association.
    project_id    INTEGER REFERENCES project (id) ON DELETE SET NULL,
    -- As the client named it. Kept verbatim because "Rapport 2025 VF (2).pdf"
    -- is how they will refer to it on the phone.
    filename      TEXT NOT NULL,
    media_type    TEXT,
    bytes         INTEGER NOT NULL,
    sha256        TEXT NOT NULL,
    kind          TEXT,
    note          TEXT,
    -- What an agent reads. Null with extract_error set means the file is held
    -- but unreadable, which is a state worth showing rather than hiding.
    text          TEXT,
    text_chars    INTEGER,
    extracted_at  TEXT,
    extract_error TEXT,
    uploaded_by   TEXT,
    created_at    TEXT NOT NULL,
    -- The same document uploaded twice is one row. Per client rather than
    -- globally: two clients may legitimately hold the same standard contract,
    -- and merging them across a client boundary would be a confidentiality
    -- failure, not a deduplication.
    UNIQUE (client_id, sha256)
);

CREATE INDEX IF NOT EXISTS ix_attachment_client ON attachment (client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_attachment_project ON attachment (project_id);
