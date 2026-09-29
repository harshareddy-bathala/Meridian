-- 0022 — areas of interest, and the regional alerts raised about them
--
-- Stage 32 watches places. An area of interest is a place and a label; what the
-- ingested public products say about it over time, and which of our own
-- receptions cover it, are computed from a dataset snapshot and published as
-- files, never stored here (D-229). What is stored is what cannot be
-- recomputed and must be kept: the areas an operator registered, and the
-- alerts raised about them together with every attempt to deliver one.
--
-- **Nothing here describes a person** (D-137, D-227). No owner, no contact, no
-- address: a label and a polygon. Who registers an area is an operator at the
-- command line; nothing here is published until the team settles D-137.
--
-- **Not the platform's own monitoring.** Prometheus and Grafana watch Meridian;
-- these tables watch places. They share no name with the metrics, the alert
-- rules or Alertmanager, and none of them is read by `/metrics` (Stage 32).
--
-- See DECISIONS.md D-137, D-227, D-229, D-231, D-232.


-- A place and a label.
--
-- `geometry` is a GeoJSON Polygon with one ring, longitude before latitude, as
-- GeoJSON orders them. jsonb rather than a geometry type because PostGIS is not
-- installed and nothing here needs a spatial index: areas are few, and every
-- spatial question is answered from a snapshot, in Python.
--
-- The centroid and area are derived by `meridian.regions.geometry` and stored
-- so a listing does not need the geometry; `geometry_sha256` names the exact
-- polygon, so a change of shape is a new area rather than a rewrite of one
-- whose series were already computed.
create table areas_of_interest (
    area_id            bigint      generated always as identity primary key,
    label              text        not null
                                   check (length(btrim(label)) between 1 and 120),
    geometry           jsonb       not null
                                   check (geometry->>'type' = 'Polygon'),
    geometry_sha256    bytea       not null check (octet_length(geometry_sha256) = 32),
    centroid_lat_deg   double precision not null check (centroid_lat_deg between -90 and 90),
    centroid_lon_deg   double precision not null check (centroid_lon_deg between -180 and 180),
    area_km2           double precision not null check (area_km2 > 0),
    created_at         timestamptz not null default now(),

    -- Retires an area without deleting it: a series that vanished could not be
    -- checked against what was published from it.
    active             boolean     not null default true,
    notes              text,

    constraint area_geometry_is_unique unique (geometry_sha256)
);

comment on table areas_of_interest is
    'A place and a label — never a person. Series and coverage are computed from '
    'snapshots, not stored. docs/DECISIONS.md D-137, D-227, D-229.';


-- One regional alert: a change against a baseline that crossed its threshold,
-- with the interval that says how sure we are.
--
-- `alert_id` is derived from what the alert is about — area, quantity, rule,
-- both periods and the report it came from — so recording the same report twice
-- writes nothing the second time.
create table region_alerts (
    alert_id           text        primary key check (alert_id ~ '^ra_[0-9a-f]{24}$'),
    area_id            bigint      not null references areas_of_interest (area_id),
    quantity           text        not null check (quantity ~ '^[a-z][a-z0-9_]{0,63}$'),
    rule               text        not null check (length(btrim(rule)) > 0),
    baseline_from      timestamptz not null,
    baseline_until     timestamptz not null,
    current_from       timestamptz not null,
    current_until      timestamptz not null,
    baseline_value     double precision not null,
    current_value      double precision not null,
    change             double precision not null,
    change_low         double precision not null,
    change_high        double precision not null,
    threshold          double precision not null,
    report_sha256      bytea       not null check (octet_length(report_sha256) = 32),
    summary            text        not null check (length(btrim(summary)) > 0),
    recorded_at        timestamptz not null default now(),

    constraint region_alert_interval_is_ordered
        check (change_low <= change and change <= change_high),
    constraint region_alert_periods_are_ordered
        check (baseline_from < baseline_until and current_from < current_until)
);

comment on table region_alerts is
    'A change in an area against its baseline, beyond its threshold with its '
    'interval stated. Append-only. docs/DECISIONS.md D-231.';


-- Every attempt to deliver an alert, appended.
--
-- Stage 29 delivers notifications and is not built, so the only channel is
-- `record_only`: the alert is recorded, and the attempt says that no channel
-- exists yet. Stage 29 widens the CHECK when it adds one.
create table region_alert_deliveries (
    delivery_id        bigint      generated always as identity primary key,
    alert_id           text        not null references region_alerts (alert_id),
    channel            text        not null check (channel in ('record_only')),
    outcome            text        not null
                                   check (outcome in ('recorded', 'delivered', 'failed')),
    detail             text        not null check (length(btrim(detail)) > 0),
    attempted_at       timestamptz not null default now()
);

create index region_alert_deliveries_alert_idx
    on region_alert_deliveries (alert_id, attempted_at);


-- The provenance view gains the ground an artefact covers, appended so every
-- existing column keeps its position. A regional series needs it to tell a
-- day that was asked about and had no fires from a day nobody asked about
-- (D-221), and to place a tile behind an area as imagery.
create or replace view ingest_provenance as
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
    r.superseded_by,
    r.spatial_extent
from ingest_records r
join ingest_sources s on s.source_id = r.source_id;
