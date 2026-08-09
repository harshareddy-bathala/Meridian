"""``SkyfieldOrbitService.pass_windows`` against Skyfield's own event finder.

Our search brackets the horizon crossing coarsely and bisects it.
``EarthSatellite.find_events`` reaches the same answer a different way, so
agreement is evidence about our search rather than a statement it makes about
itself. That is why the expected values here are computed by the reference at
test time instead of transcribed: the point is the *comparison*, and a
transcribed number would only pin today's answer in place.

*What this proves and what it does not.* Both sides share one propagator, so a
fault inside Skyfield's own TEME-to-Earth-fixed rotation appears in both and
this file stays green — the same limit ``test_look_angles_reference.py`` states
about itself. What it does prove is the part we wrote: the bracketing, the
refinement, the elevation floor, and the rule about which scan a pass belongs to.

The element set and site below are the ones transcribed in
``test_look_angles_reference.py``, with their source and licence. They are
repeated rather than shared so this file reads top to bottom on its own.

Reference: Skyfield documentation, "Earth Satellites",
https://rhodesmill.org/skyfield/earth-satellites.html · retrieved 2026-08-08 ·
MIT. See also ATTRIBUTION.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

# TID251 confines skyfield to meridian.orbit so the platform depends on the
# OrbitService interface rather than on a propagator. This file is the exception
# that proves our implementation right: it has to reach the library directly,
# because the library's independent answer is the whole point of the comparison.
from skyfield.api import EarthSatellite, load, wgs84  # noqa: TID251

from meridian.orbit import skyfield_service
from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.orbit.types import ElementSet, GroundSite, PassSearch

ISS_LINE1 = "1 25544U 98067A   14020.93268519  .00009878  00000-0  18200-3 0  5082"
ISS_LINE2 = "2 25544  51.6498 109.4756 0003572  55.9686 274.8005 15.49815350868473"

BLUFFTON = GroundSite(lat_deg=+40.8939, lon_deg=-83.8917, alt_m=0.0)
WINDOW_START = datetime(2014, 1, 23, 4, 0, tzinfo=UTC)
WINDOW_END = datetime(2014, 1, 23, 10, 0, tzinfo=UTC)

EXPECTED_PASS_COUNT = 4
"""Rises the reference reports over this window at the geometric horizon.

Asserted rather than merely derived from the reference, so a change that made
both sides agree on the *wrong* set of passes would still fail here."""

SPLIT = datetime(2014, 1, 23, 6, 25, tzinfo=UTC)
"""An instant inside the second pass, which runs 06:21:36 to 06:32:21.

Chosen to fall mid-pass on purpose: splitting the window here is what makes the
boundary rule testable at all."""

AGREEMENT_S = 0.5
"""Bound on how far the two searches may disagree on a boundary.

Measured at 0.14 s at worst across these four passes — the sum of our 0.05 s
refinement tolerance and the reference's own. Half a second is roughly three
times that, and still hundreds of times smaller than any real fault: a mistaken
bracket or a frame error moves a pass by minutes, not by fractions of a second.
"""

EVENT_SEARCH_PAD = timedelta(hours=1)
"""Widens the reference search so a pass rising just inside the window is still
followed by the culmination and set that complete it."""

RISE, CULMINATE, SET = 0, 1, 2
"""``find_events`` event codes, in the order it documents them."""


def reference_passes(
    start: datetime, end: datetime, floor_deg: float
) -> list[tuple[datetime, datetime, datetime]]:
    """Rise, culmination and set per pass, from Skyfield's own event finder.

    Keyed on the rise the same way ``pass_windows`` is, so the two lists line up
    pass for pass.
    """
    timescale = load.timescale(builtin=True)
    satellite = EarthSatellite(ISS_LINE1, ISS_LINE2, "ISS", timescale)
    topos = wgs84.latlon(BLUFFTON.lat_deg, BLUFFTON.lon_deg, elevation_m=BLUFFTON.alt_m)
    times, events = satellite.find_events(
        topos,
        timescale.from_datetime(start - EVENT_SEARCH_PAD),
        timescale.from_datetime(end + EVENT_SEARCH_PAD),
        altitude_degrees=floor_deg,
    )

    grouped: list[tuple[datetime, datetime, datetime]] = []
    pending: list[datetime] = []
    for t, event in zip(times, events, strict=True):
        if event == RISE:
            pending = [t.utc_datetime()]
        elif pending:
            pending.append(t.utc_datetime())
            if len(pending) == SET + 1:
                grouped.append((pending[RISE], pending[CULMINATE], pending[SET]))
                pending = []
    return [pass_ for pass_ in grouped if start <= pass_[RISE] < end]


@pytest.fixture(scope="module")
def service() -> SkyfieldOrbitService:
    """One service for the file — building a timescale parses a leap-second table."""
    return SkyfieldOrbitService()


@pytest.fixture
def iss() -> ElementSet:
    return ElementSet(
        satellite_id="norad:25544",
        epoch=datetime(2014, 1, 20, 22, 23, 4, tzinfo=UTC),
        line1=ISS_LINE1,
        line2=ISS_LINE2,
        source="manual",
    )


def search_over(
    iss: ElementSet, start: datetime, end: datetime, floor_deg: float = 0.0
) -> PassSearch:
    """A search of the reference site over ``[start, end)``."""
    return PassSearch(iss, BLUFFTON, start, end, min_elevation_deg=floor_deg)


def test_every_boundary_agrees_with_skyfields_own_event_finder(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """Acquisition, culmination and loss, pass for pass, by two algorithms."""
    ours = service.pass_windows(search_over(iss, WINDOW_START, WINDOW_END))
    theirs = reference_passes(WINDOW_START, WINDOW_END, 0.0)

    assert len(ours) == EXPECTED_PASS_COUNT
    assert len(theirs) == EXPECTED_PASS_COUNT
    for window, (rise, culminate, loss) in zip(ours, theirs, strict=True):
        assert abs((window.aos - rise).total_seconds()) < AGREEMENT_S
        assert abs((window.los - loss).total_seconds()) < AGREEMENT_S
        assert abs((window.max_elevation_at - culminate).total_seconds()) < AGREEMENT_S


def test_the_reported_peak_is_the_elevation_at_the_reported_instant(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """``max_elevation_deg`` and ``max_elevation_at`` have to describe one moment.

    Checked through ``look_angles``, which reaches elevation by a different path
    than the search does, so a window that named the right instant and carried
    the elevation of some other sample would fail here.
    """
    window = service.pass_windows(search_over(iss, WINDOW_START, WINDOW_END))[1]

    at_the_peak = service.look_angles(
        iss,
        BLUFFTON,
        window.max_elevation_at,
        window.max_elevation_at + timedelta(seconds=1),
        step_s=1.0,
    )
    assert at_the_peak[0].elevation_deg == pytest.approx(
        window.max_elevation_deg, abs=1e-6
    )


def test_a_raised_floor_returns_a_subset_of_the_passes(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """No pass may appear at ten degrees that was absent at the horizon.

    One of these four peaks at 5.1°, so raising the floor must drop it and leave
    the rest. A search that returned *more* passes at a stricter floor would be
    finding spurious crossings, which coarse sampling can do if the bracketing
    is wrong.
    """
    at_horizon = service.pass_windows(search_over(iss, WINDOW_START, WINDOW_END))
    at_ten = service.pass_windows(
        search_over(iss, WINDOW_START, WINDOW_END, floor_deg=10.0)
    )

    assert len(at_ten) < len(at_horizon)
    assert all(window.min_elevation_deg == 10.0 for window in at_ten)
    for window in at_ten:
        assert any(
            abs((window.aos - horizon_window.aos).total_seconds()) < 300.0
            for horizon_window in at_horizon
        )


def test_the_floor_is_carried_on_every_window(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """A window without its floor cannot be compared with one found at another."""
    windows = service.pass_windows(search_over(iss, WINDOW_START, WINDOW_END))
    assert all(window.min_elevation_deg == 0.0 for window in windows)


def test_element_set_age_is_measured_at_acquisition(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """Age is the feature every timing error is later attributed to.

    Measured at AOS rather than at the search start, so two passes found by one
    search carry different ages — which is the whole reason the field exists.
    """
    windows = service.pass_windows(search_over(iss, WINDOW_START, WINDOW_END))

    for window in windows:
        assert window.element_set_age_s == pytest.approx(
            (window.aos - iss.epoch).total_seconds()
        )
        assert window.element_set_epoch == iss.epoch
    assert windows[0].element_set_age_s < windows[-1].element_set_age_s


def test_two_adjacent_searches_partition_the_passes_between_them(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """The boundary rule, which is the reason the search looks past its interval.

    ``SPLIT`` falls in the middle of the second pass. That pass belongs to the
    earlier search, because it *rose* there — and it comes back whole, with a
    loss of signal after the split, rather than clipped at it. The later search
    must not report it again.
    """
    whole = service.pass_windows(search_over(iss, WINDOW_START, WINDOW_END))
    earlier = service.pass_windows(search_over(iss, WINDOW_START, SPLIT))
    later = service.pass_windows(search_over(iss, SPLIT, WINDOW_END))

    assert len(earlier) + len(later) == len(whole)
    for window, undivided in zip(earlier + later, whole, strict=True):
        assert abs((window.aos - undivided.aos).total_seconds()) < AGREEMENT_S

    straddling = earlier[-1]
    assert straddling.aos < SPLIT < straddling.los
    assert all(window.aos > SPLIT for window in later)


def test_a_pass_rising_exactly_at_the_interval_end_is_left_to_the_next_search(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """The interval is half-open, and this is the only seam where that shows.

    A real pass never rises at a round instant, so the coincidence is built
    rather than waited for: the acquisition of the second pass is used as the
    ``end`` of a new search. Because the coarse grid is aligned to a fixed
    anchor rather than to the horizon, that search refines the same bracket and
    computes the identical instant rather than one a hair away.

    An inclusive end would return that pass here *and* in the search beginning
    at the same instant, so a rolling pass-generation job would double-count
    every pass it happened to split on.
    """
    seam = service.pass_windows(search_over(iss, WINDOW_START, WINDOW_END))[1].aos

    before_the_seam = service.pass_windows(search_over(iss, WINDOW_START, seam))

    assert len(before_the_seam) == 1
    assert all(window.aos < seam for window in before_the_seam)


def test_one_pass_gets_one_acquisition_whatever_horizon_found_it(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """Identity, to the microsecond, across horizons that do not line up.

    A pass-generation job runs over a rolling horizon, so the same physical pass
    is recomputed by searches beginning at unrelated instants. Before the coarse
    grid was aligned to a fixed anchor, shifting the horizon by seven seconds
    moved every acquisition by about two milliseconds — enough that no two runs
    ever agreed, and a job keyed on acquisition would have stored a fresh copy
    of every pass on every run while looking perfectly correct.

    Equality is asserted, not approximation: near-equality is exactly the state
    that was wrong, and a tolerance here would have accepted it.
    """
    horizons = [
        service.pass_windows(
            search_over(iss, WINDOW_START + timedelta(seconds=shift), WINDOW_END)
        )
        for shift in (0, 7, 13, 17)
    ]

    reference = horizons[0]
    assert len(reference) == EXPECTED_PASS_COUNT
    for shifted in horizons[1:]:
        assert [window.aos for window in shifted] == [
            window.aos for window in reference
        ]
        assert [window.los for window in shifted] == [
            window.los for window in reference
        ]


def test_a_pass_outlasting_the_search_margin_raises_rather_than_being_clipped(
    service: SkyfieldOrbitService, iss: ElementSet, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard on :data:`PASS_SEARCH_MARGIN_S`, with the margin made too small.

    Thirty minutes is comfortably longer than any low Earth orbit pass, so this
    cannot be provoked with a real element set — it is provoked by shrinking the
    margin to a minute, which is what the guard would face on an orbit high
    enough to outlast it. The alternative to raising is a window that ends when
    the search stopped looking rather than when the satellite set, and nothing
    downstream could tell the two apart.
    """
    monkeypatch.setattr(skyfield_service, "PASS_SEARCH_MARGIN_S", 60.0)

    with pytest.raises(ValueError, match="outlasts"):
        service.pass_windows(search_over(iss, WINDOW_START, SPLIT))


def test_a_naive_datetime_is_refused_at_this_boundary(
    service: SkyfieldOrbitService, iss: ElementSet
) -> None:
    """CLAUDE.local.md section 6: a naive datetime is a bug, not a tolerance."""
    with pytest.raises(ValueError, match="naive"):
        service.pass_windows(
            search_over(iss, WINDOW_START.replace(tzinfo=None), WINDOW_END)
        )
