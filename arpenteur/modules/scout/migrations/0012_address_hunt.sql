-- How hard we looked for a company's address, and where the looking stopped.
--
-- `core.contact` holds addresses that were found. This holds the search, which
-- is a different fact and the one that was missing: a company with no contact
-- row could mean nobody has looked, the site was unreadable, robots.txt said
-- no, or every tier ran and the company genuinely publishes no address. Those
-- are four different answers and the absence of a row said none of them.
--
-- WHY THE NEGATIVE IS WORTH STORING. A settled negative is what stops the
-- expensive tiers being paid for twice. Without it a run over thirty thousand
-- companies re-searches the ones that were already exhausted, every time, and
-- the tier that costs money is the one that gets repeated.
--
-- `tried` carries the candidates that failed and why. An inference that
-- offered info@, kontakt@ and contact@ and found no MX at any of them is a
-- fact about that domain worth keeping: it says the guessing was done and did
-- not work, so the next run reaches for a different tier rather than the same
-- one.
--
-- The tier names are the cascade in order, and a row records the one that
-- answered:
--   site      read off the company's own pages
--   search    found by a web search, then read
--   inferred  constructed at a known domain, the domain accepts mail
--   none      every tier ran and there is nothing to find

CREATE TABLE IF NOT EXISTS intel.address_hunt (
    company_id BIGINT PRIMARY KEY REFERENCES core.company (id) ON DELETE CASCADE,
    -- site | search | inferred | none | blocked | unreadable
    outcome    TEXT NOT NULL,
    -- 1 to 5, the tier that answered. Null when nothing answered.
    tier       INTEGER,
    -- What the company's site was resolved to, kept even when no address came
    -- off it: the domain is what the inference tiers need, and finding it
    -- again would cost another search.
    domain     TEXT,
    -- Every address the cascade found, not just one, because a régie
    -- publishes several and which one to write to is the operator's call: a
    -- general `info@` reaches the switchboard, a named `gerance@` reaches the
    -- department that does handovers. Collapsing them to one would throw away
    -- the more useful half and there would be no way to get it back without
    -- reading the site again.
    --
    -- Ordered, and the order is a judgement: role addresses first, since they
    -- survive a person leaving, then the rest as the pages gave them up. A
    -- reader that wants exactly one address takes `addresses[1]`.
    addresses  TEXT[] NOT NULL DEFAULT '{}',
    -- published | inferred. What `core.contact.method` was written with, so a
    -- reader of this table alone can still tell a fact from a candidate.
    method     TEXT,
    pages      INTEGER NOT NULL DEFAULT 0,
    searches   INTEGER NOT NULL DEFAULT 0,
    -- Candidates offered and rejected, each with its reason.
    tried      JSONB,
    note       TEXT,
    hunted_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS address_hunt_outcome_idx
    ON intel.address_hunt (outcome);
-- "What is still worth another tier", which is the query that picks the next
-- run's work.
CREATE INDEX IF NOT EXISTS address_hunt_open_idx
    ON intel.address_hunt (tier) WHERE outcome NOT IN ('site', 'search', 'inferred');
