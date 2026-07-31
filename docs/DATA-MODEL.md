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

`(pass_id, station_id, issued_at, predicted_yield, priority, decision, reason, model_config, state)`

`reason` is human-readable and shown on the dashboard. `model_config` records which ablation configuration produced the prediction — required for the evaluation to be reproducible.

Skipped passes are recorded too. A scheduler that only logs what it chose cannot be evaluated.

**`state` tracks what the station did with it**, as distinct from `decision`, which is what the scheduler wanted:

```
issued  →  held  →  in_progress  →  reported
        ↘
          expired
```

| State | Meaning | Set when |
|---|---|---|
| `issued` | Delivered by the platform, not yet acknowledged | Scheduler issues it in a heartbeat response |
| `held` | Station confirms it holds it | `assignment_id` appears in `held_assignments` (MSP §4.2) |
| `in_progress` | Station is executing | Heartbeat `listening` block references it |
| `reported` | An observation has been received | Observation ingested |
| `expired` | Issued, never held, window has passed | Reconciliation, after `end_at` |

`expired` is the **decline** case. It is not the same as an observation with outcome `not_attempted`: `expired` means the station never took the work, `not_attempted` means it took the work and then failed to start. The reliability layer needs both and must never merge them.

State is derived by reconciling `held_assignments` against what was issued — see `docs/DECISIONS.md` D-003 and D-008.

### `observations` *(hypertable)*
One row per attempt, including attempts that produced nothing.

`(assignment_id, station_id, satellite_id, started_at, ended_at, outcome, signal_detected, first_detection_at, peak_snr_db, simulated, provenance, submitted_at)`

`outcome` is a constrained type taking exactly these five values, and **`docs/MSP-SPEC.md` §4.4 is the authority** — this table restates them, it does not define them. Two documents holding the same enum will drift unless one of them is named as the source:

| Value | Meaning |
|---|---|
| `decoded` | Signal received and successfully decoded |
| `signal_no_decode` | Signal present, decoding failed |
| `no_signal` | Station verifiably listening, nothing detected |
| `aborted` | Station started but could not complete |
| `not_attempted` | Station never began — offline or unhealthy |

`no_signal` and `not_attempted` must never be conflated: the first is data, the second is an operational failure. Neither is the same as a declined assignment, which never produces an observation row at all and appears as `assignments.state = 'expired'`.

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

### `noise_measurements` *(hypertable)*
Measured noise floor per station, azimuth bin and time.

`(station_id, measured_at, azimuth_bin_deg, centre_freq_hz, noise_floor_dbm, source, simulated)`

`source` distinguishes a measurement taken during an observation from one taken by a dedicated survey sweep — the two have different duty cycles and the model should be able to weight them differently.

### `interference_profiles`
Derived per station: noise floor by azimuth bin **and hour of day**, with the sample count behind each cell.

`docs/EVALUATION.md` §2 lists the interference profile as one of our own features, and the prediction module consumes it — but nothing held the underlying measurement, so the feature had no source. `noise_measurements` is that source and this is the profile derived from it.

Versioned exactly as `horizon_profiles` is, and for the same reason: a prediction must be traceable to the profile that produced it. See `docs/DECISIONS.md` D-009.

*Hour of day matters and a single aggregate would hide it — a rooftop in a city has a different noise floor at 8 a.m. than at 8 p.m., which is the whole reason this feature exists.*

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
