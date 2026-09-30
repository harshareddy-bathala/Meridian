"""The orbit-uncertainty section: timing error from a snapshot, and SC-3.

The world here is one station whose clock runs two seconds fast, so every
detection it stamps is two seconds late and its heartbeats report an offset of
−2 s (D-025). Its passes are of satellites in two regimes, on element sets
from a few hours to five days old, with a true timing error that grows by a
quarter of a second a day. Some passes have no heartbeat near them, one lands
inside the clock's uncertainty, one assignment carried no stated 1σ, and a
simulated station reports too.

**Each claim has its positive control:** the sign convention is pinned with a
known pass, and the reversed sign fails; a heartbeat after the window is not
read; the slope is found near the one the world was built with; the two
populations never mix.

Reference: docs/DECISIONS.md D-025, D-100, D-177, D-239; ``EVALUATION.md`` §6.
"""

from __future__ import annotations

import json
import random
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

from meridian.cli import main
from meridian.datasets.publish import read_directory
from meridian.reports.config import OrbitConfig
from meridian.reports.detections import Detection, detections
from meridian.reports.orbit import orbit_rows

START = datetime(2026, 9, 2, tzinfo=UTC)
FAST_S = 2.0
"""How far ahead the station's clock runs: its offset is −2 s (D-025)."""

SLOPE = 0.25
REGIMES = {"norad:1": "leo", "norad:2": "meo"}

Tables = dict[str, list[Mapping[str, object]]]


def timing_world(*, simulated_too: bool = True) -> Tables:
    """40 measured passes, and 6 simulated ones, with known timing errors."""
    rng = random.Random(39)
    rows: Tables = {
        "passes": [],
        "assignments": [],
        "observations": [],
        "heartbeats": [],
        "element_sets": [],
        "satellites": [
            {"satellite_id": one, "orbital_regime": regime, "priority": 1.0}
            for one, regime in REGIMES.items()
        ],
    }
    for number in range(1, 41):
        age_days = rng.uniform(0.1, 5.0)
        error = SLOPE * age_days + rng.gauss(0.0, 0.1) + 0.3
        _add(rows, number, (age_days, error), clock=number % 8 != 0)
    for number in range(41, 47):
        if simulated_too:
            _add(rows, number, (1.0, 0.5), clock=True, simulated=True)
    return rows


def _add(
    rows: Tables,
    number: int,
    timing: tuple[float, float],
    *,
    clock: bool,
    simulated: bool = False,
) -> None:
    """A pass at ``(element-set age in days, true timing error in s)``."""
    age_days, true_error_s = timing
    station = "st_sim" if simulated else "st_a"
    satellite = "norad:1" if number % 2 else "norad:2"
    aos = START + timedelta(days=number // 3, hours=number % 3 * 7)
    heard_true = aos + timedelta(seconds=true_error_s)
    heard_station = heard_true + timedelta(seconds=FAST_S)
    rows["element_sets"].append(
        {
            "id": number,
            "satellite_id": satellite,
            "epoch": aos - timedelta(days=age_days),
        }
    )
    rows["passes"].append(
        {
            "id": number,
            "satellite_id": satellite,
            "station_id": station,
            "aos": aos,
            "los": aos + timedelta(minutes=12),
            "max_elevation_deg": 40.0,
            "aos_azimuth_deg": 350.0,
            "los_azimuth_deg": 170.0,
            "element_set_id": number,
            "computed_at": aos - timedelta(hours=6),
            "simulated": simulated,
        }
    )
    rows["assignments"].append(
        {
            "assignment_id": f"as_{number}",
            "pass_id": number,
            "station_id": station,
            "start_at": aos,
            "end_at": aos + timedelta(minutes=12),
            "timing_uncertainty_s": None if number == 7 else 1.5,
            "decision": "scheduled",
            "state": "reported",
            "model_config": "A",
            "simulated": simulated,
        }
    )
    rows["observations"].append(
        {
            "assignment_id": f"as_{number}",
            "revision": 1,
            "outcome": "decoded",
            "first_detection_at": heard_station,
            "noise_floor_dbfs": None,
            "receiver_gain_db": None,
            "simulated": simulated,
        }
    )
    if clock:
        rows["heartbeats"].append(
            {
                "station_id": station,
                "received_at": heard_true + timedelta(seconds=40),
                "clock_offset_s": -FAST_S,
                "clock_uncertainty_s": 0.05,
            }
        )


def detection(**overrides: Any) -> Detection:
    fields: dict[str, Any] = {
        "assignment_id": "as_1",
        "station_id": "st_a",
        "satellite_id": "norad:1",
        "regime": "leo",
        "aos": START,
        "element_set_age_days": 1.0,
        "uncorrected_s": 10.0,
        "clock_offset_s": -3.0,
        "clock_uncertainty_s": 0.05,
        "sigma_s": 1.0,
        "sigma_source": "assignment",
        "simulated": False,
    }
    return Detection(**(fields | overrides))


def files_of(raw_snapshot: Any, tables: Tables) -> Mapping[str, bytes]:
    return read_directory(raw_snapshot(tables)).files


# --- the sign convention and the clock (D-025) -------------------------------


def test_a_fast_clock_moves_the_detection_earlier() -> None:
    """Stamped 10 s after the rise by a clock 3 s fast: the pass was heard at 7 s."""
    assert detection().error_s == 7.0


def test_the_reversed_sign_would_have_been_caught() -> None:
    """Positive control: with the sign flipped the same pass reads 13 s."""
    one = detection(clock_offset_s=3.0)

    assert one.error_s == 13.0
    assert one.error_s != detection().error_s


def test_the_worlds_detections_recover_the_true_error(raw_snapshot: Any) -> None:
    found = [
        one
        for one in detections(files_of(raw_snapshot, timing_world()))
        if one.error_s is not None and not one.simulated
    ]

    assert found
    for one in found:
        assert one.uncorrected_s - one.error_s == pytest.approx(FAST_S)


def test_an_unknown_offset_is_never_assumed_zero(raw_snapshot: Any) -> None:
    found = detections(files_of(raw_snapshot, timing_world()))
    unknown = [one for one in found if one.excluded == "clock_offset_unknown"]

    assert unknown
    assert all(one.error_s is None for one in unknown)


def test_a_heartbeat_after_the_window_describes_another_clock(
    raw_snapshot: Any,
) -> None:
    tables = timing_world(simulated_too=False)
    heard = tables["observations"][0]["first_detection_at"]
    assert isinstance(heard, datetime)
    tables["heartbeats"] = [
        {
            "station_id": "st_a",
            "received_at": heard + timedelta(minutes=6),
            "clock_offset_s": -FAST_S,
            "clock_uncertainty_s": 0.05,
        }
    ]

    first = detections(files_of(raw_snapshot, tables))[0]

    assert first.clock_offset_s is None


def test_the_latest_revision_is_the_one_read(raw_snapshot: Any) -> None:
    tables = timing_world(simulated_too=False)
    later = dict(tables["observations"][0]) | {
        "revision": 2,
        "first_detection_at": None,
    }
    tables["observations"].append(later)

    found = {one.assignment_id for one in detections(files_of(raw_snapshot, tables))}

    assert "as_1" not in found


def test_an_assignment_with_no_stated_sigma_takes_the_prior(raw_snapshot: Any) -> None:
    found = {
        one.assignment_id: one
        for one in detections(files_of(raw_snapshot, timing_world()))
    }

    assert found["as_7"].sigma_source == "prior"
    assert found["as_1"].sigma_source == "assignment"
    assert found["as_1"].sigma_s == 1.5


def test_an_error_inside_the_clocks_uncertainty_is_excluded_by_name() -> None:
    assert detection(uncorrected_s=3.02).excluded == "within_clock_uncertainty"
    assert detection().excluded is None


# --- the section ---------------------------------------------------------------


def section(raw_snapshot: Any, *, seed: int = 1) -> list[dict[str, Any]]:
    rows = orbit_rows(
        detections(files_of(raw_snapshot, timing_world())),
        OrbitConfig(resamples=300, min_young=3),
        seed=seed,
    )
    return json.loads(json.dumps(rows, default=str))


def of(rows: Sequence[dict[str, Any]], kind: str, **match: str) -> list[dict[str, Any]]:
    return [
        one
        for one in rows
        if one["row"] == kind and all(one.get(k) == v for k, v in match.items())
    ]


def test_the_slope_is_found_near_the_one_the_world_was_built_with(
    raw_snapshot: Any,
) -> None:
    regimes = of(section(raw_snapshot), "regime", population="measured")

    assert {one["regime"] for one in regimes} == {"leo", "meo"}
    for one in regimes:
        assert one["slope_s_per_day"] == pytest.approx(SLOPE, abs=0.1)
        interval = one["slope_interval"]
        assert interval["low"] <= one["slope_s_per_day"] <= interval["high"]


def test_every_exclusion_is_counted_and_nothing_is_pooled(raw_snapshot: Any) -> None:
    rows = section(raw_snapshot)
    measured = of(rows, "detections", population="measured")[0]
    simulated = of(rows, "detections", population="simulated")[0]

    assert measured["detections"] == 40
    assert simulated["detections"] == 6
    assert measured["clock_offset_unknown"] == 5
    assert (
        measured["kept"]
        + measured["clock_offset_unknown"]
        + measured["within_clock_uncertainty"]
        == 40
    )
    assert measured["sigma_from_prior"] == 1


def test_sc3_is_the_measured_coverage_under_the_exclusions(raw_snapshot: Any) -> None:
    rows = section(raw_snapshot)
    sc3 = of(rows, "sc3")[0]
    coverage = of(rows, "coverage", population="measured")[0]["kept"]
    kept = [
        one
        for one in of(rows, "detection", population="measured")
        if one["excluded"] is None
    ]

    assert sc3["status"] == "measured"
    assert sc3["n"] == len(kept)
    inside = sum(1 for one in kept if abs(one["error_s"]) <= one["sigma_s"])
    assert coverage["within"] == inside
    assert sc3["coverage"] == pytest.approx(inside / len(kept), abs=1e-6)
    assert sc3["point_meets"] == (sc3["coverage"] >= 0.68)


def test_the_spread_test_runs_only_with_enough_young_element_sets(
    raw_snapshot: Any,
) -> None:
    spread = of(section(raw_snapshot), "spread", population="measured")[0]

    assert spread["young"] >= 3
    assert spread["verdict"] in {"fit", "not fit as written"}
    strict = orbit_rows(
        detections(files_of(raw_snapshot, timing_world())),
        OrbitConfig(resamples=300, min_young=1000),
        seed=1,
    )
    assert of(strict, "spread", population="measured")[0]["verdict"] == "not tested"


def test_another_seed_moves_the_interval_and_not_the_slope(raw_snapshot: Any) -> None:
    first = of(section(raw_snapshot, seed=1), "regime", population="measured")
    second = of(section(raw_snapshot, seed=2), "regime", population="measured")

    assert [one["slope_s_per_day"] for one in first] == [
        one["slope_s_per_day"] for one in second
    ]
    assert [one["slope_interval"] for one in first] != [
        one["slope_interval"] for one in second
    ]


def test_a_snapshot_without_detections_measures_nothing(raw_snapshot: Any) -> None:
    rows = orbit_rows(detections(files_of(raw_snapshot, {})), OrbitConfig(), seed=1)

    assert of(rows, "sc3")[0]["status"] == "not measured"
    assert of(rows, "detections", population="measured")[0]["detections"] == 0


def test_the_section_and_its_figures_are_in_a_built_run(
    raw_snapshot: Any, datasets_root: Path, tmp_path: Path, capsys: Any
) -> None:
    config = tmp_path / "evaluation.toml"
    config.write_text("[orbit]\nresamples = 200\nmin_young = 3\n", encoding="utf-8")
    code = main(
        [
            "report",
            "--root",
            str(datasets_root),
            "build",
            "--snapshot",
            str(raw_snapshot(timing_world())),
            "--config",
            str(config),
            "--seed",
            "7",
        ]
    )
    out = capsys.readouterr()
    assert code == 0, out.err
    run = Path(out.out.splitlines()[0].split(": ", 1)[1].rsplit(" (", 1)[0])
    report = (run / "report.md").read_text(encoding="utf-8")

    assert "## Orbit uncertainty" in report
    assert "### Simulated passes — SIMULATED" in report
    for population in ("measured", "simulated"):
        figure = run / f"orbit_timing_{population}.svg"
        assert ElementTree.fromstring(figure.read_bytes()).tag.endswith("svg")
        assert f"]({figure.name})" in report
    assert b"SIMULATED" in (run / "orbit_timing_simulated.svg").read_bytes()
    assert b"SIMULATED" not in (run / "orbit_timing_measured.svg").read_bytes()
    assert main(["report", "--root", str(datasets_root), "verify", str(run)]) == 0


def test_two_heartbeats_at_one_instant_one_without_uncertainty_are_read(
    raw_snapshot: Any,
) -> None:
    """Found in review: sorting compared None with a float and raised."""
    tables = timing_world(simulated_too=False)
    twin = dict(tables["heartbeats"][0]) | {"clock_uncertainty_s": None}
    tables["heartbeats"].append(twin)

    assert detections(files_of(raw_snapshot, tables))
