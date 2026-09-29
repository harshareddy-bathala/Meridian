"""The ground beneath a satellite, checked against the sky above that ground.

A sub-satellite point is where the satellite is straight overhead. So a site
placed at each point ``ground_track`` returns must see the satellite at an
elevation of 90° at that instant — a check that uses Skyfield's look angles,
already tested against a published worked example, as the authority for the
new method, rather than testing the method against itself.

And the export's frozen ground tracks: one per pass with a report, simulated
flagged, samples every 30 s over the pass's window.

Reference: docs/DECISIONS.md D-158, D-230.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meridian.datasets.ground_tracks import GroundRows, compute_ground_tracks
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.orbit.types import ElementSet, GroundSite

LINE1 = "1 25544U 98067A   14020.93268519  .00009878  00000-0  18200-3 0  5082"
LINE2 = "2 25544  51.6498 109.4756 0003572  55.9686 274.8005 15.49815350868473"
T0 = datetime(2014, 1, 21, 22, 23, 4, tzinfo=UTC)


@pytest.fixture(scope="module")
def service() -> SkyfieldOrbitService:
    return SkyfieldOrbitService()


@pytest.fixture
def iss() -> ElementSet:
    return ElementSet(satellite_id="norad:25544", epoch=T0, line1=LINE1, line2=LINE2)


def test_the_satellite_is_overhead_at_every_point_of_its_ground_track(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    track = service.ground_track(iss, T0, T0 + timedelta(minutes=5), step_s=60.0)
    assert len(track) == 5
    for point in track:
        below = GroundSite(lat_deg=point.lat_deg, lon_deg=point.lon_deg, alt_m=0.0)
        angle = service.look_angles(
            iss, below, point.t, point.t + timedelta(seconds=1), step_s=1.0
        )[0]
        assert angle.elevation_deg == pytest.approx(90.0, abs=0.05)


def test_a_track_stays_inside_the_orbits_inclination(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    track = service.ground_track(iss, T0, T0 + timedelta(minutes=95), step_s=60.0)
    assert max(abs(one.lat_deg) for one in track) <= 51.65 + 0.2
    assert all(-180.0 <= one.lon_deg <= 180.0 for one in track)


def test_a_non_positive_step_is_refused(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    with pytest.raises(ValueError, match="step_s"):
        service.ground_track(iss, T0, T0 + timedelta(minutes=1), step_s=0.0)


def test_export_freezes_a_track_for_each_reported_pass_and_flags_simulated(
    service: SkyfieldOrbitService,
) -> None:
    passes = [
        {
            "id": n,
            "satellite_id": "norad:25544",
            "station_id": "st",
            "aos": T0,
            "los": T0 + timedelta(minutes=2),
            "element_set_id": 1,
            "simulated": n == 3,
        }
        for n in (1, 2, 3)
    ]
    rows = GroundRows(
        passes=passes,
        assignments=[{"assignment_id": f"as_{n}", "pass_id": n} for n in (1, 2, 3)],
        observations=[{"assignment_id": "as_1"}, {"assignment_id": "as_3"}],
        element_sets=[
            {
                "id": 1,
                "satellite_id": "norad:25544",
                "epoch": T0,
                "line1": LINE1,
                "line2": LINE2,
            }
        ],
    )
    tracks = compute_ground_tracks(rows, service)
    assert [one["pass_id"] for one in tracks.rows] == [1, 3]
    assert [one["simulated"] for one in tracks.rows] == [False, True]
    assert len(tracks.rows[0]["lat_deg"]) == 4  # type: ignore[arg-type]
    assert tracks.counts["pass_ground_tracks"] == 2
