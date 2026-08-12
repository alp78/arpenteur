-- Arpenteur schema, version 12: the work that follows a yes.
--
-- Everything before this stage is about finding companies. This is about the
-- ones that answered, and it is a different job: a client has projects, a
-- project has a history, and the history has to survive an audit rather than
-- merely jog a memory.
--
-- Three decisions, each taken against how the established tools do it.
--
-- ONE EVENT LOG, NOT TWO. The comparison of professional-services tools makes
-- the same point repeatedly: the compliance audit trail and the narrative
-- client timeline are the same stream of events rendered for two audiences.
-- Tools that build them separately drift, and the drift is always discovered
-- during the audit. So there is one `event` table, written once, projected
-- differently: the client page reads it as a story, an export reads it as a
-- record.
--
-- A PLANNED EVENT IS AN EVENT. A meeting next Tuesday and a meeting last
-- Tuesday differ by a date and a state, not by their nature. Keeping them in
-- one table means the calendar and the history are the same query, so a
-- meeting cannot be in the diary and missing from the file.
--
-- NOTHING IS DELETED. `state` carries cancelled; no row is removed. An audit
-- trail with a delete button is a diary.
--
-- The client is a company, not a copy of one. Promoting a prospect keeps the
-- register entry, the officers, the research and the notes already gathered.
-- The comparisons call the alternative the handoff gap: a CRM that stops at
-- "closed won" makes the first day of the engagement a copy-paste job.

-- A company being worked with rather than surveyed.
CREATE TABLE IF NOT EXISTS client (
    id          INTEGER PRIMARY KEY,
    company_id  INTEGER NOT NULL UNIQUE REFERENCES company (id) ON DELETE CASCADE,
    -- Human reference, used on invoices and in conversation: BOIS-GENTIL.
    -- Unique so it can be quoted without ambiguity, and editable because the
    -- generated form is a starting point rather than a decision.
    code        TEXT NOT NULL UNIQUE,
    status      TEXT NOT NULL DEFAULT 'active',
    since       TEXT,
    owner       TEXT,
    -- Standing context: what this relationship is, who decides, how they buy.
    -- Distinct from an event, which is something that happened.
    brief       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT
);

CREATE INDEX IF NOT EXISTS ix_client_status ON client (status);

-- A piece of work for a client. One client may have several, running or past.
CREATE TABLE IF NOT EXISTS project (
    id          INTEGER PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES client (id) ON DELETE CASCADE,
    -- BOIS-GENTIL-01. Its own identifier, because a project outlives the
    -- conversation that started it and has to be referable in an invoice, a
    -- contract and a file name.
    code        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'proposed',
    -- How it is paid for. The comparisons single this out as what separates a
    -- tool that fits professional services from one that does not: fixed fee,
    -- time and materials, and retainer are genuinely different arrangements
    -- and a single "budget" number cannot represent all three.
    billing     TEXT,
    budget      REAL,
    currency    TEXT DEFAULT 'CHF',
    summary     TEXT,
    started_at  TEXT,
    due_at      TEXT,
    closed_at   TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT
);

CREATE INDEX IF NOT EXISTS ix_project_client ON project (client_id, status);

-- Everything that happened, or is going to. The audit trail and the timeline
-- and the calendar, which are one thing seen from three angles.
CREATE TABLE IF NOT EXISTS event (
    id          INTEGER PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES client (id) ON DELETE CASCADE,
    -- Nullable: an event can concern the relationship rather than any one
    -- project, and forcing a project on it would invent an association.
    project_id  INTEGER REFERENCES project (id) ON DELETE SET NULL,
    kind        TEXT NOT NULL,
    -- planned | done | cancelled. A meeting moves from the first to the
    -- second; nothing is ever deleted.
    state       TEXT NOT NULL DEFAULT 'done',
    -- When it happened, or is due to. Future dates are what make this the
    -- calendar as well as the history.
    occurred_at TEXT NOT NULL,
    ends_at     TEXT,
    subject     TEXT,
    body        TEXT,
    -- in | out | null. Meaningful for correspondence, absent for a note.
    direction   TEXT,
    -- The other party: an address, a name, or both.
    with_whom   TEXT,
    -- Who recorded it. Not who it is about.
    author      TEXT,
    -- manual | imported. Provenance, as everywhere else in this schema.
    source      TEXT NOT NULL DEFAULT 'manual',
    -- The mail Message-ID, so importing the same message twice is not two
    -- rows. The established tools all lose this and duplicate correspondence
    -- on re-import; it costs one column to not.
    external_id TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (client_id, external_id)
);

CREATE INDEX IF NOT EXISTS ix_event_client ON event (client_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_event_project ON event (project_id, occurred_at DESC);
-- The calendar reads forward from today across every client at once, so the
-- date leads this index rather than the client.
CREATE INDEX IF NOT EXISTS ix_event_diary ON event (occurred_at) WHERE state = 'planned';
