"""Stage 28's report half: SC-9 from sealed simulated fleets, on runs written by hand.

The fleets are flown by ``deploy/tools/watch_runs.py`` and, small, in
``tests/integration/test_watch_gate.py``. This is the section's arithmetic:
delay, the loss by then, lead, which came first, the false-alarm rate and its
interval, and SC-9 against its proposed targets; and a report built with
``--watches`` that verifies with every socket refused.

Reference: docs/DECISIONS.md D-105, D-294, D-296; ``EVALUATION.md`` §11.3.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian.cli import main
from meridian.datasets.publish import read_directory
from meridian.datasets.watch_runs import publish_watch_run, read_watch_run
from meridian.reports.render import render_report
from meridian.reports.render_watch import render_watch
from meridian.reports.watch import _poisson_interval, judge_degradations, watch_rows

T0 = datetime(2026, 8, 15, tzinfo=UTC)
STATIONS = ["st_aaaaaa", "st_bbbbbb", "st_cccccc"]


def opened(station: str, rate: float, at: datetime) -> str:
    return json.dumps(
        {
            "ledger": 1,
            "event": "open",
            "run_id": "watch-degradation-1",
            "kind": "signal_degradation",
            "target": f"station:{STATIONS.index(station) + 1}",
            "at": at.isoformat().replace("+00:00", "Z"),
            "station_id": station,
            "seed": 7,
            "detail": {"rate_db_per_day": rate},
        }
    )


def case(index: int, hours: float, outcome: str, clean: str = "decoded") -> dict:
    start = T0 + timedelta(hours=hours)
    return {
        "assignment_id": f"as_{index}_{hours}",
        "station_index": index,
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(minutes=12)).isoformat(),
        "outcome": outcome,
        "clean_outcome": clean,
        "simulated": True,
    }


def warning(index: int, hours: float) -> dict:
    return {
        "station_index": index,
        "raised_at": (T0 + timedelta(hours=hours)).isoformat(),
        "shortfall_db": 3.4,
        "simulated": True,
    }


def run_record(scenario: str, days: float) -> dict:
    return {
        "scenario": scenario,
        "master_seed": 4471,
        "stations": len(STATIONS),
        "station_ids": STATIONS,
        "start": (T0 - timedelta(days=3)).isoformat(),
        "watched_from": T0.isoformat(),
        "end": (T0 + timedelta(days=days)).isoformat(),
        "simulated": True,
    }


@pytest.fixture
def degraded(tmp_path: Path):
    ledger = "\n".join(
        [
            opened(STATIONS[0], 4.0, T0),
            opened(STATIONS[1], 8.0, T0),
            opened(STATIONS[2], 1.0, T0),
        ]
    )
    cases = [
        # Station 1: a marginal pass that only lost signal, then a lost decode
        # after the warning.
        case(1, 4.0, "signal_no_decode", clean="signal_no_decode"),
        case(1, 20.0, "no_signal"),
        # Station 2: a lost decode before the warning.
        case(2, 2.0, "signal_no_decode"),
        # Station 3: neither, by the run's end.
        case(3, 6.0, "decoded"),
    ]
    warnings = [warning(1, 10.0), warning(2, 5.0), warning(1, -30.0)]
    path = publish_watch_run(
        ledger,
        run_record("degradation", 3.0),
        cases,
        warnings,
        root=tmp_path,
        stamp=("0030", T0, T0 + timedelta(days=3)),
    ).path
    return read_watch_run(path)


@pytest.fixture
def clean(tmp_path: Path):
    path = publish_watch_run(
        "",
        run_record("clean", 10.0),
        [case(1, 2.0, "decoded")],
        # One before the watched stretch is the clean stretch's, not counted.
        [warning(2, 30.0), warning(3, -2.0)],
        root=tmp_path,
        stamp=("0030", T0, T0 + timedelta(days=10)),
    ).path
    return read_watch_run(path)


def test_each_degradation_is_judged_against_what_followed_it(degraded) -> None:
    found = {one.station_index: one for one in judge_degradations(degraded)}

    warned = found[1]
    assert (warned.first, warned.delay_h, warned.lead_h) == ("warning", 10.0, 10.0)
    assert warned.loss_db == pytest.approx(4.0 * 10 / 24, abs=0.01)
    late = found[2]
    assert (late.first, late.lead_h) == ("failure", -3.0)
    assert found[3].first == "neither"


def test_a_pass_the_clean_outcome_would_not_have_decoded_is_no_failure(
    degraded,
) -> None:
    (first,) = (one for one in judge_degradations(degraded) if one.station_index == 1)

    assert first.failed_at == T0 + timedelta(hours=20)


def test_the_summary_leaves_out_what_neither_came_to(degraded, clean) -> None:
    rows = watch_rows([degraded, clean])
    (summary,) = (one for one in rows if one["row"] == "summary")

    assert (summary["decided"], summary["neither"], summary["warned_first"]) == (
        2,
        1,
        1,
    )
    assert summary["lead_h"] == {"min": -3.0, "median": 3.5, "max": 10.0}


def test_false_alarms_are_counted_per_station_per_30_days(degraded, clean) -> None:
    rows = watch_rows([degraded, clean])
    (alarms,) = (one for one in rows if one["row"] == "false_alarms")
    (sc9,) = (one for one in rows if one["row"] == "sc9")

    # One warning over 3 stations for 10 days: one per 30 station-days.
    assert (alarms["warnings"], alarms["station_days"]) == (1, 30.0)
    assert alarms["per_station_30_days"] == 1.0
    assert sc9["false_alarms_met"] is True
    assert sc9["warned_first_met"] is False
    assert sc9["met"] is False


def test_the_poisson_interval_is_exact() -> None:
    low, high = _poisson_interval(0)
    assert (low, round(high, 3)) == (0.0, 3.689)
    low, high = _poisson_interval(5)
    assert (round(low, 3), round(high, 3)) == (1.623, 11.668)


def test_every_row_is_simulated_and_the_section_renders(degraded, clean) -> None:
    rows = watch_rows([degraded, clean])

    assert all(one["simulated"] is True for one in rows)
    text = "\n".join(render_watch(rows))
    assert "## Receive-chain watch" in text
    assert "SC-9 against its proposed targets: **not met**." in text
    assert "failure first" in text


def test_no_run_is_not_measured() -> None:
    text = "\n".join(render_watch(watch_rows([])))

    assert "Not measured: no sealed watch run was given." in text


def test_a_measured_row_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not simulated"):
        publish_watch_run(
            "",
            run_record("clean", 1.0),
            [{**case(1, 1.0, "decoded"), "simulated": False}],
            [],
            root=tmp_path,
            stamp=("0030", T0, T0),
        )


# --- through `meridian report` -------------------------------------------


EXAMPLE = (
    Path(__file__).resolve().parents[2] / "analysis/configs/evaluation.toml.example"
)


@pytest.fixture
def guarded(no_network: Any, network_guard: Any) -> Any:
    network_guard()
    return no_network


@pytest.fixture
def snapshot(raw_snapshot: Any, archive_world: Any) -> Path:
    return raw_snapshot(archive_world)


@pytest.fixture
def watched(degraded: Any, clean: Any) -> list[str]:
    """Both sealed runs, as ``--watches`` arguments."""
    return [
        arg
        for one in (degraded, clean)
        for arg in ("--watches", str(one.directory.path))
    ]


def report(root: Path, *args: str) -> int:
    return main(["report", "--root", str(root), *args])


def test_built_with_watch_runs_it_verifies_and_regenerates(
    snapshot: Path,
    datasets_root: Path,
    guarded: Any,
    watched: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:

    assert (
        report(
            datasets_root,
            "build",
            "--snapshot",
            str(snapshot),
            "--config",
            str(EXAMPLE),
            "--seed",
            "4471",
            *watched,
        )
        == 0
    )
    first = capsys.readouterr().out.splitlines()[0]
    run = Path(first.split(": ", 1)[1].rsplit(" (", 1)[0])

    assert report(datasets_root, "verify", str(run)) == 0
    assert "regenerates identically" in capsys.readouterr().out
    files = read_directory(run).files
    parsed = {
        name.removesuffix(".jsonl"): [json.loads(one) for one in data.splitlines()]
        for name, data in files.items()
        if name.endswith(".jsonl")
    }
    assert render_report(parsed) == files["report.md"]
    assert "## Receive-chain watch" in files["report.md"].decode()
    assert read_directory(run).manifest.transformation_version == "report-9"
    assert guarded.attempts == []
