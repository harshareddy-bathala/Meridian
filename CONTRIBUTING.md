# Contributing to Meridian

Thank you for being here. Meridian is built to be picked up by people who were not in the room, so everything you need should be written down. If something is not, that is a bug worth reporting too.

## Ways to help

- **Report a problem.** [Open an issue](https://github.com/harshareddy-bathala/Meridian/issues/new/choose), with what you ran, what you expected and what happened.
- **Build a station.** Implement [MSP](docs/MSP-SPEC.md) on your own hardware and tell us where the specification was unclear. A protocol that a stranger has never implemented has not really been reviewed.
- **Pick up a [good first issue](https://github.com/harshareddy-bathala/Meridian/labels/good%20first%20issue).** Each one names the files involved and says when it is done.
- **Improve the documents.** A sentence that confused you will confuse the next person.

Security problems go to [SECURITY.md](SECURITY.md), never to a public issue.

## Getting set up

You need [uv](https://docs.astral.sh/uv/), Docker, and Node 24 if you are touching the dashboard.

```bash
git clone https://github.com/harshareddy-bathala/Meridian.git && cd Meridian
uv sync --dev
cp deploy/.env.example deploy/.env
docker compose -f deploy/docker-compose.yml up -d --build    # the whole platform, under ten minutes
```

Then, before every pull request:

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy platform/src client/src simulator/src
uv run pytest -m "not integration and not e2e and not msp_conformance"
```

The [README](README.md#development) shows how to run the integration suite against a real TimescaleDB, and the dashboard's lint, type check and build. The site has its own checks: `python site/tools/shell.py --check`, `python site/tools/stamp_assets.py --check` and `python site/tools/verify_site.py` (see [`site/README.md`](site/README.md)).

## How a change is shaped

[`docs/GIT-WORKFLOW.md`](docs/GIT-WORKFLOW.md) is the full rulebook. In short:

- **One branch and one pull request per piece of work**, cut from `main`.
- **Small commits, one logical change each.** CI checks every subject: `type(scope): lowercase summary`, 60 characters or fewer after the prefix.
  - Types: `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `chore`, `ci`, `spec`.
  - Scopes: the module you touched, such as `scheduler`, `api`, `client`, `simulator`, `dashboard`, `site`, `deploy` or `ingest`. `docs` is for documents and `repo` is for the repository itself. The full list is in `.github/workflows/ci.yml` › `conventions`.
- **Tests come with the change.** A fix arrives with the test that would have caught it.
- **A module stays under 400 lines.** CI enforces it on the platform, client and simulator.
- **One review, minimum, before merge.**

## Decisions are written down

When a change makes a choice someone could reasonably have made differently, add an entry to [`docs/DECISIONS.md`](docs/DECISIONS.md): what was decided, why, and what was rejected. Use the next free `D-` number and cite it from the code. Read the entries your change touches before you start; most "why is it like this?" questions are answered there.

## The rules nothing may break

These come from [`CLAUDE.md`](CLAUDE.md), and a pull request that breaks one will not be merged however good the rest is:

1. **Never reimplement orbit propagation.** Use `sgp4` or `skyfield`. The same goes for error-correcting codes, demodulation and rotator wire protocols.
2. **Reading another project's source is logged in [`ATTRIBUTION.md`](ATTRIBUTION.md)**, in the same commit as the work it informed.
3. **No source copied from a GPL or AGPL project.** Read it, understand it, write your own.
4. **The station never transmits.** No transmit code path, ever.
5. **Simulated data is labelled as simulated** at every layer: database, API, dashboard, report and site.
6. **Model evaluation uses temporal splits only.** Never shuffle time-series observations into train and test.
7. **Absence is not a miss.** A station that reported nothing has only missed a pass if its heartbeat confirms it was listening.
8. **Every published number is regenerable** from a dataset snapshot, a config file and a seed.

And one about the platform: **no external service may become a runtime dependency** of scheduling or reception. External data is training input only.

## Module boundaries

The scheduler consumes predictions and never reads the observation store. The prediction module knows nothing about MSP. The station client knows nothing about the database, and installs without it. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) has the full picture.

## Using AI assistants

You may. **Never submit code you cannot explain line by line**: you are responsible for it as if you had typed it. [`CLAUDE.md`](CLAUDE.md) is the context an assistant should read first, and [`docs/GIT-WORKFLOW.md`](docs/GIT-WORKFLOW.md) Rule 10 has the rest.

## Code of Conduct

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## Licence

By contributing, you agree that your contribution is licensed under [Apache-2.0](LICENSE), the licence of the project.
