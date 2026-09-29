-- 0021 — published environmental and space-weather values
--
-- Stage 31 brings in values other people publish about the atmosphere, the
-- ionosphere and the ground: a geomagnetic index, cloud cover, fire
-- detections, vegetation, precipitation, aerosol, night-time lights. They
-- arrive through Stage 14's ingest unchanged (D-132): an artefact is an
-- `ingest_records` row under an `ingest_sources` row that carries its licence
-- and terms, and this table holds what an artefact normalises to.
--
-- Nothing here is read at runtime by scheduling or reception. Features read
-- these rows from a dataset snapshot (Stage 15), never from this table while a
-- pass is being decided, and no pass is ever skipped because of a value in it
-- (D-131). The table exists so that a snapshot can carry them.
--
-- **`published_at` is the load-bearing column.** The value a pass may use is
-- the one published before it (D-131). A revision of the same interval is a new
-- row with a later `published_at`, never an update, so a model that selects on
-- `observed_from` alone would read the future and a model that selects on
-- `published_at` cannot. `published_at` is never later than the retrieval that
-- brought the value in, and `published_basis` says whether the artefact
-- declared it or our own fetch bounds it (D-222).
--
-- **A missing value is a row with no value and a reason** (D-221), never a
-- zero and never absent. "The source published a gap for this hour" and "we
-- never asked about this hour" are different facts, and the pre-pass rule must
-- not fall back to an older value across a published gap.
--
-- **A tile is never a row here** (D-133). A rendered tile is an
-- `ingest_records` row with `payload_kind = 'tile'`, displayed and referenced
-- and never read for a number; the loader skips it before a normaliser runs.
--
-- Plain table, no hypertable, no compression, for the reason 0016 gives for the
-- archive tables: append-only, read in bulk by the snapshot export, and
-- frequently loaded long after the interval it describes. No `simulated`
-- column: these rows describe the world, not a station's reception, and a
-- simulated station's pass that reads one keeps its flag on the observation.
--
-- See DECISIONS.md D-131, D-132, D-133, D-140, D-221, D-222.

create table environment_samples (
    sample_id              bigint      generated always as identity primary key,
    record_id              bigint      not null references ingest_records (record_id),
    source_id              text        not null references ingest_sources (source_id),

    -- The normaliser that produced this row. Part of the key: re-normalising an
    -- artefact under a new version appends beside the old row (D-140).
    transformation_version text        not null
                                       check (length(btrim(transformation_version)) > 0),

    -- What makes the sample unique inside its artefact, in the source's terms.
    series_key             text        not null check (length(btrim(series_key)) > 0),

    -- Of this row's canonical form. The same key under the same version with a
    -- different digest is a nondeterministic normaliser, refused by the loader.
    content_sha256         bytea       not null
                                       check (octet_length(content_sha256) = 32),

    -- Lowercase free text at first, as `station_capabilities.modes` is:
    -- product naming varies too much between sources to freeze yet.
    quantity               text        not null check (quantity ~ '^[a-z][a-z0-9_]{0,63}$'),
    value                  double precision,
    missing_reason         text,
    value_unit             text        not null check (length(btrim(value_unit)) > 0),

    -- The interval the value describes, closed at both ends.
    observed_from          timestamptz not null,
    observed_to            timestamptz not null,

    published_at           timestamptz not null,
    published_basis        text        not null
                                       check (published_basis in (
                                           'source_declared', 'retrieved'
                                       )),

    -- The source's product name and version, as published.
    product                text        not null check (length(btrim(product)) > 0),

    -- Where, if anywhere: a pair or nothing, as `archive_stations` holds it.
    lat_deg                double precision check (lat_deg between -90 and 90),
    lon_deg                double precision check (lon_deg between -180 and 180),
    footprint_m            double precision check (footprint_m > 0),

    -- The source's own quality flag, verbatim.
    quality                text,

    loaded_at              timestamptz not null default now(),

    constraint environment_sample_unique
        unique (record_id, series_key, transformation_version),
    constraint environment_sample_value_or_reason
        check ((value is null) <> (missing_reason is null)),
    constraint environment_sample_missing_reason_is_said
        check (missing_reason is null or length(btrim(missing_reason)) > 0),
    constraint environment_sample_interval_is_ordered
        check (observed_to >= observed_from),
    constraint environment_sample_location_is_a_pair
        check ((lat_deg is null) = (lon_deg is null))
);

comment on table environment_samples is
    'Values public products published, one row per value per artefact. '
    'Append-only; a revision is a new row with a later published_at. A missing '
    'value is a row with a reason, never a zero. docs/DECISIONS.md D-221, D-222.';

create index environment_samples_quantity_idx
    on environment_samples (quantity, observed_from);
create index environment_samples_published_idx
    on environment_samples (quantity, published_at);
create index environment_samples_record_idx
    on environment_samples (record_id);
