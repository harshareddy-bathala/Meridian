-- 0017 — what happened to each settled, scheduled pass, and on what evidence
--
-- Stage 20 is the first stage that publishes a reliability number. Every one of
-- them is counted from this table, and every row here says what it was decided
-- from, so a figure can be traced back to the assignments, the observation and
-- the heartbeat evidence behind it (the roadmap's Stage 20 gate).
--
-- **One row per physical pass per station, per method and configuration.** A
-- pass is classified once its window has settled: closed, plus a margin for a
-- report still in a station's queue (D-146). The unit is the pass, not the
-- assignment, for the reason D-148 gives: two assignments of one rise are one
-- reception. `assignment_id` is the lowest id among them and `assignment_ids`
-- lists them all.
--
-- **Append-only.** Rules change by a new `method` and parameters by a new
-- `config_sha256`, and either writes new rows beside the old. Nothing updates a
-- classification: an earlier figure must stay reproducible from the rows it was
-- counted from. Re-running under the same method and configuration writes
-- nothing, which the unique key enforces rather than a check of ours.
--
-- **The class is stored and its consequences are not.** Whether a class counts
-- as captured, and whether it spends the loss budget, are rules in
-- `meridian.reliability`, read from the class in one place. A `captured` column
-- would be a second statement of that rule, free to disagree with the first.
--
-- **Plain table, no hypertable.** Classifications are few, a handful per
-- station per day, and are read by window and station. `element_sets` is the
-- precedent: `create_hypertable(..., migrate_data => true)` later is supported,
-- and un-hypertabling is not.
--
-- See DECISIONS.md D-180, D-181, D-182.

create table pass_classifications (
    classification_id  bigint      generated always as identity primary key,

    -- The pass's representative assignment: the lowest id among the scheduled
    -- assignments pooled into it. It is the key, because a pass id is one
    -- prediction and a rise can have several (D-063, D-148).
    assignment_id      text        not null references assignments (assignment_id),
    assignment_ids     text[]      not null
                                   check (cardinality(assignment_ids) >= 1
                                          and assignment_id = assignment_ids[1]),

    pass_id            bigint      not null references passes (id),
    station_id         text        not null references stations (station_id),
    satellite_id       text        not null references satellites (satellite_id),

    -- The earliest start and the latest end among the pooled assignments'
    -- windows: the interval the station was asked to listen in.
    window_start       timestamptz not null,
    window_end         timestamptz not null,

    classification     text        not null
                                   check (classification in (
                                       'successful_reception',
                                       'signal_no_decode',
                                       'confirmed_miss',
                                       'satellite_silent',
                                       'satellite_state_indeterminate',
                                       'station_unavailable',
                                       'station_not_confirmed_listening',
                                       'assignment_declined'
                                   )),

    -- Everything the classification read: each assignment and its state, the
    -- report and its revision, whether the station was heard, the registry's
    -- listening answer, and the counts D-147 judged the satellite by. Its shape
    -- is `meridian.reliability.accounting`'s, versioned by `method`.
    evidence           jsonb       not null check (jsonb_typeof(evidence) = 'object'),

    method             text        not null check (length(btrim(method)) > 0),
    config_sha256      bytea       not null check (octet_length(config_sha256) = 32),
    classified_at      timestamptz not null default now(),

    -- The pass's population, pooled as D-146 pools it: simulated if the pass,
    -- any of its assignments or its report is. Measured and simulated figures
    -- are never counted together (CLAUDE.md rule 5).
    simulated          boolean     not null,

    constraint pass_classification_window_ordered check (window_start < window_end),
    constraint pass_classification_once
        unique (assignment_id, method, config_sha256)
);

create index pass_classifications_window_idx
    on pass_classifications (method, config_sha256, window_end);
create index pass_classifications_station_idx
    on pass_classifications (station_id, window_end);

comment on table pass_classifications is
    'What happened to each settled, scheduled pass, and the evidence it was '
    'decided from. Append-only; a new method or configuration writes new rows. '
    'docs/DECISIONS.md D-180, D-182.';
