# Data Model

PostgreSQL with TimescaleDB. Observations and heartbeats are hypertables.

---

## Core tables

### `stations`
Identity, location, operator, registration time, token hash, `simulated` flag, current health state, last heartbeat.

Capabilities are a separate table (`station_capabilities`) — a station may have several: a VHF fixed antenna and a UHF tracking antenna are distinct capabilities with different frequency ranges, polarisation and elevation limits.

### `element_sets`
Every element set ever retrieved, for every tracked object. **Never overwritten** — the historical series is what makes uncertainty modelling possible.

`(satellite_id, epoch, retrieved_at, line1, line2, source)`

Divergence between successive sets for the same object is a derived view, not a stored column.

### `satellites`
Catalogue identity, name, orbital regime, known transmitters (frequency, mode, polarisation), and observed activity status. The last matters for the silent-satellite confound in `docs/EVALUATION.md`.

### `passes`
Computed pass windows — **not** observations. A pass exists whether or not anyone observed it, which is exactly what the completeness ratio in the evaluation methodology requires.

`(satellite_id, station_id, aos, los, max_elevation, aos_azimuth, los_azimuth, element_set_id, computed_at)`

Note `element_set_id`: which element set produced this prediction, so timing error can be attributed to element-set age.

### `assignments`
Scheduler output. Links a pass to a station with a decision record.

`(pass_id, station_id, issued_at, predicted_yield, priority, decision, reason, model_config)`

`reason` is human-readable and shown on the dashboard. `model_config` records which ablation configuration produced the prediction — required for the evaluation to be reproducible.

Skipped passes are recorded too. A scheduler that only logs what it chose cannot be evaluated.

### `observations` *(hypertable)*
One row per attempt, including attempts that produced nothing.

`(assignment_id, station_id, satellite_id, started_at, ended_at, outcome, signal_detected, first_detection_at, peak_snr_db, simulated, provenance, submitted_at)`

`first_detection_at` minus predicted AOS is the timing-error measurement.

Immutable once written. Corrections are new rows referencing the original.

### `heartbeats` *(hypertable)*
`(station_id, sent_at, received_at, state, listening_assignment_id, listening_freq_hz, listening_satellite_id, health_json, clock_offset_s)`

**This table is what allows absence to be interpreted.** Without a heartbeat confirming a station was listening on the right frequency for the right target, a missing observation is meaningless.

Retention: full resolution 90 days, then downsampled.

### `products`
Artifacts from an observation — waterfalls, images, decoded frames. Content-addressed by hash, stored on disk, referenced here.

### `horizon_profiles`
Derived per station: usable elevation floor by azimuth bin, with the sample count and confidence behind each bin. Recomputed on a schedule; versioned so a prediction can be traced to the profile that produced it.

---

## Conventions

- **All timestamps UTC**, stored as `timestamptz`. No exceptions, no local time anywhere.
- **Frequencies in Hz** as `bigint`. Never floats, never MHz.
- **Angles in degrees** as `double precision`. Azimuth 0–360, elevation −90 to +90.
- **`simulated boolean not null default false`** on every table that can hold simulated data. Never nullable — an unknown provenance is a bug.
- **Satellite identity** is `norad:NNNNN` as text, not a bare integer. Objects without NORAD IDs exist.
- **Soft delete only.** Nothing in the observation lineage is ever hard-deleted.

---

## Derived views

- `pass_completeness` — observed ÷ available, per station-day. Drives the selection-bias mitigation.
- `element_set_divergence` — position difference between successive element sets at common epochs.
- `timing_error` — first detection minus predicted AOS, joined to element-set age.
- `sli_current` — the four service level indicators over a rolling window.

Views, not materialised tables, until profiling proves otherwise.
