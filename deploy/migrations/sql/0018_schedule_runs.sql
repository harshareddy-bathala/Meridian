-- 0018 — Every scheduler run is a record, and every decision says why
--
-- Stage 18 schedules by solving a programme over the values a model gives
-- (D-167, D-168). A decision is then only explainable with the run that made
-- it: which configuration and model, how old the history the model read was,
-- whether the solver proved its answer or fell back, and the value of every
-- pass it weighed. docs/PROJECT.md §13 calls the screen that shows this "the
-- entire project", so it is stored when it is decided, never reconstructed.
-- See docs/DECISIONS.md D-170.
--
-- **One row per run that decided anything.** A round over a horizon it has
-- already decided considers nothing and writes nothing, here as in
-- `assignments`; the jobs service's metrics count those rounds instead.

create table schedule_runs (
    run_id              text primary key,
    decided_at          timestamptz not null,
    -- The run's `now`: when liveness was judged and the schedule made.
    horizon_start       timestamptz not null,
    horizon_end         timestamptz not null,
    model_config        text not null
        constraint schedule_run_config check (model_config in ('A', 'B', 'C', 'D')),
    config_sha256       bytea not null,
    parameters          jsonb not null,
    -- The schedule configuration's resolved values; config_sha256 is theirs.
    yield_source        text not null
        constraint schedule_run_yield_source
            check (yield_source in ('model', 'elevation_proxy')),
    model_sha256        bytea,
    history_sha256      bytea,
    history_as_of       timestamptz,
    solver              text not null,
    solver_version      text not null,
    status              text not null
        constraint schedule_run_status
            check (status in ('optimal', 'time_limit', 'fallback')),
    objective           double precision not null,
    bound               double precision,
    time_limit_s        double precision not null,
    runtime_s           double precision not null,
    detail              text,
    stations            integer not null,
    candidates          integer not null,
    scheduled           integer not null,
    skipped             integer not null,
    created_at          timestamptz not null default now(),

    -- A model's probability is a model's: the run names it. The proxy names none.
    constraint schedule_run_model_named
        check ((yield_source = 'model') = (model_sha256 is not null)),
    -- A history is a dataset at an instant; one without the other is neither.
    constraint schedule_run_history_whole
        check ((history_sha256 is null) = (history_as_of is null)),
    -- Only a model reads history.
    constraint schedule_run_history_is_a_model_s
        check (history_sha256 is null or model_sha256 is not null),
    constraint schedule_run_horizon_ordered check (horizon_start < horizon_end),
    constraint schedule_run_counts
        check (candidates = scheduled + skipped and least(stations, scheduled, skipped) >= 0)
);

create index schedule_runs_decided_at on schedule_runs (decided_at);

comment on table schedule_runs is
    'One scheduler run: its configuration, model, history and solver outcome. '
    'docs/DECISIONS.md D-170.';

-- Decisions made before this migration keep nulls: they were Stage 7's
-- greedy baselines, made by no recorded run, and inventing one for them would
-- publish an explanation nobody computed.
alter table assignments
    add column schedule_run_id text
        constraint assignment_schedule_run references schedule_runs (run_id),
    add column model_sha256 bytea,
    add column explanation jsonb;

create index assignments_schedule_run on assignments (schedule_run_id);

comment on column assignments.explanation is
    'Why this decision: the terms of its value, the passes it was weighed '
    'against, and the rule that decided it. docs/DECISIONS.md D-170.';
