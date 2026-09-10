-- Separate "we looked everywhere and there is nothing" from "we stopped early".
--
-- `0012_address_hunt.sql` gave the cascade four outcomes and made `none` the
-- settled negative: every tier ran, the company publishes no address, stop
-- paying to find out again. That contract is what lets an export print a dash
-- and mean it, and it is why the open-work index selects on the outcome.
--
-- Two writers broke the contract, both by writing `none` for a result that had
-- not earned it.
--
-- The cascade wrote it whenever nothing answered, including on a machine with
-- no API credit where the search tier never ran at all. The agency directory
-- writer was worse: a firm listed with no public address got a `none` row
-- although not one tier of the cascade had touched it, because the directory
-- having nothing was recorded as the world having nothing.
--
-- WHY THIS MATTERED MORE THAN A WRONG LABEL. The selection that picks the next
-- run's work skipped any company holding a row, so a premature `none` did not
-- merely misreport — it retired the company from the search. 2,360 firms left
-- the cascade without their own websites ever being read, and the record of
-- that said they publish nothing. A negative that suppresses the work which
-- would overturn it cannot be audited from the inside; nothing looks wrong.
--
-- So `partial` is added, meaning the looking is unfinished, and it reads as
-- open everywhere `none` reads as closed.
--
-- The rows corrected here are every `none` written without a search actually
-- running. On this database that is all of them, because the search tier has
-- never had credit to run with: the directory rows and the cascade's own. A
-- row that did search and found nothing keeps `none` and stays settled.

UPDATE intel.address_hunt
   SET outcome = 'partial',
       note = CASE
           WHEN note LIKE 'ImmoScout24%'
               THEN note || '; the directory had none, the cascade is still owed'
           ELSE 'ran without the search tier'
       END
 WHERE outcome = 'none'
   AND searches = 0;

-- The open-work index already covers this: it is defined over every outcome
-- outside site/search/inferred, so `partial` falls into it without a change.
-- Restated rather than altered, because a reader arriving at this file should
-- not have to open 0012 to find out whether the new value is indexed.
