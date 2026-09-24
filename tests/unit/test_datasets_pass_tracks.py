"""``meridian.datasets.pass_tracks`` — D-158's frozen tracks, without a database.

Most cases hand in a fake orbit service returning chosen angles, so the
rounding, the folding and the counts are checked exactly. One case propagates
for real, with the element set and site ``test_pass_windows_reference.py``
transcribes, so a track is known to agree with the pass window it belongs to.

Reference: docs/DECISIONS.md D-078, D-158.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from meridian.datasets.pass_tracks import (
    TRACK_STEP_S,
    TrackRows,
    compute_pass_tracks,
)
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.orbit.types import ElementSet, GroundSite, LookAngle, PassSearch

ISS_LINE1 = "1 25544U 98067A   14020.93268519  .00009878  00000-0  18200-3 0  5082"
ISS_LINE2 = "2 25544  51.6498 109.4756 0003572  55.9686 274.8005 15.49815350868473"
ISS_EPOCH = datetime(2014, 1, 20, 22, 23, 4, tzinfo=UTC)
BLUFFTON = {"station_id": "st_a", "lat_deg": 40.8939, "lon_deg": -83.8917, "alt_m": 0.0}
AOS = datetime(2014, 1, 23, 6, 0, tzinfo=UTC)


@dataclass
class FakeOrbit:
    """Answers with the azimuths given, one sample every step, and keeps the calls."""

    azimuths: Sequence[float] = (10.0,)
    calls: list[tuple[str, datetime, datetime, float]] = field(default_factory=list)

    def look_angles(
        self,
        element_set: ElementSet,
        site: GroundSite,
        start: datetime,
        end: datetime,
        *,
        step_s: float,
    ) -> list[LookAngle]:
        self.calls.append((element_set.satellite_id, start, end, step_s))
        assert site.lat_deg == BLUFFTON["lat_deg"]
        return [
            LookAngle(
                t=start + timedelta(seconds=step_s * n),
                azimuth_deg=azimuth,
                elevation_deg=12.3456,
                range_km=1000.0,
                range_rate_km_s=0.0,
            )
            for n, azimuth in enumerate(self.azimuths)
        ]


def element_set(element_set_id: int = 1) -> Mapping[str, object]:
    return {
        "id": element_set_id,
        "satellite_id": "norad:25544",
        "epoch": ISS_EPOCH,
        "line1": ISS_LINE1,
        "line2": ISS_LINE2,
    }


def pass_(pass_id: int, **fields: object) -> Mapping[str, object]:
    return {
        "id": pass_id,
        "station_id": "st_a",
        "aos": AOS,
        "los": AOS + timedelta(minutes=10),
        "element_set_id": 1,
        "simulated": False,
    } | fields


def rows(*passes: Mapping[str, object]) -> TrackRows:
    return TrackRows(passes=passes, stations=[BLUFFTON], element_sets=[element_set()])


def test_a_track_is_sampled_over_the_pass_s_own_window() -> None:
    orbit = FakeOrbit()

    (track,) = compute_pass_tracks(rows(pass_(7)), orbit).rows

    assert orbit.calls == [
        ("norad:25544", AOS, AOS + timedelta(minutes=10), TRACK_STEP_S)
    ]
    assert track == {
        "pass_id": 7,
        "start": AOS,
        "step_s": 30,
        "azimuth_deg": [10.0],
        "elevation_deg": [12.35],
    }


def test_azimuth_is_folded_after_rounding_so_it_is_never_360() -> None:
    """The service unwraps azimuth; a file of a sky has it in [0, 360)."""
    orbit = FakeOrbit(azimuths=(358.5, 359.996, 361.25, -0.001))

    (track,) = compute_pass_tracks(rows(pass_(7)), orbit).rows

    assert track["azimuth_deg"] == [358.5, 0.0, 1.25, 0.0]


def test_tracks_are_in_pass_order_whatever_order_the_rows_came_in() -> None:
    result = compute_pass_tracks(rows(pass_(9), pass_(3), pass_(5)), FakeOrbit())

    assert [one["pass_id"] for one in result.rows] == [3, 5, 9]


def test_what_cannot_be_tracked_is_counted_not_dropped() -> None:
    result = compute_pass_tracks(
        rows(
            pass_(1),
            pass_(2, simulated=True),
            pass_(3, station_id="st_gone"),
            pass_(4, element_set_id=99),
        ),
        FakeOrbit(),
    )

    assert [one["pass_id"] for one in result.rows] == [1]
    assert result.counts == {
        "pass_tracks": 1,
        "pass_tracks.simulated_skipped": 1,
        "pass_tracks.without_station": 1,
        "pass_tracks.without_element_set": 1,
    }


def test_a_simulated_pass_is_never_propagated() -> None:
    """D-078: computed only to be ignored, so not computed."""
    orbit = FakeOrbit()

    compute_pass_tracks(rows(pass_(2, simulated=True)), orbit)

    assert orbit.calls == []


def test_a_real_track_agrees_with_its_own_pass_window() -> None:
    """Propagated for real: rises at the horizon, peaks at the window's peak."""
    orbit = SkyfieldOrbitService()
    iss = ElementSet(
        satellite_id="norad:25544", epoch=ISS_EPOCH, line1=ISS_LINE1, line2=ISS_LINE2
    )
    site = GroundSite(lat_deg=40.8939, lon_deg=-83.8917, alt_m=0.0)
    window = orbit.pass_windows(
        PassSearch(
            element_set=iss,
            site=site,
            start=datetime(2014, 1, 23, 4, 0, tzinfo=UTC),
            end=datetime(2014, 1, 23, 10, 0, tzinfo=UTC),
            min_elevation_deg=0.0,
        )
    )[0]

    (track,) = compute_pass_tracks(
        rows(pass_(1, aos=window.aos, los=window.los)), orbit
    ).rows

    elevations = track["elevation_deg"]
    duration_s = (window.los - window.aos).total_seconds()
    assert len(elevations) == -(-duration_s // TRACK_STEP_S)
    assert elevations[0] == pytest.approx(0.0, abs=0.05)
    assert max(elevations) <= window.max_elevation_deg + 0.01
    assert max(elevations) == pytest.approx(window.max_elevation_deg, abs=1.0)
    assert track["azimuth_deg"][0] == pytest.approx(
        window.aos_azimuth_deg % 360.0, abs=0.05
    )
    assert all(0.0 <= one < 360.0 for one in track["azimuth_deg"])
