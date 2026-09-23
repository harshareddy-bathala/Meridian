-- 0016 — the ingest provenance tables, and archive receptions kept apart
--
-- Stage 14 is the first stage whose input we do not control. It retrieves
-- artefacts from published archives, holds them unchanged, and normalises them
-- into rows that training and evaluation may read. Nothing here is a runtime
-- dependency of scheduling or reception: the independence test says Meridian
-- schedules, receives, decodes, monitors and reports with every external
-- service offline, and D-102 keeps loss diagnosis on our own evidence.
--
-- Four tables and a view. `ingest_sources` and `ingest_records` are the
-- provenance pair, created here and reused unchanged by Stage 31 rather than
-- duplicated (D-140). `archive_stations` and `archive_observations` hold what
-- an archive published about somebody else's receptions (D-139).
--
-- **Why archive receptions are not rows in `observations`.** That table is keyed
-- (assignment_id, revision, started_at); `observation_id` is generated from
-- (assignment_id, revision) (D-027); `station_id` references our own
-- `stations`. A reception someone else made has no Meridian assignment and no
-- registered station, so storing one there means inventing both. Keeping them
-- apart is also what keeps every reliability figure computed from rows whose
-- listening evidence we hold — rule 7 is meaningless without a heartbeat.
--
-- **Plain tables, no hypertable, no compression.** `element_sets` is the
-- precedent: append-only, time-stamped, read in bulk by a later stage rather
-- than in recent windows. `create_hypertable(..., migrate_data => true)` later
-- is cheap and supported; un-hypertabling is not, and `downgrade()` always
-- raises (GIT-WORKFLOW.md rule 9). Compression would be actively wrong here —
-- archive receptions are months old when they load, so a policy on `started_at`
-- would compress a chunk on creation and every backfill would write into a
-- compressed one. It also retires D-119's risk for this file: every table is
-- new and empty, and every CHECK is created with its table rather than added to
-- something that already holds compressed chunks.
--
-- **No `simulated` column anywhere below.** `element_sets` is the documented
-- exception to ARCHITECTURE.md rule 4 because its provenance lives in `source`;
-- these are the same case, and here the table name is the label. The obligation
-- that replaces the column is that these rows are never pooled with
-- `observations` — which is why the outcome vocabulary below deliberately does
-- not match MSP's.
--
-- See DECISIONS.md D-138, D-139, D-140, D-141.


-- One row per source we take data from.
--
-- **Insert-only, and a change of terms is a new `source_id`.** Editing the
-- licence on this row would silently restate the terms every record already
-- stored arrived under, and those terms are what decide whether the evidence
-- dataset may republish a record or must reference it by checksum (D-104,
-- D-136). A new source id keeps each record pointing at what was actually
-- agreed to.
--
-- `licence`, `terms_url` and `attribution_entry` are `not null` and non-empty
-- on purpose: this is D-134 enforced by the database rather than remembered by
-- a reviewer. A source cannot be registered without its terms written down, so
-- no record can exist that arrived under terms nobody recorded.
create table ingest_sources (
    -- Ours, never the source's own name for itself, and pattern-checked because
    -- it is also a directory name in the raw store (D-141). No string from a
    -- remote source is ever a path element.
    source_id          text        primary key
                                   check (source_id ~ '^[a-z][a-z0-9_]{1,63}$'),

    -- What kind of thing this publishes. Classes, not vendors: ATTRIBUTION.md
    -- names classes deliberately, and Stage 31 adds its rows against the same
    -- enumeration (D-132).
    source_class       text        not null
                                   check (source_class in (
                                       'archive_receptions',
                                       'space_weather',
                                       'atmospheric',
                                       'imagery',
                                       'regional_product'
                                   )),
    name               text        not null check (length(btrim(name)) > 0),

    licence            text        not null check (length(btrim(licence)) > 0),

    -- https because a terms page fetched over plain http is a terms page an
    -- intermediary can rewrite, and this column is the evidence for a claim
    -- about what we were permitted to do.
    terms_url          text        not null check (terms_url ~ '^https://'),

    access_constraint  text        not null
                                   check (access_constraint in (
                                       'none', 'key_counted', 'registration'
                                   )),

    -- Names the ATTRIBUTION.md entry, so a record traces to the terms it
    -- arrived under rather than to whatever that file says today.
    attribution_entry  text        not null
                                   check (length(btrim(attribution_entry)) > 0),

    added_at           timestamptz not null default now(),

    -- Retires a source without deleting it. Records already taken under it stay
    -- readable and keep their provenance; nothing new is fetched.
    active             boolean     not null default true
);

comment on table ingest_sources is
    'One row per external source. Insert-only: changed terms mean a new '
    'source_id, so stored records keep pointing at the terms they arrived '
    'under. docs/DECISIONS.md D-134, D-140.';


-- One row per retrieved artefact. Append-only: a re-fetch that differs is a new
-- row, never an overwrite — D-015's discipline for observations, applied to
-- data we did not author either.
create table ingest_records (
    record_id            bigint      generated always as identity primary key,
    source_id            text        not null references ingest_sources (source_id),

    -- The source's own identifier for this artefact, kept verbatim. It lives
    -- here and in the manifest, and **never** in a path (D-141).
    original_identifier  text        not null
                                     check (length(btrim(original_identifier)) > 0),

    -- The source's own version of the artefact — usually a header it returned.
    -- Not defaulted and not nullable: a retrieval that cannot say which version
    -- it took is refused before anything is published, because an artefact of
    -- unknown vintage cannot be compared with its successor.
    source_version       text        not null
                                     check (length(btrim(source_version)) > 0),

    -- 'tile' marks a styled raster meant to be displayed. **No query may derive
    -- a value from one** (D-133): a tile is a picture of a measurement, and
    -- sampling its pixels for a number invents precision the tile never had.
    payload_kind         text        not null check (payload_kind in ('data', 'tile')),

    -- When *we* fetched it. Deliberately not defaulted to now(): a manifest
    -- written on an earlier run holds the truth, and this table is loaded from
    -- the manifest rather than at the moment of the fetch.
    retrieved_at         timestamptz not null,

    -- Of the raw bytes as downloaded, before any normalisation — so the hash
    -- proves what arrived, not what we made of it. bytea rather than hex text:
    -- 32 bytes instead of 64, and the length check makes a short or padded
    -- digest unstorable.
    sha256               bytea       not null check (octet_length(sha256) = 32),

    -- Where it sits under the raw root, relative. Absolute paths and any `..`
    -- segment are refused: the store's layout is built from our own timestamp
    -- and our own checksum, and this constraint is what stops a future writer
    -- reintroducing a remote string as a path.
    raw_path             text        not null
                                     check (raw_path <> ''
                                            and raw_path !~ '(^/|(^|/)\.\.(/|$))'),

    media_type           text        not null check (length(btrim(media_type)) > 0),
    byte_count           bigint      not null check (byte_count >= 0),

    -- The interval the artefact *describes*, which is not `retrieved_at` and is
    -- not interchangeable with it. A feature lookup selects on what an artefact
    -- describes and on when it was published, never on when we happened to
    -- fetch it (D-131). Null where the artefact describes no interval.
    valid_from           timestamptz,
    valid_to             timestamptz,

    -- The ground it covers, as a bounding box. jsonb rather than a geometry
    -- type because PostGIS is not installed and a box is all Stage 14 reads;
    -- Stage 32 may want more, and can add it without rewriting this column.
    spatial_extent       jsonb,

    -- Set on the older row when a differing re-fetch arrives, in the same
    -- transaction as the insert. Filling a column that was null is the only
    -- update in the whole write path.
    superseded_by        bigint      references ingest_records (record_id),

    -- An identical re-fetch is one row: the loader conflicts here and returns
    -- the id it already has. A *differing* re-fetch has a different digest and
    -- so inserts, which is what makes supersession visible rather than silent.
    constraint ingest_record_unique
        unique (source_id, original_identifier, sha256),

    constraint ingest_record_interval_needs_a_start
        check (valid_to is null or valid_from is not null),
    constraint ingest_record_interval_is_ordered
        check (valid_from is null or valid_to is null or valid_from <= valid_to),
    constraint ingest_record_is_not_its_own_successor
        check (superseded_by is distinct from record_id)
);

comment on table ingest_records is
    'One row per retrieved artefact, append-only. sha256 is of the raw bytes as '
    'downloaded. docs/DECISIONS.md D-140, D-141.';

create index ingest_records_source_idx on ingest_records (source_id, retrieved_at);
create index ingest_records_superseded_idx on ingest_records (superseded_by)
    where superseded_by is not null;


-- The station an archive says made a reception.
--
-- Stage 16's completeness ratio is observed ÷ geometrically available, and the
-- denominator is **ours**: computed by our own orbit service from this
-- station's published location, never taken from the archive. That is why the
-- location lands here in 0016 rather than later — migrations are forward-only,
-- so extracting these columns afterwards costs more than a table now.
--
-- Content-keyed, exactly as `element_sets` is (D-057). A station whose
-- published coordinates change becomes a **new row** rather than silently
-- rewriting every denominator already computed from the old ones.
create table archive_stations (
    archive_station_id bigint      generated always as identity primary key,
    record_id          bigint      not null references ingest_records (record_id),
    source_id          text        not null references ingest_sources (source_id),

    source_station_key text        not null
                                   check (length(btrim(source_station_key)) > 0),
    name               text,

    -- Published, not measured, and frequently absent — many archives publish a
    -- grid square or nothing at all. Null is the honest answer and the reason
    -- `denominator_inputs` below exists.
    lat_deg            double precision check (lat_deg between -90 and 90),
    lon_deg            double precision check (lon_deg between -180 and 180),
    alt_m              double precision,

    capability_json    jsonb,

    content_sha256     bytea       not null
                                   check (octet_length(content_sha256) = 32),
    first_seen_at      timestamptz not null default now(),

    -- What Stage 16 can actually compute for this station. Generated, so it
    -- cannot disagree with the columns it summarises, and stored so the
    -- coverage figure is a `group by` rather than a scan with a case.
    --
    -- The point is that incomplete stations are **counted and published**
    -- instead of silently dropped: a denominator computed over the stations we
    -- happened to have coordinates for, reported as though it covered all of
    -- them, is the selection bias this project exists to measure arriving
    -- through the back door.
    denominator_inputs text
        generated always as (
            case
                when lat_deg is null then 'neither'
                when capability_json is null then 'location_only'
                else 'location_and_capability'
            end
        ) stored,

    constraint archive_station_unique
        unique (source_id, source_station_key, content_sha256),
    constraint archive_station_location_is_a_pair
        check ((lat_deg is null) = (lon_deg is null))
);

comment on table archive_stations is
    'A station as an archive published it. Content-keyed: changed coordinates '
    'are a new row, never a rewrite. docs/DECISIONS.md D-139.';

create index archive_stations_key_idx on archive_stations (source_id, source_station_key);


-- A reception an archive published. Never a row in `observations` (D-139).
create table archive_observations (
    archive_observation_id bigint   generated always as identity primary key,
    record_id              bigint   not null references ingest_records (record_id),
    source_id              text     not null references ingest_sources (source_id),

    source_observation_id  text     not null
                                    check (length(btrim(source_observation_id)) > 0),

    -- The normaliser that produced this row. It lives here rather than on
    -- `ingest_records` because retrieval and transformation are separate events:
    -- an artefact is retrieved once and may be normalised many times, and a
    -- column on the arrival would have to be overwritten to say so (D-140).
    transformation_version text     not null
                                    check (length(btrim(transformation_version)) > 0),

    -- Of this row's canonical form. Re-normalising under the same version must
    -- reproduce it exactly; a mismatch means a nondeterministic normaliser,
    -- which the loader refuses rather than absorbs.
    content_sha256         bytea    not null
                                    check (octet_length(content_sha256) = 32),

    archive_station_id     bigint   references archive_stations (archive_station_id),

    -- **Deliberately not a foreign key to `satellites`.** An FK would force the
    -- load path into one of two wrongs: drop receptions for objects we do not
    -- track — a second selection filter stacked invisibly on the archive's own
    -- — or insert into `satellites`, which would let an external archive decide
    -- what pass generation propagates. Instead the key is canonical text, joined
    -- at read time, and coverage is reported as a number rather than applied as
    -- a filter.
    satellite_key          text     not null check (length(btrim(satellite_key)) > 0),
    satellite_key_kind     text     not null
                                    check (satellite_key_kind in (
                                        'norad',
                                        'international_designator',
                                        'source_name'
                                    )),

    started_at             timestamptz not null,
    ended_at               timestamptz,

    max_elevation_deg      double precision
                                    check (max_elevation_deg between -90 and 90),
    centre_freq_hz         bigint   check (centre_freq_hz > 0),
    mode                   text,

    -- **A different vocabulary from MSP's five, on purpose.** `no_signal`
    -- asserts that a station was verifiably listening and heard nothing
    -- (D-010), and we hold no heartbeat for someone else's station — so the
    -- value that would mean that is `no_data`, which claims only that the
    -- archive has none. An accidental `union` of this column with
    -- `observations.outcome` then fails a CHECK instead of quietly returning a
    -- plausible number.
    archive_outcome        text     not null
                                    check (archive_outcome in (
                                        'decoded',
                                        'signal_no_decode',
                                        'no_data',
                                        'unknown'
                                    )),

    -- The archive's own string, verbatim, so every mapping into the four values
    -- above stays auditable against the source rather than trusted.
    source_outcome         text,

    peak_snr_db            double precision,
    frames_decoded         integer  check (frames_decoded >= 0),
    loaded_at              timestamptz not null default now(),

    -- Loading the same artefact twice writes nothing the second time. Keyed on
    -- the transformation as well, so re-normalising under a new version appends
    -- a second row rather than colliding with the first.
    constraint archive_observation_unique
        unique (record_id, source_observation_id, transformation_version),

    constraint archive_observation_window_is_ordered
        check (ended_at is null or ended_at >= started_at)
);

comment on table archive_observations is
    'A reception as an archive published it. Its outcomes are not MSP outcomes: '
    'no_data, not no_signal, because no heartbeat evidence exists. '
    'docs/DECISIONS.md D-139.';

create index archive_observations_satellite_idx
    on archive_observations (satellite_key, started_at);
create index archive_observations_station_idx
    on archive_observations (archive_station_id, started_at);


-- Every stored artefact beside the terms it arrived under.
--
-- "Where did this number come from, and were we allowed to use it?" is a
-- question asked in a viva, and it should be one query rather than a join
-- somebody reconstructs under pressure. The provenance columns are `not null`
-- on both sides, so `select count(*) from ingest_provenance where licence is
-- null` returning zero is a property of the schema and not of the loader —
-- which is exactly what the integration test asserts.
create view ingest_provenance as
select
    r.record_id,
    r.source_id,
    s.name               as source_name,
    s.source_class,
    s.licence,
    s.terms_url,
    s.attribution_entry,
    s.access_constraint,
    r.original_identifier,
    r.source_version,
    r.payload_kind,
    r.retrieved_at,
    r.sha256,
    r.raw_path,
    r.media_type,
    r.byte_count,
    r.valid_from,
    r.valid_to,
    r.superseded_by
from ingest_records r
join ingest_sources s on s.source_id = r.source_id;


-- `observations.provenance` still admits 'archive'. It is retired, not removed.
--
-- Narrowing the CHECK would mean altering a constraint on a compressed
-- hypertable, which D-119 established we may not assume is safe on TimescaleDB
-- 2.29, and the column is published by the public API. So the retirement is a
-- comment — catalogue-only, and safe on any chunk — plus the model:
-- `insert_observation` never sets it, and no row has ever carried it.
comment on column observations.provenance is
    'station or manual. ''archive'' is retired and unused as of 0016: archive '
    'receptions live in archive_observations, because this table cannot key a '
    'reception with no assignment and no registered station. The value stays in '
    'the CHECK because altering a constraint on a compressed hypertable is not '
    'assumed safe (D-119). docs/DECISIONS.md D-139.';
