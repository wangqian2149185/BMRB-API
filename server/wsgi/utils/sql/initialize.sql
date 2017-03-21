-- yum install postgresql-contrib
-- psql -d bmrbeverything -U postgres
--   CREATE EXTENSION pg_trgm;

DROP TABLE "instant";
CREATE TABLE "instant" (id varchar(12), title text, citations text[], authors text[], tsv tsvector);

INSERT INTO "instant"
SELECT
 entry."ID",
 clean_title(entry."Title"),
 array_agg(DISTINCT clean_title(citation."Title")),
 array_agg(DISTINCT REPLACE(Replace(citation_author."Given_name", '.', '') || ' ' || COALESCE(Replace(citation_author."Middle_initials", '.', ''),'') || ' ' || Replace(citation_author."Family_name", '.', ''), '  ', ' '))
FROM macromolecules."Entry" as entry
LEFT JOIN macromolecules."Citation" AS citation
  ON entry."ID"=citation."Entry_ID"
LEFT JOIN macromolecules."Citation_author" AS citation_author
  ON entry."ID"=citation_author."Entry_ID"
GROUP BY entry."ID",entry."Title";

INSERT INTO "instant"
SELECT
 entry."ID",
 clean_title(entry."Title"),
 array_agg(DISTINCT clean_title(citation."Title")),
 array_agg(DISTINCT REPLACE(Replace(citation_author."Given_name", '.', '') || ' ' || COALESCE(Replace(citation_author."Middle_initials", '.', ''),'') || ' ' || Replace(citation_author."Family_name", '.', ''), '  ', ' '))
FROM metabolomics."Entry" as entry
LEFT JOIN metabolomics."Citation" AS citation
  ON entry."ID"=citation."Entry_ID"
LEFT JOIN metabolomics."Citation_author" AS citation_author
  ON entry."ID"=citation_author."Entry_ID"
GROUP BY entry."ID",entry."Title";

CREATE INDEX tsv_idx ON "instant" USING gin(tsv);
UPDATE "instant" SET tsv =
    setweight(to_tsvector("instant".id), 'A') ||
    setweight(to_tsvector(array_to_string("instant".authors, ' ')), 'B') ||
    setweight(to_tsvector("instant".title), 'C') ||
    setweight(to_tsvector(array_to_string("instant".citations, ' ')), 'D')
;
