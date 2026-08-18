-- What line of business a company is in, as opposed to how it fits a campaign.
--
-- `intel.segment` already sorts companies, but it sorts them by fit: a
-- laboratory is `data-driven` and `compliance-sector` because those are
-- reasons to approach it, not because they are what it does for a living. So
-- the survey could be sliced by "worth talking to about data" and could not be
-- sliced by "restaurants", which is the first question anybody actually asks
-- of a register sweep.
--
-- Kept apart from `segment` rather than added to its vocabulary, for two
-- reasons. A company has several segments and one sector: it is in catering or
-- it is in construction, and a company that is genuinely both is a company
-- whose purpose text is describing a group. And mixing them would make the one
-- Segment dropdown offer `hospitality` beside `sme-owner`, which reads as one
-- list and is two different questions.
--
-- In `intel` rather than on `core.company` because this is inferred and the
-- register never said it. Truncating this table loses a classification run and
-- nothing the state filed.

CREATE TABLE IF NOT EXISTS intel.sector (
    company_id    BIGINT PRIMARY KEY REFERENCES core.company (id) ON DELETE CASCADE,
    sector        TEXT NOT NULL,
    confidence    REAL,
    rationale     TEXT,
    model         TEXT,
    classified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Same rule as everywhere else in this schema: a person's answer outranks
    -- a model's, permanently, and `save_sector` refuses to overwrite a row
    -- that says 'manual'.
    source        TEXT NOT NULL DEFAULT 'model'
);

CREATE INDEX IF NOT EXISTS sector_name_idx ON intel.sector (sector);
CREATE INDEX IF NOT EXISTS sector_manual_idx ON intel.sector (company_id)
    WHERE source = 'manual';
