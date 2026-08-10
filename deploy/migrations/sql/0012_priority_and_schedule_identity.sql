-- 0012 — operator priority has a home, and a schedule can be re-run
--
-- Two gaps found by writing the scheduler run, both of which make a baseline
-- unusable rather than merely awkward. See DECISIONS.md D-066.


-- Configuration B is "elevation + priority weighting" (EVALUATION.md §3), and
-- there was nowhere to put the priority. `assignments.priority` records what a
-- decision *used*, which is the right thing for it to record and useless as an
-- input: reading it back would mean deriving next week's weighting from last
-- week's schedule, so the first run would have nothing and every run after it
-- would be quoting itself.
--
-- It belongs to the satellite because that is what an operator has an opinion
-- about. "Meteor-M is the project, this cubesat is a bonus" is a statement
-- about objects, not about individual passes.
--
-- Default 1.0 matches assignments.priority's default, and that agreement is
-- load bearing: configuration B reduces exactly to configuration A when nobody
-- has expressed a preference, so any measured D − B gap comes from priorities
-- an operator actually set rather than from B using a different formula.
alter table satellites
    add column priority double precision not null default 1.0
        constraint satellite_priority_is_positive check (priority > 0);

comment on column satellites.priority is
    'Operator weighting for configuration B. 1.0 is neutral. Zero is not '
    '"never" — deactivate the satellite instead. See docs/DECISIONS.md D-066.';


-- Re-running the scheduler over one horizon inserted a second complete copy of
-- the schedule. That is the failure D-063 fixed for `passes`, in the table that
-- consumes them, and it is worse here: two `scheduled` assignments for one pass
-- means one station is told twice to receive the same thing, and MSP §4.2's
-- reconciliation has two ids for one reception.
--
-- One decision per pass per configuration. Both parts matter. Keying on
-- `pass_id` alone would make configurations A and B mutually exclusive, and
-- running both over one horizon is exactly what the ablation in EVALUATION.md §3
-- requires — the two schedules have to coexist to be compared.
--
-- `model_config` is nullable on this table and stays nullable: rows predating
-- the ablation exist. Postgres treats NULLs as distinct in a unique constraint,
-- so those rows are unconstrained here, which is the honest outcome — a row
-- that does not say which configuration produced it cannot be deduplicated
-- against one that does.
alter table assignments
    add constraint assignment_decision_unique unique (pass_id, model_config);

comment on constraint assignment_decision_unique on assignments is
    'One decision per pass per configuration, so a run can be repeated. '
    'See docs/DECISIONS.md D-066.';
