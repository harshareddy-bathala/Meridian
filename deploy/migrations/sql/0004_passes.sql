-- 0004 — computed passes and scheduler assignments

-- Computed pass windows, NOT observations. A pass exists whether or not anyone
-- observed it, which is exactly what the completeness ratio in EVALUATION.md §4.1
-- requires: the denominator is computed by us, never taken from an archive.
create table passes (
    id                  bigint      generated always as identity primary key,
    satellite_id        text        not null references satellites (satellite_id),
    station_id          text        not null references stations (station_id),

    aos                 timestamptz not null,
    los                 timestamptz not null,
    max_elevation_deg   double precision not null check (max_elevation_deg between -90 and 90),
    max_elevation_at    timestamptz not null,
    aos_azimuth_deg     double precision not null check (aos_azimuth_deg between 0 and 360),
    los_azimuth_deg     double precision not null check (los_azimuth_deg between 0 and 360),

    -- Which element set produced this prediction, so timing error can be
    -- attributed to element-set age (DATA-MODEL.md).
    element_set_id      bigint      not null references element_sets (id),

    -- The elevation floor this window was computed against. Without it a stored
    -- pass is uninterpretable: a 5-degree pass is *absent* from a 10-degree
    -- search rather than nonexistent, and the completeness ratio would silently
    -- compare windows computed against different floors.
    min_elevation_deg   double precision not null,

    computed_at         timestamptz not null default now(),
    -- Propagated from the station (D-013), never read from a payload.
    simulated           boolean     not null default false,

    constraint pass_window_ordered check (aos < los)
);

create index passes_station_aos_idx on passes (station_id, aos);
create index passes_satellite_aos_idx on passes (satellite_id, aos);


-- Scheduler output. Links a pass to a station with a decision record.
-- Skipped passes are recorded too: a scheduler that only logs what it chose
-- cannot be evaluated.
create table assignments (
    assignment_id       text        primary key,
    pass_id             bigint      not null references passes (id),
    station_id          text        not null references stations (station_id),
    issued_at           timestamptz not null default now(),

    -- The assignment's own window, NOT the pass's aos/los (D-021). Widened from
    -- the pass by timing_uncertainty_s, because a station recording at exactly
    -- the predicted acquisition time starts after a pass whose element set was
    -- stale has already begun.
    start_at            timestamptz not null,
    end_at              timestamptz not null,

    -- Needed to produce the MSP §4.3 assignment message.
    centre_freq_hz      bigint      not null,
    mode                text        not null,
    timing_uncertainty_s double precision not null,

    predicted_yield     double precision check (predicted_yield between 0 and 1),
    priority            double precision not null default 1.0,

    -- What the scheduler wanted, as distinct from what the station did.
    decision            text        not null default 'scheduled'
                                    check (decision in ('scheduled', 'skipped')),
    -- Human-readable and shown on the dashboard. PROJECT.md §13 calls the screen
    -- that displays this "the entire project", so it is written when the decision
    -- is made and never reconstructed afterwards.
    reason              text        not null,
    -- Which ablation configuration produced the prediction — required for the
    -- evaluation in EVALUATION.md §3 to be reproducible.
    model_config        text,

    -- What the station did with it (D-008). Derived by reconciling
    -- held_assignments against what was issued.
    --
    --   issued -> held -> in_progress -> reported
    --          \
    --           -> expired
    --
    -- `expired` is the DECLINE case: the station never took the work. It is not
    -- the same as an observation with outcome 'not_attempted', which means the
    -- station took the work and then failed to start. The reliability layer needs
    -- both and must never merge them.
    --
    -- Phase 1 does not reissue; an assignment expires only after end_at (D-022).
    state               text        not null default 'issued'
                                    check (state in ('issued', 'held', 'in_progress',
                                                     'reported', 'expired')),

    simulated           boolean     not null default false,

    constraint assignment_window_ordered check (start_at < end_at)
);

create index assignments_station_state_idx on assignments (station_id, state);
create index assignments_station_start_idx on assignments (station_id, start_at);
create index assignments_pass_idx on assignments (pass_id);
