-- Arpenteur schema, version 3: what the open web says about a company.
--
-- The registry answers who exists and who signs. It does not answer what a
-- company sells, what it runs on, or how to write to it. That lives on the
-- company's own site, so this table holds one row per company describing what
-- was found there, and emails land in `contact` where the reach stage already
-- expects them.
--
-- One row per company, not per page. The stage asks a model to search, read
-- and summarise in a single pass, so the unit of provenance is the research
-- call, not the individual URL. Every field it produces is an inference from
-- text on a page, which is why `confidence` and `status` sit beside them: a
-- guessed website is worse than none if it is silently treated as fact.

CREATE TABLE IF NOT EXISTS site (
    company_id    INTEGER PRIMARY KEY REFERENCES company (id) ON DELETE CASCADE,
    -- found | not_found | ambiguous. A company with no web presence is a
    -- real and common outcome, and must be distinguishable from one that has
    -- simply not been looked at yet.
    status        TEXT NOT NULL,
    url           TEXT,
    -- How confident the model is that this site belongs to *this* company,
    -- rather than a namesake. Swiss SME names collide often.
    confidence    REAL,
    language      TEXT,
    -- Prose, for a human to read on the company page.
    summary       TEXT,
    -- JSON arrays. Stored as text rather than joined tables: they are read
    -- whole, written whole, and never queried by element.
    offerings     TEXT,
    -- Clues about IT need: legacy stack, hiring, manual process, compliance
    -- pressure, a site that is itself evidence of neglect.
    it_signals    TEXT,
    partnerships  TEXT,
    tech_stack    TEXT,
    -- What the site suggests about size, when it says anything at all.
    size_hint     TEXT,
    -- Provenance. source_url is the page the model actually read; method
    -- records how the site was found, since a guessed domain and a searched
    -- one deserve different trust.
    source_url    TEXT,
    method        TEXT,
    fetched_at    TEXT,
    model         TEXT,
    -- Cost control: the searches and fetches this row cost, so a run can be
    -- priced afterwards rather than guessed at.
    searches      INTEGER,
    fetches       INTEGER
);

CREATE INDEX IF NOT EXISTS site_status_idx ON site (status);
