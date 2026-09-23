"""Stage 16's gate: every archive-derived result carries its completeness.

The roadmap states it as: *every archive-derived result automatically includes
completeness information and, where relevant, IPW diagnostics.* "Automatically"
is tested here as three claims:

* **no dataset without it** — whatever the snapshot holds, even nothing,
  ``label`` writes the station-days, the propensities and the summary, and
  every labelling run of one snapshot writes the same ones, with the network
  refused;
* **no result without it** — :class:`EvaluationResult`'s completeness has no
  default and no ``None``, so leaving it out is an error at construction;
* **it means what it says** — one archive reception fewer lowers
  completeness, a deterministic policy is reported as the positivity
  violation it is, and no outcome moves a propensity (D-152).

Reference: docs/DECISIONS.md D-149, D-150, D-152, D-153, D-154.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian.cli import main
from meridian.datasets.completeness import CompletenessSummary
from meridian.datasets.evaluation import (
    PROPENSITIES,
    STATION_DAYS,
    build_evaluation_dataset,
)
from meridian.datasets.label_config import LabelConfig
from meridian.datasets.manifest import content_sha256
from meridian.datasets.publish import read_directory
from meridian.datasets.result import EvaluationResult
from meridian.datasets.result_reader import read_results

EARLY = datetime(2026, 9, 23, 7, 0, tzinfo=UTC)
LATE = datetime(2027, 3, 1, 12, 0, tzinfo=UTC)
AOS = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
"""Three days before the fixtures' ``as_of``: settled."""

LABEL_IN_A_FRESH_PROCESS = (
    "import sys; from meridian.cli import main; sys.exit(main(sys.argv[1:]))"
)

Tables = Mapping[str, Sequence[Mapping[str, object]]]


def label(raw: Path, root: Path, created_at: datetime = EARLY) -> Any:
    return build_evaluation_dataset(
        read_directory(raw), LabelConfig(), root=root, created_at=created_at
    )


def weighting(published: Any, population: str) -> dict[str, Any]:
    return published.manifest.summary["populations"][population]["weighting"]


def completeness(published: Any, population: str) -> dict[str, Any]:
    return published.manifest.summary["populations"][population]["completeness"]


def with_rows(world: Tables, table: str, rows: Sequence[Mapping[str, object]]) -> Any:
    return dict(world) | {table: rows}


# --- no dataset without it ------------------------------------------------------


@pytest.mark.parametrize("which", ["nothing", "own only", "own and archive"])
def test_every_evaluation_dataset_carries_its_completeness(
    raw_snapshot: Any,
    datasets_root: Path,
    world: Any,
    archive_world: Any,
    which: str,
) -> None:
    """Even a snapshot of nothing says so, rather than saying nothing."""
    tables = {"nothing": {}, "own only": world, "own and archive": archive_world}
    published = label(raw_snapshot(tables[which]), datasets_root)

    listed = {one.name for one in published.manifest.files}
    assert {STATION_DAYS, PROPENSITIES} <= listed
    populations = published.manifest.summary["populations"]
    assert set(populations) == {"own", "archive"}
    for one in populations.values():
        assert set(one) == {"completeness", "weighting"}
    assert [one.population for one in read_results(read_directory(published.path))] == [
        "own",
        "archive",
    ]


def test_three_labellings_agree_on_the_selection_with_no_network(
    raw_snapshot: Any,
    archive_world: Any,
    datasets_root: Path,
    no_network: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stage 15's gate, over the files Stage 16 adds: months apart, and at a prompt."""
    raw = raw_snapshot(archive_world)

    first = label(raw, datasets_root / "a", EARLY)
    second = label(raw, datasets_root / "b", LATE)
    code = main(["snapshot", "--root", str(datasets_root / "c"), "label", str(raw)])
    third = read_directory(
        Path(capsys.readouterr().out.splitlines()[0].split(": ", 1)[1].rsplit(" (")[0])
    )

    assert code == 0
    assert no_network.attempts == []
    assert content_sha256(first.manifest) == content_sha256(second.manifest)
    assert content_sha256(first.manifest) == content_sha256(third.manifest)
    for name in (STATION_DAYS, PROPENSITIES):
        assert (first.path / name).read_bytes() == third.files[name]


def test_the_selection_does_not_depend_on_the_process(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path
) -> None:
    """Two hash seeds: no set or frozenset order reaches a byte."""
    raw = raw_snapshot(archive_world)
    names = []
    for seed, root in (("0", "p"), ("4471", "q")):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                LABEL_IN_A_FRESH_PROCESS,
                "snapshot",
                "--root",
                str(datasets_root / root),
                "label",
                str(raw),
            ],
            capture_output=True,
            text=True,
            check=True,
            env=os.environ | {"PYTHONHASHSEED": seed},
        )
        names.append(Path(result.stdout.splitlines()[0].split(": ", 1)[1]).parts[-1])

    assert names[0] == names[1]


# --- no result without it -------------------------------------------------------


def test_a_result_s_completeness_has_no_default_and_no_none() -> None:
    """The annotation a type checker reads, and the absence of a way round it."""
    (field,) = [
        one
        for one in dataclasses.fields(EvaluationResult)
        if one.name == "completeness"
    ]

    assert field.default is dataclasses.MISSING
    assert field.default_factory is dataclasses.MISSING
    assert field.type == CompletenessSummary.__name__
    with pytest.raises(TypeError):
        EvaluationResult(population="own", weighting=None)  # type: ignore[call-arg]


# --- it means what it says ----------------------------------------------------


def test_one_archive_reception_fewer_lowers_completeness(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path
) -> None:
    """Drop the decoded reception on day 0: 2/3 becomes 1/3."""
    fewer = [
        one
        for one in archive_world["archive_observations"]
        if one["archive_outcome"] != "decoded"
    ]

    before = completeness(label(raw_snapshot(archive_world), datasets_root), "archive")
    after = completeness(
        label(
            raw_snapshot(with_rows(archive_world, "archive_observations", fewer)),
            datasets_root,
        ),
        "archive",
    )

    assert after["attempted"] == before["attempted"] - 1
    assert after["eligible"] == before["eligible"]
    assert after["distribution"]["deciles"][0] < before["distribution"]["deciles"][0]


def test_a_station_s_last_reception_ends_its_span_rather_than_scoring_zero(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path
) -> None:
    """D-150: without day 2's reception, days 1 and 2 are not the station's."""
    fewer = [
        one
        for one in archive_world["archive_observations"]
        if one["archive_outcome"] != "unknown"
    ]

    after = completeness(
        label(
            raw_snapshot(with_rows(archive_world, "archive_observations", fewer)),
            datasets_root,
        ),
        "archive",
    )

    assert after["station_days"] == {
        "retained": 0,
        "below_threshold": 1,
        "empty": 0,
        "inactive": 0,
    }


def deterministic_world() -> dict[str, list[dict[str, object]]]:
    """One station whose policy takes every 70° pass and no 10° pass."""
    tables: dict[str, list[dict[str, object]]] = {
        "passes": [],
        "assignments": [],
        "observations": [],
        "listening": [],
        "element_sets": [
            {"id": 1, "satellite_id": "norad:57166", "epoch": AOS - timedelta(hours=6)}
        ],
        "stations": [{"station_id": "st_a", "lon_deg": 77.6}],
    }
    for n in range(6):
        taken = n % 2 == 0
        aos = AOS + timedelta(hours=2 * n)
        tables["passes"].append(
            {
                "id": 100 + n,
                "satellite_id": "norad:57166",
                "station_id": "st_a",
                "aos": aos,
                "los": aos + timedelta(minutes=11),
                "max_elevation_deg": 70.0 if taken else 10.0,
                "element_set_id": 1,
                "simulated": False,
            }
        )
        if not taken:
            continue
        tables["assignments"].append(
            {
                "assignment_id": f"as_{n}",
                "pass_id": 100 + n,
                "station_id": "st_a",
                "start_at": aos,
                "end_at": aos + timedelta(minutes=11),
                "decision": "scheduled",
                "state": "reported",
                "model_config": "A",
                "simulated": False,
            }
        )
        tables["observations"].append(
            {
                "assignment_id": f"as_{n}",
                "revision": 1,
                "outcome": "decoded",
                "simulated": False,
            }
        )
        tables["listening"].append(
            {"assignment_id": f"as_{n}", "listening_confirmed": True}
        )
    return tables


def test_a_deterministic_policy_is_reported_as_a_positivity_violation(
    raw_snapshot: Any,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D-152: every pass certain or unsupported, and the report says both."""
    published = label(raw_snapshot(deterministic_world()), datasets_root)
    own = weighting(published, "own")

    assert (own["available"], own["certain"], own["unsupported"]) == (6, 3, 3)
    assert own["overlap"]["attempted"] == [0] * 9 + [3]
    assert own["overlap"]["not_attempted"] == [3] + [0] * 9
    assert own["unreliable"] is True

    assert main(["snapshot", "completeness", str(published.path)]) == 0
    assert "unsupported 3 · certain 3" in capsys.readouterr().out


def test_no_outcome_moves_a_propensity(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path
) -> None:
    """D-152 end to end: every outcome changed, the propensity file byte-identical.

    The changes keep each satellite heard, so no pass changes eligibility —
    a satellite judged silent leaves the denominator by D-149, which is an
    outcome deciding what was available, not what was likely to be attempted.
    The weighted rates move, which shows the outcomes did arrive.
    """
    swapped = {"decoded": "signal_no_decode", "no_signal": "signal_no_decode"}
    observations = [
        dict(one) | {"outcome": swapped[str(one["outcome"])]}
        for one in archive_world["observations"]
    ]
    archive_swapped = {"decoded": "no_data", "no_data": "decoded", "unknown": "decoded"}
    receptions = [
        dict(one) | {"archive_outcome": archive_swapped[str(one["archive_outcome"])]}
        for one in archive_world["archive_observations"]
    ]
    changed = with_rows(
        with_rows(archive_world, "observations", observations),
        "archive_observations",
        receptions,
    )

    before = label(raw_snapshot(archive_world), datasets_root / "x")
    after = label(raw_snapshot(changed), datasets_root / "y")

    assert (before.path / PROPENSITIES).read_bytes() == (
        after.path / PROPENSITIES
    ).read_bytes()
    for population in ("own", "archive"):
        assert (
            weighting(before, population)["unweighted"]
            != weighting(after, population)["unweighted"]
        )
