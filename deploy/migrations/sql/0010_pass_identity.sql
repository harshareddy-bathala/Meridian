-- 0010 — a computed pass has an identity, so regenerating one is free
--
-- `passes` shipped with no unique key at all. The pass-generation job runs over
-- a rolling horizon and is expected to be re-run — after a crash, after a new
-- element set arrives, or simply on the next tick — and every re-run would have
-- inserted a second copy of every pass it had already stored.
--
-- Duplicated pass rows are not a tidiness problem. `passes` is the denominator
-- of the completeness ratio in EVALUATION.md §4.1: it is the record of what was
-- *possible*, against which what was observed is measured. Doubling it halves
-- every completeness figure the project publishes. See DECISIONS.md D-063.


-- Identity is the prediction, not the pass.
--
-- Two rows for one physical pass are correct when they come from different
-- element sets: the same rise, predicted twice, seconds apart, and the
-- difference between them is the measurement that makes element-set age a
-- usable feature. That is the same reasoning D-057 applied to element_sets —
-- the series is the measurement, so nothing in it is overwritten.
--
-- What must never happen is the *same* prediction stored twice. That is what
-- this key forbids: one (station, satellite, element set, floor) computes one
-- window, and re-running the job with unchanged inputs writes nothing.
--
-- `aos` is in the key rather than being implied by the other four because one
-- element set predicts many passes over a horizon — a station sees the same
-- satellite roughly every ninety minutes.
--
-- This is only sound because a pass computation is deterministic to the
-- microsecond regardless of the horizon that found it: the coarse scan grid is
-- aligned to a fixed anchor in orbit/skyfield_service.py, so two overlapping
-- horizons refine the identical acquisition rather than two a few milliseconds
-- apart. Without that alignment this constraint would silently never fire.
alter table passes
    add constraint pass_prediction_unique
        unique (station_id, satellite_id, element_set_id, min_elevation_deg, aos);

comment on constraint pass_prediction_unique on passes is
    'One prediction per (station, satellite, element set, floor, acquisition). '
    'Requires the aligned scan grid — see docs/DECISIONS.md D-063.';
