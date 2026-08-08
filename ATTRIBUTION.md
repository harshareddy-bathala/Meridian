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
| IBM Plex Sans, IBM Plex Mono | SIL OFL-1.1 | Typography on the public site |

**Note on the fonts.** IBM Plex is the one dependency **vendored into this repository** rather than installed: three latin-subset `.woff2` files in `site/fonts/`, unmodified, taken from the `@ibm/plex-sans` and `@ibm/plex-mono` distributions on 2026-08-04. The site self-hosts them because it makes no third-party requests at runtime — a property its `Content-Security-Policy` enforces rather than asserts, and one a font CDN would break. OFL-1.1 permits redistribution provided the licence travels with the files and the Reserved Font Name is not applied to modified versions; `site/fonts/OFL.txt` ships alongside them and the files are bit-identical to upstream. No font was renamed, subsetted further or otherwise altered by us.

**Note on process boundaries.** GPL-licensed decoders are invoked as separate processes over defined interfaces, not linked into our code. This keeps our licensing decision independent.

**Licence decision, 2026-07-31.** Apache-2.0, recorded in `docs/DECISIONS.md`. Chosen deliberately, not inherited: permissive terms plus an explicit patent grant suit a published protocol and a reference client intended for third-party implementation. The process boundary above is what makes this available to us, and it must be maintained — linking a GPL decoder into our code would change the answer.

## Log

**2026-08-08 — a note on how this log starts.** The four entries below were added
in one commit, retrospectively, which breaks the first rule above. They are
recorded rather than quietly skipped because the alternative is worse: an empty
log sitting beside `platform/src/meridian/store/pool.py`, whose module docstring
names the page it was written from. The lapse is the honest thing to defend — the
rule was written in Stage 0 and not carried into the habit of committing, and it
was an audit rather than review that noticed. From this entry forward the rule
binds normally: same commit, no exceptions. Nothing in the four was copied; each
was read, understood and written independently, which is exactly the case this
file exists to document.

```
2026-08-01  Foreign keys and unique indexes against a TimescaleDB hypertable
            Read: TimescaleDB 2.29 documentation and behaviour, verified by
                  running it (Apache-2.0 / Timescale License, mixed)
            Wrote: docs/DECISIONS.md D-015's correction note, and
                  docs/DATA-MODEL.md's natural-key rule. An earlier draft
                  rejected a supersedes_id pointer on the grounds that nothing
                  can hold a foreign key onto a hypertable. That was true
                  before 2.11 and is false on 2.29, which both creates and
                  enforces such a key. The decision did not change; the stated
                  reasoning did, from a false technical claim to the real
                  modelling argument. The companion finding — that a unique
                  index still requires the partitioning column, error quoted
                  verbatim in DATA-MODEL.md — is what forced the natural keys.
            Copied: none

2026-08-06  psycopg 3 connection pooling
            Read: psycopg 3 documentation, "Connection pools"
                  https://www.psycopg.org/psycopg3/docs/advanced/pool.html
                  (LGPL-3.0 project; documentation read, no source copied)
            Wrote: platform/src/meridian/store/pool.py — one pool opened in the
                  FastAPI lifespan, with min/max sizes derived from D-030's poll
                  interval and fifty stations rather than taken from any example,
                  and a `configure` hook setting the session time zone to UTC,
                  which the documentation does not suggest and DATA-MODEL.md
                  requires. The module docstring carries this citation inline.
            Copied: none

2026-08-06  Exponential backoff with full jitter
            Read: AWS Architecture Blog, "Exponential Backoff And Jitter"
                  (article, no licence attached to the prose; no code taken)
            Wrote: client/src/meridian_client/transport.py RetryPolicy —
                  `random.uniform(0, min(cap, base * 2**(n-1)))`. Full jitter
                  rather than backoff-plus-a-small-random-term, chosen because
                  it is the variant that decorrelates a fleet, which is the
                  property that matters at Phase 3's fifty simulated stations
                  and not the property the article was optimising for.
            Copied: none
```
