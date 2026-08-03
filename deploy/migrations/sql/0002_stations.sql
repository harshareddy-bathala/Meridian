-- 0002 — invite tokens, stations, capabilities
--
-- Conventions (DATA-MODEL.md): all timestamps timestamptz UTC; frequencies bigint
-- Hz, never floats and never MHz; angles double precision degrees; `simulated`
-- not null on every table that can hold simulated data.
--
-- Enums are text + CHECK rather than Postgres enum types (D-013): a CHECK is
-- dropped and recreated inside one transaction, an enum alteration is not, and
-- merged migrations are never edited.

-- Invite tokens are rows, not a configuration value (D-020). MSP §4.1 requires a
-- token to be *consumed* by a successful registration and to fail on reuse. A
-- single env var cannot be consumed, cannot be revoked per operator, and cannot
-- admit a second station.
create table invite_tokens (
    token_sha256            bytea       primary key,
    label                   text        not null,
    created_at              timestamptz not null default now(),
    expires_at              timestamptz,
    consumed_at             timestamptz,
    consumed_by_station_id  text,

    -- Null for an ordinary invite, which admits a NEW station. When set the
    -- invite is BOUND: it admits only the station it names and rotates that
    -- station's bearer token (D-034).
    --
    -- This is the recovery path for a station that received 401. Its own invite
    -- was consumed at registration and cannot be presented again, and D-023's
    -- window has long since closed — it has heartbeat and it registered months
    -- ago. Without this column the operator's replacement invite is simply an
    -- unconsumed invite, so registration reads it as a new station and creates a
    -- duplicate row for one physical installation.
    --
    -- A bound invite ignores D-023's recovery window. The window exists to
    -- require authorisation for a rotation that is not a dropped-packet retry;
    -- an operator issuing an invite against a named station is that
    -- authorisation, stated explicitly rather than inferred from a clock.
    issued_for_station_id   text,

    -- Consumption is all-or-nothing: a token cannot be half-used.
    constraint invite_consumed_together check (
        (consumed_at is null and consumed_by_station_id is null)
        or (consumed_at is not null and consumed_by_station_id is not null)
    ),

    -- A bound invite may only ever be consumed by the station it names. Enforced
    -- here rather than in the register handler because it is the whole security
    -- property of the binding: a bound invite that could be redeemed by anyone
    -- else is a credential rotation on someone else's station.
    constraint invite_bound_consumed_by_its_station check (
        issued_for_station_id is null
        or consumed_by_station_id is null
        or issued_for_station_id = consumed_by_station_id
    )
);

comment on table invite_tokens is
    'Single-use registration invites. See docs/DECISIONS.md D-006 and D-020.';


create table stations (
    -- MSP already forces this id to exist, be unique and be stable, so it is the
    -- primary key; a surrogate beside it would buy nothing but joins (D-013).
    station_id          text        primary key,
    name                text        not null,
    operator            text        not null,

    lat_deg             double precision not null check (lat_deg between -90 and 90),
    lon_deg             double precision not null check (lon_deg between -180 and 360),
    -- Altitude is required, not defaulted: MSP §4.1 says it materially affects
    -- pass geometry, and a silent 0 biases every prediction for a rooftop site.
    alt_m               double precision not null,

    registered_at       timestamptz not null default now(),

    -- Opaque bearer token stored hashed, not signed (D-017).
    token_sha256        bytea       not null,
    token_issued_at     timestamptz not null default now(),
    token_revoked_at    timestamptz,

    -- Hash of the key the client generated and kept, which is what makes
    -- registration recoverable (D-023). The register response carries the only
    -- copy of the bearer token; if it is lost in flight the invite is consumed
    -- and the station has nothing. Re-presenting the same invite with a matching
    -- key rotates the token onto this same station_id instead of failing.
    --
    -- Recovery is allowed only while last_heartbeat_at is null AND the request is
    -- within the recovery window of registered_at — both, not either. A station
    -- that has heartbeat holds a working token; one that never heartbeat but
    -- registered a month ago would otherwise leave its consumed invite live
    -- forever. Past that window, rotation is a bound invite (D-034) and the same
    -- key proves identity there too.
    registration_key_sha256 bytea   not null,

    simulated           boolean     not null default false,
    -- Required when simulated, absent when not (MSP §5). The constraint *is* the
    -- protocol rule, which is why it lives here rather than in application code.
    simulator_run_id    text,
    seed                bigint,

    -- The platform's derived conclusion, distinct from the `state` a station
    -- reports and the `health` object it sends (D-013). Thresholds come from
    -- SC-5: stale at 60 s, offline at 90 s.
    liveness            text        not null default 'never_seen'
                                    check (liveness in ('never_seen', 'online', 'stale', 'offline')),
    last_heartbeat_at   timestamptz,

    client_impl         text,
    client_version      text,

    deleted_at          timestamptz,

    constraint station_simulated_fields check (
        (simulated and simulator_run_id is not null and seed is not null)
        or (not simulated and simulator_run_id is null and seed is null)
    )
);

comment on column stations.liveness is
    'Derived by the platform, never reported by the station. See D-013.';

create index stations_liveness_idx on stations (liveness) where deleted_at is null;
create index stations_simulated_idx on stations (simulated) where deleted_at is null;

-- Both foreign keys are added after `stations` exists, because invite_tokens is
-- created first: an invite is what admits the station that would otherwise have
-- to exist before it.
alter table invite_tokens
    add constraint invite_consumed_by_fk
    foreign key (consumed_by_station_id) references stations (station_id);

alter table invite_tokens
    add constraint invite_issued_for_fk
    foreign key (issued_for_station_id) references stations (station_id);

create index invite_tokens_issued_for_idx
    on invite_tokens (issued_for_station_id) where issued_for_station_id is not null;


-- A station may have several capabilities: a VHF fixed antenna and a UHF tracking
-- antenna are distinct, with different ranges, polarisation and elevation limits.
create table station_capabilities (
    id                  bigint      generated always as identity primary key,
    station_id          text        not null references stations (station_id),

    band                text        not null
                                    check (band in ('vhf', 'uhf', 'l', 's', 'other')),
    freq_min_hz         bigint      not null,
    freq_max_hz         bigint      not null,
    -- Free-text lowercase in Phase 1: decoder naming varies too much across
    -- projects to freeze an enum this early.
    modes               text[]      not null default '{}',
    polarisation        text        not null
                                    check (polarisation in ('rhcp', 'lhcp', 'linear_v',
                                                            'linear_h', 'linear', 'none')),
    tracking            boolean     not null default false,
    -- The station's *declared* floor, not its measured horizon. The platform
    -- learns the real obstruction profile from outcome history and may override
    -- this downward per azimuth (MSP §4.1).
    min_elevation_deg   double precision not null
                                    check (min_elevation_deg between -90 and 90),

    -- An obstruction the operator already knows about, azimuth-resolved and
    -- optional: [{"az_deg": 0, "min_el_deg": 25}, ...] (D-031, MSP §4.1).
    --
    -- DECLARED, and it never merges into a learned profile. horizon_profiles
    -- (Phase 2) carries source in ('declared','learned') and the scheduler takes
    -- max(declared, learned) per bin, so a declaration constrains scheduling
    -- immediately without becoming training data for the model that would
    -- otherwise be predicting it.
    horizon_mask_json   jsonb       not null default '[]'::jsonb,

    deleted_at          timestamptz,

    constraint capability_freq_range check (freq_min_hz <= freq_max_hz),
    constraint capability_horizon_mask_is_array check (
        jsonb_typeof(horizon_mask_json) = 'array'
    )
);

create index station_capabilities_station_idx on station_capabilities (station_id);
