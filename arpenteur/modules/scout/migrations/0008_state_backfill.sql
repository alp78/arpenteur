-- Where everything already on the board stood when the history began.
--
-- `audit.state_change` starts empty, which means every company already being
-- worked has a state and no date, and the board reports none of them as having
-- sat anywhere. That is the one thing the table was added to fix.
--
-- So one row per prospect, saying what it is now and the earliest date the row
-- itself can vouch for. It is deliberately NOT dressed up as a transition:
-- `from_state` is null because nothing here knows what it moved from, and the
-- reason says so in words that will still be true when somebody reads this row
-- in a year and wonders why forty companies all changed state on the same day.
--
-- `created_at` rather than `updated_at`: a prospect's `updated_at` moves when
-- an owner or a next action is typed, so using it would date the state to an
-- edit that did not touch it. The creation date understates how long something
-- has sat in its current column, and understating is the safe direction for a
-- number whose purpose is to make somebody chase what has gone quiet.

INSERT INTO audit.state_change (entity, entity_id, company_id, from_state,
                                to_state, by, reason, at)
SELECT 'prospect', p.company_id, p.company_id, NULL, p.state, 'backfill',
       'Recorded when the history began. Earlier transitions were not kept, '
       'and this date is when the prospect row was created rather than when it '
       'reached this state.',
       p.created_at
  FROM intel.prospect p
 WHERE NOT EXISTS (
     SELECT 1 FROM audit.state_change s
      WHERE s.entity = 'prospect' AND s.entity_id = p.company_id
 );
