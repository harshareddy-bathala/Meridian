"""Reproducible evaluation reports: each figure from a snapshot, a config and a seed.

Stage 22 of the roadmap. ``meridian report build`` reads a raw snapshot and one
experiment configuration, derives every component's seed from one master seed,
and publishes a sealed **run directory**:

* ``report.md`` — the report a reader opens, rendered only from the results
  files beside it;
* one ``*.jsonl`` results file per section, canonical and hashed;
* ``config.toml`` — the configuration exactly as it was given, so regenerating
  a run needs the snapshot and nothing else;
* ``manifest.json`` — every file's digest, the inputs' hashes, the master and
  derived seeds, and an ``environment`` block (commit, dependency versions,
  runtimes) that is recorded and **not hashed** (D-235).

``meridian report verify`` rebuilds a run from what it recorded and compares
hashes, which is the stage's completion gate as a command.

The sections arrive one at a time:

* **data** — provenance, the labels and their exclusions, completeness and
  weighting, and the silences §5 of ``EVALUATION.md`` counts apart;
* **prediction** — A, C, D and D∖conditions fitted, published and judged,
  with station-day bootstrap intervals, the comparisons between them, SC-2,
  and a reliability diagram per model (D-237);
* **scheduling** — seven schedulers replayed on those models over the test
  span, SC-1 as D − B with D − greedy B beside it, oracle regret, and every
  schedule checked; runtimes measured into the environment (D-238);
* **orbit uncertainty** — timing error from the snapshot, clock-corrected,
  against element-set age by regime, 1σ coverage for SC-3, the exclusions
  counted, and §6.3's spread test (D-239).

**Nothing here opens a database or a socket.** The snapshot is the only input
that holds data, and a report command never fetches anything (rule 8, and the
roadmap's "no report command may silently fetch mutable external data").

Reference: docs/DECISIONS.md D-234 to D-239.
"""
