-- Arpenteur schema, version 11: what a run said while it was running.
--
-- The `run` table already records that a stage started, finished, and what it
-- produced. That is the right shape for a history and the wrong shape for
-- watching: a hydration of sixty thousand companies is one row that says
-- "running" for eight hours.
--
-- Every stage already takes an injectable `log` callable, precisely so the
-- sink can be swapped. Sending it here gives the console the step-by-step
-- view without touching a single stage's internals.
--
-- A table rather than an in-memory buffer, for three reasons. The log
-- survives a page reload and a console restart, which is the difference
-- between a progress view and a toy. A run started from the command line can
-- write here too, so both entry points are watchable. And the console can
-- stay with polling, which it already chose deliberately: one indexed query
-- against SQLite, no hanging connection when a run dies.
--
-- `seq` rather than ordering by id: a reader asks for everything after the
-- last line it has, and per-run numbering makes that a range scan on the
-- index rather than a filter over the whole table.
CREATE TABLE IF NOT EXISTS run_line (
    id     INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES run (id) ON DELETE CASCADE,
    seq    INTEGER NOT NULL,
    at     TEXT NOT NULL,
    text   TEXT NOT NULL,
    UNIQUE (run_id, seq)
);

CREATE INDEX IF NOT EXISTS ix_run_line_run ON run_line (run_id, seq);

-- Who asked for the run. A run started from the console is a person clicking
-- a button, one from the CLI is a person typing; the difference matters when
-- reading back an expensive afternoon.
ALTER TABLE run ADD COLUMN started_by TEXT;
