"""``report.md``: the header and every section, rendered from the results files.

The report is a function of the parsed ``*.jsonl`` files and nothing else — not
the objects that made them — so every number it prints is in a hashed file, and
``verify`` can check it by rendering those files again. The code version and
the runtimes are not in it: they are in the manifest's unhashed
``environment`` block, and the header says so (D-235).

Reference: docs/DECISIONS.md D-235, D-236.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from meridian.reports.markdown import cell, table
from meridian.reports.render_data import render_data
from meridian.reports.render_prediction import render_prediction
from meridian.reports.render_scheduling import render_scheduling

__all__ = ["render_report"]

Row = Mapping[str, object]


def render_report(
    *,
    run: Sequence[Row],
    data: Sequence[Row],
    prediction: Sequence[Row],
    scheduling: Sequence[Row],
) -> bytes:
    """The whole report, as the bytes written to ``report.md``.

    Args:
        run: ``run.jsonl``, parsed: the run record.
        data: ``data.jsonl``, parsed: the data section.
        prediction: ``prediction.jsonl``, parsed: the prediction section.
        scheduling: ``scheduling.jsonl``, parsed: the scheduling section.
    """
    lines = [
        *_header(run),
        *render_data(data),
        *render_prediction(prediction),
        *render_scheduling(scheduling),
    ]
    return ("\n".join(lines).rstrip("\n") + "\n").encode("utf-8")


def _header(run: Sequence[Row]) -> list[str]:
    record = next(one for one in run if one["row"] == "run")
    seeds = [one for one in run if one["row"] == "seed"]
    seed_lines = (
        table(
            ("Component", "Seed"),
            [(cell(one["component"]), cell(one["seed"])) for one in seeds],
        )
        if seeds
        else ["No section of this report draws a random number."]
    )
    return [
        "# Meridian evaluation report",
        "",
        "Every number below is regenerated from the snapshot, the configuration"
        " and the seed named here: `meridian report verify <this directory>`.",
        "",
        *table(
            ("Run", "Value"),
            [
                ("method", cell(record["method"])),
                ("raw snapshot", f"`{record['snapshot_sha256']}`"),
                ("configuration", f"`{record['config_sha256']}` (`config.toml`)"),
                ("master seed", cell(record["seed"])),
            ],
        ),
        "",
        "**Derived seeds**",
        "",
        *seed_lines,
        "",
        "The code version, dependency versions and runtimes are in"
        " `manifest.json` under `environment`: recorded, and not part of this"
        " report's hash (D-235).",
        "",
    ]
