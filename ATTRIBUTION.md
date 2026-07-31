# Attribution

Every idea, algorithm or approach in this repository that was learned by reading another project's source or documentation is recorded here, in the same commit as the work it describes.

## Why this file exists

Three separate reasons, all of them serious:

1. **Licence.** The open ground station projects we learn from are licensed under GPL-3.0 and AGPL-3.0. Copying their source into this repository would impose those terms on us and require preserving their notices. We do not copy source. This file records that we read and reimplemented.
2. **Academic integrity.** Unacknowledged reuse in a submitted project is plagiarism, and consequences for it are more severe than for any technical shortfall.
3. **Defence.** In a viva, "did you copy this?" is a fair question. This file turns an accusation into a demonstration of professional practice. Its absence is what looks bad.

## Rules

- One entry per instance, added in the **same commit** as the work — never retrospectively.
- Record: date, what was read, its licence, what we wrote, and an explicit statement about whether any code was copied.
- Reading source to understand an approach and then writing our own implementation is normal engineering and is what this file documents.
- If code *is* ever copied, it must be recorded here, the licence obligations met, and the team lead informed before the commit lands.

## Format

```
YYYY-MM-DD  <topic>
            Read: <project / file / doc> (<licence>)
            Wrote: <what we implemented, and how it differs>
            Copied: none
```

## Dependencies

Libraries used as dependencies rather than reimplemented. Not attribution in the same sense, but recorded for completeness.

| Library | Licence | Used for |
|---|---|---|
| `sgp4` | MIT | Orbit propagation |
| `skyfield` | MIT | Coordinate frames, look angles |
| Hamlib | LGPL-2.1 | Rotator control protocol |
| GNU Radio | GPL-3.0 | Demodulation (invoked as a separate process) |
| SatDump | GPL-3.0 | Decoding (invoked as a separate process) |
| FastAPI, SQLAlchemy, scikit-learn, NumPy, SciPy | MIT / BSD | Platform and modelling |
| Prometheus, Grafana | Apache-2.0 / AGPL-3.0 | Metrics and dashboards |

**Note on process boundaries.** GPL-licensed decoders are invoked as separate processes over defined interfaces, not linked into our code. This keeps our licensing decision independent.

**Licence decision, 2026-07-31.** Apache-2.0, recorded in `docs/DECISIONS.md`. Chosen deliberately, not inherited: permissive terms plus an explicit patent grant suit a published protocol and a reference client intended for third-party implementation. The process boundary above is what makes this available to us, and it must be maintained — linking a GPL decoder into our code would change the answer.

## Log

```
(entries begin here)
```
