-- An address with no house number, or no street, used to gain a row on every
-- geocode run: the unique rule treats two NULLs as different, so the conflict
-- clause never fired. Fold the duplicates onto their first row, then make the
-- rule read NULLs as equal.
WITH dup AS (
    SELECT id,
           MIN(id) OVER (PARTITION BY street, house_number, postal_code, city, country) AS keep
      FROM geo.address
)
UPDATE geo.establishment e
   SET address_id = d.keep
  FROM dup d
 WHERE e.address_id = d.id AND d.id <> d.keep;

WITH dup AS (
    SELECT id,
           MIN(id) OVER (PARTITION BY street, house_number, postal_code, city, country) AS keep
      FROM geo.address
)
DELETE FROM geo.address a
 USING dup d
 WHERE a.id = d.id AND d.id <> d.keep;

ALTER TABLE geo.address
    DROP CONSTRAINT IF EXISTS address_street_house_number_postal_code_city_country_key;
ALTER TABLE geo.address
    ADD CONSTRAINT address_parts_key
    UNIQUE NULLS NOT DISTINCT (street, house_number, postal_code, city, country);
