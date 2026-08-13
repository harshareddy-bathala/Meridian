<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="site/brand/meridian-banner-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="site/brand/meridian-banner-light.svg">
    <img src="site/brand/meridian-banner-light.svg" width="100%"
         alt="Meridian — predictive scheduling and reliability for satellite ground stations">
  </picture>
</p>

Predictive scheduling and reliability for satellite ground stations, and an open protocol for stations to join a network.

Satellites in low Earth orbit are visible for eight to fifteen minutes at a time, a few times a day. A pass that is missed is lost permanently — there is no retry. Yet the decision of which pass to receive is almost universally made on geometry alone: whichever satellite goes highest.

Meridian predicts which reception opportunities are worth taking, schedules them, monitors the stations doing the work, and reports measured reliability. It also measures something nobody publishes: how wrong public orbital data actually is, and what that costs.

**The software is the contribution. The station is the proof.**

---

## Status

**Phase 1 — Foundations.** Data model, orbit service, MSP draft, station registry, first simulated station.

Done when a virtual station is visible on the public site from outside the college network.

Not yet started: prediction models, scheduler, reliability layer, hardware.

[**meridian.org.in**](https://meridian.org.in) — the static public site, in `site/`: the front page plus the architecture, protocol, documentation and about pages. It is deliberately not the dashboard: the exit criterion above is met by the live dashboard on `dash.meridian.org.in`, tunnelled from the station, which is a separate surface with a separate uptime story. See D-036, D-037 and D-038 for the two-theme system and the move to five pages, and D-039 and D-040 for the contrast pass and the document rail.

Contact: [hello@meridian.org.in](mailto:hello@meridian.org.in), or [issues](https://github.com/harshareddy-bathala/Meridian/issues) and [discussions](https://github.com/harshareddy-bathala/Meridian/discussions) for anything technical.

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
| `docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md` | The staged build order, and what each stage's completion gate is |
| `CLAUDE.md` | Context for AI coding assistants |
| `ATTRIBUTION.md` | Log of ideas read from other projects |
| `site/` | The static site at `meridian.org.in`. No build step — the directory is what gets served. |
| `site/brand/` | Logo exports for marketing. Generated; see `site/brand/README.md`. |

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

CI measures it on every pull request rather than taking the figure on trust — a cold `docker compose up --build` on a clean runner, timed from before the build to the first healthy `/healthz`, failing over 600 seconds.

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
| `uv run pytest -m msp_conformance` | nothing — it drives the app in-process | yes, from `GET /msp/v0/time` |
| `uv run pytest -m e2e` | the full compose stack | **not yet** — Stage 10 |

`e2e/` exists with its marker wired and no tests in it, so that command currently selects nothing and pytest exits `5`. That is the expected state until there is a stack to drive — the directory is there first so the marker wiring is settled before anyone writes an end-to-end test, rather than being invented alongside one. CI runs the marker anyway, tolerating exit `5` and nothing else, so the first end-to-end test to land executes rather than sitting collected-but-never-run.

Conformance tests assert the **bytes** `docs/MSP-SPEC.md` promises — field names, exact error bodies, status codes, version handling — rather than that an operation works. They are what a third-party station implementation would be tested against, which is why they spell out expected bodies in full instead of computing them from the code under test.

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

A station needs three things before it can do anything: a catalogue to be
scheduled against, an invite to join with, and somewhere to keep its identity.

```bash
# What this deployment tracks. Idempotent — running it twice writes nothing.
uv run meridian catalogue load --file deploy/catalogue/development.json

# One invite per station. Tokens go to stdout, one per line.
uv run meridian invite create --label sim --count 1 > invites.txt

# Passes can only be generated for stations that already exist, so this comes
# after the first run registers them.
uv run python -m meridian_sim.station --count 1 --seed 4471 \
    --invites invites.txt --state-dir ./sim-state
```

Or let compose do all of it: `docker compose -f deploy/docker-compose.yml
--profile sim up` brings up the seeding, the fleet and a loop that keeps
generating passes for it.

Simulated stations speak real MSP to the real platform, through the same client
a physical station runs. They are indistinguishable from physical stations at
the protocol level — and labelled as simulated in every record, response and
display.

Everything about a run is derived from its seed: the same seed produces the same
stations in the same places hearing the same things, and `--scenario` injects
faults that are as reproducible as a clean run. What that claim does and does not
cover is `docs/DECISIONS.md` D-077.

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
