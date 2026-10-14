-- Two indexes nothing queries, and the extension nothing uses.
--
-- company_search_idx was built for a full-text search over name and purpose
-- that the console never ran: the list searches by name similarity and by
-- UID, and the people page by folded name. person_name_trgm_idx served a
-- trigram lookup that the resolve stage replaced with its own folded key.
-- Both cost a write on every hydrated company and officer for nothing read.
-- btree_gin was installed for a composite index that was never created.
DROP INDEX IF EXISTS core.company_search_idx;
DROP INDEX IF EXISTS core.person_name_trgm_idx;
DROP EXTENSION IF EXISTS btree_gin;
