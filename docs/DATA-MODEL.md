# Data Model

PostgreSQL with TimescaleDB. Observations and heartbeats are hypertables.

> **Phase 1 scope.** D-018 builds eight of the tables below plus `invite_tokens` (D-020) and `satellite_transmitters` (D-021). `products`, `noise_measurements`, `horizon_profiles` and `interference_profiles` were deferred — see D-018 for why each one waited — and Stage 19 built them once each had a producer and a consumer (migration `0023`, D-173 to D-176). Two derived views and an hourly heartbeat aggregate followed (migration `0024`, D-177, D-178).
>
> **Post-reception tables** — for the reception verdict, loss diagnosis, the station health watch, owner reports and the evidence dataset — are described under their own heading below. Stage 26 built the first two, `reception_ratings` and `reception_verdicts` (migrations `0027` and `0028`, D-260, D-263), and Stage 27 the third, `loss_diagnoses` (migration `0029`, D-272); the rest are **planned, not built**. The rules they share are D-104.

---

## Core tables

### `stations`
Identity, location, operator, registration time, token hash, registration key hash, `simulated` flag, last heartbeat. Liveness is **not** a column — it is derived from `last_heartbeat_at` on read (D-054).

`simulator_run_id` and `seed` are required when `simulated` is true and absent when it is false (MSP §5), enforced as a `CHECK` constraint rather than in application code — the constraint *is* the protocol rule, and the schema is the right place for it.

`location_precision_decimals` is how many decimal places the operator permits `lat_deg` and `lon_deg` to be **published** at — 1 to 6, `CHECK`ed, defaulting to 2 (~1.1 km at the equator). It changes nothing about what is stored: the coordinate columns keep every digit the station sent, and `find_receiving_stations` serves them unrounded to pass generation and the scheduler, because a coarsened latitude would move predicted acquisition by seconds. The rounding happens once, when a public response is serialised. Added by migration `0014`, where the column default is also the backfill for every station registered before MSP 0.2 defined the field. See D-082.

`registration_key_sha256` is the hash of the key the client generated and kept, and it is what makes registration recoverable. Presenting a consumed invite with the matching key rotates the bearer token onto the same `station_id` instead of failing; a different key is `403 invalid_invite`. Recovery is allowed only while `last_heartbeat_at is null` **and** the request is within the recovery window of `registered_at` — both conditions, since a station that has heartbeat holds a working token and one that never heartbeat would otherwise leave a consumed invite live forever. See D-023 as amended by D-034.

A station past that window rotates its token through a **bound** invite instead: an `invite_tokens` row naming its `station_id`, issued by an operator, presented with the same `registration_key`. It produces no second station row and ignores the recovery window, because the operator's act of issuing it is the authorisation the window otherwise stands in for. See D-034.

**Liveness is the platform's derived conclusion, not what the station reported** (D-013). The station reports `state` in each heartbeat and a `health` object alongside it; both are stored on `heartbeats` as sent. Liveness takes `never_seen`, `online`, `stale` (60 s, two missed heartbeats) or `offline` (90 s, three). The 90 s comes from SC-5, which requires an injected node failure to be detected within that time — the success criterion sets the threshold, not the other way round.

**It is computed on read, never stored** (D-054). `stations.liveness` existed as a column from `0002` until `0008` and nothing ever wrote it. A stored conclusion is correct only until the clock passes its next threshold, and nothing moves the clock on the platform's behalf — so a station that went quiet would keep reading `online` until an unrelated write refreshed it, which is the case liveness exists to detect. `meridian.registry.liveness` owns the vocabulary and both thresholds; `last_heartbeat_at` is the only thing stored.

Capabilities are a separate table — see below.

### `station_capabilities`
`(id, station_id, band, freq_min_hz, freq_max_hz, modes, polarisation, tracking, min_elevation_deg, horizon_mask_json, deleted_at)`

A station may have several: a VHF fixed antenna and a UHF tracking antenna are distinct capabilities with different frequency ranges, polarisation and elevation limits. Each carries the `modes` array from MSP §4.1 and a `tracking` flag.

`freq_min_hz` and `freq_max_hz` are the range the scheduler joins against `satellite_transmitters.centre_freq_hz` to select a transmitter, under `capability_freq_range` guaranteeing the range is not inverted.

`min_elevation_deg` is the station's **declared** floor, not its measured horizon — the platform learns the real obstruction profile from outcome history and may override this downward per azimuth.

`horizon_mask_json` holds the optional azimuth-resolved obstruction an operator declared at registration, defaulting to `[]`. **It is never merged into a learned profile.** `horizon_profiles` carries `source in ('declared', 'learned')`, and only the declared mask constrains scheduling: a pass whose track clears it nowhere is left undecided (D-175, amending D-031's `max(declared, learned)`). A declaration therefore constrains scheduling without ever becoming an input to the model that would otherwise be predicting it. The mask travels inside each capability (MSP §4.1); the reference client once sent it where it was ignored, which D-175 records and fixed.

### `invite_tokens`
`(token_sha256, label, created_at, expires_at, consumed_at, consumed_by_station_id, issued_for_station_id)`

MSP §4.1 requires an invite token to be **consumed** by a successful registration and to fail on reuse. That needs a row per token — a single configuration value cannot be consumed, cannot be revoked per operator, and cannot admit a second station. See D-020.

`issued_for_station_id` is null for an ordinary invite, which admits a new station. When set, the invite is **bound**: it admits only the station it names, rotating that station's bearer token rather than creating a row. It is how an operator recovers a station whose token was revoked, since that station's own invite was consumed at registration and cannot be presented again. See D-034.

### `element_sets`
Every element set ever retrieved, for every tracked object. **Never overwritten** — the historical series is what makes uncertainty modelling possible.

`(id, satellite_id, epoch, retrieved_at, line1, line2, source, content_sha256)`

A set is identified by `(satellite_id, source, content_sha256)` — its **contents**, not its epoch. Two different sets can carry the same epoch from one source, and keying on epoch discarded the second (D-057). `content_sha256` is a generated column, so no row can carry a hash that disagrees with its lines.

Divergence between successive sets for the same object is computed on demand, not stored. It lives in `meridian.orbit` rather than in SQL because measuring it means propagating both sets to a common instant and differencing the positions, which needs the propagator.

### `satellites`
Catalogue identity, name, orbital regime, and observed activity status. The last matters for the silent-satellite confound in `docs/EVALUATION.md`.

### `satellite_transmitters`
`(satellite_id, centre_freq_hz, mode, polarisation, bandwidth_hz, active, source, frame_interval_s)`

A satellite's known transmitters, as a child table rather than a column on `satellites`. The scheduler selects a transmitter by joining it against a station capability's frequency range — `freq_min_hz <= centre_freq_hz <= freq_max_hz` — and that predicate is not indexable inside a JSON blob. `active` carries the silent-satellite status. See D-021.

**`frame_interval_s`, the nominal seconds between frames** (migration `0026`, D-250). The reception verdict compares frames decoded against frames *expected*. Expected is the pass's duration, acquisition to loss, over this interval, and the platform computes it so that every station's ratio has one definition. The column is nullable with no default. Where the interval is unknown it is null, and the verdict omits that ratio rather than guessing it (D-104). `meridian catalogue load` writes it for a new downlink and fills it where it is null, and never replaces an interval already held.

### `passes`
Computed pass windows — **not** observations. A pass exists whether or not anyone observed it, which is exactly what the completeness ratio in the evaluation methodology requires.

`(id, satellite_id, station_id, aos, los, max_elevation_deg, max_elevation_at, min_elevation_deg, aos_azimuth_deg, los_azimuth_deg, element_set_id, computed_at, simulated)`

Note `element_set_id`: which element set produced this prediction, so timing error can be attributed to element-set age.

### `assignments`
Scheduler output. Links a pass to a station with a decision record.

`(assignment_id, pass_id, station_id, issued_at, start_at, end_at, centre_freq_hz, mode, timing_uncertainty_s, predicted_yield, priority, decision, reason, model_config, score, conflicts_with_assignment_id, state, simulated, schedule_run_id, model_sha256, explanation, revision, revoked_reason, revoked_at)`

`reason` is human-readable and shown on the dashboard. `model_config` records which ablation configuration produced the prediction — required for the evaluation to be reproducible.

**Every decision since Stage 18 names its run and says why** (D-170, migration 0018):
- `schedule_run_id` is the `schedule_runs` row that made it;
- `score` is the value the optimiser weighed: yield × frames × priority, the last under B and D (D-168);
- `predicted_yield` is a model's probability, and null under the elevation proxy, which is not a prediction;
- `model_sha256` names that model;
- `explanation` (jsonb) holds the terms of the value, every pass the decision was weighed against with its value, the rule that decided a skip, the alternative (what took a skip's slot, or the best pass a selection displaced), and the run's solver status and history `as_of`.

Rows from before migration 0018 keep nulls: no recorded run made them.

**A pass can be decided again** (D-171, migration 0019). `revision` numbers a configuration's decisions about one pass, the highest current, and `(pass_id, model_config, revision)` is unique. A round decides again a pass whose current decision is a skip, or an assignment revoked while its station was offline; a skip decided again for the same reason is not written. Revision 0's id is the one minted before revisions existed. `revoked_reason` and `revoked_at` are set together, exactly when `state = 'revoked'`. An offline revocation the returning station names in `held_assignments` goes back to `held`: MSP cannot take work back, and a station holding it will execute it.

**`start_at` and `end_at` are the assignment's window, not the pass's `aos`/`los`.** They are widened from the pass by `timing_uncertainty_s`, because a station recording at exactly the predicted acquisition time starts after a pass whose element set was stale has already begun. These five columns are what let this table produce the MSP §4.3 assignment message; without them it could not. See D-021.

Skipped passes are recorded too. A scheduler that only logs what it chose cannot be evaluated.

**A skip is a record, never an assignment** (D-165). Its `state` stays `issued` for good — `check (decision = 'scheduled' or state = 'issued')`, migration 0017 — and every query that delivers, moves or expires a row filters on `decision = 'scheduled'`. The state machine below is an assignment's, so it is a scheduled row's alone.

**`state` tracks what the station did with it**, as distinct from `decision`, which is what the scheduler wanted:

```
issued  →  held  →  in_progress  →  reported
        ↘       ↘
          expired  revoked   (held → revoked, or issued → revoked, before the window)
```

| State | Meaning | Set when |
|---|---|---|
| `issued` | Delivered by the platform, not yet acknowledged | Scheduler issues it in a heartbeat response |
| `held` | Station confirms it holds it | `assignment_id` appears in `held_assignments` (MSP §4.2) |
| `in_progress` | Station is executing | Heartbeat `listening` block references it |
| `reported` | An observation has been received | Observation ingested |
| `expired` | Never reported, window has passed | Reconciliation, once `now > end_at` |
| `revoked` | Taken back before its window began; never delivered again | A held assignment the station drops (`revoked_reason = 'declined'`), or a round finding its station offline (`'offline'`) — D-171 |

**Delivery is repeated, not once-only** (D-026). Every heartbeat returns each of this station's assignments whose `start_at` falls in the next two hours and which is not yet `reported`, capped at 8 and sorted by `start_at` — including ones the station already listed in `held_assignments`. That is what makes a lost heartbeat response harmless, and it is why no delivery-receipt column exists on this table: there is nothing to receipt.

`expired` is the **decline** case. It is not the same as an observation with outcome `not_attempted`: `expired` means the station never took the work, `not_attempted` means it took the work and then failed to start. The reliability layer needs both and must never merge them.

State is derived by reconciling `held_assignments` against what was issued — see `docs/DECISIONS.md` D-003 and D-008.

### `schedule_runs`
One scheduler run that decided something (D-170, migration 0018). A round with nothing new to decide writes no row.

`(run_id, decided_at, horizon_start, horizon_end, model_config, config_sha256, parameters, yield_source, model_sha256, history_sha256, history_as_of, solver, solver_version, status, objective, bound, time_limit_s, runtime_s, detail, stations, candidates, scheduled, skipped, created_at)`

- `parameters` is the resolved `schedule.toml`, and `config_sha256` its hash.
- `yield_source` is `model` or `elevation_proxy`, and a model's run names it (`model_sha256`).
- A model that reads history names the labelled dataset it read and its `as_of`; the two are null together.
- `status` is `optimal`, `time_limit` (the best found, valid, not proven) or `fallback` (greedy's, and `detail` says why). `objective` is the value of the schedule written; `bound` is the solver's proven upper bound, null on a fallback.

### `observations` *(hypertable)*
One row per attempt, including attempts that produced nothing.

`(assignment_id, revision, observation_id, station_id, satellite_id, started_at, ended_at, outcome, signal_detected, first_detection_at, peak_snr_db, doppler_samples, products_json, client_notes, simulated, provenance, submitted_at, content_sha256, noise_floor_dbfs, receiver_gain_db, snr_samples, decoder, decoder_version, frames_decoded, frames_failed)`

`observation_id` is the public identifier MSP §4.4's acknowledgement returns, and it is a **stored generated column** derived from `(assignment_id, revision)` rather than an allocated value:

```sql
create function observation_id(assignment_id text, revision integer)
returns text language sql immutable strict parallel safe as $$
    select 'ob_' || substr(
        encode(sha256(convert_to($1 || ':' || $2::text, 'UTF8')), 'hex'), 1, 12)
$$;

observation_id text generated always as (observation_id(assignment_id, revision)) stored
```

The expression lives in a function rather than inline in the column because a generated column may only call an immutable one, and naming it makes that marking explicit and reviewable.

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

A `supersedes_id` pointer would have been the obvious shape and is rejected on modelling grounds: `revision` orders the lineage explicitly instead of requiring a chain walk, and `(assignment_id, revision)` must exist as the key regardless. `content_sha256` over the canonical body makes a byte-identical resubmission — the queued-retry case of MSP §6 — a no-op rather than a new revision. See D-015. **What "canonical" means is D-070**: a rendering of the stored record with sorted keys, no whitespace, timestamps normalised to UTC milliseconds and arrays in submitted order — not the received bytes, which could not be regenerated from a dataset snapshot. Only the platform ever computes it; the hash appears in no MSP message.

This table also carries `products_json`, holding the MSP §4.4 `products` array verbatim (D-018). It stays the record of what was sent now that `products` holds its normalised rows (D-176).

**MSP 0.3's reception evidence** (migration `0015`, D-119) is seven nullable columns: the noise floor in dBFS with the receiver gain it was measured at, `snr_samples` as sent, and the flattened `decode` block — `decoder`, `decoder_version`, `frames_decoded`, `frames_failed`. **Null is "not measured"**, which is every observation stored before 0.3, and no column has a default that would put a measurement into a row that never had one. `CHECK`s restate D-117: counts are non-negative, a floor carries its gain, statistics name their decoder, frames agree with the outcome, and `not_attempted` measured nothing. The 512-sample cap is enforced by the request model, as for `doppler_samples`. The new keys enter `content_sha256`'s canonical body only when present, so every digest stored before them still matches its row (D-118).

Partitioned on `started_at`, but ingest rejects a `started_at` outside `[now − 30 days, now + 1 hour]` as `malformed` — a client-supplied timestamp must never be trusted to place a chunk (D-013).

### `heartbeats` *(hypertable)*
`(id, station_id, sent_at, received_at, state, held_assignments, listening_assignment_id, listening_satellite_id, listening_freq_hz, listening_mode, health_json, clock_offset_s, clock_uncertainty_s, simulated)`

**This table is what allows absence to be interpreted.** Without a heartbeat confirming a station was listening on the right frequency for the right target, a missing observation is meaningless.

The listening block is stored whole — assignment, satellite, frequency **and mode** — under an all-or-nothing `CHECK`. `mode` is not decoration: a station tuned to the right frequency running the wrong demodulator did not observe the pass, and `Registry.was_listening()` is the sole authority on what counts as a confirmed miss. `health_json` is opaque and stored verbatim, capped at 4 KiB by the ingest path. See D-028.

`held_assignments text[] not null default '{}'` is the mechanism D-003 and D-008 rest on — the platform reconciles it against what it issued, and a decline is absence from the list. Never nullable: MSP §4.2 says an empty list is *meaningful* and must be sent as `[]`, so "holds nothing" and "said nothing" must stay distinguishable. See D-014.

`clock_offset_s` and `clock_uncertainty_s` are both nullable, and `null` means unknown — never conflated with `0.0`. `EVALUATION.md` §6.1 discards any timing error smaller than the reported uncertainty, which needs both numbers (D-016).

Partitioned on `received_at`, the platform's clock, not the station's `sent_at` (D-013). The MSP handler stamps it, and the station's `last_heartbeat_at`, with the one instant it reconciles the heartbeat at, so a heartbeat and every decision taken from it share one time (D-195).

Retention: **none — raw heartbeats are never dropped**, only compressed after 7 days (D-178). `Registry.was_listening`, the snapshot export and Stage 20's reclassification read raw rows for any window a pass can be asked about, and a count per hour cannot answer them. The `heartbeats_hourly` continuous aggregate serves the reads that need coverage rather than evidence. The 90-day figure planned here was withdrawn when the queries were written down; Stage 33 revisits it with measured volume.

### `pass_classifications`
What happened to each settled, scheduled pass, and the evidence it was decided from. Every reliability figure is counted from these rows (Stage 20).

`(classification_id, assignment_id, assignment_ids, pass_id, station_id, satellite_id, window_start, window_end, classification, evidence, method, config_sha256, classified_at, simulated)`

- **One row per physical pass per station**, once its pooled window has closed plus the settle margin. `assignment_ids` lists every scheduled assignment pooled into the pass, and `assignment_id` is the first of them. A GIN index serves `assignment_ids @> array[…]`, the question which row holds an assignment.
- **`classification`** is one of the eight classes of `meridian.reliability.classification` (D-180, D-181). Whether a class counts as captured, or spends the loss budget, is decided in code from the class, and deliberately not stored as a second column that could disagree with it.
- **`evidence`** is everything the classification read: each assignment and its state, the report and its revision, whether the station was heard, the registry's listening answer for each assignment, and the receptions D-147 judged the satellite by.
- **Append-only.** Unique on `(assignment_id, method, config_sha256)`, so a changed rule or parameter writes new rows beside the old, and a re-run writes nothing. See D-182.

### `assignment_revocations`
Every revocation of an assignment and every reinstatement of one (Stage 21).

`(event_id, assignment_id, station_id, event, reason, at)`

- **One row per event**, written by the same statement that moves the assignment, so an event and the change it records cannot disagree. `event` is `revoked` or `reinstated`; `reason` is `declined` or `offline` on a revocation and null on a reinstatement.
- **`at` is the platform's instant for the decision**: the scheduling round's `now` for an offline revocation, the heartbeat's for a decline or a reinstatement.
- **Append-only, and the reason it exists.** A reinstatement clears the assignment's `revoked_reason` and `revoked_at` (D-171), which is right for its state and would otherwise leave no record that the platform ever took the work back. `meridian reliability faults` reads it to show the scheduler replanned while a station was offline (D-196).

### `products`
What a station declared it holds from one reception — a waterfall, an image, decoded frames — named by `sha256` (migration `0023`, D-176).

`(id, assignment_id, revision, observation_started_at, station_id, element_index, kind, sha256, size_bytes, uri, created_at, simulated)`, with a foreign key to the observation revision it came from.

- **Producer:** observation ingest, from each element of `products_json` with a non-empty `kind` and a 64-hex `sha256`, in the transaction that writes the revision. Migration `0023` backfilled every earlier observation by the same rule. The reference client declares only files its decoder named and its product store holds.
- **Consumers:** the public observation list, which serves kind, hash and size, never `uri`; the raw snapshot's `products.jsonl`, without `uri` — though the snapshot's `observations` still carry it inside `products_json`, verbatim, and a raw snapshot is private; Stage 30's evidence dataset, by hash.
- **No transfer.** MSP 0.x defines none (D-029); `uri` is `station:products/<sha256>` for the reference client, meaning held by the station, not fetchable. An element outside the rule stays in `products_json` and gets no row.
- **Retention:** as long as the observation it belongs to — never dropped. The bytes live on the station under its store's cap, oldest evicted first, and an eviction is not reported.

### `horizon_profiles`
A station's horizon by azimuth bin: what a capability's mask **declared**, and what the station's detections **learned**. Two `source` values, never merged (D-031, D-174).

`(id, station_id, source, method, capability_id, dataset_sha256, trained_from, trained_until, azimuth_deg, azimuth_width_deg, min_elevation_deg, sample_count, built_at, simulated)`. A declared row names its capability and nothing learned; a learned row names its dataset, its span and its sample count. A bin holds its floor from `azimuth_deg` for `azimuth_width_deg`, clockwise.

- **Producer:** `meridian profiles build`, and the jobs service's `profiles` task every round. A declared profile is written when a capability's mask changes. A learned profile is built once per labelled dataset by the same functions Stage 17's features call, 36 sectors of 10°.
- **Consumers:** `GET /api/v1/stations/{id}/profiles` and the dashboard's sky plot; Stage 27's obstruction evidence. The scheduler constrains on the capability's mask directly, which is what these declared rows record.
- **Nothing here feeds prediction.** Live scoring computes its environment from the dataset in memory (D-157, D-169).
- **Retention:** append-only and never dropped: a new dataset or mask writes new rows, and an earlier profile stays so a diagnosis can cite the one it read.

### `noise_measurements` *(hypertable)*
Measured noise floor, one row per reception revision or survey reading (migration `0023`, D-173).

`(id, station_id, measured_at, source, assignment_id, revision, centre_freq_hz, bandwidth_hz, azimuth_deg, noise_floor_dbfs, receiver_gain_db, simulated, recorded_at)`, keyed `(id, measured_at)`.

**dBFS at a stated gain**, never dBm: RF calibration is outside what a station can honestly claim (D-103, D-104). `source` is `observation` or `survey`; an observation's row names its assignment and revision and has no azimuth, because one floor covers a whole pass and an unpointed antenna pointed nowhere. The sector is assigned where a profile is derived.

- **Producer:** observation ingest, from each revision carrying a floor, by the query migration `0023` backfilled with. Nothing produces a survey row yet; the `CHECK` admits one.
- **Consumers:** the raw snapshot, and so every labelled dataset; Stage 25's verdict and Stage 27's interference cause.
- Partitioned on `measured_at`, the observation's `started_at`, under the same ingest bound as `observations` (D-013). No foreign key to `observations`: TimescaleDB refuses one between hypertables, so ingest writes both in one transaction and a test checks every row against its observation.
- **Retention:** never dropped (D-178), no compression policy: its volume is the observation count.

### `interference_profiles`
A station's noise floor by 45° sector of the pass's peak and 4-hour band of local solar hour, over the station's median, with the gains behind each cell (migration `0023`, D-174).

`(id, station_id, method, dataset_sha256, trained_from, trained_until, azimuth_deg, azimuth_width_deg, hour_start, hour_width, noise_lift_db, station_median_dbfs, sample_count, gain_min_db, gain_max_db, built_at, simulated)`. All 48 cells are written, an empty one at its prior with no gains (D-161).

- **Producer and consumers:** as `horizon_profiles`' learned rows: built once per dataset, served by the profiles endpoint, cited by Stage 27, which compares "at the same gain" and can refuse a cell whose gains differ.
- **Retention:** append-only and never dropped, as `horizon_profiles`.

*Hour of day matters and a single aggregate would hide it — a rooftop in a city has a different noise floor at 8 a.m. than at 8 p.m., which is the whole reason this feature exists.*

---

## Post-reception tables

Seven tables for modules 13–17. `reception_ratings`, `reception_verdicts` and `loss_diagnoses` are built (migrations `0027`, `0028` and `0029`, D-260, D-263, D-272). The others do not exist yet, and their column tuples are the intent, settled finally when each stage writes its migration. What they share is decided in D-104:

- **Append-only, bound to what they describe.** A verdict belongs to one observation revision; a new revision gets a new verdict and the old one stays, exactly as D-015 keeps the old observation.
- **Every row names the method that produced it** — a versioned string, as `method` on the orbit service's uncertainty (D-060). A new model version appends; it never rewrites an earlier conclusion, because the evidence dataset must be able to say which version concluded what.
- **`simulated` is copied from the station's registry record**, never inferred, as for every table that can hold simulated data.
- **Simulator ground truth is in none of them.** An injected fault's cause lives in the simulator's own run record and is joined only at evaluation (D-105).
- **The word "health" appears in none of their names** — D-013 already separated `state`, `health` and liveness.

### `reception_ratings`
`(id, assignment_id, revision, observation_started_at, station_id, usable, rubric, rater, rated_at, simulated)`, with a foreign key to the observation revision it rates.

The label the verdict is calibrated against: a person's answer to whether a reception's decoded product is usable, given without seeing the verdict or any of its inputs (D-106, D-260). Append-only. A second rating of the same revision is a new row, and the latest is the label.

- **Producer:** `meridian verdict rate`, for a measured revision that declared at least one product. A simulated reception, or one with no product, is refused. A revision with no product is unusable by definition, and the labeller says so without a row here.
- **Consumers:** the raw snapshot's `reception_ratings.jsonl`, read by `meridian.datasets.usable_labels`; `meridian verdict queue`, which lists what is unrated and reads nothing the verdict reads.
- **`rater` is a tag, not a name.** Its `CHECK` admits lowercase letters, digits, `_` and `-`, up to 16 characters, so a full name does not fit. `rubric` names the written instructions followed (`usable-1`, in `OPERATIONS.md`).
- **Retention:** never dropped, as every label.

### `reception_verdicts`
`(assignment_id, revision, observation_started_at, station_id, probability_usable, method, route, inputs_sha256, partial_below, computed_at, simulated)`, keyed by `(assignment_id, revision, method)`, with a foreign key to the observation revision.

One row per observation revision per method. `probability_usable` is `0..1` and is a **calibrated probability**, not a score. `inputs_sha256` hashes the exact inputs the verdict read (D-261): outcome, detection, `peak_snr_db`, frames decoded and expected, decoder and version, listening, mode. So a verdict can be traced to what it saw and recomputed from a raw snapshot. `route` says which of the three models scored it (`full`, `snr`, `outcome`), so a verdict made from less evidence is visible as one. `partial_below` is the threshold the model was fitted with (D-262). Every closed reception gets one, including a pass that received nothing, whose verdict is low and which then goes to loss diagnosis.

- **Producer:** `meridian.verdict_build`, run by `meridian verdict apply` and by the jobs service when `VERDICT_MODEL` is set (D-263). It scores every observation revision of a scheduled assignment whose window has closed and which has no row by that method.
- **Consumers:** the raw snapshot's `reception_verdicts.jsonl`; Stage 27's diagnosis, which reads a decoded reception below `partial_below` as partial; Stage 30's evidence dataset.
- **`simulated`** is copied from the observation. A simulated reception's verdict is never a label or a training row (D-078).
- **Retention:** never dropped. A new method appends beside the old rows.

A plain table rather than a hypertable: its volume is the observation count, not the heartbeat count.

### `loss_diagnoses`
`(diagnosis_id, assignment_id, revision, observation_started_at, station_id, classification_id, cause, candidates_json, evidence_json, method, config_sha256, verdict_method, computed_at, simulated)`, unique on `(assignment_id, revision, method, config_sha256)` with nulls not distinct, with foreign keys to the assignment, the observation revision and the Stage 20 classification it read.

Written for every **failed or partial reception**: a current observation whose outcome is not `decoded`, or a `decoded` one whose verdict, under the deployment's verdict model, is below that model's `partial_below`. Also for every held assignment whose window passed with **no observation at all**, which is why `revision` and `observation_started_at` are nullable, and null together. An `expired` assignment is a decline and gets no row (D-008), and nor does a revoked one (D-171). A loss is diagnosed once its pass is classified, since "not listening" is read from that classification (D-272).

`cause` is exactly one of:

| Value | Meaning | Evidence it rests on |
|---|---|---|
| `satellite_silent` | The transmitter was not transmitting | catalogue `active` status; other stations' receptions of the satellite within 45 minutes (D-276) |
| `station_not_listening` | The station was not confirmed listening, or never began: Stage 20's class, read and not restated (D-273) | the pass's `pass_classifications` row, which asked `Registry.was_listening()` |
| `obstruction` | Signal lost in sectors the station's own earlier passes lost it in, or behind its declared horizon (D-274) | the station's observations and their `snr_samples`, the pass's track from its element set, declared `horizon_profiles` |
| `interference` | The noise floor was raised against the station's own at the same gain (D-275) | observation-sourced `noise_measurements`; the `interference_profiles` cell is cited |
| `timing_fault` | The station's clock, listening or recording was off by more than the stated timing uncertainty allows (D-277) | `heartbeats` (`sent_at`, `received_at`, the listening block, `clock_offset_s`, `clock_uncertainty_s`); the observation's window against the assignment's |
| `undetermined` | The evidence does not support any cause | — |

`undetermined` is a value, never a null: "we looked and cannot say" and "we have not looked" must stay distinguishable. `candidates_json` keeps every cause tested, fired or not, with its support and what its test found; `evidence_json` records what the diagnosis read and the thresholds it read it under. So an owner report and the evidence dataset can both show *why*, not only *what* (D-273).

- **Producer:** `meridian.reliability.diagnosis_run`, run by `meridian diagnosis run` and by the jobs service.
- **Consumers:** `meridian diagnosis explain`; the raw snapshot's `loss_diagnoses.jsonl`, measured and simulated counted apart; the evaluation report's real cases (SC-8's simulated matrix is joined to the simulator's ledger outside the database, D-105); Stages 28, 29 and 30.
- **`simulated`** is copied from the assignment. A simulated reception is never evidence about a measured station's loss.
- **Method and configuration:** `method` is `diagnosis-N`, and `config_sha256` hashes the `[diagnosis]` thresholds. A re-run under both writes nothing; a changed threshold diagnoses every loss again beside the old rows (D-182).
- **Retention:** never dropped.

A plain table: its volume is the loss count.

### `signal_baselines` *(planned)*
`(id, station_id, capability_id, elevation_bin_deg, snr_db_median, snr_db_p10, sample_count, trained_from, trained_to, method, computed_at, simulated)`

A station's own signal strength by elevation, per receive chain — two antennas on one station degrade separately. Versioned exactly as `horizon_profiles` is, so a warning can be traced to the baseline it was raised against. Built from `peak_snr_db` against maximum elevation under MSP 0.2, and from per-sample SNR once stations send MSP 0.3's `snr_samples` (D-103).

### `receive_chain_warnings` *(planned)*
`(id, station_id, capability_id, baseline_id, raised_at, cleared_at, shortfall_db, affected_bins_json, method, simulated)`

A warning that a receive chain is degrading, raised before reception fails. `cleared_at` is written once, when the shortfall recovers. **Warnings are never deleted**: SC-9's detection delay and false-alarm rate are computed from exactly these rows, and a deleted false alarm is a false-alarm rate that improves itself.

### `report_deliveries` *(planned)*
`(id, station_id, kind, assignment_id, period_start, channel, template_version, content_sha256, queued_at, sent_at, failed_at, attempts, error, simulated)`

`kind` is `pass` (with `assignment_id`) or `weekly` (with `period_start`); `channel` is `email` or `telegram`. The rendered text is not stored — `template_version` and the stored results it read regenerate it, and `content_sha256` proves the regeneration matches. **No recipient address is held in this table**; where one is held at all is D-107's open question. A simulated station's report is rendered and recorded like any other and delivered only to a test sink, never to a person.

### `dataset_exports`
`(export_id, snapshot_id, snapshot_sha256, config_sha256, seed, code_version, includes_simulated, row_counts_json, content_sha256, created_at)`

One row per evidence-dataset package; the package itself is files, as `products` are. `content_sha256` is the hash that regenerating from the same `snapshot_sha256`, `config_sha256` and `seed` must reproduce (`CLAUDE.md` rule 8). Measured and simulated receptions are separate files inside the package, and `includes_simulated` is false unless simulated ones were requested by name. Products are referenced by their `sha256`, not bundled, so the hash covers the records whatever the image store holds.

---

## Ingest and archive tables

Seven tables and a view, for modules 18 and 19. **Five are built.** Migration `0016` built `ingest_sources` and `ingest_records` — the provenance pair, reused unchanged by Stage 31 rather than duplicated (D-140) — and `archive_stations` and `archive_observations`, which hold what an archive published about somebody else's receptions (D-139). Migration `0021` built `environment_samples`, Stage 31's published values (D-221), and migration `0022` built Stage 32's `areas_of_interest` with the two regional alert tables below. `area_series` is not built: series are published as files (D-229). What all of them share is decided in D-132, D-133, D-134 and D-140:

- **Raw arrivals are append-only.** A re-fetch that differs is a new row, never an overwrite — the discipline D-015 applies to observations, applied to data we did not author either.
- **Provenance is complete or the record is refused.** Source, original identifier, source version, retrieval time, licence and checksum, for every record, whatever it carries. The version of the transformation that produced a value sits on the normalised row rather than on the arrival (D-140): an artefact is retrieved once and may be normalised many times.
- **A tile is marked as a tile**, and a tile row may never be read for a number (D-133).
- **None of these tables carries `simulated`.** They describe the world, not a station's reception; where such a value becomes a feature of a simulated station's pass, the flag stays on the observation, where it has always been.
- **No secret is stored in any of them.** Keys and registration credentials are supplied as secrets, per `GIT-WORKFLOW.md` Rule 4.

### `ingest_sources`
`(source_id, source_class, name, licence, terms_url, access_constraint, attribution_entry, added_at, active)`

One row per source we take data from, created by Stage 14's migration and **insert-only**: a change of terms is a new `source_id`, so stored records keep pointing at the terms they arrived under (D-140). `licence` and `terms_url` are recorded here and in `ATTRIBUTION.md` before the first retrieval (D-134); `attribution_entry` names the entry, so a record can be traced to the terms it arrived under — which is what decides whether the evidence dataset may republish it (D-136). `access_constraint` is `none`, `key_counted` or `registration`.

### `ingest_records`
`(record_id, source_id, original_identifier, source_version, payload_kind, retrieved_at, sha256, raw_path, media_type, byte_count, valid_from, valid_to, spatial_extent, superseded_by)`

One row per retrieved artefact — Stage 14's provenance list, as columns. `raw_path` locates the artefact inside the raw store, relative to its root, and no part of it comes from the source (D-141). `sha256` is of the raw bytes as downloaded, before any normalisation, so the hash proves what arrived rather than what we made of it. `superseded_by` links a re-fetch that differs to the row it replaces, and nothing is deleted.

`valid_from`/`valid_to` is the interval the artefact *describes*, which is not `retrieved_at` and is not interchangeable with it: a feature lookup selects on what the artefact describes and on when it was published, never on when we happened to fetch it (D-131).

`payload_kind` is `data` or `tile`. A `tile` row exists to be displayed and referenced; **no query may derive a value from one** (D-133).

### `archive_stations`
`(archive_station_id, record_id, source_id, source_station_key, name, lat_deg, lon_deg, alt_m, capability_json, content_sha256, denominator_inputs, first_seen_at)`

A station as an archive published it — never a row in `stations`, which holds stations that registered, hold a token and send heartbeats.

**Content-keyed, exactly as `element_sets` is** (D-057). A station whose published coordinates change becomes a new row rather than silently rewriting every completeness denominator already computed from the old ones. A location is stored as a pair or not at all: a latitude without a longitude is not a place, and would be used as one.

**`denominator_inputs` is generated** — `neither`, `location_only` or `location_and_capability` — so Stage 16 can *count and publish* how many stations it can compute a denominator for. A ratio computed over the stations we happened to have coordinates for, reported as though it covered all of them, is this project's own methodological threat arriving through the back door.

### `archive_observations`
`(archive_observation_id, record_id, source_id, source_observation_id, transformation_version, content_sha256, archive_station_id, satellite_key, satellite_key_kind, started_at, ended_at, max_elevation_deg, centre_freq_hz, mode, archive_outcome, source_outcome, peak_snr_db, frames_decoded, loaded_at)`

A reception as an archive published it. `observations` cannot hold one: it is keyed `(assignment_id, revision, started_at)`, its `observation_id` is generated from `(assignment_id, revision)` (D-027), and `station_id` references our own `stations` — so an archive reception has no assignment and no registered station to key it by (D-139).

**`archive_outcome` is deliberately not MSP's five.** It is `decoded`, `signal_no_decode`, `no_data` or `unknown`. `no_signal` would assert that a station was verifiably listening and heard nothing (rule 7, D-010), and no heartbeat exists for somebody else's station; `no_data` claims only that the archive holds none. Different values mean an accidental `union` of the two tables fails a `CHECK` instead of returning a plausible number. `source_outcome` keeps the archive's own string verbatim, so every mapping stays auditable.

**`satellite_key` has no foreign key to `satellites`**, and that is the decision rather than an omission. An FK would force the load path either to drop receptions for objects we do not track — a second selection filter stacked invisibly on the archive's own — or to insert into `satellites`, letting an external archive decide what pass generation propagates. The key is canonical text with its `satellite_key_kind`, joined at read time; coverage is reported as a number, never applied as a filter.

`transformation_version` is part of the key, so re-normalising an artefact under a new normaliser appends rather than overwrites (D-140).

### `ingest_provenance` *(view)*

Every stored artefact beside the terms it arrived under, joining `ingest_records` to `ingest_sources`. The provenance columns are `not null` on both sides, so "is every record's licence recorded?" is a query that returns zero by construction rather than by the loader behaving.

**None of these four is a hypertable, and none is compressed.** `element_sets` is the precedent: append-only, time-stamped, read in bulk rather than in recent windows. Making one a hypertable later is supported and cheap; undoing it is not, and `downgrade()` always raises. Compression would also be perverse — archive receptions are months old when they load, so a policy on `started_at` would compress a chunk on creation and every backfill would write into a compressed one.

### `environment_samples`
`(sample_id, record_id, source_id, transformation_version, series_key, content_sha256, quantity, value, missing_reason, value_unit, observed_from, observed_to, published_at, published_basis, product, lat_deg, lon_deg, footprint_m, quality, loaded_at)`

The normalised values features and regional series are read from — an index, a condition, a composite's pixel, a detection — built by migration `0021` (D-221). One row per value per artefact, keyed `(record_id, series_key, transformation_version)` and content-hashed, so re-normalising appends and a disagreeing normaliser is refused, as for archive receptions (D-140, D-142).

**`published_at` is the load-bearing column.** It is when the value became available: the artefact's own production time where it declares one earlier than our fetch (`published_basis = 'source_declared'`), otherwise our retrieval (`'retrieved'`), and never later than the retrieval (D-222). The value used for a pass is chosen among rows published before it (D-131). A later revision of the same interval is a new row with a later `published_at`, and a model that selects on `observed_from` alone has read the future.

**A missing value is a row**: `value` null and `missing_reason` saying why, exactly one of the two present by CHECK. It is never a zero, and the pre-pass rule never falls back to an older value across one (D-221).

`observed_from`/`observed_to` is the interval described, closed, equal for an instantaneous detection. Location is `lat_deg`/`lon_deg` as a pair, null for a global value, with `footprint_m` the side of the square it stands for. `product` names the source's product and version and `quality` its own flag, verbatim. No `area_id`: which area a value falls in is Stage 32's computation over these rows.

### `areas_of_interest`
`(area_id, label, geometry, geometry_sha256, centroid_lat_deg, centroid_lon_deg, area_km2, created_at, active, notes)`

Built by migration `0022` with Stage 32. A place and a label — nothing else. **No owner, no contact, no address**: an area of interest describes ground, and the moment it describes a person it becomes personal data the project does not hold (`PROJECT.md` §16). Who may register one, and whether a registration is public, is open (D-137); until it is settled an operator registers one at the command line, nothing about an area is published, and a label or note that looks like an email address, a phone number or a street address is refused (D-227).

`geometry` is a GeoJSON Polygon with one ring, CHECKed as a Polygon; the centroid and area are derived by `meridian.regions.geometry` and stored so a listing does not need the geometry. `geometry_sha256` is unique, so one shape is one area and a changed shape is a new area. `active` retires an area without deleting it, because a series that vanishes cannot be checked against what was published from it. `notes` is never exported into a snapshot.

### `area_series` *(not built — D-229)*

Planned as a table of what the ingested products say about each area. **A series is a pure function of a raw snapshot and a configuration**, so it is published as files in a regional report instead (`data/datasets/regions/`, below), where every point names its sources, products, record ids and retrieval time. A table would be a cache of that function that could drift from it.

### `region_alerts` and `region_alert_deliveries`
`region_alerts (alert_id, area_id, quantity, rule, baseline_from, baseline_until, current_from, current_until, baseline_value, current_value, change, change_low, change_high, threshold, report_sha256, summary, recorded_at)`
`region_alert_deliveries (delivery_id, alert_id, channel, outcome, detail, attempted_at)`

Built by migration `0022`. A regional alert is a change in an area against its baseline whose whole interval lay beyond its threshold (D-231), recorded from a regional report. `alert_id` is `ra_` and 24 hex digits derived from what the alert is about and the snapshot and configuration it came from, so recording a report twice writes nothing; a CHECK holds the interval in order — not the change inside it, which a percentile bootstrap does not promise. Every delivery attempt is appended; the only channel is `record_only` until Stage 29 builds notifications (D-232). Neither table shares a name with the platform's Prometheus alerting (D-228).

---

## Dataset snapshots *(files, not tables)*

Stage 15's two artefacts, and Stage 17's models, are directories on disk, not rows. Each is content-addressed and sealed read-only, and each carries a `manifest.json` listing its files with their sha256 and row counts. The directory's hash is the sha256 of that manifest's canonical bytes, with `created_at` left out (D-144). Rows are canonical JSON Lines, one table per file, in primary-key order (D-070's rules).

### Raw snapshot — `data/datasets/snapshots/<as_of>-<hash prefix>/`

Written by `meridian snapshot export`, the only step that reads the database, inside one `REPEATABLE READ, READ ONLY` transaction. `as_of` is that transaction's time and cannot be chosen, because several columns are current state rather than history (D-143). It holds, for passes from `--since` to `as_of`:

- `passes`, `assignments`, and every `observations` revision submitted by `as_of`, with the `noise_measurements` and `products` recorded from them *(Stage 19)* — `products` without its `uri` column, which names a place on one station, though `products_json` still holds it verbatim (D-173, D-176);
- `listening` — per settled scheduled assignment, `listening_confirmed` as `Registry.was_listening()` answered it at export (D-145) — and the `heartbeats` overlapping those windows;
- the `element_sets` the passes were computed from, `satellites` with their `transmitters`, and `stations` with their `capabilities`, effective from `registered_at` until `deleted_at`;
- `archive_stations`, `archive_observations` and `ingest_provenance`, kept in their own files and their own vocabulary;
- `environment_samples` *(Stage 31)* — every published value made public before `as_of` that describes time from a week before `since`, with the artefacts it cites added to `ingest_records` and their sources' terms to the manifest (D-222). A snapshot from before Stage 31 has no such file and reads as holding none;
- `areas_of_interest` *(Stage 32)* — every area registered before `as_of`, active or retired, without its notes (D-227). `ingest_records` also carries every public artefact fetched about time near the scope — a FIRMS day with no detections, a tile — with its `spatial_extent` (D-229);
- `pass_ground_tracks` *(Stage 32)* — per pass with a report, measured or simulated and flagged: the sub-satellite point every 30 s over `[aos, los)`, to three decimals of a degree, propagated at export so no regional computation propagates (D-230);
- `archive_passes` *(Stage 16)* — the passes our orbit service says each archive station could have received, for the satellites it was seen receiving, propagated at export so labelling never propagates (D-150). What could not be computed is counted in the manifest, not left out.
- `pass_tracks` *(Stage 17)* — per measured pass, where it was in the sky: `pass_id`, `start` (its `aos`), `step_s` (30), and `azimuth_deg` and `elevation_deg` sampled every 30 s over `[aos, los)`, to a hundredth of a degree with azimuth folded into `[0, 360)`. Propagated at export from the pass's own element set over its own station, after the snapshot transaction has read everything and closed, so that no feature propagates and no hash rests on `sgp4` agreeing to the last bit (D-158). Simulated passes get no track, and a pass whose station or element set is not in the snapshot is counted under `pass_tracks.*` in the manifest, never dropped.

Every row that has a `simulated` column keeps it. **A raw snapshot is outside the database backup and cannot be retaken**, since no later export can have the same `as_of`.

### Evaluation dataset — `data/datasets/evaluation/<hash prefix>/`

Written by `meridian snapshot label` from a raw snapshot and a labelling configuration, with no database, clock or network — so the same two inputs always give the same hash, which is Stage 15's gate.

- `labels.jsonl` — one row per geometrically available **physical** pass, every prediction of one rise grouped and listed as `pass_ids` (D-148): keys, `label` or `exclusion_reason`, `source_outcome`, `listening_confirmed`, `scheduled_by` and `simulated`. The labels and their order of precedence are D-146; the satellite-silent evidence is D-147.
- `archive_receptions.jsonl` — archive receptions with their own outcome vocabulary and provenance. They never receive a Meridian label.
- `station_days.jsonl` *(Stage 16)* — per station-day and population (`own`, or `archive` with the station as `archive:<id>`): eligible, attempted and usable passes, completeness, and a status of `retained`, `below_threshold`, `empty` or `inactive` (D-149 to D-151). Simulated passes have no station-days.
- `propensities.jsonl` *(Stage 16)* — per eligible pass: its population, station, satellite, `aos`, peak elevation and whether it was attempted; the model, the fallback level used, the cell and its available and attempted counts; the propensity; and the floored weight, null for a pass not attempted (D-152, D-153). A propensity of 0 is a pass with no support. **No outcome is written here.**
- `manifest.json` — the raw snapshot's hash, the transformation version, the configuration's sha256 and values, and the measured and simulated counts reported apart. From Stage 16 it also counts station-days by population and status, and carries a **`summary`**: per population, the completeness summary (statuses, totals, deciles, histogram, sensitivity) and either the weight diagnostics or a stated reason there are none (D-154). `summary` is optional in the format, and written and hashed only when present, so a manifest from before Stage 16 keeps its bytes and its hash; `meridian snapshot completeness` refuses a dataset without one.


### Model — `data/datasets/models/<hash prefix>/` *(Stage 17)*

Written by `meridian model fit` from an evaluation dataset, its raw snapshot and a model configuration (`deploy/model.toml.example`). Published by the same rules and read by the same reader as a snapshot, so a changed byte is refused (D-163).

- `model.json` — one canonical JSON object, with trailing newline:
  - `model_format` (1) and `configuration` (`A` to `D`), `population` (`own` or `archive`), `weighted_by_priority`, `reads_history` and `min_station_history`;
  - `feature_version` — the version of the feature code that computed its examples, `features-1` today. A model is scored and evaluated only by the same feature code, and refused by any other (D-255);
  - `configured` — the calibrated logistic regression: `features` in order, the training span's `mean` and `scale` per feature, `coefficients` on the standardised features, `intercept`, and `calibration` (`method` `platt`, `a`, `b`) so that p = sigmoid(a × logit + b);
  - `fallback` — the geometry-only model in the same shape, present exactly when the configuration reads history (D-161), null otherwise;
  - `train_until`, `validate_until` and `as_of` — the three spans (D-162); `inverse_regularisation`, `weighting` and `seed`;
  - `libraries` — the numpy and scikit-learn versions that fitted it;
  - `dataset_sha256` and `config_sha256`, so a copy of the file on its own still names its inputs.

  Every number is rounded to 12 significant figures, and the model was standardised and calibrated with the rounded values, so what is stored is what was used. It is scored by `meridian.prediction.score` with the standard library alone (D-155).
- `manifest.json` — kind `model`; the dataset's hash as `derived_from`, `model-1` as the transformation version, the configuration's hash and resolved values as `parameters`; the archive `sources` the dataset carried, with their licences and terms, so a model fitted on archive receptions still names whose they were; and counts: `examples.train`, `.validate` and `.test` with their `_decoded`, `examples.simulated` (usable passes left out, D-078) and `examples.without_weight` (left out of an `ipw` fit for want of a weight).

**A model holds no example.** Examples are rebuilt from the dataset and its raw snapshot whenever they are needed, since they are a pure function of both (D-157), and `meridian model evaluate` follows `derived_from` to find them, checking each hash.

### Regional report — `data/datasets/regions/<hash prefix>/` *(Stage 32)*

Written by `meridian regions report` from a raw snapshot and a regional configuration (`deploy/regions.toml.example`), with no database, network or clock, so the same two inputs always name the same directory (D-229). Published and read by the same rules as a snapshot.

- `areas.jsonl` — every area in the snapshot: id, label, area, active, bounding box and the shape's digest.
- `series.jsonl` — per active area, quantity and period: the value or a `missing_reason`, how many published values it combines, its unit, the `method` that placed the product on the area (`regions-1: …`), and the `sources`, `products`, `record_ids` and newest `retrieved_at` behind it.
- `changes.jsonl` — per area and quantity with a rule: both periods' counts and means, the change, its bootstrap interval, and a verdict of `alert`, `within` or `insufficient` with the reason (D-231). `alerts.jsonl` is the `alert` rows, each with its derived `alert_id`.
- `coverage.jsonl` — each decoded reception whose ground track came within half a swath of an area, with the closest approach and `simulated` (D-230).
- `ingest_agreement.jsonl` and `weather_agreement.jsonl` — the two cross-checks, as rates with Wilson intervals and counts (D-233).
- `imagery.jsonl` — tiles overlapping each area, by record id, each labelled as imagery that is never read for a value (D-133).
- `manifest.json` — kind `regions_report`; the raw snapshot as `derived_from`, `regions-1` as the transformation version, the configuration's hash and values; the sources' terms; and counts, coverage measured and simulated apart.
---

## Conventions

- **All timestamps UTC**, stored as `timestamptz`. No exceptions, no local time anywhere.
- **Frequencies in Hz** as `bigint`. Never floats, never MHz.
- **Angles in degrees** as `double precision`. Azimuth 0–360, elevation −90 to +90.
  Latitude −90 to +90 and longitude −180 to +180 (ISO 6709) — stated because the
  azimuth range above is not the longitude range, and `stations.lon_deg` shipped
  with a 360 upper bound taken from it, under which 200° and −160° were two
  storable spellings of the same meridian. Corrected by migration 0007, which
  rewrites any row stored under the old range before narrowing the `CHECK`
  (D-052).
- **A column name spells its words out**, whatever the wire calls the field.
  `CLAUDE.local.md` §4 permits only the abbreviations in `GLOSSARY.md`, so
  `stations.client_implementation` is not `client_impl` even though MSP §4.1
  puts `client.impl` on the wire. The protocol's spelling is carried by a
  Pydantic alias in `api/models/`, which is the single place the two vocabularies
  are allowed to meet.
- **`simulated boolean not null default false`** on every table that can hold simulated data — `stations`, `passes`, `assignments`, `observations`, `heartbeats`, and without the default on `products`, `noise_measurements`, `horizon_profiles` and `interference_profiles`. Never nullable: an unknown provenance is a bug. Always copied from the station's registry record, never read from a payload.
- **Satellite identity** is `norad:NNNNN` as text, not a bare integer. Objects without NORAD IDs exist.
- **Soft delete only.** Nothing in the observation lineage is ever hard-deleted.

## Keys, enums and hypertables

Settled in D-013 and D-021, because `DATA-MODEL.md` previously gave column names without types, keys or nullability and the migrations could not be written from it.

- **Where MSP already forces an id to exist, be unique and be stable, it is the primary key** — `stations.station_id`, `assignments.assignment_id`, `satellites.satellite_id`. A second surrogate key beside them buys nothing but joins. Everything else gets `bigint generated always as identity`.
- **A hypertable's primary key must include its partitioning column.** TimescaleDB requires it — verified against 2.29, which rejects the hypertable outright with *"cannot create a unique index without the column used in partitioning"*. So `observations` is keyed `(assignment_id, revision, started_at)` and `heartbeats` `(id, received_at)`. A constraint of the storage engine, not a modelling preference.
- **Enums are `text` with a `CHECK` constraint**, not Postgres `enum` types. A `CHECK` is dropped and recreated inside one transaction; altering an `enum` is not, and Rule 9 of `GIT-WORKFLOW.md` forbids editing a merged migration.
- **`simulated` is copied from the station's registry record, never read from the payload.** MSP §3 says station-submitted data is untrusted, and provenance is the last field to take on trust. `store.stations.find_station_provenance` is how a caller reads it back (D-048). `element_sets` is the one table that records provenance in `source` instead of a boolean, deliberately — D-049.
- **The schema is forward-only.** No migration implements `downgrade()`; each raises `NotImplementedError`, and a unit test asserts every revision does so in its own right rather than inheriting a silent `pass`. Rolling back means restoring a backup, not stepping the schema down. This follows from Rule 9 of `GIT-WORKFLOW.md` — a merged migration is history — and is stated here because otherwise a contributor discovers it from a stack trace.

| Column | Values |
|---|---|
| liveness (derived on read, not a column — D-054) | `never_seen`, `online`, `stale`, `offline` |
| `heartbeats.state` (reported) | `idle`, `slewing`, `listening`, `processing`, `degraded`, `maintenance` — MSP §4.2 |
| `assignments.state` | `issued`, `held`, `in_progress`, `reported`, `expired` — D-008 |
| `assignments.decision` | `scheduled`, `skipped` |
| `assignment_revocations.event` / `.reason` | `revoked`, `reinstated` / `declined`, `offline` — D-196 |
| `pass_classifications.classification` | `successful_reception`, `signal_no_decode`, `confirmed_miss`, `satellite_silent`, `satellite_state_indeterminate`, `station_unavailable`, `station_not_confirmed_listening`, `assignment_declined` — D-180 |
| `observations.outcome` | the five values of MSP §4.4 — D-010 |
| `observations.provenance` | `station`, `archive`, `manual` |
| `element_sets.source` | `celestrak`, `spacetrack`, `manual`, `simulator` |
| `noise_measurements.source` | `observation`, `survey` — D-173 |
| `horizon_profiles.source` | `declared`, `learned` — D-031, D-174 |
| `satellites.orbital_regime` | `leo`, `meo`, `geo`, `heo`, `other` — `EVALUATION.md` §6.1 segments by it |
| `station_capabilities.band` | `vhf`, `uhf`, `l`, `s`, `other` |
| `station_capabilities.polarisation` | `rhcp`, `lhcp`, `linear_v`, `linear_h`, `linear`, `none` |
| `station_capabilities.modes` | free-text lowercase array in Phase 1 — decoder naming varies too much to freeze |
| `loss_diagnoses.cause` | `satellite_silent`, `station_not_listening`, `obstruction`, `interference`, `timing_fault`, `undetermined` — D-104 |
| `report_deliveries.kind` *(planned)* | `pass`, `weekly` — D-098 |
| `report_deliveries.channel` *(planned)* | `email`, `telegram` — D-098 |
| `ingest_sources.access_constraint` | `none`, `key_counted`, `registration` — D-132 |
| `ingest_records.payload_kind` | `data`, `tile` — a `tile` is never read for a value, D-133 |
| `environment_samples.quantity` | lowercase words, `^[a-z][a-z0-9_]{0,63}$`: `kp_index`, `cloud_cover`, `aerosol_optical_depth`, `fire_radiative_power`, `ndvi`, `precipitation`, `night_lights_radiance` so far — free text, as `station_capabilities.modes` is, because naming varies too much between sources to freeze |
| `environment_samples.published_basis` | `source_declared`, `retrieved` — D-222 |

---

## Derived views

`observations_current` exposes the highest revision per assignment, and it ships alongside the `observations` table because appending corrections rather than overwriting them is meaningless without something that reads the current one.

Two more are built (migration `0024`, D-177). **They are operators' reads, not reported numbers**: a view over live tables answers differently each time it is read, and every published figure comes from a snapshot (rule 8).

- `timing_error` — first detection against predicted AOS for each current observation, **corrected by the station's clock offset** from its nearest heartbeat (`EVALUATION.md` §6.1, D-025), with the uncorrected figure and element-set age beside it. §6.1's exclusions are named in `excluded` — `clock_offset_unknown`, `within_clock_uncertainty` — and not applied. `meridian passes timing` reads it.
- `scheduler_performance` — each schedule run with its solver status and what became of its assignments: revoked, expired, each outcome, still owed, frames decoded. `meridian schedule runs` reads it.

`heartbeats_hourly` is a real-time continuous aggregate: heartbeats and listening heartbeats per station and hour, refreshed every 30 minutes from the first heartbeat (D-178). `GET /api/v1/stations/{id}/uptime` reads it. A row written below its refresh watermark — only a hand-written or restored one can be — is counted at the next refresh.

**Not views, and why** (D-177):

- `pass_completeness` — completeness is `station_days.jsonl` in every evaluation dataset (D-149, D-154), regenerable from a snapshot.
- element-set divergence — it needs the propagator, so it lives in `meridian.orbit`, and Stage 17 reads its own from a rise's predictions (D-159).
- `sli_current` — the service level indicators are counted in `meridian.reliability` from `pass_classifications` (D-184), because whether a class counts as captured or lost is a rule in code (D-182), and a snapshot must be able to count the same figures without a database.

Views, not materialised tables, until profiling proves otherwise.
