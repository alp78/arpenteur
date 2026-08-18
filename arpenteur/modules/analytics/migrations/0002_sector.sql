-- Where the survey is concentrated by trade.
--
-- A new file rather than an edit to 0001, because a migration that has been
-- applied is history and rewriting it means the database in front of you and
-- the file describing it stop agreeing.
--
-- This is the one of the two classification reports that adds up. A company
-- carries several segments, so `survey_by_segment` counts it once per segment
-- and its `companies` column sums to more than the survey; a company carries
-- one sector, so every row here is disjoint and the column is a share of the
-- whole. It is the difference between "which angles are represented" and
-- "what is actually out there".

CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.survey_by_sector AS
SELECT
    x.sector,
    COUNT(*)                                              AS companies,
    COUNT(*) FILTER (WHERE x.source = 'manual')           AS set_by_hand,
    COUNT(w.company_id)                                   AS researched,
    COUNT(f.company_id) FILTER (WHERE f.band = 'high')    AS high,
    ROUND(AVG(f.score), 1)                                AS mean_score
  FROM intel.sector x
  LEFT JOIN intel.fit f ON f.company_id = x.company_id
  LEFT JOIN intel.site w ON w.company_id = x.company_id
 GROUP BY x.sector;

-- A unique index is what lets `analytics.refresh()` do it concurrently, so
-- refreshing the reports does not lock out whoever is reading them.
CREATE UNIQUE INDEX IF NOT EXISTS survey_by_sector_idx
    ON analytics.survey_by_sector (sector);
