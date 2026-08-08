# CLAUDE.md

Context for AI coding assistants working in this repository. Read this before writing code.

---

## What this project is

**Meridian** is a control platform for satellite ground stations. It decides which satellite passes are worth receiving, schedules them, monitors the stations doing the work, and reports measured reliability. It also defines **MSP** (Meridian Station Protocol), an open protocol any receiving station can implement to join the network.

One physical station is built by the team. It is the network's first member and the instrument that validates the software. **The software is the contribution; the station is the proof.**

This is a final year project, team of 3, two semesters. It is assessed academically and will be defended in a viva.

---

## Naming — keep it minimal

Only two proper nouns exist. Do not invent more.

- **Meridian** — the project
- **MSP** — the protocol

Everything else is described in lowercase plain terms: *the platform*, *the station client*, *the dashboard*, *the scheduler*, *the simulator*, *a station*.

Never name a component "agent". Use "client" (client/server). Reviewers read "agent" as "AI agent" and get confused about what was built.

---

## Hard rules

1. **Never reimplement `sgp4`.** Use the `sgp4` / `skyfield` libraries. A hand-written propagator will be slower and subtly wrong. Same for error-correcting codes, demodulation, and rotator wire protocols.
2. **Update `ATTRIBUTION.md` in the same commit** as any work informed by reading another project's source. One line: date, what was read, what was written independently, licence of the source.
3. **Never copy source from GPL/AGPL projects into this repository.** Read, understand, write our own.
4. **The station never transmits.** No transmit code paths, ever. Receive only.
5. **Simulated data is labelled as simulated** at every layer — database column, API field, dashboard badge, report. A simulated result presented as measured destroys the project's credibility.
6. **Temporal splits only** for model evaluation. Never `train_test_split(shuffle=True)` on time-series observation data — it leaks the future.
7. **Absence is not a miss.** A station that reported no data is only counted as having missed a pass if its heartbeat confirmed it was listening, on the right frequency, for the right target. This distinction is load-bearing for every reliability metric.
8. **Every number in a report is regenerable** from a dataset snapshot, a config file and a seed.

---

## The independence test

> If every external service went offline permanently tomorrow, Meridian would still schedule, receive, decode, monitor and report using our own station alone.

Build the standalone path first; add external archive ingest as enrichment afterwards. Never make an external service a runtime dependency of scheduling or reception. External data is training input only.

---

## Architecture

```
dashboard / public API
        │
     platform
     ├── orbit service          propagation, element-set archive, uncertainty
     ├── prediction             yield model, horizon inference
     ├── scheduler              constrained optimiser
     ├── registry               stations, capabilities, health
     ├── observation store      the system of record
     └── reliability            SLIs, SLOs, loss budget, failure injection
        │
       MSP  ── open protocol ──
        │
  ┌─────┴──────┬──────────────┬─────────────┐
station 001   microcontroller  simulator   future stations
(Pi + SDR)    station          (50 virtual)
```

Module boundaries are firm. The scheduler must not read the observation store directly; it consumes predictions. The prediction module must not know about MSP. The station client must not know about the database.

---

## Repository layout

Directories marked *(planned)* do not exist yet. Do not create them before the
stage that needs them — an empty directory reads as work that stalled.

```
meridian/
├── CLAUDE.md
├── README.md
├── ATTRIBUTION.md
├── LICENSE               Apache-2.0
├── docs/
│   ├── ARCHITECTURE.md
│   ├── MSP-SPEC.md
│   ├── DATA-MODEL.md
│   ├── EVALUATION.md
│   ├── DECISIONS.md      decisions taken during implementation
│   ├── GIT-WORKFLOW.md   branching, commits, migration rules
│   ├── GLOSSARY.md
│   ├── PROJECT.md        the full project document
│   └── SOFTWARE-IMPLEMENTATION-ROADMAP.md   the staged build order
├── platform/             distribution: meridian
│   └── src/meridian/
│       ├── orbit/        propagation, element sets, uncertainty
│       ├── prediction/   features, models, calibration
│       ├── scheduler/    optimiser, policies, baselines
│       ├── registry/     station registration and health
│       ├── observations/ ingest, store, dedup
│       ├── reliability/  SLI computation, budget, chaos
│       ├── store/        SQL access layer
│       └── api/          public + MSP endpoints
├── client/               distribution: meridian-client
│   └── src/meridian_client/     reference station client
├── simulator/            distribution: meridian-sim
│   └── src/meridian_sim/        virtual stations speaking MSP
├── tests/                unit, integration, msp_conformance, e2e
├── site/                 the static public website
├── deploy/               compose files, migrations, config, dashboards
├── firmware/             (planned) Arduino rotator controller
├── dashboard/            (planned) web front end
├── ingest/               (planned) external archive adapters
└── analysis/             (planned) notebooks and evaluation scripts
```

**Three Python distributions, `src/` layout, per D-012.** `platform/` is a distribution root, not an import package — **never create `platform/__init__.py`**. `platform` is a stdlib module name, and shadowing it produces `AttributeError`s from inside third-party libraries at import time. The client and simulator are separate distributions so the reference client installs on a Pi without `fastapi` or `psycopg`, which enforces "the station client knows nothing about the database" at install time rather than at review time.

---

## Stack

- **Python 3.11+** for the platform. Type hints throughout. `ruff` and `mypy` in CI.
- **PostgreSQL + TimescaleDB** — observations are time-series. Runs on the Pi's NVMe.
- **FastAPI** for both the public API and MSP endpoints.
- **Prometheus + Grafana** for metrics. The platform exposes `/metrics`.
- **Docker Compose** — `docker compose up` must bring up the entire platform on a clean machine in under ten minutes. This is a hard requirement, not an aspiration.
- **C++ (Arduino)** for the rotator controller on an Arduino Uno R4 WiFi (Renesas RA4M1 — *not* AVR; AVR-targeted libraries will not port unchanged).
- **MicroPython or Arduino C++** for the microcontroller station.

---

## Domain notes that will save you time

- **Coordinate frames are where silent bugs live.** TEME (what SGP4 outputs) is not ECEF is not topocentric. Convert deliberately and write tests with known ground-truth passes.
- **A pass is 8–15 minutes and never repeats.** Missed work is permanently lost. This is why the reliability model is unusual.
- **Element sets have an epoch and decay in accuracy with age.** Element-set age is a first-class feature everywhere.
- **Primary reception target at 137 MHz is Meteor-M LRPT** (digital). The older analogue APT service is no longer operating — ignore pre-2025 tutorials on this point.
- **Elevation is the strongest single predictor.** It is a feature in our model, not a competing baseline to be discarded.

---

## Evaluation — read `docs/EVALUATION.md` before writing any model code

Key points that constrain implementation:

- Four model configurations are evaluated and reported: **A** elevation only, **B** elevation + priority, **C** our features only, **D** all combined. The code must support running any configuration by config flag.
- **Selection bias is the project's main methodological threat.** Archives only contain passes someone chose to observe. Any evaluation code must compute and report a completeness ratio, and must support inverse-propensity weighting.
- Calibration matters more than accuracy. Every model output ships with a reliability diagram and a Brier score against a base-rate predictor.

---

## Current phase

**Phase 1 — Foundations (weeks 1–7).**

In scope now: data model and store, orbit service with pass prediction, MSP draft, station registry, a simulated station registering and appearing on the dashboard.

Not yet: prediction models, scheduler optimisation, reliability layer, hardware. Do not scaffold these beyond empty module stubs with documented interfaces.

**Phase 1 exit criterion:** a virtual station is visible on the public site from outside the college network.

---

## Working style

- Small commits. Tests with every merge. One reviewer minimum.
- Interfaces are defined before implementations, especially at module boundaries.
- If a piece of work would take more than two days without producing something demonstrable, break it down.
- When uncertain whether to build or use a library, apply the test: *does writing this ourselves produce a result or teach us something?* If neither, use the library and note it.
