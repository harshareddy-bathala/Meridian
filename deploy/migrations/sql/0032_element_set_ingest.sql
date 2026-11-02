-- 0032 — element sets arrive through ingest, and say which artefact they came from
--
-- Until Stage 34 every element set was loaded from a file an operator put on
-- disk (D-079), as source `manual`. Live sets now come from a public catalogue
-- through `meridian-ingest` (D-320), and the platform still fetches nothing
-- (D-138, D-225). Two widenings make that recordable:
--
-- * `ingest_sources.source_class` gains `orbital_elements`. A catalogue of
--   element sets is not one of Stage 14's or Stage 31's classes, and a class is
--   what D-132 records about a source, not its vendor.
-- * `element_sets.ingest_record_id` names the artefact a fetched set was read
--   from, so every set the scheduler propagates traces to a retrieval, its
--   source version and the terms it arrived under (D-132, D-140). Null for a
--   set from a file, which has no artefact; a `manual` or `simulator` set may
--   not claim one, and a fetched one may not lose it.
--
-- `element_set_content_unique (satellite_id, source, content_sha256)` is left
-- as it is (D-057): the same lines fetched twice are one row, which keeps the
-- artefact that first carried them.

alter table ingest_sources drop constraint ingest_sources_source_class_check;
alter table ingest_sources add constraint ingest_sources_source_class_check
    check (source_class in (
        'archive_receptions',
        'space_weather',
        'atmospheric',
        'imagery',
        'regional_product',
        'orbital_elements'
    ));

alter table element_sets
    add column ingest_record_id bigint references ingest_records (record_id);

alter table element_sets add constraint element_sets_ingest_record_source_check
    check (ingest_record_id is null or source in ('celestrak', 'spacetrack'));

comment on column element_sets.ingest_record_id is
    'The ingest artefact a fetched set was read from (D-320). Null for a set loaded from a file.';
