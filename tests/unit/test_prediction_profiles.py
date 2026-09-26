"""``meridian.prediction.profiles`` — D-159's learned environment, by hand.

One station, one pass a day at 06:00 UTC, every pass on the same five-sample
track through the 40–50° sector: elevations 0, 10, 20, 10, 0 at 30 s steps.
A detection 60 s after the track starts is therefore at 20°, and every number
below can be worked out from that.

With a 24 h settle margin, the pass on day ``k`` sees days ``0 .. k-2``.

Reference: docs/DECISIONS.md D-146, D-148, D-157, D-159, D-161.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from meridian.datasets.labels import LabelledPass
from meridian.datasets.publish import read_directory
from meridian.prediction.feature_rows import (
    FeatureRows,
    PassGeometry,
    PassTrack,
    Reading,
    read_feature_rows,
)
from meridian.prediction.profiles import ENVIRONMENT, SHRINK, Environment

DAY0 = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)
MARGIN_S = 24 * 3600
TRACK = PassTrack(
    start=DAY0,
    step_s=30,
    azimuth_deg=(40.0, 42.0, 44.0, 46.0, 48.0),
    elevation_deg=(0.0, 10.0, 20.0, 10.0, 0.0),
)


def labelled(pass_id: int, day: float, **fields: Any) -> LabelledPass:
    aos = DAY0 + timedelta(days=day)
    base = LabelledPass(
        pass_id=pass_id,
        pass_ids=(pass_id,),
        station_id="st_a",
        satellite_id="norad:57166",
        aos=aos,
        los=aos + timedelta(minutes=2),
        label="successful_reception",
        exclusion_reason=None,
        source_outcome="decoded",
        listening_confirmed=True,
        scheduled_by=("A",),
        simulated=False,
    )
    return replace(base, **fields)


def geometry(one: LabelledPass, **fields: Any) -> PassGeometry:
    base = PassGeometry(
        aos=one.aos,
        computed_at=one.aos - timedelta(hours=6),
        max_elevation_deg=20.0,
        aos_azimuth_deg=40.0,
        los_azimuth_deg=48.0,
        element_set_epoch=one.aos - timedelta(hours=12),
        track=replace(TRACK, start=one.aos),
    )
    return replace(base, **fields)


def reading(
    one: LabelledPass,
    *,
    detected_s: float | None = 60.0,
    noise: float | None = -100.0,
    **fields: Any,
) -> Reading:
    base = Reading(
        assignment_id=f"as_{one.pass_id}",
        outcome="decoded",
        first_detection_at=None
        if detected_s is None
        else one.aos + timedelta(seconds=detected_s),
        noise_floor_dbfs=noise,
        simulated=False,
    )
    return replace(base, **fields)


def world(
    passes: list[LabelledPass], readings: dict[int, tuple[Reading, ...]], **geo: Any
) -> FeatureRows:
    return FeatureRows(
        geometry={one.pass_id: geometry(one, **geo) for one in passes},
        bands={},
        longitudes={"st_a": 0.0},
        readings=readings,
    )


def values(
    target: LabelledPass, passes: list[LabelledPass], rows: FeatureRows
) -> dict[str, float]:
    environment = Environment(passes, rows, settle_margin_s=MARGIN_S)
    found = environment.values(target, rows.geometry[target.pass_id])
    return dict(zip([name for name, _ in ENVIRONMENT], found, strict=True))


def daily(days: int) -> list[LabelledPass]:
    return [labelled(n, n) for n in range(days)]


# --- the horizon ------------------------------------------------------------------


def test_with_no_history_the_horizon_is_the_geometric_one() -> None:
    passes = daily(1)

    got = values(passes[0], passes, world(passes, {}))

    assert got["horizon_clear_share"] == pytest.approx(3 / 5)
    assert got["horizon_n"] == 0.0


def test_detections_raise_the_horizon_by_their_count() -> None:
    """Five detections at 20°: shrunk to 5/(5+5) of 20 = 10°, so only 20° clears."""
    passes = daily(7)
    rows = world(passes, {one.pass_id: (reading(one),) for one in passes})

    got = values(passes[6], passes, rows)

    assert got["horizon_n"] == 5.0
    assert got["horizon_clear_share"] == pytest.approx(1 / 5)


def test_the_horizon_is_the_lower_quartile_of_detections() -> None:
    """Detections at 10°, 20°, 20° and 20°: the quartile is 10°, shrunk by 4/9."""
    passes = daily(6)
    at = {0: 30.0, 1: 60.0, 2: 60.0, 3: 60.0}
    rows = world(
        passes,
        {n: (reading(passes[n], detected_s=s),) for n, s in at.items()},
        max_elevation_deg=20.0,
        track=None,
    )
    rows = replace(
        rows,
        geometry={
            n: replace(held, track=replace(TRACK, start=passes[n].aos))
            if n in at
            else held
            for n, held in rows.geometry.items()
        },
    )

    got = values(passes[5], passes, rows)

    floor = 4 / (4 + SHRINK) * 10.0
    assert got["horizon_n"] == 4.0
    assert got["horizon_clear_share"] == pytest.approx((20.0 - floor) / 20.0)


def test_a_detection_between_samples_is_placed_between_them() -> None:
    """45 s: halfway from 10° to 20°, so 15°.

    Twenty of them lift the floor to 20/25 of 15° = 12°, above the track's 10°
    samples, so only the 20° one clears. Placed at the earlier sample instead,
    they would be 10°, the floor 8°, and three samples would clear.
    """
    passes = daily(22)
    rows = world(
        passes, {one.pass_id: (reading(one, detected_s=45.0),) for one in passes}
    )

    got = values(passes[21], passes, rows)

    assert got["horizon_n"] == 20.0
    assert got["horizon_clear_share"] == pytest.approx(1 / 5)


def test_other_sectors_do_not_move_this_one_s_horizon() -> None:
    passes = daily(7)
    elsewhere = replace(TRACK, azimuth_deg=(200.0, 202.0, 204.0, 206.0, 208.0))
    rows = world(passes, {one.pass_id: (reading(one),) for one in passes[:-1]})
    rows = replace(
        rows,
        geometry={
            **{
                one.pass_id: replace(
                    rows.geometry[one.pass_id], track=replace(elsewhere, start=one.aos)
                )
                for one in passes[:-1]
            },
            6: rows.geometry[6],
        },
    )

    got = values(passes[6], passes, rows)

    assert got["horizon_n"] == 0.0
    assert got["horizon_clear_share"] == pytest.approx(3 / 5)


# --- interference -----------------------------------------------------------------


def test_a_louder_cell_reads_above_the_station_s_median() -> None:
    """Five at -100 dBFS this hour, five at -110 at 18:00: +5 dB, shrunk to 2.5."""
    morning = [labelled(n, n) for n in range(5)]
    evening = [labelled(10 + n, n + 0.5) for n in range(5)]
    target = labelled(99, 8)
    passes = [*morning, *evening, target]
    rows = world(
        passes,
        {
            **{one.pass_id: (reading(one, noise=-100.0),) for one in morning},
            **{one.pass_id: (reading(one, noise=-110.0),) for one in evening},
        },
    )

    got = values(target, passes, rows)

    assert got["interference_n"] == 5.0
    assert got["interference_db"] == pytest.approx(5 / (5 + SHRINK) * 5.0)


def test_local_solar_hour_moves_the_cell_with_the_station_s_longitude() -> None:
    """Readings at 03:00 UTC, a pass at 06:00 UTC: two UTC bands, one local one.

    At 15° E they are 04:00 and 07:00 local, both in the 04–08 band, so the
    pass reads the five readings. At 0° they are in different bands and it
    reads none — so the longitude, and only the longitude, joins them.
    """
    early = [labelled(n, n - 0.125) for n in range(5)]
    target = labelled(9, 7)
    passes = [*early, target]
    rows = world(passes, {one.pass_id: (reading(one),) for one in early})

    east = values(target, passes, replace(rows, longitudes={"st_a": 15.0}))
    greenwich = values(target, passes, replace(rows, longitudes={"st_a": 0.0}))

    assert east["interference_n"] == 5.0
    assert greenwich["interference_n"] == 0.0


# --- timing and divergence ------------------------------------------------------


def test_timing_error_is_the_median_first_detection_after_aos() -> None:
    passes = daily(5)
    at = {0: -20.0, 1: 60.0, 2: 90.0}
    rows = world(
        passes, {n: (reading(passes[n], detected_s=s),) for n, s in at.items()}
    )

    got = values(passes[4], passes, rows)

    assert got["timing_error_s"] == 60.0
    assert got["timing_error_n"] == 3.0


def test_divergence_is_the_spread_of_predictions_made_before_the_pass() -> None:
    """Three predictions of one rise; the third was made after it and is left out."""
    target = labelled(1, 0, pass_ids=(1, 2, 3))
    rows = FeatureRows(
        geometry={
            1: geometry(target),
            2: geometry(target, aos=target.aos + timedelta(seconds=20)),
            3: geometry(
                target,
                aos=target.aos + timedelta(seconds=50),
                computed_at=target.aos + timedelta(minutes=1),
            ),
        },
        bands={},
        longitudes={},
        readings={},
    )

    assert values(target, [target], rows)["element_set_divergence_s"] == 20.0


# --- which report, and whose -------------------------------------------------------


def test_the_most_informative_measured_report_is_read() -> None:
    passes = daily(3)
    rows = world(
        passes,
        {
            0: (
                reading(
                    passes[0],
                    detected_s=None,
                    noise=None,
                    outcome="no_signal",
                    assignment_id="a",
                ),
                reading(passes[0], noise=-100.0, assignment_id="b"),
                reading(passes[0], noise=-80.0, simulated=True, assignment_id="c"),
            )
        },
    )

    got = values(passes[2], passes, rows)

    assert got["interference_n"] == 1.0
    assert got["timing_error_n"] == 1.0


def test_a_simulated_pass_teaches_the_environment_nothing() -> None:
    passes = [labelled(0, 0, simulated=True), labelled(1, 5)]
    rows = world(passes, {0: (reading(passes[0]),)})

    got = values(passes[1], passes, rows)

    assert got["horizon_n"] == got["interference_n"] == got["timing_error_n"] == 0.0


def test_a_simulated_report_on_a_measured_pass_is_not_read() -> None:
    """D-078 holds per report too, not only per pass."""
    passes = daily(3)
    rows = world(passes, {0: (reading(passes[0], simulated=True),)})

    got = values(passes[2], passes, rows)

    assert got["horizon_n"] == got["interference_n"] == got["timing_error_n"] == 0.0


# --- D-157, for the environment -----------------------------------------------------


def test_no_reading_after_a_pass_began_reaches_its_environment() -> None:
    passes = daily(8)
    rows = world(passes, {one.pass_id: (reading(one),) for one in passes})
    target = passes[-1]
    settled = timedelta(seconds=MARGIN_S)
    changed = replace(
        rows,
        readings={
            one.pass_id: (reading(one, detected_s=0.0, noise=-60.0),)
            if one.los + settled > target.aos
            else rows.readings[one.pass_id]
            for one in passes
        },
    )

    assert values(target, passes, changed) == values(target, passes, rows)


def test_a_settled_reading_does_reach_it() -> None:
    """The positive control: one settled detection withdrawn, and it shows.

    Withdrawn rather than moved, because a quartile and a median can each
    absorb one moved value without changing — which would make the control
    pass for a reason that says nothing about the test above.
    """
    passes = daily(8)
    rows = world(passes, {one.pass_id: (reading(one),) for one in passes})
    changed = replace(
        rows,
        readings={
            **rows.readings,
            0: (reading(passes[0], detected_s=None, noise=None),),
        },
    )

    before = values(passes[-1], passes, rows)
    after = values(passes[-1], passes, changed)

    assert after["horizon_n"] == before["horizon_n"] - 1
    assert after["interference_n"] == before["interference_n"] - 1
    assert after["timing_error_n"] == before["timing_error_n"] - 1


# --- read from a snapshot -----------------------------------------------------------


def test_readings_are_each_assignment_s_latest_revision(
    raw_snapshot: Any, world: Any
) -> None:
    detected = datetime(2026, 9, 20, 6, 1, tzinfo=UTC)
    first = dict(world["observations"][0])
    second = first | {
        "revision": 2,
        "first_detection_at": detected,
        "noise_floor_dbfs": -97.5,
    }
    tables = dict(world) | {
        "observations": [first, second, *world["observations"][1:]],
        "stations": [{"station_id": "st_a", "lon_deg": 77.6}],
    }

    rows = read_feature_rows(read_directory(raw_snapshot(tables)).files)

    (latest,) = rows.readings[1]
    assert latest.first_detection_at == detected
    assert latest.noise_floor_dbfs == -97.5
    assert rows.longitudes == {"st_a": 77.6}
