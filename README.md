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
| `CLAUDE.md` | Context for AI coding assistants |
| `ATTRIBUTION.md` | Log of ideas read from other projects |

---

## Quick start

```bash
git clone <repo> && cd meridian
cp deploy/.env.example .env
docker compose up
```

Platform on `:8000`, dashboard on `:3000`, Grafana on `:3001`.

Bringing the whole platform up on a clean machine in under ten minutes is a hard requirement, not an aspiration. If it takes longer, that is a bug.

---

## Running a simulated station

```bash
python -m simulator.station --count 1 --seed 4471
```

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
