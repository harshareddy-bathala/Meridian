"""Stage 26's report half: SC-7 regenerated from a snapshot, a configuration and a seed.

The completion gate reads: *every measured reception carries a versioned
verdict, and SC-7's report — Brier score against the base rate, reliability
diagram and segment calibration — regenerates from a snapshot, a configuration
and a seed.* The first clause is the database's and is asserted in
``tests/integration/test_verdict_gate.py``. This is the second, through
``meridian report build`` and ``meridian report verify``, with every database
connection and socket refused.

The world is the fittable one Stage 22's gate uses, with what a verdict reads
added: an SNR rising with elevation, decoder statistics, a frame interval,
products for every decode, and a blind rating of each, usable more often the
higher the pass. A third of the passes report no decoder statistics, so every
route is fitted.

**Each claim has its positive control:** another seed moves SC-7's interval
and leaves every fitted figure alone, and a report without split dates says
*not measured* instead of a number.

Reference: docs/DECISIONS.md D-235, D-236, D-260 to D-264.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from meridian.cli import main
from meridian.datasets.publish import read_directory
from meridian.reports.render import render_report
from meridian.reports.render_verdict import verdict_figures

pytestmark = pytest.mark.usefixtures("no_network")
"""Every database connection and socket refused, as Stage 22's gate does."""

SETTINGS = """
[prediction]
min_station_history = 5
folds = 2
train_until = 2026-09-11T00:00:00Z
validate_until = 2026-09-16T00:00:00Z
resamples = 200

[scheduling]
time_limit_s = 5.0
resamples = 200

[orbit]
resamples = 200
"""

VERDICT = """
[verdict]
train_until = 2026-09-11T00:00:00Z
validate_until = 2026-09-16T00:00:00Z
partial_below = 0.4
resamples = 200
"""

Tables = Mapping[str, Sequence[Mapping[str, object]]]


def rated_world(evaluation_world: Tables) -> dict[str, list[Mapping[str, object]]]:
    """The fittable world, with the evidence a verdict reads and a rating each."""
    rng = random.Random(26)
    world = {name: list(rows) for name, rows in evaluation_world.items()}
    passes = {one["id"]: one for one in world["passes"]}
    assignments = {one["assignment_id"]: one for one in world["assignments"]}
    world["assignments"] = [
        dict(one) | {"centre_freq_hz": 137_900_000, "mode": "lrpt"}
        for one in world["assignments"]
    ]
    world["transmitters"] = [
        dict(one) | {"mode": "lrpt", "frame_interval_s": 0.113778}
        for one in world["transmitters"]
    ]
    observations, products, ratings, listening = [], [], [], []
    for number, one in enumerate(world["observations"]):
        assignment = assignments[str(one["assignment_id"])]
        held = passes[assignment["pass_id"]]
        peak = float(str(held["max_elevation_deg"]))
        decoded = one["outcome"] == "decoded"
        statistics = number % 3 != 0
        snr = round(peak / 6.0 + rng.uniform(-2.0, 2.0), 2)
        observations.append(
            dict(one)
            | {
                "station_id": assignment["station_id"],
                "satellite_id": held["satellite_id"],
                "started_at": held["aos"],
                "signal_detected": True,
                "peak_snr_db": snr,
                "frames_decoded": one["frames_decoded"] if statistics else None,
                "decoder": "satdump" if statistics else None,
                "decoder_version": "1.2.2" if statistics else None,
            }
        )
        listening.append(
            {"assignment_id": one["assignment_id"], "listening_confirmed": True}
        )
        if not decoded:
            continue
        products.append({"assignment_id": one["assignment_id"], "revision": 1})
        usable = rng.random() < 1.0 / (1.0 + math.exp(-(peak - 45.0) / 8.0))
        ratings.append(
            {
                "id": number + 1,
                "assignment_id": one["assignment_id"],
                "revision": 1,
                "usable": usable,
                "rubric": "usable-1",
                "rated_at": held["los"],
                "simulated": False,
            }
        )
    world |= {
        "observations": observations,
        "products": products,
        "reception_ratings": ratings,
        "listening": listening,
    }
    return world


class Reports:
    """``meridian report`` over one rated world, under one datasets root."""

    def __init__(self, root: Path, snapshot: Path, tmp: Path, capsys: Any) -> None:
        self.root, self.snapshot, self.tmp, self.capsys = root, snapshot, tmp, capsys

    def build(self, seed: int = 4471, *, settings: str = SETTINGS + VERDICT) -> Path:
        config = self.tmp / f"evaluation-{abs(hash(settings))}.toml"
        config.write_text(settings, encoding="utf-8")
        self.capsys.readouterr()
        code = main(
            [
                "report",
                "--root",
                str(self.root),
                "build",
                "--snapshot",
                str(self.snapshot),
                "--config",
                str(config),
                "--seed",
                str(seed),
            ]
        )
        out = self.capsys.readouterr()
        assert code == 0, out.err
        return Path(out.out.splitlines()[0].split(": ", 1)[1].rsplit(" (", 1)[0])

    def verify(self, run: Path) -> int:
        self.capsys.readouterr()
        return main(["report", "--root", str(self.root), "verify", str(run)])


@pytest.fixture
def reports(
    evaluation_world: Tables,
    verdict_snapshot: Callable[[Any], Path],
    datasets_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> Reports:
    snapshot = verdict_snapshot(rated_world(evaluation_world))
    return Reports(datasets_root, snapshot, tmp_path, capsys)


def rows(run: Path, name: str = "verdict.jsonl") -> list[dict[str, Any]]:
    data = read_directory(run).files[name]
    return [json.loads(line) for line in data.splitlines()]


def of(found: Sequence[Mapping[str, Any]], kind: str) -> list[Mapping[str, Any]]:
    return [one for one in found if one["row"] == kind]


def test_sc7_is_measured_and_drawn(reports: Reports) -> None:
    run = reports.build()
    found = rows(run)

    (summary,) = of(found, "verdict")
    assert summary["status"] == "measured"
    assert str(summary["method"]).startswith("verdict-1:")
    (sc7,) = of(found, "sc7")
    assert sc7["status"] == "measured"
    assert sc7["interval"] is not None
    assert {one["subset"] for one in of(found, "score")} == {"every", "rated"}
    assert {one["dimension"] for one in of(found, "segment")} == {
        "station",
        "band",
        "data_type",
        "decoder_version",
        "decoder_statistics",
    }
    files = read_directory(run).files
    assert "verdict_reliability_every.svg" in files
    assert "verdict_reliability_rated.svg" in files
    report = files["report.md"].decode("utf-8")
    assert "## Reception verdict" in report
    assert "### SC-7" in report
    assert "![Reliability diagram, rated receptions only]" in report


def test_each_route_with_evidence_is_judged(reports: Reports) -> None:
    routes = {one["route"] for one in of(rows(reports.build()), "route")}

    assert routes == {"full", "snr"}


def test_the_run_verifies(reports: Reports) -> None:
    assert reports.verify(reports.build()) == 0


def test_built_twice_it_is_the_same_run(reports: Reports) -> None:
    assert reports.build() == reports.build()


def test_the_report_and_figures_regenerate_from_the_results(reports: Reports) -> None:
    run = reports.build()
    files = read_directory(run).files
    parsed = {
        name.removesuffix(".jsonl"): [json.loads(one) for one in data.splitlines()]
        for name, data in files.items()
        if name.endswith(".jsonl")
    }

    assert render_report(parsed) == files["report.md"]
    for name, figure in verdict_figures(parsed["verdict"]).items():
        assert files[name] == figure


def test_another_seed_moves_the_interval_and_not_the_fit(reports: Reports) -> None:
    first = rows(reports.build(1))
    second = rows(reports.build(2))

    def fitted(found: Sequence[Mapping[str, Any]]) -> list[Any]:
        return [
            (one["subset"], one["brier"], one["base_brier"], one["skill"])
            for one in of(found, "score")
        ]

    assert fitted(first) == fitted(second)
    assert of(first, "verdict")[0]["method"] != of(second, "verdict")[0]["method"]
    assert [one["interval"] for one in of(first, "score")] != [
        one["interval"] for one in of(second, "score")
    ]


def test_without_dates_sc7_is_not_measured(reports: Reports) -> None:
    found = rows(reports.build(settings=SETTINGS))

    (summary,) = of(found, "verdict")
    assert summary["status"] == "not_measured"
    assert "names no train_until" in summary["reason"]
    assert not of(found, "sc7")


def test_a_validation_span_to_the_snapshot_s_end_is_not_measured(
    reports: Reports,
) -> None:
    """Review fix: no test span is *not measured*, never a failed build."""
    settings = SETTINGS + VERDICT.replace(
        "validate_until = 2026-09-16T00:00:00Z",
        "validate_until = 2026-09-23T06:00:00Z",
    )

    found = rows(reports.build(settings=settings))

    (summary,) = of(found, "verdict")
    assert summary["status"] == "not_measured"
    assert "test 0" in summary["reason"]
