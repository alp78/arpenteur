-- The notes are searched in full text and listed by client, never filtered by
-- tag in SQL: the tags are read into the page and matched there. The GIN
-- index over them cost a write per note for nothing read.
DROP INDEX IF EXISTS crm.note_tags_idx;
