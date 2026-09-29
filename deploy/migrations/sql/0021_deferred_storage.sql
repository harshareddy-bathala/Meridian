-- 0021 — The four deferred tables: noise, the two profiles, and products
--
-- D-018 deferred these until something produced them and something read them.
-- Stage 19 builds both ends, and this migration is the storage. Each table's
-- producer, consumer and retention are settled in docs/DECISIONS.md:
--   noise_measurements     D-173   one row per reception, dBFS at a stated gain
--   horizon_profiles       D-174, D-175   declared and learned, never merged
--   interference_profiles  D-174   the Stage 17 cells, with the gains behind them
--   products               D-176   a manifest of what a station holds
--
-- Every table carries `simulated`, copied from what the station's registry
-- record says and never inferred (rule 5). Every table is append-only: a new
-- revision, a new dataset or a new mask writes new rows and the old ones stay,
-- as D-015 keeps an old observation. Nothing here is ever deleted by a policy
-- (D-178), and none of these tables has a retention policy.


-- ---------------------------------------------------------------------------
-- noise_measurements (D-173)
--
-- A hypertable, as D-009 planned, because a survey sweep will write many rows a
-- pass where an observation writes one. Partitioned on measured_at, the
-- observation's own started_at: this is D-013's stated exception, taken for the
-- same reason and with the same bound — ingest refuses a started_at outside
-- [now - 30 days, now + 1 hour], so no row lands in a 1970 chunk.
--
-- No compression policy: one row a reception is the observation count, not
-- the heartbeat count. The first survey producer adds one if its volume needs it.
--
-- No foreign key to observations. TimescaleDB refuses a key from one hypertable
-- into another, so the pairing is kept by the ingest path writing both in one
-- transaction, and tests/integration asserts it.

create table noise_measurements (
    id                  bigint      generated always as identity,
    station_id          text        not null references stations (station_id),
    measured_at         timestamptz not null,

    -- 'observation': the floor a station reported with a reception.
    -- 'survey': a dedicated sweep, which nothing produces yet; admitted now so
    -- the first one needs no migration. The two have different duty cycles.
    source              text        not null
                                    check (source in ('observation', 'survey')),
    assignment_id       text,
    revision            integer,

    -- What the station was tuned to: the assignment's frequency.
    centre_freq_hz      bigint      not null check (centre_freq_hz > 0),
    -- The transmitter's, where the catalogue knows it.
    bandwidth_hz        bigint      check (bandwidth_hz > 0),
    -- Where the antenna pointed. Null for an observation's floor: one figure
    -- covers a whole pass, and a station without a rotator pointed nowhere.
    -- The sector is assigned where a profile is derived, from the track (D-159).
    azimuth_deg         double precision
                                    check (azimuth_deg >= 0 and azimuth_deg < 360),

    -- dBFS at a stated gain, never dBm: RF calibration is outside what a
    -- station can honestly claim (D-103, D-104). A floor without its gain is not
    -- a measurement anyone can compare, so both are required.
    noise_floor_dbfs    double precision not null,
    receiver_gain_db    double precision not null,

    simulated           boolean     not null,
    recorded_at         timestamptz not null default now(),

    constraint noise_measurements_pkey primary key (id, measured_at),
    -- An observation's floor names its reception; a sweep names none.
    constraint noise_measurement_source_named check (
        (source = 'observation') = (assignment_id is not null)
        and (assignment_id is null) = (revision is null)
    ),
    constraint noise_measurement_observation_unpointed check (
        source <> 'observation' or azimuth_deg is null
    ),
    constraint noise_measurement_finite check (
        noise_floor_dbfs not in ('Infinity', '-Infinity', 'NaN')
        and receiver_gain_db not in ('Infinity', '-Infinity', 'NaN')
    ),
    -- One row a reception revision. Timescale requires the partitioning column
    -- in every unique key; measured_at is the revision's started_at, so it adds
    -- nothing the pair does not already fix.
    constraint noise_measurement_one_per_revision
        unique (assignment_id, revision, measured_at)
);

select create_hypertable('noise_measurements', by_range('measured_at', interval '7 days'));

create index noise_measurements_station_measured_idx
    on noise_measurements (station_id, measured_at desc);

-- Every observation already holding a floor. An observation with no assignment
-- row has no frequency to file its floor under, and gets no row; ingest never
-- accepts one, so only a hand-inserted row can be in that position.
insert into noise_measurements (
    station_id, measured_at, source, assignment_id, revision, centre_freq_hz,
    bandwidth_hz, noise_floor_dbfs, receiver_gain_db, simulated, recorded_at
)
select o.station_id, o.started_at, 'observation', o.assignment_id, o.revision,
       a.centre_freq_hz,
       case when t.bandwidth_hz > 0 then t.bandwidth_hz end,
       o.noise_floor_dbfs, o.receiver_gain_db, o.simulated, o.submitted_at
from observations o
join assignments a on a.assignment_id = o.assignment_id
left join lateral (
    select bandwidth_hz
    from satellite_transmitters t
    where t.satellite_id = o.satellite_id
      and t.centre_freq_hz = a.centre_freq_hz
      and t.mode = a.mode
      and t.deleted_at is null
    order by t.id
    limit 1
) t on true
where o.noise_floor_dbfs is not null;

comment on table noise_measurements is
    'Measured noise floor, dBFS at a stated receiver gain, one row per reception '
    'revision or survey reading. Append-only, never dropped. '
    'docs/DECISIONS.md D-009, D-103, D-173, D-178.';
comment on column noise_measurements.azimuth_deg is
    'Where the antenna pointed; null for an observation''s floor, which covers a '
    'whole pass. docs/DECISIONS.md D-173.';


-- ---------------------------------------------------------------------------
-- horizon_profiles (D-174, D-175)
--
-- One row per azimuth bin of one profile. A profile is either what a station
-- declared (its capability's horizon_mask, D-031) or what its detections say
-- (Stage 17's learned horizon, D-159), and the two are never merged into one
-- number: a declaration never overwrites a measurement and is never training
-- data for one.
--
-- A bin runs from azimuth_deg for azimuth_width_deg clockwise. A learned bin is
-- a 10° sector. A declared bin is one mask point's step, to the next point,
-- wrapping at 360°, so one point alone is a bin of 360°.
--
-- A plain table: its volume is profiles built, not heartbeats received.

create table horizon_profiles (
    id                  bigint      generated always as identity primary key,
    station_id          text        not null references stations (station_id),
    source              text        not null check (source in ('declared', 'learned')),
    -- Versioned, as the orbit service's uncertainty method is (D-060).
    method              text        not null check (method <> ''),

    -- Declared: which capability's mask. Capabilities are soft-deleted, so the
    -- key never blocks a re-registration.
    capability_id       bigint      references station_capabilities (id),
    -- Learned: the labelled dataset whose settled reports built it, and the span
    -- that dataset covers. A prediction is traced to its profile through the
    -- dataset its decision already names (D-169).
    dataset_sha256      bytea       check (length(dataset_sha256) = 32),
    trained_from        timestamptz,
    trained_until       timestamptz,

    azimuth_deg         double precision not null
                                    check (azimuth_deg >= 0 and azimuth_deg < 360),
    azimuth_width_deg   double precision not null
                                    check (azimuth_width_deg > 0
                                           and azimuth_width_deg <= 360),
    min_elevation_deg   double precision not null
                                    check (min_elevation_deg between -90 and 90),
    -- Learned: the detections behind the bin; zero means it is at its prior
    -- (D-161). Declared: null, because a declaration is not a sample.
    sample_count        integer     check (sample_count >= 0),

    built_at            timestamptz not null default now(),
    simulated           boolean     not null,

    constraint horizon_profile_declared_whole check (
        source <> 'declared' or (
            capability_id is not null and dataset_sha256 is null
            and trained_from is null and trained_until is null
            and sample_count is null
        )
    ),
    constraint horizon_profile_learned_whole check (
        source <> 'learned' or (
            capability_id is null and dataset_sha256 is not null
            and trained_from is not null and trained_until is not null
            and trained_from <= trained_until
            and sample_count is not null
        )
    )
);

-- One bin per profile. A learned profile is identified by its dataset, so
-- building from one dataset twice cannot write a second copy. A declared one is
-- identified by when it was written: a mask that changes is written again, and
-- the earlier one stays.
create unique index horizon_profiles_learned_bin_idx
    on horizon_profiles (station_id, method, dataset_sha256, azimuth_deg)
    where source = 'learned';
create unique index horizon_profiles_declared_bin_idx
    on horizon_profiles (capability_id, method, built_at, azimuth_deg)
    where source = 'declared';

create index horizon_profiles_station_idx
    on horizon_profiles (station_id, source, built_at desc);
create index horizon_profiles_dataset_idx
    on horizon_profiles (dataset_sha256) where dataset_sha256 is not null;

comment on table horizon_profiles is
    'Declared and learned horizon by azimuth bin, kept apart. Only the declared '
    'one constrains scheduling. docs/DECISIONS.md D-031, D-159, D-174, D-175.';


-- ---------------------------------------------------------------------------
-- interference_profiles (D-174)
--
-- Stage 17's interference cells, as its features compute them: a 45° sector of
-- the pass's peak by a 4-hour band of local solar hour, the cell's median floor
-- over the station's median, shrunk towards 0 dB by its count (D-159). Every
-- cell of a profile is written, an empty one at its prior with a count of zero,
-- because D-161 says no profile is ever missing.
--
-- The gains behind a cell are stated, because Stage 27 judges a raised floor
-- "at the same gain" and has to be able to refuse a cell whose gains differ.

create table interference_profiles (
    id                  bigint      generated always as identity primary key,
    station_id          text        not null references stations (station_id),
    method              text        not null check (method <> ''),
    dataset_sha256      bytea       not null check (length(dataset_sha256) = 32),
    trained_from        timestamptz not null,
    trained_until       timestamptz not null,

    azimuth_deg         double precision not null
                                    check (azimuth_deg >= 0 and azimuth_deg < 360),
    azimuth_width_deg   double precision not null
                                    check (azimuth_width_deg > 0
                                           and azimuth_width_deg <= 360),
    -- Local solar hour: UTC plus longitude over 15, as the propensity's (D-152).
    hour_start          integer     not null check (hour_start between 0 and 23),
    hour_width          integer     not null check (hour_width between 1 and 24),

    -- Relative to the station's own median floor, which is kept beside it so the
    -- cell can be read back as dBFS.
    noise_lift_db       double precision not null,
    station_median_dbfs double precision,
    sample_count        integer     not null check (sample_count >= 0),
    gain_min_db         double precision,
    gain_max_db         double precision,

    built_at            timestamptz not null default now(),
    simulated           boolean     not null,

    constraint interference_profile_trained_ordered check (trained_from <= trained_until),
    -- A cell with readings states their gains; an empty one has none to state.
    constraint interference_profile_gains_whole check (
        (sample_count = 0) = (gain_min_db is null)
        and (gain_min_db is null) = (gain_max_db is null)
        and gain_min_db <= gain_max_db
    ),
    constraint interference_profile_cell_unique unique (
        station_id, method, dataset_sha256, azimuth_deg, hour_start
    )
);

create index interference_profiles_station_idx
    on interference_profiles (station_id, built_at desc);

comment on table interference_profiles is
    'Noise floor over the station median, by peak sector and hour of local solar '
    'time, with the gains behind each cell. docs/DECISIONS.md D-009, D-159, D-174.';


-- ---------------------------------------------------------------------------
-- products (D-176)
--
-- What a station declared it holds from one reception: a waterfall, an image,
-- decoded frames. Metadata only — MSP 0.x defines no transfer (D-029), so `uri`
-- says where the station keeps it, not where anyone can fetch it.
--
-- observations.products_json stays the verbatim record of what was sent
-- (D-018). This table is its normalised form, one row per element with a
-- valid sha256 and kind; an element without them stays in products_json and
-- gets no row here.
--
-- A plain table with a foreign key into the observations hypertable, which
-- TimescaleDB 2.29 permits from a plain table. The key needs started_at because
-- it is part of observations' primary key (D-013).

create table products (
    id                      bigint      generated always as identity primary key,
    assignment_id           text        not null,
    revision                integer     not null,
    observation_started_at  timestamptz not null,
    station_id              text        not null references stations (station_id),
    -- Position in the submitted array, so a row can be matched to its element.
    element_index           integer     not null check (element_index >= 0),

    kind                    text        not null check (kind <> ''),
    sha256                  bytea       not null check (length(sha256) = 32),
    size_bytes              bigint      check (size_bytes >= 0),
    uri                     text,

    created_at              timestamptz not null default now(),
    simulated               boolean     not null,

    constraint product_observation_fk
        foreign key (assignment_id, revision, observation_started_at)
        references observations (assignment_id, revision, started_at),
    constraint product_element_unique unique (assignment_id, revision, element_index)
);

-- Stage 30's evidence dataset refers to a product by its hash (D-104).
create index products_sha256_idx on products (sha256);

-- Every element already held. The rule is ingest's: an object whose `kind` is a
-- non-empty string and whose `sha256` is 64 hex digits. `size_bytes` and `uri`
-- are taken where they have the right type and left null where they do not.
insert into products (
    assignment_id, revision, observation_started_at, station_id, element_index,
    kind, sha256, size_bytes, uri, created_at, simulated
)
select o.assignment_id, o.revision, o.started_at, o.station_id,
       (e.position - 1)::integer,
       e.element ->> 'kind',
       decode(lower(e.element ->> 'sha256'), 'hex'),
       case
           when jsonb_typeof(e.element -> 'size_bytes') = 'number'
                and (e.element ->> 'size_bytes') ~ '^[0-9]{1,18}$'
           then (e.element ->> 'size_bytes')::bigint
       end,
       case
           when jsonb_typeof(e.element -> 'uri') = 'string' then e.element ->> 'uri'
       end,
       o.submitted_at,
       o.simulated
from observations o
cross join lateral jsonb_array_elements(
    case when jsonb_typeof(o.products_json) = 'array'
         then o.products_json else '[]'::jsonb end
) with ordinality as e(element, position)
where jsonb_typeof(e.element) = 'object'
  and jsonb_typeof(e.element -> 'kind') = 'string'
  and e.element ->> 'kind' <> ''
  and jsonb_typeof(e.element -> 'sha256') = 'string'
  and e.element ->> 'sha256' ~ '^[0-9a-fA-F]{64}$';

comment on table products is
    'A manifest of what a station declared it holds from a reception, by '
    'sha256. No bytes and no transfer: uri is where the station keeps it. '
    'docs/DECISIONS.md D-029, D-176.';
comment on column products.uri is
    'Where the declaring station keeps the product; not fetchable while MSP '
    'defines no transfer, and never published. docs/DECISIONS.md D-176.';
