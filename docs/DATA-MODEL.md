# Data Model

PostgreSQL with TimescaleDB. Observations and heartbeats are hypertables.

> **Phase 1 scope.** D-018 builds eight of the tables below plus `invite_tokens` (D-020) and `satellite_transmitters` (D-021). `products`, `noise_measurements`, `horizon_profiles` and `interference_profiles` are deferred — see D-018 for why each one waits. The derived views wait with them; Phase 1 does not produce the data they read.

---

## Core tables

### `stations`
Identity, location, operator, registration time, token hash, registration key hash, `simulated` flag, derived liveness, last heartbeat.

`simulator_run_id` and `seed` are required when `simulated` is true and absent when it is false (MSP §5), enforced as a `CHECK` constraint rather than in application code — the constraint *is* the protocol rule, and the schema is the right place for it.

`registration_key_sha256` is the hash of the key the client generated and kept, and it is what makes registration recoverable. Presenting a consumed invite with the matching key rotates the bearer token onto the same `station_id` instead of failing; a different key is `403 invalid_invite`. Recovery is allowed only while `last_heartbeat_at is null` **and** the request is within the recovery window of `registered_at` — both conditions, since a station that has heartbeat holds a working token and one that never heartbeat would otherwise leave a consumed invite live forever. See D-023 as amended by D-034.

A station past that window rotates its token through a **bound** invite instead: an `invite_tokens` row naming its `station_id`, issued by an operator, presented with the same `registration_key`. It produces no second station row and ignores the recovery window, because the operator's act of issuing it is the authorisation the window otherwise stands in for. See D-034.

**`liveness` is the platform's derived conclusion, not what the station reported** (D-013). The station reports `state` in each heartbeat and a `health` object alongside it; both are stored on `heartbeats` as sent. `liveness` takes `never_seen`, `online`, `stale` (60 s, two missed heartbeats) or `offline` (90 s, three). The 90 s comes from SC-5, which requires an injected node failure to be detected within that time — the success criterion sets the threshold, not the other way round.

Capabilities are a separate table (`station_capabilities`) — a station may have several: a VHF fixed antenna and a UHF tracking antenna are distinct capabilities with different frequency ranges, polarisation and elevation limits. Each capability also carries the `modes` array from MSP §4.1 and a `tracking` flag.

`horizon_mask_json` holds the optional azimuth-resolved obstruction an operator declared at registration, defaulting to `[]`. **It is never merged into a learned profile.** The Phase 2 `horizon_profiles` table carries `source in ('declared', 'learned')` and the scheduler takes `max(declared, learned)` per bin, so a declaration constrains scheduling without ever becoming an input to the model that would otherwise be predicting it. See D-031.

### `invite_tokens`
`(token_sha256, label, created_at, expires_at, consumed_at, consumed_by_station_id, issued_for_station_id)`

MSP §4.1 requires an invite token to be **consumed** by a successful registration and to fail on reuse. That needs a row per token — a single configuration value cannot be consumed, cannot be revoked per operator, and cannot admit a second station. See D-020.

`issued_for_station_id` is null for an ordinary invite, which admits a new station. When set, the invite is **bound**: it admits only the station it names, rotating that station's bearer token rather than creating a row. It is how an operator recovers a station whose token was revoked, since that station's own invite was consumed at registration and cannot be presented again. See D-034.

### `element_sets`
Every element set ever retrieved, for every tracked object. **Never overwritten** — the historical series is what makes uncertainty modelling possible.

`(satellite_id, epoch, retrieved_at, line1, line2, source)`

Divergence between successive sets for the same object is a derived view, not a stored column.

### `satellites`
Catalogue identity, name, orbital regime, and observed activity status. The last matters for the silent-satellite confound in `docs/EVALUATION.md`.

### `satellite_transmitters`
`(satellite_id, centre_freq_hz, mode, polarisation, bandwidth_hz, active, source)`

A satellite's known transmitters, as a child table rather than a column on `satellites`. The scheduler selects a transmitter by joining it against a station capability's frequency range — `freq_min_hz <= centre_freq_hz <= freq_max_hz` — and that predicate is not indexable inside a JSON blob. `active` carries the silent-satellite status. See D-021.

### `passes`
Computed pass windows — **not** observations. A pass exists whether or not anyone observed it, which is exactly what the completeness ratio in the evaluation methodology requires.

`(satellite_id, station_id, aos, los, max_elevation, aos_azimuth, los_azimuth, element_set_id, computed_at)`

Note `element_set_id`: which element set produced this prediction, so timing error can be attributed to element-set age.

### `assignments`
Scheduler output. Links a pass to a station with a decision record.

`(pass_id, station_id, issued_at, start_at, end_at, centre_freq_hz, mode, timing_uncertainty_s, predicted_yield, priority, decision, reason, model_config, state)`

`reason` is human-readable and shown on the dashboard. `model_config` records which ablation configuration produced the prediction — required for the evaluation to be reproducible.

**`start_at` and `end_at` are the assignment's window, not the pass's `aos`/`los`.** They are widened from the pass by `timing_uncertainty_s`, because a station recording at exactly the predicted acquisition time starts after a pass whose element set was stale has already begun. These five columns are what let this table produce the MSP §4.3 assignment message; without them it could not. See D-021.

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
| `expired` | Never reported, window has passed | Reconciliation, once `now > end_at` |

**Delivery is repeated, not once-only** (D-026). Every heartbeat returns each of this station's assignments whose `start_at` falls in the next two hours and which is not yet `reported`, capped at 8 and sorted by `start_at` — including ones the station already listed in `held_assignments`. That is what makes a lost heartbeat response harmless, and it is why no delivery-receipt column exists on this table: there is nothing to receipt.

`expired` is the **decline** case. It is not the same as an observation with outcome `not_attempted`: `expired` means the station never took the work, `not_attempted` means it took the work and then failed to start. The reliability layer needs both and must never merge them.

State is derived by reconciling `held_assignments` against what was issued — see `docs/DECISIONS.md` D-003 and D-008.

### `observations` *(hypertable)*
One row per attempt, including attempts that produced nothing.

`(assignment_id, revision, observation_id, station_id, satellite_id, started_at, ended_at, outcome, signal_detected, first_detection_at, peak_snr_db, doppler_samples, products_json, client_notes, simulated, provenance, submitted_at, content_sha256)`

`observation_id` is the public identifier MSP §4.4's acknowledgement returns, and it is a **stored generated column** derived from `(assignment_id, revision)` rather than an allocated value:

```sql
observation_id text generated always as (
    'ob_' || substr(encode(sha256(convert_to(
        assignment_id || ':' || revision::text, 'UTF8')), 'hex'), 1, 12)
) stored
```

Derived, because an idempotent retry must return the *same* id as the original submission — and D-015 makes that the path on which nothing is written, so it must not require reading the row back first. Generated in the database rather than in Python, so the ingest path and the public API cannot drift. See D-027.

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

**Immutable once written. Corrections are new rows**, ordered by a `revision` counter — the natural key is `(assignment_id, revision)` and the highest revision is current. MSP §6 requires the platform to be idempotent on `assignment_id`; that is satisfied by appending rather than overwriting, and the `observations_current` view exposes the latest revision per assignment. A station that resubmits sees one current observation and no duplicate, so the protocol behaviour is unchanged — the difference is that the earlier report survives.

A `supersedes_id` pointer would have been the obvious shape and is rejected on modelling grounds: `revision` orders the lineage explicitly instead of requiring a chain walk, and `(assignment_id, revision)` must exist as the key regardless. `content_sha256` over the canonical body makes a byte-identical resubmission — the queued-retry case of MSP §6 — a no-op rather than a new revision. See D-015.

This table also carries `products_json`, holding the MSP §4.4 `products` array verbatim until O-1 is resolved and the `products` table exists (D-018).

Partitioned on `started_at`, but ingest rejects a `started_at` outside `[now − 30 days, now + 1 hour]` as `malformed` — a client-supplied timestamp must never be trusted to place a chunk (D-013).

### `heartbeats` *(hypertable)*
`(station_id, sent_at, received_at, state, held_assignments, listening_assignment_id, listening_satellite_id, listening_freq_hz, listening_mode, health_json, clock_offset_s, clock_uncertainty_s, simulated)`

**This table is what allows absence to be interpreted.** Without a heartbeat confirming a station was listening on the right frequency for the right target, a missing observation is meaningless.

The listening block is stored whole — assignment, satellite, frequency **and mode** — under an all-or-nothing `CHECK`. `mode` is not decoration: a station tuned to the right frequency running the wrong demodulator did not observe the pass, and `Registry.was_listening()` is the sole authority on what counts as a confirmed miss. `health_json` is opaque and stored verbatim, capped at 4 KiB by the ingest path. See D-028.

`held_assignments text[] not null default '{}'` is the mechanism D-003 and D-008 rest on — the platform reconciles it against what it issued, and a decline is absence from the list. Never nullable: MSP §4.2 says an empty list is *meaningful* and must be sent as `[]`, so "holds nothing" and "said nothing" must stay distinguishable. See D-014.

`clock_offset_s` and `clock_uncertainty_s` are both nullable, and `null` means unknown — never conflated with `0.0`. `EVALUATION.md` §6.1 discards any timing error smaller than the reported uncertainty, which needs both numbers (D-016).

Partitioned on `received_at`, the platform's clock, not the station's `sent_at` (D-013).

Retention: full resolution 90 days, then downsampled. **The retention policy is not created in Phase 1** — dropping chunks before the continuous aggregate that downsamples them exists is just data loss on a timer. It lands in Phase 3 with the aggregate.

### `products`
Artifacts from an observation — waterfalls, images, decoded frames. Content-addressed by hash, referenced here.

O-1 is now resolved (D-029): transfer is a **pre-signed PUT to object storage**, off the MSP path, not an inline upload — a waterfall is megabytes and an observation body is capped at 256 KiB. The table therefore stores a storage location rather than bytes, and is created at the stage where a receiver first produces a product. Until then MSP §4.4's `products` array lives verbatim in `observations.products_json`, so nothing a station sends is lost.

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
- **`simulated boolean not null default false`** on every table that can hold simulated data — `stations`, `passes`, `assignments`, `observations`, `heartbeats`. Never nullable: an unknown provenance is a bug. Always copied from the station's registry record, never read from a payload.
- **Satellite identity** is `norad:NNNNN` as text, not a bare integer. Objects without NORAD IDs exist.
- **Soft delete only.** Nothing in the observation lineage is ever hard-deleted.

## Keys, enums and hypertables

Settled in D-013 and D-021, because `DATA-MODEL.md` previously gave column names without types, keys or nullability and the migrations could not be written from it.

- **Where MSP already forces an id to exist, be unique and be stable, it is the primary key** — `stations.station_id`, `assignments.assignment_id`, `satellites.satellite_id`. A second surrogate key beside them buys nothing but joins. Everything else gets `bigint generated always as identity`.
- **A hypertable's primary key must include its partitioning column.** TimescaleDB requires it — verified against 2.29, which rejects the hypertable outright with *"cannot create a unique index without the column used in partitioning"*. So `observations` is keyed `(assignment_id, revision, started_at)` and `heartbeats` `(id, received_at)`. A constraint of the storage engine, not a modelling preference.
- **Enums are `text` with a `CHECK` constraint**, not Postgres `enum` types. A `CHECK` is dropped and recreated inside one transaction; altering an `enum` is not, and Rule 9 of `GIT-WORKFLOW.md` forbids editing a merged migration.
- **`simulated` is copied from the station's registry record, never read from the payload.** MSP §3 says station-submitted data is untrusted, and provenance is the last field to take on trust.

| Column | Values |
|---|---|
| `stations.liveness` (derived) | `never_seen`, `online`, `stale`, `offline` |
| `heartbeats.state` (reported) | `idle`, `slewing`, `listening`, `processing`, `degraded`, `maintenance` — MSP §4.2 |
| `assignments.state` | `issued`, `held`, `in_progress`, `reported`, `expired` — D-008 |
| `assignments.decision` | `scheduled`, `skipped` |
| `observations.outcome` | the five values of MSP §4.4 — D-010 |
| `observations.provenance` | `station`, `archive`, `manual` |
| `element_sets.source` | `celestrak`, `spacetrack`, `manual`, `simulator` |
| `satellites.orbital_regime` | `leo`, `meo`, `geo`, `heo`, `other` — `EVALUATION.md` §6.1 segments by it |
| `station_capabilities.band` | `vhf`, `uhf`, `l`, `s`, `other` |
| `station_capabilities.polarisation` | `rhcp`, `lhcp`, `linear_v`, `linear_h`, `linear`, `none` |
| `station_capabilities.modes` | free-text lowercase array in Phase 1 — decoder naming varies too much to freeze |

---

## Derived views

- `pass_completeness` — observed ÷ available, per station-day. Drives the selection-bias mitigation.
- `element_set_divergence` — position difference between successive element sets at common epochs.
- `timing_error` — first detection minus predicted AOS, joined to element-set age.
- `sli_current` — the four service level indicators over a rolling window.

Views, not materialised tables, until profiling proves otherwise.
