-- Arpenteur schema, version 8: how well a company fits the campaign.
--
-- The output of `match`, and the last thing produced before a human decides
-- whether to write. A score alone would be useless: nobody acts on 73 without
-- knowing why, and the reasoning is what makes the number arguable, which is
-- the only way it improves.
--
-- `source` for the same reason `segment` has one. A score corrected by hand
-- must survive the stage running again, or correcting it is wasted effort.
CREATE TABLE IF NOT EXISTS fit (
    company_id  INTEGER PRIMARY KEY REFERENCES company (id) ON DELETE CASCADE,
    -- 0 to 100. A number rather than a band alone so a shortlist can be
    -- ordered, and a band alongside it because ordering by a number invites
    -- false precision between 61 and 64.
    score       INTEGER NOT NULL,
    band        TEXT NOT NULL,
    -- Why, in the model's words, and what it would say. The advice is the
    -- actual deliverable: a ranked list without it is a spreadsheet.
    reasoning   TEXT,
    advice      TEXT,
    -- What argues against approaching. Carried separately so it cannot be
    -- lost in a paragraph nobody reads to the end.
    against     TEXT,
    -- Which of the campaign's stated signals were actually found. Lets a
    -- score be audited against the brief rather than taken on trust.
    matched     TEXT,
    source      TEXT NOT NULL DEFAULT 'model',
    -- Provenance, as every derived row here carries. `brief_hash` is a digest
    -- of campaign.md at the time: when the brief is rewritten, every score
    -- taken against the old one is stale, and this is how they are found.
    model       TEXT,
    brief_hash  TEXT,
    scored_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS fit_score_idx ON fit (score DESC);
CREATE INDEX IF NOT EXISTS fit_band_idx ON fit (band);
