# Meridian

A control platform for satellite ground stations, and an open protocol for stations to join a network.

Satellites in low Earth orbit are visible for eight to fifteen minutes at a time, a few times a day. A pass that is missed is lost permanently — there is no retry. Yet the decision of which pass to receive is almost universally made on geometry alone: whichever satellite goes highest.

Meridian predicts which reception opportunities are worth taking, schedules them, monitors the stations doing the work, and reports measured reliability. It also measures something nobody publishes: how wrong public orbital data actually is, and what that costs.

**The software is the contribution. The station is the proof.**

---

## Status

**Phase 1 — Foundations.** Data model, orbit service, MSP draft, station registry, first simulated station.

Done when a virtual station is visible on the public site from outside the college network.

Not yet started: prediction models, scheduler, reliability layer, hardware.

[**meridian.org.in**](https://meridian.org.in) — a static page describing the project, in `site/`. It is deliberately not the dashboard: the exit criterion above is met by the live dashboard on `dash.meridian.org.in`, tunnelled from the station, which is a separate surface with a separate uptime story. See D-036.

---

## What's here

| Path | Contents |
|---|---|
| `docs/MSP-SPEC.md` | The protocol. Read this first — every module depends on it. |
| `docs/ARCHITECTURE.md` | Module boundaries and data flow |
| `docs/DATA-MODEL.md` | Schema |
| `docs/EVALUATION.md` | Methodology. Read before writing any model code. |
| `docs/DECISIONS.md` | Decisions taken during implementation, and why |
| `docs/GLOSSARY.md` | Domain terms |
| `docs/PROJECT.md` | The full project document — problem, method, phases, budget |
| `docs/GIT-WORKFLOW.md` | Commit, branch and review rules |
| `CLAUDE.md` | Context for AI coding assistants |
| `ATTRIBUTION.md` | Log of ideas read from other projects |
| `site/` | The static page at `meridian.org.in`. No build step — the directory is what gets served. |

---

## Quick start

```bash
git clone <repo> && cd meridian
cp deploy/.env.example .env
docker compose -f deploy/docker-compose.yml up -d --build
curl http://localhost:8000/healthz
```

Platform on `:8000`. The database is deliberately not published — nothing outside the compose network needs it.

Optional profiles: `--profile metrics` for Prometheus and Grafana on `:3001`, `--profile sim` for a simulated station, `--profile public` for the tunnel.

Bringing the whole platform up on a clean machine in under ten minutes is a hard requirement, not an aspiration. If it takes longer, that is a bug. Measured: about five minutes cold on a laptop including image pulls and the image build, twenty seconds warm. On a Pi, pull a prebuilt image rather than building on the device.

## Development

```bash
uv sync --dev
uv run ruff check . && uv run ruff format --check . && uv lock --check
uv run mypy platform/src client/src simulator/src
uv run pytest -m "not integration and not e2e and not msp_conformance"
```

Tests are organised by what they need to run, one directory per marker:

| Command | Needs | Populated |
|---|---|---|
| `uv run pytest -m "not integration and not e2e and not msp_conformance"` | nothing | yes |
| `uv run pytest -m integration` | TimescaleDB | yes |
| `uv run pytest -m msp_conformance` | the API | **not yet** — Stage 8 |
| `uv run pytest -m e2e` | the full compose stack | **not yet** — Stage 10 |

The last two directories exist with their markers wired and no tests in them, so those two commands currently select nothing and pytest exits `5`. That is the expected state until the endpoints they exercise are built — the directories are there first so the marker wiring is settled before anyone writes a conformance test, rather than being invented alongside one.

Integration tests need a real TimescaleDB — never SQLite. The schema uses hypertables, arrays, `CHECK` constraints and generated columns, so a suite that passes on SQLite says nothing about what runs on the Pi.

```bash
docker run -d --name meridian-test -p 5433:5432 \
    -e POSTGRES_USER=meridian -e POSTGRES_PASSWORD=meridian \
    -e POSTGRES_DB=meridian_test timescale/timescaledb:2.29.0-pg16

# One DATABASE_URL serves both Alembic and psycopg — meridian.config adds and
# strips the +psycopg driver suffix as each needs it. See docs/DECISIONS.md D-033.
export DATABASE_URL=postgresql://meridian:meridian@localhost:5433/meridian_test
uv run alembic -c deploy/alembic.ini upgrade head
uv run pytest -m integration
```

---

## Running a simulated station

```bash
uv run python -m meridian_sim.station --count 1 --seed 4471
```

*(A shell today — it parses its arguments and reports which stage implements it. See `docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md` Stage 10.)*

Simulated stations speak real MSP to the real platform. They are indistinguishable from physical stations at the protocol level — and labelled as simulated in every record, response and display.

---

## Principles

**Software first.** The platform is proven before hardware is purchased.

**Open by default.** MSP is published; the reference client is released.

**Independent.** If every external service disappeared tomorrow, Meridian would still schedule, receive, decode, monitor and report.

**Measurable.** Every claim carries a number, produced by a stated method, reproducible from a seed and a config file.

**Receive only.** The station never transmits.

---

## Licence

Apache-2.0. Permissive, with an explicit patent grant — the right fit for a published protocol and a reference client we want other people to implement.

GPL-licensed decoders are invoked as separate processes over defined interfaces, never linked, so no copyleft obligation propagates into this repository. See `ATTRIBUTION.md`.
