# Changelog

Every release, newest first. Dates are UTC. The project is pre-1.0: MSP is frozen at 0.1, and everything else may still change between releases, with the reason recorded in [`docs/DECISIONS.md`](docs/DECISIONS.md).

## 0.1.0

The first release: the platform's software, complete and tested, before the first physical station receives.

### What it does

- **Predicts** which passes are worth receiving, with a yield model that runs configurations A to D by flag, reports a completeness ratio and supports inverse-propensity weighting.
- **Schedules** every station at once with a constrained optimiser, against two baselines and an oracle, and records why each decision was made.
- **Receives** through MSP 0.1 and the reference station client, and counts a pass as missed only when the station's heartbeat confirms it was listening.
- **Judges** every reception with a calibrated confidence and every loss with a cause, or *undetermined*.
- **Watches** each station's health against its own baseline, and sends owner reports.
- **Publishes** a read-only API, a live dashboard, a sealed evidence dataset and regenerable evaluation reports.
- **Keeps running offline:** scheduling and reception continue with every external service unreachable, and the dashboard serves its last good copy when the API is down.
- **Simulates** up to fifty stations speaking real MSP, with injectable faults, and labels every simulated record as simulated.

### Measured

- 41 of the 42 acceptance conditions in [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md) proven on every change. The 72-hour run on the station's own computer is the one pending.
- A cold `docker compose up` on a clean machine is held under ten minutes by CI on every pull request.

### Not yet

- No measured reception: station 001 is being built. Every figure in this release comes from the simulator, and says so.
