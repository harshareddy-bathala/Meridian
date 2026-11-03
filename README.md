<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="site/brand/meridian-banner-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="site/brand/meridian-banner-light.svg">
    <img src="site/brand/meridian-banner-light.svg" width="100%"
         alt="Meridian — predictive scheduling and reliability for satellite ground stations">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/harshareddy-bathala/Meridian/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/harshareddy-bathala/Meridian/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <a href="LICENSE"><img alt="Licence: Apache-2.0" src="https://img.shields.io/badge/licence-Apache--2.0-blue"></a>
  <a href="https://meridian.org.in"><img alt="Site: meridian.org.in" src="https://img.shields.io/badge/site-meridian.org.in-14161b"></a>
</p>

**Satellites pass overhead for a few minutes at a time, and a missed pass never comes back. Meridian decides which passes are worth catching, catches them, checks the result, and tells you what happened.**

It is open software for satellite ground stations, and **MSP**, an open protocol any receiving station can implement to join the network. One physical station is being built by the team as the network's first member. The software is the contribution; the station is the proof.

Meridian began as a final-year engineering project, built by a team of three, and is written to outlast it.

[**meridian.org.in**](https://meridian.org.in) explains it in plain words · [**the live dashboard**](https://dash.meridian.org.in) shows it running

---

## What you can do with it

| If you want to… | Start here |
|---|---|
| get satellite data or images | [Data and images](https://meridian.org.in/data/): weather images, the result of every pass, and an open dataset |
| watch an area over time | [Watch an area](https://meridian.org.in/areas/): fires, crops, night lights and cloud over a place you care about |
| run a station, or build one | [Join with a station](https://meridian.org.in/stations/), then [the protocol](docs/MSP-SPEC.md) |
| run the platform yourself | [Quick start](#quick-start), below |
| contribute code | [CONTRIBUTING.md](CONTRIBUTING.md) |
| support the project | [Support](https://meridian.org.in/support/) |

## What happens to one pass

1. **Predict.** Each upcoming pass is scored by how likely it is to return usable data, not only by how high it climbs. Elevation is one feature among several, with element-set age, station history and the station's own learned horizon.
2. **Choose.** One schedule for every station at once, solved as a constrained assignment problem, and reported against baselines that do the obvious thing.
3. **Receive.** The station records, and its heartbeat proves it was listening: right target, right frequency, right time. **Absence is not a miss** unless the heartbeat says it was listening.
4. **Check.** Every reception gets a **calibrated** confidence that its data is usable, tested against what happened with a reliability diagram and a Brier score.
5. **Explain.** Every loss gets a cause (obstruction, interference, timing, receiver, satellite silent) or *undetermined* when the evidence does not support one.
6. **Warn.** A station drifting from its own baseline raises a health warning before it fails.
7. **Tell and share.** The owner gets a plain report, and every published figure can be regenerated from a sealed dataset, a config file and a seed.

The site's [How it works](https://meridian.org.in/how-it-works/) page walks through the same seven steps with pictures.

## Where it stands

- **The software is built and tested.** Of the 42 conditions in [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md) that define it as complete, 41 are proven on every change. The last is a 72-hour run on the station's own computer.
- **A practice fleet of 50 simulated stations** runs the whole pipeline, faults included, for the demonstration. Everything it produces is labelled simulated, at every layer.
- **Station 001 is being built.** Until it has received, no measured result is published, because there is none.
- **The protocol is frozen at MSP 0.1.**

[`docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md`](docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md) says which stage comes next, and [`CHANGELOG.md`](CHANGELOG.md) what each release contains.

---

## Quick start

```bash
git clone https://github.com/harshareddy-bathala/Meridian.git && cd Meridian
cp deploy/.env.example deploy/.env
docker compose -f deploy/docker-compose.yml up -d --build
```

Then open `http://localhost:8080/` for the dashboard, or `curl http://localhost:8000/healthz` for the API.

The env file goes in `deploy/`, beside the compose file: compose reads it from there whatever directory the command runs in (D-114). On a Pi, `docker compose -f deploy/docker-compose.yml pull` fetches the prebuilt image instead of building.

The default profile runs the database, migrations, the API on `:8000`, the jobs process that generates passes and schedules them every five minutes, and the front on `:8080`, which serves the dashboard's last good copy when the API is down (D-282). The database is deliberately not published.

**Bringing the whole platform up on a clean machine in under ten minutes is a hard requirement.** CI measures it on every pull request: a cold `docker compose --profile metrics up --build` on a clean runner, timed until every service reports healthy, failing over 600 seconds. On a laptop it takes about five minutes cold and twenty seconds warm.

**Optional profiles:**

| Add | For |
|---|---|
| `--profile sim` | one simulated station |
| `-f deploy/docker-compose.demo.yml --profile demo` | the 50-station demonstration fleet (D-330); see `docs/OPERATIONS.md` § The demonstration |
| `--profile metrics` | Prometheus, Alertmanager and Grafana on `:3001`; copy `deploy/prometheus/metrics_token.example` to `metrics_token` first |
| `-f deploy/docker-compose.public.yml` | the Cloudflare tunnel; `docs/OPERATIONS.md` § Going public |

## Development

```bash
uv sync --dev
uv run ruff check . && uv run ruff format --check . && uv lock --check
uv run mypy platform/src client/src simulator/src
uv run pytest -m "not integration and not e2e and not msp_conformance"
```

The dashboard needs Node 24. `npm run dev` proxies `/api` and `/healthz` to a platform on `:8000`, so development is same-origin just as production is:

```bash
cd dashboard && npm ci
npm run lint && npm run typecheck && npm run build
npm run dev
```

Tests are organised by what they need, one marker each:

| Command | Needs |
|---|---|
| `uv run pytest -m "not integration and not e2e and not msp_conformance"` | nothing |
| `uv run pytest -m integration` | TimescaleDB, never SQLite |
| `uv run pytest -m msp_conformance` | nothing; it drives the app in-process |
| `uv run pytest -m e2e` | TimescaleDB |

Conformance tests assert the **bytes** `docs/MSP-SPEC.md` promises, field names and exact error bodies included. They are what a third-party station implementation would be tested against.

```bash
docker run -d --name meridian-test -p 5433:5432 \
    -e POSTGRES_USER=meridian -e POSTGRES_PASSWORD=meridian \
    -e POSTGRES_DB=meridian_test timescale/timescaledb:2.29.0-pg16

# One DATABASE_URL serves both Alembic and psycopg (D-033).
export DATABASE_URL=postgresql://meridian:meridian@localhost:5433/meridian_test
uv run alembic -c deploy/alembic.ini upgrade head
uv run pytest -m integration
```

[CONTRIBUTING.md](CONTRIBUTING.md) has the rest: how a change is shaped, the decision log, and the rules nothing may break.

## Running a simulated station

```bash
# What this deployment tracks. Idempotent: running it twice writes nothing.
uv run meridian catalogue load --file deploy/catalogue/development.json

# One invite per station. Tokens go to stdout, one per line.
uv run meridian invite create --label sim --count 1 > invites.txt

uv run python -m meridian_sim.station --count 1 --seed 4471 \
    --invites invites.txt --state-dir ./sim-state
```

Or `docker compose -f deploy/docker-compose.yml --profile sim up`, which does all of it. Simulated stations speak real MSP to the real platform, through the same client a physical station runs, and are labelled as simulated in every record, response and display. Everything about a run is derived from its seed, and `--scenario` injects faults as reproducibly as a clean run (D-077).

---

## Repository map

| Path | Contents |
|---|---|
| `platform/` | The platform: orbit service, prediction, scheduler, registry, observation store, reliability, public API and MSP endpoints |
| `client/` | The reference station client, which installs on a Pi without the database or web server |
| `simulator/` | Virtual stations that speak MSP |
| `ingest/` | External archive adapters, which nothing at runtime imports (D-138) |
| `dashboard/` | The live dashboard: TypeScript, Vite and React, served from the platform's own origin |
| `site/` | [meridian.org.in](https://meridian.org.in). No build step: the directory is what gets served |
| `deploy/` | Compose, migrations, the front, Prometheus rules, Grafana, backup and drill tools |
| `docs/MSP-SPEC.md` | The protocol. Read it first: every module depends on it |
| `docs/ARCHITECTURE.md` | Module boundaries and data flow |
| `docs/EVALUATION.md` | Methodology. Read it before writing any model code |
| `docs/DECISIONS.md` | Every decision taken during implementation, what was rejected and why |
| `docs/OPERATIONS.md` | The runbook: bring-up, commands, backup and restore, what to do when each alert fires |
| `docs/THREAT-MODEL.md` | What is protected, from whom, and what answers each threat |
| `docs/` | Also the data model, glossary, Git workflow, acceptance list and the full project document |

---

## Principles

- **Software first.** The platform is proven before hardware is bought.
- **Open by default.** MSP is published, and the reference client is released.
- **Independent.** If every external service went offline tomorrow, Meridian would still schedule, receive, decode, monitor and report with its own stations.
- **Measurable.** Every claim carries a number, produced by a stated method and regenerable from a seed and a config file.
- **Honest about simulation.** Simulated data is labelled as simulated everywhere, and never mixed with measured results.
- **Receive only.** The station never transmits.

## Support this project

Meridian is free and open, and like any organisation it has running costs: the platform and its link online, the data services and keys it uses, the domain, and upkeep. Support keeps those running. Anything beyond them goes to a tracking antenna, so the station can follow each satellite across the sky, then more stations, then new software.

[**meridian.org.in/support**](https://meridian.org.in/support/) has the ways to give. Meridian is not a registered charity, and support is not tax-deductible.

## Contributing

Contributions are welcome, from a typo to a station implementation. Start with [CONTRIBUTING.md](CONTRIBUTING.md), and see the [good first issues](https://github.com/harshareddy-bathala/Meridian/labels/good%20first%20issue). Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Security problems go to [SECURITY.md](SECURITY.md), not to a public issue.

Contact: [hello@meridian.org.in](mailto:hello@meridian.org.in), or [issues](https://github.com/harshareddy-bathala/Meridian/issues) for anything technical.

## Licence

Apache-2.0: permissive, with an explicit patent grant, which suits a published protocol and a reference client meant for others to implement.

GPL-licensed decoders are run as separate processes over defined interfaces, never linked, so no copyleft obligation reaches this repository. Work informed by reading other projects is logged in [`ATTRIBUTION.md`](ATTRIBUTION.md).
