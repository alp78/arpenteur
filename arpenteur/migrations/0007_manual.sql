-- Arpenteur schema, version 7: a human's judgement outranks the model's.
--
-- Three things arrive together because they are one idea. The pipeline
-- proposes; a person decides; and the decision has to survive the pipeline
-- running again. Without that last part a console is a display, not a tool:
-- correcting a label would feel like work and would be silently undone by the
-- next classification run.

-- Who said so. `model` for anything a stage inferred, `manual` for anything a
-- person set by hand. save_segments refuses to overwrite a manual row, so
-- re-running profile leaves corrections alone.
ALTER TABLE segment ADD COLUMN source TEXT NOT NULL DEFAULT 'model';

-- The follow-up log: calls, meetings, mail, and the thought you had at the
-- time. Free text on purpose. This is the part of a prospect that no register
-- and no model can supply, and the part that is worth most six months later.
CREATE TABLE IF NOT EXISTS note (
    id          INTEGER PRIMARY KEY,
    company_id  INTEGER NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    -- call, meeting, email, note, or whatever a campaign needs. Not a
    -- constrained enum: the vocabulary of a sales pipeline, a recruiting
    -- pipeline and a membership drive have little in common.
    kind        TEXT NOT NULL DEFAULT 'note',
    body        TEXT NOT NULL,
    author      TEXT,
    -- When it happened, which is not when it was typed. A call logged the
    -- next morning belongs on the day of the call.
    occurred_at TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS note_company_idx ON note (company_id, occurred_at);

-- Where a company stands in the campaign. One row per company, created the
-- first time somebody takes an interest, so the absence of a row means
-- nobody has looked rather than "new".
CREATE TABLE IF NOT EXISTS prospect (
    company_id  INTEGER PRIMARY KEY REFERENCES company (id) ON DELETE CASCADE,
    state       TEXT NOT NULL DEFAULT 'new',
    owner       TEXT,
    -- The next thing to do and when. A pipeline without these is a list.
    next_action TEXT,
    next_at     TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS prospect_state_idx ON prospect (state);
CREATE INDEX IF NOT EXISTS prospect_next_idx ON prospect (next_at);
