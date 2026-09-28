"""The passes a run decides, described as the live scorer reads them (D-169).

The claim that matters is that a track computed live is the track export froze
for training (D-158): the same propagator, the same step, the same rounding and
folding. It is checked by computing one pass both ways with the real
propagator. Then how a rise is read from the stored predictions (D-148).

Reference: docs/DECISIONS.md D-148, D-158, D-169.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from meridian.datasets.pass_tracks import TRACK_STEP_S as EXPORT_STEP_S
from meridian.datasets.pass_tracks import TrackRows, compute_pass_tracks
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.orbit.types import ElementSet, GroundSite
from meridian.scheduler.live_inputs import TRACK_STEP_S, live_inputs, rises, track_of
from meridian.store.passes import StoredPass
from meridian.store.receiving_stations import ReceivingStation

ISS_LINE1 = "1 25544U 98067A   14020.93268519  .00009878  00000-0  18200-3 0  5082"
ISS_LINE2 = "2 25544  51.6498 109.4756 0003572  55.9686 274.8005 15.49815350868473"
ISS_EPOCH = datetime(2014, 1, 20, 22, 23, 4, tzinfo=UTC)
ISS = ElementSet(
    satellite_id="norad:25544",
    epoch=ISS_EPOCH,
    line1=ISS_LINE1,
    line2=ISS_LINE2,
    source="manual",
)
STATION = ReceivingStation(
    station_id="st_a", lat_deg=40.8939, lon_deg=-83.8917, alt_m=0.0, simulated=False
)
AOS = datetime(2014, 1, 23, 6, 0, tzinfo=UTC)


def stored(
    pass_id: int,
    *,
    minute: float = 0.0,
    minutes: float = 10.0,
    satellite_id: str = "norad:25544",
    simulated: bool = False,
) -> StoredPass:
    aos = AOS + timedelta(minutes=minute)
    return StoredPass(
        id=pass_id,
        satellite_id=satellite_id,
        station_id="st_a",
        aos=aos,
        los=aos + timedelta(minutes=minutes),
        max_elevation_deg=40.0,
        max_elevation_at=aos + timedelta(minutes=minutes / 2),
        aos_azimuth_deg=200.0,
        los_azimuth_deg=20.0,
        element_set_id=1,
        min_elevation_deg=10.0,
        computed_at=aos - timedelta(hours=3),
        simulated=simulated,
    )


# --- the track export froze ---------------------------------------------------


def test_the_step_is_the_export_s() -> None:
    assert TRACK_STEP_S == EXPORT_STEP_S


def test_a_live_track_is_the_track_export_freezes_for_the_same_pass() -> None:
    orbit = SkyfieldOrbitService()
    one = stored(7)
    frozen = compute_pass_tracks(
        TrackRows(
            passes=[
                {
                    "id": 7,
                    "station_id": "st_a",
                    "aos": one.aos,
                    "los": one.los,
                    "element_set_id": 1,
                    "simulated": False,
                }
            ],
            stations=[
                {
                    "station_id": "st_a",
                    "lat_deg": STATION.lat_deg,
                    "lon_deg": STATION.lon_deg,
                    "alt_m": STATION.alt_m,
                }
            ],
            element_sets=[
                {
                    "id": 1,
                    "satellite_id": ISS.satellite_id,
                    "epoch": ISS.epoch,
                    "line1": ISS.line1,
                    "line2": ISS.line2,
                }
            ],
        ),
        orbit,
    ).rows[0]

    live = track_of(orbit, ISS, GroundSite(40.8939, -83.8917, 0.0), one)

    assert live is not None
    assert len(live.azimuth_deg) == 20
    assert (live.start, live.step_s) == (frozen["start"], frozen["step_s"])
    assert list(live.azimuth_deg) == frozen["azimuth_deg"]
    assert list(live.elevation_deg) == frozen["elevation_deg"]


def test_a_simulated_pass_has_no_track_as_export_gives_it_none() -> None:
    site = GroundSite(40.8939, -83.8917, 0.0)

    assert (
        track_of(SkyfieldOrbitService(), ISS, site, stored(7, simulated=True)) is None
    )


# --- a rise -------------------------------------------------------------------


def test_predictions_whose_windows_overlap_are_one_rise_transitively() -> None:
    """3 overlaps 1 and 2 overlaps 3, so all three are one rise, though 1 and 2
    do not overlap; 4 starts as the last one ends, and is another."""
    found = rises(
        [
            stored(1, minute=0, minutes=5),
            stored(3, minute=4, minutes=5),
            stored(2, minute=8, minutes=5),
            stored(4, minute=13, minutes=5),
        ]
    )

    assert found == {1: (1, 2, 3), 2: (1, 2, 3), 3: (1, 2, 3), 4: (4,)}


def test_two_satellites_are_never_one_rise() -> None:
    found = rises([stored(1), stored(2, satellite_id="norad:57166")])

    assert found == {1: (1,), 2: (2,)}


def test_the_scorer_is_given_every_prediction_of_a_candidate_s_rise() -> None:
    """The divergence feature reads them all; only the candidate is scored."""
    held = {
        one.id: one for one in (stored(1), stored(2, minute=0.4), stored(3, minute=90))
    }
    asked: list[int] = []

    def element_set_for(element_set_id: int) -> ElementSet:
        asked.append(element_set_id)
        return ISS

    passes, geometry = live_inputs(
        SkyfieldOrbitService(), STATION, held, [2], element_set_for
    )

    (one,) = passes
    assert (one.pass_id, one.pass_ids) == (2, (1, 2))
    assert sorted(geometry) == [1, 2]
    assert geometry[1].aos == AOS
    assert geometry[2].element_set_epoch == ISS_EPOCH
    assert geometry[2].track is not None
    assert asked == [1, 1]
