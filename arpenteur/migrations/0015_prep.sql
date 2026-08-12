-- Arpenteur schema, version 15: what to revise before walking into the room.
--
-- A client's file accumulates vocabulary faster than anyone can absorb it.
-- Fondation Bois-Gentil alone carries DMARC, SPF, DKIM, FortiMail, Exchange
-- Online, VMware Cloud Director, nLPD art. 5 let. c, ISO 9001:2015, ISO 27001,
-- SOMED, DGCS, CIVESS, CEESV, Medidata, EMS, EPSM, CAT, Sadies DIR, Opale,
-- Syselcom, Infomaniak and Digital 2030. Some of those you know cold, some you
-- half know, and the difference matters in a meeting where their own IT expert
-- is in the room. This is the list, sorted by what to look at first.
--
-- Two tables, and the split is the point.
--
-- THE BRIEFING IS VERSIONED, THE GLOSSARY IS CUMULATIVE. Regenerating after
-- the client sends their état des lieux must not reset what you have already
-- learned. So each generation is a `briefing` row, while the terms live in
-- `glossary` keyed on the client and the term itself: a new run adds what is
-- new and refreshes what it already knew, and never touches `known` or `note`.
--
-- MANUAL TRUTH OUTRANKS THE MODEL, as everywhere else in this schema. `known`
-- and `note` are the person's, and no regeneration overwrites them. It is the
-- same rule that keeps a hand-set segment label from being erased by a re-run,
-- applied to the one column here a person owns.

-- One generation of the study material.
CREATE TABLE IF NOT EXISTS briefing (
    id           INTEGER PRIMARY KEY,
    client_id    INTEGER NOT NULL REFERENCES client (id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL,
    model        TEXT,
    -- Which effort level produced it. Recorded because the same prompt at two
    -- effort levels is two different answers at two different prices, and a
    -- run that disappointed should be answerable with "it ran at low".
    effort       TEXT,
    inputs_hash  TEXT,
    read_json    TEXT,
    -- The orientation: what this client's world is, in a few sentences, for
    -- someone who has read the dossier once and needs the shape of it.
    orientation  TEXT,
    -- What to revise first, in order, with why. Distinct from the glossary:
    -- twenty terms is a list, and three hours before a meeting you need to
    -- know which four of them decide the conversation.
    agenda_json  TEXT
);

CREATE INDEX IF NOT EXISTS ix_briefing_client ON briefing (client_id, created_at DESC);

-- One term, acronym, technology or topic, and what to know about it.
CREATE TABLE IF NOT EXISTS glossary (
    id          INTEGER PRIMARY KEY,
    client_id   INTEGER NOT NULL REFERENCES client (id) ON DELETE CASCADE,
    -- The term as it appears in the file: DMARC, nLPD, Sadies DIR, Digital 2030.
    term        TEXT NOT NULL,
    -- regulatory | mail | infrastructure | sector | vendor | governance |
    -- project. A working vocabulary, in `stages/prep.py`, not a constraint
    -- here: retiring a category must not make old rows unreadable.
    category    TEXT,
    -- What the letters stand for, where they stand for something.
    expansion   TEXT,
    -- What it is, plainly, for someone who does not already know.
    what_it_is  TEXT,
    -- Why it appears in THIS client's file. The part a general glossary cannot
    -- give you and the only part that makes the entry worth reading twice.
    why_here    TEXT,
    -- What you must be able to say about it in the room without looking it up.
    must_know   TEXT,
    -- One question it lets you ask. A term you cannot turn into a question is
    -- a term you do not need before Thursday.
    question    TEXT,
    -- Where to check it: a standard number, a legal article, a specification.
    -- Named rather than linked, because a name can be verified and an invented
    -- URL looks exactly like a real one.
    reference   TEXT,
    -- must | should | nice. What decides the order of revision.
    priority    TEXT,
    -- The person's, and never written by a run. See the header.
    known       INTEGER NOT NULL DEFAULT 0,
    note        TEXT,
    first_seen  TEXT NOT NULL,
    updated_at  TEXT,
    briefing_id INTEGER REFERENCES briefing (id) ON DELETE SET NULL,
    UNIQUE (client_id, term)
);

CREATE INDEX IF NOT EXISTS ix_glossary_client ON glossary (client_id, category, term);
