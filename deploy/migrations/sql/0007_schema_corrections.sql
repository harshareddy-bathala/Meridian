-- 0007 — five constraints that were missing, wrong, or narrower on one table
-- than on its twin.
--
-- Merged migrations are never edited (GIT-WORKFLOW.md rule 9), so corrections to
-- 0002, 0003 and 0004 arrive here as a new revision rather than as an edit to
-- history. Every statement below either tightens a range or adds a constraint
-- that another table already had; none change a column's type or meaning.


-- 1. Longitude is −180..+180, not −180..360.
--
-- The 360 upper bound is azimuth's range applied to a longitude. Under it, 200
-- and −160 are two storable spellings of the same meridian, and two stations at
-- the same place would sort, group and compare as though they were 360 degrees
-- apart. ISO 6709 and DATA-MODEL.md's conventions section both say −180..+180.
--
-- Existing rows are normalised rather than rejected: a stored 200 becomes −160,
-- which is the same physical meridian, so this is lossless. Done before the
-- constraint is added, because a CHECK is validated against existing rows at
-- creation and would otherwise fail the migration on any station that used the
-- range the old constraint allowed. See D-052.
update stations set lon_deg = lon_deg - 360 where lon_deg > 180;

alter table stations drop constraint if exists stations_lon_deg_check;
alter table stations add constraint stations_lon_deg_check
    check (lon_deg between -180 and 180);


-- 2. A bearer token hash identifies exactly one station.
--
-- `store.stations.find_station_id_by_token_hash` reads with `fetchone()`. With
-- no uniqueness, two rows carrying the same hash would authenticate as whichever
-- one Postgres happened to return first — silently, and differently between
-- runs. The index makes that a write-time error on the second row instead of an
-- authentication that picks a station at random.
--
-- Not partial on `deleted_at is null`: a soft-deleted station keeps its row and
-- its hash, and a token must not become reusable by deleting the station it
-- belonged to.
create unique index stations_token_sha256_key on stations (token_sha256);


-- 3. `satellite_transmitters` gets the CHECKs its twins already have.
--
-- `element_sets.source` and `station_capabilities.polarisation` are both
-- constrained; the same two columns on this table were free text. Nothing
-- stopped 'RHCP' being stored where every query looks for 'rhcp', which is a row
-- that exists and never joins — the failure mode that looks like missing data
-- rather than like a bug.
--
-- `source` is restricted to the two values that can occur today. An external
-- archive adapter (ingest/, Phase 2) adds its own value in its own migration;
-- listing a source now that nothing can produce would be scaffolding for work
-- that has not been designed. D-021 chose CHECK over a Postgres enum precisely
-- so that widening it later is a cheap statement inside one transaction.
alter table satellite_transmitters add constraint satellite_transmitters_source_check
    check (source in ('manual', 'simulator'));

-- `polarisation` is nullable and stays nullable — a transmitter whose
-- polarisation is not known is a real record. A NULL makes the `in` test NULL,
-- and a CHECK admits anything that is not false, so the null case needs no
-- special clause. Values match `station_capabilities.polarisation` exactly; two
-- vocabularies for one physical property would defeat the join they exist for.
alter table satellite_transmitters add constraint satellite_transmitters_polarisation_check
    check (polarisation in ('rhcp', 'lhcp', 'linear_v', 'linear_h', 'linear', 'none'));


-- 4. `passes.min_elevation_deg` gets the range its twin has.
--
-- `station_capabilities.min_elevation_deg` is constrained to −90..90 and this
-- column, which records the floor a window was computed against, was not. An
-- out-of-range floor here is worse than one on a capability: it silently
-- changes what the completeness ratio in EVALUATION.md §4.1 is dividing by.
alter table passes add constraint passes_min_elevation_deg_check
    check (min_elevation_deg between -90 and 90);


-- 5. A pass reaches above the horizon, and above the floor it was computed for.
--
-- The original range allowed a maximum elevation of −40, which is not a pass:
-- GLOSSARY.md defines one as "a single period during which a satellite is above
-- a station's horizon". Tightened to 0..90.
alter table passes drop constraint if exists passes_max_elevation_deg_check;
alter table passes add constraint passes_max_elevation_deg_check
    check (max_elevation_deg between 0 and 90);

-- The pair constraint is the one that makes the two columns interpretable
-- together. A window is computed as the interval where elevation is at or above
-- `min_elevation_deg`, so the peak over that interval cannot be below it. A row
-- violating this is a propagation or frame-conversion bug — exactly the silent
-- class CLAUDE.md warns coordinate frames produce — and it is far cheaper to
-- catch on insert than in a reliability figure three stages later.
alter table passes add constraint pass_peak_above_floor
    check (max_elevation_deg >= min_elevation_deg);


-- 6. `client_impl` spells out to `client_implementation`.
--
-- CLAUDE.local.md §4 permits no abbreviation that is not in docs/GLOSSARY.md,
-- and `impl` is not one — `lat`, `lon`, `alt` and `freq` are listed there, this
-- is not. Renamed at the column rather than only in Python, because a Python
-- attribute called `client_implementation` writing to a column called
-- `client_impl` moves the abbreviation instead of removing it, and leaves the
-- INSERT in `store/stations.py` disagreeing with itself line to line.
--
-- Safe as a rename: nothing outside `store.stations.insert_station` names this
-- column, no index, constraint or view depends on it, and the MSP wire field is
-- `client.impl` either way — the wire name is fixed by the specification and is
-- carried by a Pydantic alias, not by this column's spelling.
alter table stations rename column client_impl to client_implementation;
