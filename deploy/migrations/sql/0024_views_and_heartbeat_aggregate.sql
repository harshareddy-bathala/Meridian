-- 0024 — Two operators' views, and heartbeats summarised by the hour
--
-- docs/DECISIONS.md D-177 and D-178.
--
-- The views are operators' reads, not reported numbers. Every published figure
-- is regenerated from a snapshot (rule 8), and a view over live tables answers
-- differently each time it is read. The roadmap's other three views are
-- answered elsewhere (D-177): completeness by snapshot, divergence by the orbit
-- service, and the reliability indicators by Stage 20.
--
-- Views, not materialised tables, until profiling says otherwise
-- (DATA-MODEL.md, Derived views).


-- ---------------------------------------------------------------------------
-- timing_error (D-177, EVALUATION.md §6.1)
--
-- Per current observation with a first detection: when the station first heard
-- the pass against when the platform predicted it would rise. The two instants
-- are on different clocks, so the station's is corrected by the clock offset
-- its nearest heartbeat reported (D-025):
--
--     timing_error = first_detection_at + clock_offset_s − aos
--
-- The nearest heartbeat is the station's latest received from 30 minutes before
-- the detection to 5 minutes after it. No later: an offset reported afterwards
-- describes a clock the detection was not made on.
--
-- §6.1's exclusions are carried and not applied, so a reader sees what they
-- remove:
--   clock_offset_unknown     no offset near the detection; never assumed zero
--   within_clock_uncertainty the corrected error is smaller than the reported
--                            clock uncertainty, and so indistinguishable from 0
-- `excluded` is null for a row §6.1 keeps.

create view timing_error as
select o.observation_id,
       o.assignment_id,
       o.revision,
       o.station_id,
       o.satellite_id,
       p.id                                            as pass_id,
       p.aos,
       o.first_detection_at,
       extract(epoch from o.first_detection_at - p.aos) as uncorrected_error_s,
       clock.clock_offset_s,
       clock.clock_uncertainty_s,
       extract(epoch from o.first_detection_at - p.aos) + clock.clock_offset_s
                                                       as timing_error_s,
       e.id                                            as element_set_id,
       extract(epoch from p.aos - e.epoch) / 86400.0   as element_set_age_days,
       case
           when clock.clock_offset_s is null then 'clock_offset_unknown'
           when clock.clock_uncertainty_s is not null
                and abs(extract(epoch from o.first_detection_at - p.aos)
                        + clock.clock_offset_s) < clock.clock_uncertainty_s
               then 'within_clock_uncertainty'
       end                                             as excluded,
       o.simulated
from observations_current o
join assignments a on a.assignment_id = o.assignment_id
join passes p on p.id = a.pass_id
join element_sets e on e.id = p.element_set_id
left join lateral (
    select h.clock_offset_s, h.clock_uncertainty_s
    from heartbeats h
    where h.station_id = o.station_id
      and h.clock_offset_s is not null
      and h.received_at >= o.first_detection_at - interval '30 minutes'
      and h.received_at <= o.first_detection_at + interval '5 minutes'
    order by h.received_at desc
    limit 1
) clock on true
where o.first_detection_at is not null;

comment on view timing_error is
    'First detection against predicted AOS, corrected by the station clock offset, '
    'with element-set age and EVALUATION.md §6.1''s exclusions stated, not applied. '
    'An operator''s read; reported figures come from snapshots. '
    'docs/DECISIONS.md D-025, D-177.';


-- ---------------------------------------------------------------------------
-- scheduler_performance (D-177)
--
-- Per schedule run and population: what it decided, how the solver did, and
-- what became of the assignments it made — revoked, reported by outcome, or
-- still owed. A decision belongs to the run that made it (D-170); a pass decided
-- again belongs to the later run as a new revision (D-171), so no assignment is
-- counted twice.
--
-- One row per population the run's decisions fell in, never one total across
-- both (rule 5): a run that scheduled station 001 and the simulated fleet
-- together is two rows. The run's own figures — status, candidates, scheduled,
-- skipped — are the whole run's and repeat on each; every count after them is
-- that row's population only. A run that decided nothing has one row whose
-- `simulated` is null.

create view scheduler_performance as
select r.run_id,
       r.decided_at,
       r.model_config,
       r.yield_source,
       r.status                                                  as solver_status,
       r.status = 'fallback'                                     as fell_back,
       r.runtime_s,
       r.stations,
       r.candidates,
       r.scheduled,
       r.skipped,
       a.simulated,
       count(a.assignment_id)                                    as assignments,
       count(a.assignment_id) filter (where a.state = 'revoked')  as revoked,
       count(a.assignment_id) filter (where a.state = 'expired')  as expired,
       count(o.assignment_id) filter (where o.outcome = 'decoded') as decoded,
       count(o.assignment_id) filter (where o.outcome = 'signal_no_decode')
                                                                 as signal_no_decode,
       count(o.assignment_id) filter (where o.outcome = 'no_signal') as no_signal,
       count(o.assignment_id) filter (where o.outcome = 'aborted') as aborted,
       count(o.assignment_id) filter (where o.outcome = 'not_attempted')
                                                                 as not_attempted,
       count(a.assignment_id) filter (
           where a.decision = 'scheduled'
             and a.state in ('issued', 'held', 'in_progress')
             and o.assignment_id is null
       )                                                         as outstanding,
       coalesce(sum(o.frames_decoded), 0)                        as frames_decoded
from schedule_runs r
left join assignments a on a.schedule_run_id = r.run_id
left join observations_current o on o.assignment_id = a.assignment_id
group by r.run_id, a.simulated;

comment on view scheduler_performance is
    'Each schedule run and population, with its solver status and what became of '
    'that population''s assignments; never one total across both. '
    'An operator''s read; the schedulers are compared by replay (D-172). '
    'docs/DECISIONS.md D-170, D-177.';


-- ---------------------------------------------------------------------------
-- heartbeats_hourly (D-178)
--
-- Heartbeats are never dropped: Registry.was_listening, the snapshot export and
-- Stage 20's reclassification all read raw rows for any window a pass can be
-- asked about (D-178). This continuous aggregate serves the reads that need
-- coverage rather than evidence — the public uptime series, the dashboard.
--
-- Real-time (materialized_only = false): hours the policy has not refreshed yet
-- are read from raw rows, so the view is never behind the table.
--
-- Created WITH NO DATA because Alembic runs every pending revision in one
-- transaction and materialising inside one is refused. The policy refreshes
-- from the first heartbeat (start_offset NULL), not from a recent window: a
-- window would leave every hour older than it unmaterialised, and once the
-- watermark passed them, missing from the view.
--
-- No retention policy, here or on heartbeats (D-178).

create materialized view heartbeats_hourly
with (timescaledb.continuous, timescaledb.materialized_only = false) as
select station_id,
       time_bucket(interval '1 hour', received_at) as hour,
       simulated,
       count(*)                                    as heartbeats,
       count(listening_assignment_id)              as listening,
       min(received_at)                            as first_received_at,
       max(received_at)                            as last_received_at
from heartbeats
group by station_id, time_bucket(interval '1 hour', received_at), simulated
with no data;

select add_continuous_aggregate_policy(
    'heartbeats_hourly',
    start_offset => null,
    end_offset => interval '1 hour',
    schedule_interval => interval '30 minutes'
);

comment on view heartbeats_hourly is
    'Heartbeats per station and hour: how many, how many reported listening, and '
    'the first and last. Real-time; never a substitute for raw rows as evidence. '
    'docs/DECISIONS.md D-178.';
