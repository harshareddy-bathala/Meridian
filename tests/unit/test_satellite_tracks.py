"""The live page's ground tracks, built from a fake propagator (D-361).

``build_tracks`` is what ``/api/v1/satellites/tracks`` serves. Here it is fed a
propagator that returns known points, so what is checked is the body's rules:
catalogue order, rounding, element-set age against the response's instant, and
every satellite without a track named with its reason, never dropped.

The real propagator is exercised against a database in
``tests/integration/test_public_tracks_endpoint.py``.

Reference: docs/DECISIONS.md D-086, D-361.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from meridian.api.public import tracks
from meridian.api.public.tracks import HORIZON_S, STEP_S, build_tracks
from meridian.orbit.types import ElementSet, SubPoint
from meridian.store.element_sets import StoredElementSet
from meridian.store.satellite_catalogue import CataloguedSatellite

AT = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)


class FakeOrbit:
    """Returns a straight line of points, or NaN for the ids it is told to fail."""

    def __init__(self, failing: frozenset[str] = frozenset()) -> None:
        self.failing = failing
        self.calls: list[tuple[str, datetime, datetime, float]] = []

    def ground_track(
        self, element_set: ElementSet, start: datetime, end: datetime, *, step_s: float
    ) -> list[SubPoint]:
        self.calls.append((element_set.satellite_id, start, end, step_s))
        count = int((end - start).total_seconds() // step_s)
        if element_set.satellite_id in self.failing:
            return [SubPoint(t=start, lat_deg=float("nan"), lon_deg=float("nan"))]
        return [
            SubPoint(
                t=start + timedelta(seconds=i * step_s),
                lat_deg=12.345678 + i,
                lon_deg=-170.001 - i,
            )
            for i in range(count)
        ]


def satellite(satellite_id: str, *, active: bool = True) -> CataloguedSatellite:
    return CataloguedSatellite(
        satellite_id=satellite_id,
        name=f"Object {satellite_id}",
        orbital_regime="leo",
        priority=1.0,
        is_active=active,
        latest_element_set_epoch=None,
    )


def stored(satellite_id: str, set_id: int = 1, age_h: float = 6) -> StoredElementSet:
    return StoredElementSet(
        id=set_id,
        satellite_id=satellite_id,
        epoch=AT - timedelta(hours=age_h),
        retrieved_at=AT,
        line1="1 ...",
        line2="2 ...",
        source="celestrak",
        content_sha256=b"\x00",
    )


def test_a_track_runs_from_the_minute_through_the_horizon() -> None:
    orbit = FakeOrbit()
    body = build_tracks(
        orbit, [satellite("norad:1")], {"norad:1": stored("norad:1")}, AT
    )

    (track,) = body.satellites
    assert body.status == "computed"
    assert (body.at, body.step_s, body.horizon_s) == (AT, STEP_S, HORIZON_S)
    assert len(track.track) == HORIZON_S // STEP_S + 1
    assert track.track[0].t == AT
    assert track.track[-1].t == AT + timedelta(seconds=HORIZON_S)


def test_points_are_published_to_a_hundredth_of_a_degree() -> None:
    body = build_tracks(
        FakeOrbit(), [satellite("norad:1")], {"norad:1": stored("norad:1")}, AT
    )

    first = body.satellites[0].track[0]
    assert (first.lat_deg, first.lon_deg) == (12.35, -170.0)


def test_a_track_carries_its_element_sets_age_source_and_activity() -> None:
    body = build_tracks(
        FakeOrbit(),
        [satellite("norad:1", active=False)],
        {"norad:1": stored("norad:1", age_h=30)},
        AT,
    )

    track = body.satellites[0]
    assert track.element_set_age_s == 30 * 3600
    assert track.element_set_epoch == AT - timedelta(hours=30)
    assert track.element_set_source == "celestrak"
    assert track.is_active is False


def test_every_satellite_without_a_track_is_named_with_its_reason() -> None:
    body = build_tracks(
        FakeOrbit(failing=frozenset({"norad:3"})),
        [satellite("norad:1"), satellite("norad:2"), satellite("norad:3")],
        {"norad:1": stored("norad:1"), "norad:3": stored("norad:3")},
        AT,
    )

    assert [s.satellite_id for s in body.satellites] == ["norad:1"]
    assert [(o.satellite_id, o.reason) for o in body.omitted] == [
        ("norad:2", "no_element_set"),
        ("norad:3", "propagation_failed"),
    ]


def test_a_catalogue_past_the_limit_is_cut_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tracks, "MAX_SATELLITES", 2)
    ids = ["norad:1", "norad:2", "norad:3"]
    orbit = FakeOrbit()

    body = build_tracks(
        orbit, [satellite(i) for i in ids], {i: stored(i) for i in ids}, AT
    )

    assert [s.satellite_id for s in body.satellites] == ["norad:1", "norad:2"]
    assert [(o.satellite_id, o.reason) for o in body.omitted] == [
        ("norad:3", "over_limit")
    ]
    assert len(orbit.calls) == 2


def test_one_minutes_answer_is_computed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    orbit = FakeOrbit()
    cache = tracks._MinuteCache()
    monkeypatch.setattr(cache, "_orbit", orbit)
    catalogue = [satellite("norad:1")]

    first = cache.body(catalogue, {"norad:1": stored("norad:1")}, AT)
    again = cache.body(catalogue, {"norad:1": stored("norad:1")}, AT)

    assert again is first
    assert len(orbit.calls) == 1


@pytest.mark.parametrize(
    ("catalogue", "sets", "at"),
    [
        ([satellite("norad:1")], {"norad:1": stored("norad:1", set_id=2)}, AT),
        ([satellite("norad:1", active=False)], {"norad:1": stored("norad:1")}, AT),
        (
            [satellite("norad:1")],
            {"norad:1": stored("norad:1")},
            AT + timedelta(minutes=1),
        ),
    ],
    ids=["a new element set", "a satellite falls silent", "the next minute"],
)
def test_anything_the_body_is_computed_from_computes_it_again(
    monkeypatch: pytest.MonkeyPatch,
    catalogue: list[CataloguedSatellite],
    sets: dict[str, StoredElementSet],
    at: datetime,
) -> None:
    orbit = FakeOrbit()
    cache = tracks._MinuteCache()
    monkeypatch.setattr(cache, "_orbit", orbit)
    cache.body([satellite("norad:1")], {"norad:1": stored("norad:1")}, AT)

    cache.body(catalogue, sets, at)

    assert len(orbit.calls) == 2
