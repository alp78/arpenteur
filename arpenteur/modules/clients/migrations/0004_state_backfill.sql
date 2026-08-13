-- The same backfill, for the entities this module owns.
--
-- It is a separate file from the scout's because migrations run in dependency
-- order: `audit` is created by the scout, and a scout migration cannot mention
-- `crm.client` because the clients module may not be installed at all. Each
-- module backfills what it owns, into a table that belongs to neither.
--
-- See scout/0008 for why `from_state` is null and why the reason says so.

INSERT INTO audit.state_change (entity, entity_id, company_id, from_state,
                                to_state, by, reason, at)
SELECT 'client', c.id, c.company_id, NULL, c.status, 'backfill',
       'Recorded when the history began. Earlier transitions were not kept.',
       c.created_at
  FROM crm.client c
 WHERE NOT EXISTS (
     SELECT 1 FROM audit.state_change s
      WHERE s.entity = 'client' AND s.entity_id = c.id
 );

INSERT INTO audit.state_change (entity, entity_id, company_id, from_state,
                                to_state, by, reason, at)
SELECT 'project', p.id, c.company_id, NULL, p.status, 'backfill',
       'Recorded when the history began. Earlier transitions were not kept.',
       p.created_at
  FROM crm.project p
  JOIN crm.client c ON c.id = p.client_id
 WHERE NOT EXISTS (
     SELECT 1 FROM audit.state_change s
      WHERE s.entity = 'project' AND s.entity_id = p.id
 );
