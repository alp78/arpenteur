-- Reading the whole thing back. Views only: nothing here is written to.
--
-- WHY THIS SCHEMA MAY LOOK AT WHAT IT DOES NOT REQUIRE
--
-- Every other module in this codebase depends only on what it declares, and a
-- test walks the import graph and the foreign key catalogue to prove it. This
-- one reads `crm` and `commercial` while requiring neither, and that is
-- deliberate rather than an exception nobody got round to fixing.
--
-- The rule exists so that dropping a module cannot orphan a row. A view cannot
-- orphan anything: it holds no data, nothing references it, and `DROP SCHEMA
-- commercial CASCADE` takes the views that read it with it, which is exactly
-- the right outcome. The dependency is one way and it is weak in the only
-- sense that matters.
--
-- What would be wrong is failing to install on a survey-only deployment. So
-- each block below is guarded on the schema existing, and a deployment running
-- the scout alone gets the survey half of the reports and no errors. A report
-- that is absent because the module is absent is the correct behaviour; an
-- installation that fails because a report wanted a table is not.
--
-- MATERIALISED OR NOT
--
-- Materialised where the query walks the survey, which is 185'000 rows and
-- climbing, and plain where it walks the worked set, which is dozens. A
-- materialised view is stale by definition and that is a feature here: a
-- report read twice in a morning should not cost two full scans, and nothing
-- in this schema answers a question where being an hour out of date matters.
-- `analytics.refresh()` is how they are brought up to date.

CREATE SCHEMA IF NOT EXISTS analytics;

-- --- the survey itself ---------------------------------------------------------

-- How far the survey has actually been taken. Every stage's coverage in one
-- row, because the question is always "what is left" rather than "how many are
-- done", and a count with no denominator answers neither.
CREATE OR REPLACE VIEW analytics.coverage AS
SELECT
    (SELECT COUNT(*) FROM core.company)                                AS companies,
    (SELECT COUNT(*) FROM core.company WHERE hydrated_at IS NOT NULL)                 AS hydrated,
    (SELECT COUNT(*) FROM core.company WHERE address_json IS NOT NULL) AS with_address,
    (SELECT COUNT(DISTINCT company_id) FROM intel.segment)             AS profiled,
    (SELECT COUNT(DISTINCT company_id) FROM intel.site)                AS researched,
    (SELECT COUNT(*) FROM intel.fit)                                   AS scored,
    (SELECT COUNT(DISTINCT company_id) FROM core.contact
      WHERE kind = 'email' AND NOT suppressed)                         AS reachable,
    (SELECT COUNT(*) FROM core.party)                                  AS people,
    (SELECT COUNT(*) FROM intel.prospect)                              AS on_the_board;

-- What the campaign has spent, by stage.
--
-- Read from `stats_json`, which every model stage now writes its usage into.
-- Tokens are the bill; searches and fetches are not, and counting those while
-- not counting these was precisely the wrong half. A run that reported no
-- usage contributes zero tokens and still counts as a run, so a stage with
-- calls and no tokens reads as unaccounted rather than as free.
CREATE OR REPLACE VIEW analytics.spend_by_stage AS
SELECT
    stage,
    COUNT(*)                                                    AS runs,
    COUNT(*) FILTER (WHERE error IS NOT NULL)                   AS failed,
    COALESCE(SUM((stats_json ->> 'model_calls')::BIGINT), 0)    AS model_calls,
    COALESCE(SUM((stats_json ->> 'input_tokens')::BIGINT), 0)   AS input_tokens,
    COALESCE(SUM((stats_json ->> 'output_tokens')::BIGINT), 0)  AS output_tokens,
    COALESCE(SUM((stats_json ->> 'searches')::BIGINT), 0)       AS searches,
    MIN(started_at)                                             AS first_run,
    MAX(started_at)                                             AS last_run
  FROM intel.run
 GROUP BY stage;

-- The scored survey by band, with how reachable each band actually is.
--
-- The pair is the useful part. A band full of companies nobody can write to is
-- a band that will not convert whatever its score says, and that was invisible
-- while the score was one number.
CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.band_profile AS
SELECT
    f.band,
    COUNT(*)                                        AS companies,
    ROUND(AVG(f.score), 1)                          AS mean_score,
    COUNT(*) FILTER (WHERE EXISTS (
        SELECT 1 FROM core.contact ct
         WHERE ct.company_id = f.company_id
           AND ct.kind = 'email' AND NOT ct.suppressed
    ))                                              AS reachable,
    COUNT(*) FILTER (WHERE f.source = 'manual')     AS set_by_hand
  FROM intel.fit f
 GROUP BY f.band;

CREATE UNIQUE INDEX IF NOT EXISTS band_profile_idx
    ON analytics.band_profile (band);

-- Where the survey is, by canton and segment. Materialised: it groups the
-- whole company table twice.
CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.survey_by_canton AS
SELECT
    c.canton,
    COUNT(*)                                          AS companies,
    COUNT(*) FILTER (WHERE c.hydrated_at IS NOT NULL)                AS hydrated,
    COUNT(f.company_id)                               AS scored,
    COUNT(*) FILTER (WHERE f.band = 'high')           AS high,
    COUNT(p.company_id)                               AS on_the_board
  FROM core.company c
  LEFT JOIN intel.fit f ON f.company_id = c.id
  LEFT JOIN intel.prospect p ON p.company_id = c.id
 GROUP BY c.canton;

CREATE UNIQUE INDEX IF NOT EXISTS survey_by_canton_idx
    ON analytics.survey_by_canton (canton);

CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.survey_by_segment AS
SELECT
    s.segment,
    COUNT(DISTINCT s.company_id)                                   AS companies,
    COUNT(DISTINCT s.company_id) FILTER (WHERE s.source = 'manual') AS set_by_hand,
    COUNT(DISTINCT f.company_id) FILTER (WHERE f.band = 'high')    AS high,
    ROUND(AVG(f.score), 1)                                         AS mean_score
  FROM intel.segment s
  LEFT JOIN intel.fit f ON f.company_id = s.company_id
 GROUP BY s.segment;

CREATE UNIQUE INDEX IF NOT EXISTS survey_by_segment_idx
    ON analytics.survey_by_segment (segment);

-- --- the funnel ------------------------------------------------------------------

-- How many companies have ever reached each state, and how long they sat in
-- the one before.
--
-- This is what `audit.state_change` was added for. A status column says where
-- something is; only the transitions say how it got there and how long it
-- took, and "how long does a prospect sit before a meeting" is the question a
-- pipeline is actually read for.
--
-- Reached-ever rather than currently-in: a company that went new to contacted
-- to meeting has reached three states, and counting only the current one makes
-- every stage of the funnel look narrow except the last.
CREATE OR REPLACE VIEW analytics.funnel AS
WITH moves AS (
    SELECT
        entity,
        entity_id,
        to_state,
        at,
        LAG(at) OVER (PARTITION BY entity, entity_id ORDER BY at, id) AS previous_at
      FROM audit.state_change
     WHERE entity = 'prospect'
)
SELECT
    to_state                                                      AS state,
    COUNT(DISTINCT entity_id)                                     AS reached,
    ROUND(AVG(EXTRACT(EPOCH FROM (at - previous_at)) / 86400)::NUMERIC, 1)
                                                                  AS mean_days_from_previous,
    ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (
        ORDER BY EXTRACT(EPOCH FROM (at - previous_at)) / 86400))::NUMERIC, 1)
                                                                  AS median_days_from_previous
  FROM moves
 GROUP BY to_state;

-- What is sitting still. The list a Monday morning is for: on the board, not
-- moved recently, and nobody has said why.
CREATE OR REPLACE VIEW analytics.gone_quiet AS
SELECT
    p.company_id,
    c.name,
    p.state,
    p.owner,
    p.next_action,
    p.next_at,
    (SELECT MAX(at) FROM audit.state_change s
      WHERE s.entity = 'prospect' AND s.entity_id = p.company_id) AS last_move,
    EXTRACT(DAY FROM now() - (
        SELECT MAX(at) FROM audit.state_change s
         WHERE s.entity = 'prospect' AND s.entity_id = p.company_id
    ))                                                            AS days_still
  FROM intel.prospect p
  JOIN core.company c ON c.id = p.company_id
 WHERE p.state NOT IN ('won', 'lost', 'parked');

-- Whether the ranking was any good, weighted by how it was chosen.
--
-- The join to `selection_p` is the whole reason this view is not simply
-- "conversion by band". Outcomes exist only for companies that were
-- approached, and they were approached because they ranked well, so a naive
-- reading concludes that the score works no matter what the score does. The
-- propensity recorded at selection is what lets a later analysis weight by its
-- inverse. It is a percentile rather than a calibrated probability and is not
-- to be read as one; what it supports is a comparison, not a claim.
CREATE OR REPLACE VIEW analytics.selection_outcomes AS
SELECT
    p.selection_band                                        AS band_when_picked,
    COUNT(*)                                                AS picked,
    ROUND(AVG(p.selection_p)::NUMERIC, 3)                   AS mean_propensity,
    COUNT(*) FILTER (WHERE p.state = 'won')                 AS won,
    COUNT(*) FILTER (WHERE p.state = 'lost')                AS lost,
    ROUND(AVG(p.selection_score)::NUMERIC, 1)               AS mean_score_when_picked
  FROM intel.prospect p
 WHERE p.selected_at IS NOT NULL
 GROUP BY p.selection_band;

-- --- what the client half adds, when it is installed -------------------------------

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'crm') THEN
        -- Engagements and how much of each one's file is actually filled in.
        -- Dossier coverage is here because an empty dossier is the single best
        -- predictor of a meeting that goes badly, and it is invisible on any
        -- page that shows one client at a time.
        EXECUTE $view$
        CREATE OR REPLACE VIEW analytics.client_coverage AS
        SELECT
            cl.id                                                    AS client_id,
            cl.code,
            co.name,
            cl.status,
            (SELECT COUNT(*) FROM crm.project pr
              WHERE pr.client_id = cl.id AND pr.status = 'active')   AS active_projects,
            (SELECT COUNT(*) FROM crm.dossier d
              WHERE d.client_id = cl.id
                AND COALESCE(TRIM(d.body), '') <> '')                AS dossier_sections,
            (SELECT COUNT(*) FROM crm.attachment a
              WHERE a.client_id = cl.id)                             AS documents,
            (SELECT COUNT(*) FROM crm.glossary g
              WHERE g.client_id = cl.id)                             AS terms,
            (SELECT COUNT(*) FROM crm.glossary g
              WHERE g.client_id = cl.id AND g.known)                 AS terms_known,
            (SELECT COUNT(*) FROM crm.interaction i
              WHERE i.client_id = cl.id)                             AS interactions,
            (SELECT MAX(i.occurred_at) FROM crm.interaction i
              WHERE i.client_id = cl.id AND i.state = 'done')        AS last_contact
          FROM crm.client cl
          JOIN core.company co ON co.id = cl.company_id
        $view$;

        -- Correspondence read, matched and filed. The queue's own honesty
        -- check: a matcher proposing nothing, or a queue nobody empties, both
        -- look fine from the Mail page and neither is.
        EXECUTE $view$
        CREATE OR REPLACE VIEW analytics.mail_review AS
        SELECT
            state,
            COUNT(*)                                     AS messages,
            COUNT(company_id)                            AS matched,
            COUNT(*) FILTER (WHERE match_method = 'thread') AS by_thread,
            MIN(sent_at)                                 AS oldest,
            MAX(sent_at)                                 AS newest
          FROM crm.email_message
         GROUP BY state
        $view$;
    END IF;
END $$;

-- --- what the money half adds, when it is installed ---------------------------------

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.schemata
                WHERE schema_name = 'commercial') THEN
        -- What is out and what it is worth, by state. The newest version of
        -- each quote, because an earlier one is not a second opportunity.
        EXECUTE $view$
        CREATE OR REPLACE VIEW analytics.pipeline AS
        SELECT
            q.status,
            COUNT(*)                                       AS quotes,
            COALESCE(SUM(v.gross_total), 0)                AS value,
            ROUND(AVG(v.gross_total), 2)                   AS mean_value
          FROM commercial.quote q
          LEFT JOIN LATERAL (
              SELECT gross_total FROM commercial.quote_version
               WHERE quote_id = q.id ORDER BY version DESC LIMIT 1
          ) v ON TRUE
         GROUP BY q.status
        $view$;

        -- Billed, received, and what is late. `days_late` is signed on
        -- purpose: a negative number is an invoice not yet due, and folding
        -- those to zero would make a healthy ledger look like a chased one.
        EXECUTE $view$
        CREATE OR REPLACE VIEW analytics.revenue AS
        SELECT
            DATE_TRUNC('month', i.issued_on)::DATE         AS month,
            COUNT(*)                                       AS invoices,
            SUM(i.net_total)                               AS net,
            SUM(i.vat_total)                               AS vat,
            SUM(i.gross_total)                             AS gross,
            SUM(i.paid_total)                              AS received,
            SUM(i.gross_total - i.paid_total)
                FILTER (WHERE i.status IN ('issued', 'part_paid', 'overdue'))
                                                           AS outstanding
          FROM commercial.invoice i
         WHERE i.issued_on IS NOT NULL AND i.status <> 'cancelled'
         GROUP BY 1
        $view$;

        EXECUTE $view$
        CREATE OR REPLACE VIEW analytics.aged_debt AS
        SELECT
            i.id                                           AS invoice_id,
            i.number,
            c.code                                         AS client_code,
            co.name                                        AS company_name,
            i.issued_on,
            i.due_on,
            i.gross_total - i.paid_total                   AS owed,
            (CURRENT_DATE - i.due_on)                      AS days_late
          FROM commercial.invoice i
          JOIN crm.client c ON c.id = i.client_id
          JOIN core.company co ON co.id = c.company_id
         WHERE i.status IN ('issued', 'part_paid', 'overdue')
           AND i.gross_total > i.paid_total
        $view$;

        -- Hours logged against hours billed. The question a fixed fee never
        -- answers on its own: whether it was worth taking.
        EXECUTE $view$
        CREATE OR REPLACE VIEW analytics.effort AS
        SELECT
            t.client_id,
            c.code                                                     AS client_code,
            SUM(t.minutes)                                             AS minutes,
            SUM(t.minutes) FILTER (WHERE t.billable)                   AS billable_minutes,
            SUM(t.minutes) FILTER (WHERE t.billable
                 AND t.invoice_line_id IS NULL)                        AS unbilled_minutes
          FROM commercial.time_entry t
          JOIN crm.client c ON c.id = t.client_id
         GROUP BY t.client_id, c.code
        $view$;
    END IF;
END $$;

-- Bringing the materialised views up to date.
--
-- CONCURRENTLY where there is a unique index to allow it, so a refresh does
-- not lock out a page that is reading. It cannot run inside a transaction
-- block, which is why this is a procedure the caller commits around rather
-- than a function called mid-statement.
CREATE OR REPLACE PROCEDURE analytics.refresh()
LANGUAGE plpgsql AS $$
DECLARE
    view_name TEXT;
BEGIN
    FOR view_name IN
        SELECT matviewname FROM pg_matviews WHERE schemaname = 'analytics'
    LOOP
        EXECUTE FORMAT('REFRESH MATERIALIZED VIEW CONCURRENTLY analytics.%I',
                       view_name);
    END LOOP;
END $$;
