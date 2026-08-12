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
      └────────────────────────┬──────────────────────────┘
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
| `client`, `simulator` | A station that registers, holds work, executes it and delivers observations from a durable queue; one virtual station. Completed in Stages 10 and 13. |
| `dashboard`, `ingest` | No directory yet — Stages 11 and 14. |
| `firmware` | No directory yet, and excluded from the software roadmap: it is built alongside the antenna and rotator rather than in a software stage. |

### `platform/orbit`
Propagation, element-set archive, uncertainty model.

Owns the `sgp4`/`skyfield` boundary — **no other module imports them directly.** Everything goes through this module's interface, so a propagator change never ripples outward.

Provides: pass windows for a location and capability; look angles over time; expected Doppler curve; position uncertainty for a given element-set age; historical element-set divergence.

*Coordinate frames are where silent bugs live. TEME (SGP4 output) is not ECEF is not topocentric. Convert deliberately; test against known ground-truth passes.*

### `platform/prediction`
Feature extraction, yield model, horizon inference, calibration.

Provides: `P(decode | station, pass)`; learned per-azimuth horizon profile; calibration metrics.

**Must support running any of the four ablation configurations by config flag** — see `docs/EVALUATION.md`. Must degrade to geometry-only for stations with no history.

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

### `platform/api`
Two surfaces: the public read API and the MSP endpoints. Thin — validation and serialisation only, no business logic.

### `client`
Reference station client. Polls heartbeat, receives assignments, drives the receiver and rotator, decodes, submits observations.

Must survive: network loss mid-pass (continue, queue results), power loss (rejoin cleanly), and clock skew (report offset). Knows nothing about the database.

### `simulator`
Virtual stations speaking real MSP over the real network stack to the real platform. Deterministic from a seed. Outcome distributions fitted to real archive data.

Not a mock. It is a client implementation, and it is what makes software-first development possible.

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

---

## Deployment

Everything in Docker Compose. Postgres + TimescaleDB on the Pi's NVMe. Public access via a secure tunnel — no static IP, works from behind the college network.

`docker compose up` on a clean machine must produce a working platform in under ten minutes.
