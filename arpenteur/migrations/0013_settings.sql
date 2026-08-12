-- Arpenteur schema, version 13: settings an operator can change.
--
-- The territory, the segment vocabulary and what counts as a signal decide
-- what the whole pipeline does, and they lived only in arpenteur.toml. That
-- made the most consequential decisions in the tool the ones you had to leave
-- the application to make, which is backwards. It also hid them: a survey
-- silently missing every foundation because 0110 was absent from a list in a
-- file is not a discoverable state.
--
-- Overlay rather than replacement. arpenteur.toml keeps its comments and its
-- defaults, and stays the reference for someone reading the repository; a row
-- here is this deployment's decision, applied over it. Rewriting the TOML from
-- a web form was the obvious alternative and would have destroyed sixty lines
-- of explanation that are the reason the file is readable.
--
-- Values are JSON so a list stays a list. `updated_at` and `updated_by` are
-- here because changing the territory changes what every later stage will
-- spend money on, and "who widened this and when" is a question that gets
-- asked after the bill.
CREATE TABLE IF NOT EXISTS setting (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT
);
