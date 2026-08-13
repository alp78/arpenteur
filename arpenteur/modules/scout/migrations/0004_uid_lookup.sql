-- Look a company up by the identifier a person actually typed.
--
-- `CHE-103.895.771`, `CHE103895771` and `103895771` are one identifier written
-- three ways: on a letterhead, in the register's API, and by someone reading it
-- off a screen. The console normalises to the digits and matches on those,
-- which is correct and, without this, a sequential scan over 184'939 rows —
-- measured at 105 ms for what should be a point lookup.
--
-- An expression index rather than a normalised column: the raw `uid` is what
-- the register issued and what gets quoted back to it, so deriving the search
-- key leaves one authority for the value instead of two that can disagree.
CREATE INDEX IF NOT EXISTS company_uid_digits_idx
    ON core.company ((regexp_replace(uid, '[^0-9]', '', 'g')) text_pattern_ops);
