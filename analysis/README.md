# analysis

The configurations Meridian's evaluation reports are built from.

A report is built by the platform's own command, not by a script in this
directory, so the code that produces every figure is tested, typed and reviewed
like the rest of the platform:

```sh
meridian report build \
  --snapshot data/datasets/snapshots/<raw snapshot> \
  --config analysis/configs/evaluation.toml.example \
  --seed 4471
meridian report verify data/datasets/reports/<run>
```

`build` publishes a sealed run directory:
- `report.md`, the report a reader opens;
- one `*.jsonl` results file per section;
- `config.toml`, the configuration exactly as it was given;
- `manifest.json`, which names the snapshot, the configuration, the master seed and every seed derived from it.

`verify` rebuilds the run from what it recorded and exits 0 only if the hash is the same. `docs/OPERATIONS.md` § Evaluation reports is the runbook.

## What is here

- `configs/` holds experiment configurations, one table per report section. A configuration that produced a reported figure is committed here, so the figure can be regenerated from a snapshot, this file and a seed (`EVALUATION.md` §9).

The roadmap sketches more directories here: snapshots, features, models, scheduler, reliability, reports and tests. They are not created:
- snapshots, models and runs are content-addressed directories under the datasets root, which is outside the repository;
- the code lives in `platform/src/meridian/reports/`;
- the tests live in `tests/`.

An empty directory would read as work that stalled (D-234).

Notebooks may explore a snapshot, but a figure that goes in the report comes from `meridian report build`.
