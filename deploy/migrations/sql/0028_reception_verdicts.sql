-- 0028 — reception verdicts: a calibrated probability that a reception is usable
--
-- One row per observation revision per method (DATA-MODEL.md, D-104). A verdict
-- belongs to one revision: a new revision gets a new verdict, and a new method
-- (a refit, D-262) appends beside the old one. Nothing is ever updated. The
-- evidence dataset must be able to say which method concluded what (Stage 30).
--
-- `inputs_sha256` hashes exactly what the verdict read (D-261), so a verdict can
-- be traced to its inputs and recomputed from a raw snapshot. `route` says
-- which of the three models scored it: a reception without an SNR or without
-- decoder statistics is scored by a model fitted without them, never with a
-- zero in their place.
--
-- `simulated` is copied from the observation, never inferred (rule 5). A
-- simulated reception gets a verdict, labelled, so Stage 27 can read a partial
-- reception's evidence on a simulated fleet; no verdict of one is ever a label
-- or a training row (D-078, D-105).
--
-- A plain table rather than a hypertable: its volume is the observation count.
-- The key into observations carries started_at because it is part of that
-- hypertable's primary key (D-013).

create table reception_verdicts (
    assignment_id           text        not null,
    revision                integer     not null,
    observation_started_at  timestamptz not null,
    station_id              text        not null references stations (station_id),

    probability_usable      double precision not null
                                        check (probability_usable >= 0
                                               and probability_usable <= 1),
    method                  text        not null
                                        check (method ~ '^verdict-[0-9]+:[0-9a-f]{12}$'),
    route                   text        not null
                                        check (route in ('full', 'snr', 'outcome')),
    inputs_sha256           bytea       not null check (length(inputs_sha256) = 32),
    -- Copied from the model, so a verdict says which threshold it was read
    -- against without the model file at hand (D-262).
    partial_below           double precision not null
                                        check (partial_below > 0 and partial_below < 1),
    computed_at             timestamptz not null default now(),
    simulated               boolean     not null,

    constraint reception_verdicts_pkey primary key (assignment_id, revision, method),
    constraint reception_verdict_observation_fk
        foreign key (assignment_id, revision, observation_started_at)
        references observations (assignment_id, revision, started_at)
);

create index reception_verdicts_station_idx
    on reception_verdicts (station_id, observation_started_at);

comment on table reception_verdicts is
    'A calibrated probability that one observation revision is usable, by '
    'method. Append-only; a new revision or a new method adds a row (D-104, '
    'D-261, D-262).';
