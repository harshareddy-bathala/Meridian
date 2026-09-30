"""``meridian_sim.sky_track`` against the platform's own orbit service.

The simulator places a pass in a station's sky with its own call into Skyfield
(D-252), because it shares no code with the platform (D-138). The two must still
agree on where the satellite was, or an obstruction the simulator injects would
sit in a different part of the sky from the one the platform's profiles and
diagnosis look at. This checks it against a pass the platform finds.

No marker: pure computation on a real element set.

Reference: docs/DECISIONS.md D-138, D-252.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.orbit.types import ElementSet, GroundSite, PassSearch
from meridian_sim.sky_track import Site, track

LINE1 = "1 57166U 23091A   26223.50000000  .00000100  00000-0  50000-4 0  9990"
LINE2 = "2 57166  98.7041 210.4322 0002726  80.4113 279.7297 14.22000000160126"
EPOCH = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
BENGALURU = (12.9716, 77.5946, 920.0)


def test_the_simulator_sees_a_pass_where_the_platform_predicts_it() -> None:
    """Rise, culmination and set, to a hundredth of a degree."""
    lat, lon, alt = BENGALURU
    orbit = SkyfieldOrbitService()
    elements = ElementSet("norad:57166", EPOCH, LINE1, LINE2)
    (first, *_) = orbit.pass_windows(
        PassSearch(
            element_set=elements,
            site=GroundSite(lat, lon, alt),
            start=EPOCH,
            end=EPOCH + timedelta(days=1),
        )
    )

    rise, peak, fall = track(
        LINE1,
        LINE2,
        Site(lat, lon, alt),
        (first.aos, first.max_elevation_at, first.los),
    )

    assert rise.elevation_deg == pytest.approx(0.0, abs=0.01)
    assert fall.elevation_deg == pytest.approx(0.0, abs=0.01)
    assert peak.elevation_deg == pytest.approx(first.max_elevation_deg, abs=0.01)
    assert rise.azimuth_deg == pytest.approx(first.aos_azimuth_deg % 360, abs=0.01)
    assert fall.azimuth_deg == pytest.approx(first.los_azimuth_deg % 360, abs=0.01)


def test_no_instants_is_no_track() -> None:
    assert track(LINE1, LINE2, Site(0.0, 0.0, 0.0), ()) == ()
