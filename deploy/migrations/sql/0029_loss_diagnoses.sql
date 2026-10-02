-- 0029 — loss diagnoses: why a reception was lost, one cause or undetermined
--
-- One row per lost reception per method and configuration (DATA-MODEL.md,
-- D-272). A lost reception is a current observation that did not decode, one
-- that decoded below the verdict model's partial threshold, or a held window
-- that passed with nothing reported; `revision` and `observation_started_at`
-- are null together for that last kind. An expired or revoked assignment never
-- gets a row (D-008, D-171).
--
-- `cause` is exactly one of five causes or `undetermined`, never null: "we
-- looked and cannot say" is a value, and "we have not looked" is the absence of
-- a row (D-104). `candidates_json` keeps every cause tested, fired or not, with
-- what its test found; `evidence_json` keeps what the diagnosis read, the
-- thresholds among it (D-273).
--
-- Append-only. A re-run under the same method and configuration writes nothing
-- (the unique key, nulls not distinct, so an empty window is diagnosed once too);
-- a changed threshold writes beside the old rows, as a classification does
-- (D-182). `classification_id` names the Stage 20 classification the diagnosis
-- read "not listening" from, never restating it (D-180). `verdict_method` names
-- the verdict a partial reception was read against, if one was.
--
-- `simulated` is copied from the assignment, never inferred (rule 5). Ground
-- truth from the simulator never enters this table (D-105).
--
-- A plain table: its volume is the loss count.

create table loss_diagnoses (
    diagnosis_id            bigint      generated always as identity primary key,
    assignment_id           text        not null references assignments (assignment_id),
    revision                integer,
    observation_started_at  timestamptz,
    station_id              text        not null references stations (station_id),
    classification_id       bigint      not null
                                        references pass_classifications (classification_id),

    cause                   text        not null
                                        check (cause in ('satellite_silent',
                                                         'station_not_listening',
                                                         'obstruction',
                                                         'interference',
                                                         'timing_fault',
                                                         'undetermined')),
    candidates_json         jsonb       not null
                                        check (jsonb_typeof(candidates_json) = 'array'),
    evidence_json           jsonb       not null
                                        check (jsonb_typeof(evidence_json) = 'object'),
    method                  text        not null check (method ~ '^diagnosis-[0-9]+$'),
    config_sha256           bytea       not null check (length(config_sha256) = 32),
    verdict_method          text        check (verdict_method ~ '^verdict-[0-9]+:[0-9a-f]{12}$'),
    computed_at             timestamptz not null default now(),
    simulated               boolean     not null,

    constraint loss_diagnosis_revision_paired
        check ((revision is null) = (observation_started_at is null)),
    -- MATCH SIMPLE: an empty window, with no revision, references nothing.
    constraint loss_diagnosis_observation_fk
        foreign key (assignment_id, revision, observation_started_at)
        references observations (assignment_id, revision, started_at),
    constraint loss_diagnosis_once
        unique nulls not distinct (assignment_id, revision, method, config_sha256)
);

create index loss_diagnoses_station_idx on loss_diagnoses (station_id, computed_at);

comment on table loss_diagnoses is
    'Why one reception was lost: a cause or undetermined, every candidate '
    'tested, by method and configuration. Append-only (D-104, D-272, D-273).';
