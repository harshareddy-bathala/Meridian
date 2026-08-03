-- 0006 — heartbeats (hypertable)
--
-- THIS TABLE IS WHAT ALLOWS ABSENCE TO BE INTERPRETED. Without a heartbeat
-- confirming a station was listening on the right frequency for the right target,
-- a missing observation is meaningless — it could be a miss, or a station that was
-- switched off. Absence is not a miss.

create table heartbeats (
    id                  bigint      generated always as identity,
    station_id          text        not null references stations (station_id),

    -- sent_at is the station's clock and is untrusted. received_at is ours and is
    -- the partition column (D-013): a station with a dead RTC reporting
    -- 1970-01-01 must not be able to create a 1970 chunk.
    sent_at             timestamptz not null,
    received_at         timestamptz not null default now(),

    -- What the station REPORTED (MSP §4.2). Distinct from stations.liveness,
    -- which is what the platform concluded — see D-013 on why these are not both
    -- called `state`.
    state               text        not null
                                    check (state in ('idle', 'slewing', 'listening',
                                                     'processing', 'degraded', 'maintenance')),

    -- The mechanism D-003 and D-008 rest on. The platform reconciles this against
    -- what it issued; A DECLINE IS ABSENCE FROM THIS LIST. Never nullable: MSP
    -- §4.2 says an empty list is meaningful and must be sent as [], so "holds
    -- nothing" and "said nothing" must stay distinguishable (D-014).
    held_assignments    text[]      not null default '{}',

    -- The listening block is the most important field in the protocol. With it,
    -- the platform can assert that a station was tuned to a specific frequency
    -- for a specific target at a specific time and heard nothing — which is a
    -- real measurement rather than an absence.
    listening_assignment_id text,
    listening_satellite_id  text,
    listening_freq_hz       bigint,
    -- mode is stored, not dropped (D-028). A station tuned to the right
    -- frequency running the wrong demodulator did not observe the pass, and
    -- Registry.was_listening() is the only authority on what counts as a
    -- confirmed miss — it cannot answer honestly without this column.
    listening_mode          text,

    -- Opaque diagnostic JSON, stored verbatim. Capped at 4 KiB by the ingest
    -- path (D-028): unbounded opaque JSON written every 30 s by fifty stations
    -- is storage exhaustion with no attacker required.
    health_json         jsonb       not null default '{}'::jsonb,

    -- Copied from the station's registry record, never read from the payload.
    -- D-013 ruled that the flag extends to heartbeats; without it no dashboard
    -- query can honour the rule that simulated and measured data never mix.
    simulated           boolean     not null default false,

    -- Both nullable, and NULL means unknown — never conflated with 0.0. A station
    -- claiming a perfect clock and one that cannot measure its own are opposite
    -- cases. EVALUATION.md §6.1 discards timing error smaller than the reported
    -- uncertainty, which needs both numbers (D-016).
    clock_offset_s      double precision,
    clock_uncertainty_s double precision,

    -- Timescale requires the partitioning column in every unique key.
    constraint heartbeats_pkey primary key (id, received_at),

    -- If any part of the listening block is present, all of it must be. A partial
    -- block cannot support the assertion the block exists to make.
    constraint heartbeat_listening_complete check (
        (listening_assignment_id is null
         and listening_satellite_id is null
         and listening_freq_hz is null
         and listening_mode is null)
        or (listening_assignment_id is not null
            and listening_satellite_id is not null
            and listening_freq_hz is not null
            and listening_mode is not null)
    )
);

select create_hypertable('heartbeats', by_range('received_at', interval '1 day'));

-- The index behind Registry.was_listening, which is the query every reliability
-- metric in the project ultimately depends on.
create index heartbeats_listening_idx
    on heartbeats (station_id, listening_satellite_id, received_at desc)
    where listening_assignment_id is not null;

create index heartbeats_station_received_idx on heartbeats (station_id, received_at desc);
create index heartbeats_simulated_idx on heartbeats (simulated, received_at desc);

alter table heartbeats set (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'station_id',
    timescaledb.compress_orderby = 'received_at desc'
);
select add_compression_policy('heartbeats', interval '7 days');

-- Retention is deliberately absent here too. DATA-MODEL.md specifies full
-- resolution for 90 days then downsampled; the downsampling aggregate is Phase 3
-- and the drop policy lands with it, not before it.
