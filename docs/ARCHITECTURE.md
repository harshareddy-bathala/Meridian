# Architecture

## Layers

```
                        ANYONE, ANYWHERE
                               │
                  ┌────────────▼─────────────┐
                  │   DASHBOARD + PUBLIC API │
                  └────────────┬─────────────┘
                               │
      ┌────────────────────────▼──────────────────────────┐
      │                  PLATFORM                         │      ┌──────────────┐
      │                                                   │      │  External    │
      │  orbit ──▶ prediction ──▶ scheduler               │◀╌╌╌╌╌┤  archives    │
      │    │            │              │                  │      │  (optional,  │
      │    └────────────┴──────────────┘                  │      │  data only)  │
      │              observation store                    │      └──────────────┘
      │         registry        reliability               │
      │         notifications   datasets                  │╌╌╌╌╌▶ email, Telegram
      └────────────────────────┬──────────────────────────┘       (optional, outbound)
                               │
      ┌────────────────────────▼──────────────────────────┐
      │       MERIDIAN STATION PROTOCOL  (MSP)            │
      └───────┬─────────────────┬─────────────────┬───────┘
              │                 │                 │
      ┌───────▼──────┐  ┌───────▼───────┐  ┌──────▼───────┐
      │ Station 001  │  │ Microcontrol- │  │ 50 simulated │
      │ Pi + SDR     │  │ ler station   │  │ (labelled)   │
      └──────────────┘  └───────────────┘  └──────────────┘
```

**The independence test:** remove every dashed element and the system still schedules, receives, decodes, monitors and reports.

---

## Modules

The sections below describe each module's responsibility in the finished system. Several of them are not built yet, so this table says what is on disk today — a contributor can then tell a design from an implementation before reading on. Stage numbers refer to `docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md`.

| Module | On disk today |
|---|---|
| `platform/orbit`, `registry`, `store`, `api` | Implemented, and still growing. The API serves all four MSP endpoints; the public read API is Stage 11. |
| `platform/scheduler` | Value types, and the two baselines that exist to be beaten (Stage 7). The constrained optimiser and the retrospective oracle are Stage 18. |
| `platform/prediction` | Its interface and nothing else — Stage 17. |
| `platform/observations` | Ingest and the canonical body a revision is compared against (Stage 9). |
| `platform/reliability` | Its interface and nothing else — Stage 20. |
| `client`, `simulator` | A station that registers, holds work, executes it and delivers observations from a durable queue, and a deterministic fleet of virtual ones that drives it over real MSP (Stage 10). The receiver and decoder behind the client's execution seam are Stage 13. |
| `dashboard`, `ingest` | No directory yet — Stages 11 and 14. |
| `platform/notifications`, `platform/datasets` | No directory yet — Stages 29 and 30, which add the post-reception layer (D-099). |
| `firmware` | No directory yet, and excluded from the software roadmap: it is built alongside the antenna and rotator rather than in a software stage. |

### `platform/orbit`
Propagation, element-set archive, uncertainty model.

Owns the `sgp4`/`skyfield` boundary — **no other module imports them directly.** Everything goes through this module's interface, so a propagator change never ripples outward.

Provides: pass windows for a location and capability; look angles over time; expected Doppler curve; position uncertainty for a given element-set age; historical element-set divergence.

*Coordinate frames are where silent bugs live. TEME (SGP4 output) is not ECEF is not topocentric. Convert deliberately; test against known ground-truth passes.*

### `platform/prediction`
Feature extraction, yield model, horizon inference, calibration.

Provides: `P(decode | station, pass)`; learned per-azimuth horizon profile; calibration metrics; **the reception verdict** (module 13).

**Must support running any of the four ablation configurations by config flag** — see `docs/EVALUATION.md`. Must degrade to geometry-only for stations with no history.

**The reception verdict** is a calibrated probability that a finished reception is usable, computed for every observation revision — including one that received nothing. It is a different quantity from yield: yield is estimated *before* a pass for the scheduler, the verdict *after* it from what was received. Its inputs are stored observation fields (outcome, `peak_snr_db`, decoder statistics, frames decoded against frames expected), read through `platform/observations`, and listening evidence, read through `platform/registry`. It is held to the same discipline as yield — temporal splits, a reliability diagram and a Brier score against a base rate — and is trained and evaluated on measured receptions only (D-078, D-102). A pass's own verdict is never a feature of that pass's yield prediction. It does not decide whether a pass was captured; reliability does, using it. See D-099.

Knows nothing about MSP or HTTP.

### `platform/scheduler`
Constrained optimisation over candidate passes.

Consumes predictions; **does not read the observation store directly.** Enforces non-overlap including slew and settling time, per-station capability limits, and operator priority weights. Produces assignments and the reasoning behind each — the dashboard shows *why* a pass was chosen or skipped, so the justification must be a first-class output, not reconstructed later.

Also computes the retrospective oracle schedule for the schedule-efficiency metric.

### `platform/registry`
Station registration, capabilities, tokens, health state, last-heartbeat age.

The authority on whether a station was listening at a given moment. Every reliability metric depends on this being right.

### `platform/observations`
Ingest, normalisation, deduplication, the system of record.

Records are immutable once written; corrections are additive. Every record carries provenance (which station or archive, when retrieved) and a `simulated` flag propagated from MSP registration.

### `platform/reliability`
SLI computation, SLO evaluation, irrecoverable-loss budget, failure injection.

**Absence is not a miss.** A pass counts as missed only when the registry confirms the station was listening on the right frequency for the right target. Encode this in one place, here, and never duplicate the logic.

**Loss diagnosis** (module 14) builds on that classification rather than restating it. For every failed or partial reception, and every held assignment that produced no observation, it names the most likely cause from evidence — satellite silent, station not listening, obstruction in that direction, interference, or a timing or clock fault — and says **undetermined** when the evidence does not support one. A declined (`expired`) assignment is not a reception and is not diagnosed. Evidence is Meridian's own: heartbeats, the network's contemporaneous receptions, the catalogue's transmitter status, and the horizon and interference profiles. Archive data is not consulted at runtime (D-099), and a simulated reception is never evidence about a measured one.

**The station health watch** (module 15) compares a station's signal strength at each elevation against that station's own history, and raises a warning when the receive chain — antenna, cable, LNA — is degrading, before reception fails. Elevation comes from `platform/orbit`, signal measurements from `platform/observations`. Its conclusions are named receive-chain warnings, not "health", which already means the registry's liveness and the heartbeat's reported object (D-013).

### `platform/notifications`
Owner reports (module 16): a plain-language message to a station's owner after each pass — what was received and its verdict, or the diagnosed cause of the loss — and a weekly station summary.

Reads the verdict, diagnoses and receive-chain warnings through the prediction and reliability interfaces, and renders them through **versioned templates only**. No language model, so the same stored results and template version always produce the same text (D-095). Delivers by email, or through a Telegram bot.

**Outbound and optional.** A failed delivery is recorded and retried, and never delays or changes scheduling, reception or any stored result — the independence test holds with this module switched off. It is a separate module because it aggregates three others and owns the platform's only outbound messaging dependency; inside any one of them it would make that module depend on the rest (D-099). Where an owner's address comes from is open (D-104).

### `platform/datasets`
The evidence dataset (module 17), and the snapshot manifest and content hashing Stage 15 defines, shared rather than written twice.

Exports receptions with their verdict, diagnosed cause and full provenance — station, element set, `simulated`, content hash — as a package whose hash is regenerable from a snapshot, a configuration and a seed. Reads stored rows through `store` and **never recomputes** a verdict or a diagnosis, which is what keeps the hash stable. Measured and simulated receptions are written to separate files, and simulated ones are exported only when asked for by name (D-101).

### `platform/api`
Two surfaces: the public read API and the MSP endpoints. Thin — validation and serialisation only, no business logic.

### `client`
Reference station client. Polls heartbeat, receives assignments, drives the receiver and rotator, decodes, submits observations.

Must survive: network loss mid-pass (continue, queue results), power loss (rejoin cleanly), and clock skew (report offset). Knows nothing about the database.

### `simulator`
Virtual stations speaking real MSP over the real network stack to the real platform. Deterministic from a seed, and from each station's own index — raising the station count leaves station 1 unchanged (D-075). Geometry is real: it propagates real catalogue element sets, so only its *outcomes* are synthetic.

Not a mock. It is a client implementation — the same transport, held-work record, upload queue and loop the reference station runs, with a decision model where a real station puts a radio — and it is what makes software-first development possible.

Outcomes are elevation-driven, which is what makes simulated traffic worth generating and is also why **simulated observations are excluded from every model training and evaluation set**, not merely from reported aggregates (D-078). Distributions fitted to real archive data are Stage 14's, once there is an archive to fit them to.

### `firmware`
Arduino rotator controller. Stepper control, homing, limit switches, network command interface.

Target is an Arduino Uno R4 WiFi — **Renesas RA4M1, not AVR.** AVR-targeted stepper libraries will not port unchanged.

### `ingest`
External archive adapters. **Optional path.** Failure here degrades model quality; it never blocks scheduling or reception.

---

## Rules

1. Module boundaries are firm. Cross-module access goes through interfaces, not database queries.
2. Only `platform/orbit` imports propagation libraries.
3. Only `platform/reliability` decides what counts as a miss.
4. The `simulated` flag propagates from MSP registration to every derived record and every API response.
5. The station client never assumes connectivity. Reception is never blocked on the platform being reachable.
6. No transmit code paths anywhere.
7. Owner reports and dataset exports read stored results; they never recompute a verdict or a diagnosis, and no delivery failure reaches scheduling or reception.

---

## Deployment

Everything in Docker Compose. Postgres + TimescaleDB on the Pi's NVMe. Public access via a secure tunnel — no static IP, works from behind the college network.

`docker compose up` on a clean machine must produce a working platform in under ten minutes.
