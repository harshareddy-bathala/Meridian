-- 0030 — the receive-chain watch: each chain's signal by elevation, and warnings
--
-- Stage 28 compares a receive chain's signal at each elevation with its own past
-- and warns when it falls short, before a reception fails (D-290). A chain is a
-- capability: the one whose frequency range holds the assignment's frequency.
--
-- `signal_baselines` holds one row per baseline, not per bin (D-291). Its bins
-- are trained together and judged together, so a warning names the whole row it
-- was raised against. `evidence` says what the bins were made of:
-- `samples` are MSP 0.3's snr_samples, each placed at its elevation by the
-- pass's own element set; `peak` is MSP 0.2's peak_snr_db at the pass's maximum
-- elevation. The two are never mixed in one baseline. A baseline is written once
-- and never updated: building again over the same interval under the same
-- configuration writes nothing (signal_baseline_once), and a later interval
-- writes beside it.
--
-- `receive_chain_warnings` is raised once and cleared once, and never deleted
-- (D-292). SC-9's detection delay and false-alarm rate are counted from these
-- rows, so a deleted false alarm would be a rate that improves itself. At most
-- one warning is open per chain, method and configuration.
--
-- A warning's baseline is referenced together with its station, capability and
-- `simulated` (D-291). A baseline's `simulated` is its station's, so a warning on
-- a measured station can only name that station's own measured baseline: the
-- roadmap's "a simulated station's baseline is never applied to a measured one"
-- is a check the database makes on every insert.
--
-- `simulated` is copied from the station, never inferred (rule 5). Simulator
-- ground truth never enters either table (D-105). "Health" is in neither name
-- (D-013, D-104).
--
-- Plain tables: their volume is baselines built and warnings raised.

create table signal_baselines (
    id                  bigint      generated always as identity primary key,
    station_id          text        not null references stations (station_id),
    -- The receive chain. Capabilities are soft-deleted, so the key never blocks
    -- a re-registration.
    capability_id       bigint      not null references station_capabilities (id),
    evidence            text        not null check (evidence in ('samples', 'peak')),
    bin_width_deg       double precision not null
                                    check (bin_width_deg > 0 and bin_width_deg <= 90),
    -- [{"elevation_deg", "median_db", "p10_db", "receptions", "samples"}], one
    -- per bin with enough receptions, lowest elevation first.
    bins_json           jsonb       not null
                                    check (jsonb_typeof(bins_json) = 'array'
                                           and jsonb_array_length(bins_json) > 0),
    receptions          integer     not null check (receptions > 0),
    trained_from        timestamptz not null,
    trained_until       timestamptz not null,
    method              text        not null check (method ~ '^baseline-[0-9]+$'),
    config_sha256       bytea       not null check (length(config_sha256) = 32),
    computed_at         timestamptz not null default now(),
    simulated           boolean     not null,

    constraint signal_baseline_interval check (trained_from < trained_until),
    constraint signal_baseline_once unique (
        station_id, capability_id, evidence, method, config_sha256,
        trained_from, trained_until
    ),
    -- What a warning references (D-291).
    constraint signal_baseline_chain unique (id, station_id, capability_id, simulated)
);

create index signal_baselines_chain_idx
    on signal_baselines (station_id, capability_id, evidence, trained_until desc);

comment on table signal_baselines is
    'One receive chain''s signal by elevation over a stated interval, written '
    'once (D-290, D-291).';


create table receive_chain_warnings (
    id                  bigint      generated always as identity primary key,
    station_id          text        not null,
    capability_id       bigint      not null,
    baseline_id         bigint      not null,
    -- When the watch concluded, which is when the warning could be seen (D-292).
    raised_at           timestamptz not null,
    -- How far short of its baseline the chain was, in decibels, when raised.
    shortfall_db        double precision not null check (shortfall_db > 0),
    -- [{"elevation_deg", "shortfall_db", "receptions"}]: the bins that fell short.
    affected_bins_json  jsonb       not null
                                    check (jsonb_typeof(affected_bins_json) = 'array'),
    -- The receptions read, and the thresholds they were read under.
    evidence_json       jsonb       not null
                                    check (jsonb_typeof(evidence_json) = 'object'),
    -- Written once, together, when the shortfall recovers.
    cleared_at          timestamptz,
    cleared_shortfall_db double precision,
    method              text        not null check (method ~ '^watch-[0-9]+$'),
    config_sha256       bytea       not null check (length(config_sha256) = 32),
    simulated           boolean     not null,

    constraint receive_chain_warning_baseline_fk
        foreign key (baseline_id, station_id, capability_id, simulated)
        references signal_baselines (id, station_id, capability_id, simulated),
    constraint receive_chain_warning_cleared_whole
        check ((cleared_at is null) = (cleared_shortfall_db is null)),
    constraint receive_chain_warning_cleared_after
        check (cleared_at is null or cleared_at >= raised_at)
);

-- One open warning per chain, method and configuration (D-292).
create unique index receive_chain_warnings_open_idx
    on receive_chain_warnings (station_id, capability_id, method, config_sha256)
    where cleared_at is null;

create index receive_chain_warnings_station_idx
    on receive_chain_warnings (station_id, raised_at);

comment on table receive_chain_warnings is
    'A receive chain falling short of its own baseline: raised once, cleared '
    'once, never deleted (D-292).';
