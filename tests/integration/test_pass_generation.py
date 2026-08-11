"""``meridian.pass_generation`` against real TimescaleDB.

Marked ``integration``. The job's decisions — which station is propagated for
which satellite, at which floor, from which element set — are checked against a
stub propagator that records what it was asked, so a failure names the decision
that changed rather than a pass time that moved by a second. One test at the end
runs the real propagator, because a job that only ever ran against a stub has
not been shown to work.

Idempotence needs the real unique constraint, which is why these are integration
tests and not unit tests: the claim "running it twice changes nothing" is a
claim about the database, not about Python.

Uses the ``rollback`` fixture pattern established in ``test_store_invites.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.orbit.skyfield_service import SkyfieldOrbitService  # noqa: E402
from meridian.orbit.types import PassSearch, PassWindow  # noqa: E402
from meridian.pass_generation import (  # noqa: E402
    GenerationHorizon,
    generate_passes,
)
from meridian.store.passes import find_passes_in_horizon  # noqa: E402

pytestmark = pytest.mark.integration

VHF_STATION = "st_vhf"
UHF_STATION = "st_uhf"
SIMULATED_STATION = "st_sim"

VHF_SATELLITE = "norad:40069"
"""Carries a 137.1 MHz LRPT downlink — receivable by the VHF station only."""

UHF_SATELLITE = "norad:99999"
"""Carries a 437.5 MHz FSK downlink — receivable by the UHF station only."""

METEOR_LRPT_HZ = 137_100_000
UHF_BEACON_HZ = 437_500_000

HORIZON_START = datetime(2026, 8, 14, 0, 0, 0, tzinfo=UTC)
HORIZON = GenerationHorizon(start=HORIZON_START, end=HORIZON_START + timedelta(days=1))

CURRENT_EPOCH = datetime(2026, 8, 13, 2, 11, 0, tzinfo=UTC)
"""Before the horizon opens, so this is the set current at its start."""

FUTURE_EPOCH = datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC)
"""Inside the horizon. Newer, and deliberately not the one that should be used —
a pass predicted for a past horizon must use the set that was current then."""

LINE1 = "1 40069U 14037A   26226.09270833  .00000123  00000-0  76543-4 0  9991"
LINE2 = "2 40069  98.6021 213.4109 0005678  12.3456 347.7890 14.20800000123456"


def a_credential_hash(seed: int) -> bytes:
    """A distinct 32-byte hash per station — ``token_sha256`` is unique."""
    return bytes([seed]) * 32


class RecordingOrbitService:
    """An ``OrbitService`` that records its searches and invents one pass each.

    Substituted for the propagator so the job's *decisions* can be asserted
    directly. Real geometry would answer the same questions, but a test reading
    "this station was propagated for that satellite at that floor" fails with
    the reason attached, where one reading "expected 4 passes, got 3" does not.
    """

    def __init__(self) -> None:
        self.searches: list[PassSearch] = []

    def pass_windows(self, search: PassSearch) -> list[PassWindow]:
        """One window per search, at a time derived from the search itself."""
        self.searches.append(search)
        aos = search.start + timedelta(hours=1)
        return [
            PassWindow(
                satellite_id=search.element_set.satellite_id,
                aos=aos,
                los=aos + timedelta(minutes=11),
                max_elevation_deg=42.0,
                max_elevation_at=aos + timedelta(minutes=5, seconds=30),
                aos_azimuth_deg=13.0,
                los_azimuth_deg=197.0,
                element_set_epoch=search.element_set.epoch,
                element_set_age_s=search.element_set.age_s(aos),
                min_elevation_deg=search.min_elevation_deg,
            )
        ]

    def propagated_pairs(self) -> set[tuple[float, float, str]]:
        """Each search as (latitude, longitude, satellite) — one per pair.

        Keyed on the site rather than the station id because a ``PassSearch``
        carries a ``GroundSite``, not a station; the fixture gives each station a
        distinct latitude so the two are interchangeable here.
        """
        return {
            (search.site.lat_deg, search.site.lon_deg, search.element_set.satellite_id)
            for search in self.searches
        }


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_invites.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


def _insert_station(cur: Any, station_id: str, *, seed: int, lat_deg: float) -> None:
    """One station at its own latitude, so searches can be told apart."""
    cur.execute(
        "insert into stations (station_id, name, operator, lat_deg, lon_deg,"
        " alt_m, token_sha256, registration_key_sha256)"
        " values (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            station_id,
            "Test",
            "tests",
            lat_deg,
            0.0,
            0.0,
            a_credential_hash(seed),
            a_credential_hash(seed + 100),
        ),
    )


def _insert_capability(
    cur: Any,
    station_id: str,
    *,
    freq_range_hz: tuple[int, int],
    modes: list[str],
    min_elevation_deg: float,
) -> None:
    """One receiving chain for a station."""
    cur.execute(
        "insert into station_capabilities (station_id, band, freq_min_hz,"
        " freq_max_hz, modes, polarisation, min_elevation_deg)"
        " values (%s, %s, %s, %s, %s, %s, %s)",
        (
            station_id,
            "vhf",
            freq_range_hz[0],
            freq_range_hz[1],
            modes,
            "rhcp",
            min_elevation_deg,
        ),
    )


def _insert_satellite(cur: Any, satellite_id: str, *, freq_hz: int, mode: str) -> None:
    """One satellite with one live downlink."""
    cur.execute(
        "insert into satellites (satellite_id, name) values (%s, %s)",
        (satellite_id, satellite_id),
    )
    cur.execute(
        "insert into satellite_transmitters (satellite_id, centre_freq_hz, mode)"
        " values (%s, %s, %s)",
        (satellite_id, freq_hz, mode),
    )


def _insert_element_set(
    cur: Any, satellite_id: str, epoch: datetime, *, line2: str = LINE2
) -> int:
    """One archived element set, returning its id.

    ``line2`` is a parameter because ``element_set_content_unique`` keys on the
    *contents* (D-057): a second set for the same satellite has to differ in its
    lines, not merely in its epoch, or the insert collapses onto the first and
    the test silently asserts nothing.
    """
    cur.execute(
        "insert into element_sets (satellite_id, epoch, line1, line2, source)"
        " values (%s, %s, %s, %s, %s) returning id",
        (satellite_id, epoch, LINE1, line2, "manual"),
    )
    (element_set_id,) = cur.fetchone()
    return int(element_set_id)


REFITTED_LINE2 = LINE2.replace("14.20800000", "14.20900000")
"""The same object with a slightly different mean motion — a second, genuinely
different set for one satellite, which is what D-057 keeps as its own row."""


@pytest.fixture
def network(rollback: Any) -> dict[str, int]:
    """Two stations on different bands, two satellites, one set each.

    Deliberately crossed: the VHF station can only receive the VHF satellite and
    the UHF station only the UHF one, so a job that ignored capabilities would
    propagate four pairs where three of the four possibilities are wrong.
    """
    with rollback.cursor() as cur:
        _insert_station(cur, VHF_STATION, seed=1, lat_deg=51.0)
        _insert_capability(
            cur,
            VHF_STATION,
            freq_range_hz=(136_000_000, 138_000_000),
            modes=["lrpt"],
            min_elevation_deg=10.0,
        )

        _insert_station(cur, UHF_STATION, seed=2, lat_deg=52.0)
        _insert_capability(
            cur,
            UHF_STATION,
            freq_range_hz=(430_000_000, 440_000_000),
            modes=["fsk"],
            min_elevation_deg=5.0,
        )

        _insert_satellite(cur, VHF_SATELLITE, freq_hz=METEOR_LRPT_HZ, mode="lrpt")
        _insert_satellite(cur, UHF_SATELLITE, freq_hz=UHF_BEACON_HZ, mode="fsk")

        return {
            VHF_SATELLITE: _insert_element_set(cur, VHF_SATELLITE, CURRENT_EPOCH),
            UHF_SATELLITE: _insert_element_set(cur, UHF_SATELLITE, CURRENT_EPOCH),
        }


# --- which pairs are propagated at all ----------------------------------------


def test_a_station_is_propagated_only_for_satellites_it_can_receive(
    rollback: Any, network: dict[str, int]
) -> None:
    """The capability filter, asserted in both directions.

    Four station-satellite combinations exist and two are receivable. Asserting
    the propagated set exactly — rather than only that the mismatches are absent
    — is what makes this fail if the filter is inverted rather than dropped.
    """
    assert network  # the fixture seeded the catalogue
    orbit = RecordingOrbitService()

    report = generate_passes(rollback, orbit, HORIZON)

    assert orbit.propagated_pairs() == {
        (51.0, 0.0, VHF_SATELLITE),
        (52.0, 0.0, UHF_SATELLITE),
    }
    assert report.stations_considered == 2
    assert report.pairs_propagated == 2
    assert report.passes_computed == 2


def test_each_search_uses_that_stations_own_declared_floor(
    rollback: Any, network: dict[str, int]
) -> None:
    """A floor is per station, not a platform-wide constant.

    The two stations declared 10 and 5 degrees. A job that searched both at one
    floor would still produce plausible passes, and the error would only surface
    much later as a completeness ratio computed over windows that were never
    comparable.
    """
    assert network
    orbit = RecordingOrbitService()

    generate_passes(rollback, orbit, HORIZON)

    floors_by_latitude = {
        search.site.lat_deg: search.min_elevation_deg for search in orbit.searches
    }
    assert floors_by_latitude == {51.0: 10.0, 52.0: 5.0}


def test_a_station_whose_token_was_revoked_is_not_propagated(
    rollback: Any, network: dict[str, int]
) -> None:
    """A revoked station can never authenticate, so it can never take a pass.

    Counting its passes would pad EVALUATION.md §4.1's denominator with
    opportunities nothing could have acted on, and every completeness figure
    would inherit the padding.
    """
    assert network
    with rollback.cursor() as cur:
        cur.execute(
            "update stations set token_revoked_at = now() where station_id = %s",
            (VHF_STATION,),
        )

    orbit = RecordingOrbitService()
    report = generate_passes(rollback, orbit, HORIZON)

    assert report.stations_considered == 1
    assert orbit.propagated_pairs() == {(52.0, 0.0, UHF_SATELLITE)}


def test_an_inactive_transmitter_takes_its_satellite_out_of_the_run(
    rollback: Any, network: dict[str, int]
) -> None:
    """EVALUATION.md §5's silent-satellite confound, kept out at the source.

    A pass computed for a transmitter known to be switched off is an opportunity
    nobody could ever have taken, and it enters the denominator looking exactly
    like one that was real.
    """
    assert network
    with rollback.cursor() as cur:
        cur.execute(
            "update satellite_transmitters set active = false where satellite_id = %s",
            (VHF_SATELLITE,),
        )

    orbit = RecordingOrbitService()
    report = generate_passes(rollback, orbit, HORIZON)

    assert report.pairs_propagated == 1
    assert orbit.propagated_pairs() == {(52.0, 0.0, UHF_SATELLITE)}


# --- which element set, and what gets stored ----------------------------------


def test_the_element_set_current_at_the_horizon_start_is_used(
    rollback: Any, network: dict[str, int]
) -> None:
    """Not simply the newest — the one that was current when the horizon opened.

    A newer set exists with an epoch inside the horizon. Using it would change
    the job's answer every time a catalogue published, and a re-run over a past
    horizon would attribute its timing error to elements that did not exist yet.
    """
    with rollback.cursor() as cur:
        newer = _insert_element_set(
            cur, VHF_SATELLITE, FUTURE_EPOCH, line2=REFITTED_LINE2
        )

    generate_passes(rollback, RecordingOrbitService(), HORIZON)

    stored = find_passes_in_horizon(rollback, VHF_STATION, HORIZON.start, HORIZON.end)
    assert [one.element_set_id for one in stored] == [network[VHF_SATELLITE]]
    assert newer not in {one.element_set_id for one in stored}


def test_a_satellite_the_archive_cannot_place_is_named_in_the_report(
    rollback: Any, network: dict[str, int]
) -> None:
    """Reported, not skipped in silence.

    The object is tracked and its transmitter is live, so producing no passes
    for it is a gap in the archive an operator can close — and one that looks
    identical to "this satellite never rises" if the job says nothing.
    """
    with rollback.cursor() as cur:
        cur.execute("delete from element_sets where id = %s", (network[UHF_SATELLITE],))

    report = generate_passes(rollback, RecordingOrbitService(), HORIZON)

    assert report.satellites_without_element_set == (UHF_SATELLITE,)
    assert report.pairs_propagated == 1


def test_a_pass_computed_for_a_simulated_station_is_stored_as_simulated(
    rollback: Any, network: dict[str, int]
) -> None:
    """CLAUDE.md rule 5, at the one layer where the flag is first written.

    The station's registration record is the only admissible source (D-013).
    There is no payload here to take it from, which is exactly why this is worth
    pinning: nothing would fail loudly if the job defaulted it to false, and
    every simulated pass would then be indistinguishable from a measured one.
    """
    assert network
    with rollback.cursor() as cur:
        _insert_station(cur, SIMULATED_STATION, seed=3, lat_deg=53.0)
        cur.execute(
            "update stations set simulated = true, simulator_run_id = %s, seed = %s"
            " where station_id = %s",
            ("run-stage7", 4471, SIMULATED_STATION),
        )
        _insert_capability(
            cur,
            SIMULATED_STATION,
            freq_range_hz=(136_000_000, 138_000_000),
            modes=["lrpt"],
            min_elevation_deg=10.0,
        )

    generate_passes(rollback, RecordingOrbitService(), HORIZON)

    simulated = find_passes_in_horizon(
        rollback, SIMULATED_STATION, HORIZON.start, HORIZON.end
    )
    real = find_passes_in_horizon(rollback, VHF_STATION, HORIZON.start, HORIZON.end)

    assert [one.simulated for one in simulated] == [True]
    assert [one.simulated for one in real] == [False]


# --- running it again ---------------------------------------------------------


def test_running_twice_over_one_horizon_stores_nothing_the_second_time(
    rollback: Any, network: dict[str, int]
) -> None:
    """The property that makes the job safe on a timer and after a crash.

    The second run still *computes* both passes — it does the same work — and
    the gap between ``passes_computed`` and ``passes_stored`` is how a caller
    sees that nothing changed. A silent duplicate would be far worse than a
    visible one: ``passes`` is the completeness denominator, so storing every
    pass twice halves every ratio published from it.
    """
    assert network
    first = generate_passes(rollback, RecordingOrbitService(), HORIZON)
    second = generate_passes(rollback, RecordingOrbitService(), HORIZON)

    assert first.passes_stored == 2
    assert second.passes_computed == 2
    assert second.passes_stored == 0


def test_a_newer_element_set_writes_a_second_prediction(
    rollback: Any, network: dict[str, int]
) -> None:
    """D-063's other half: better elements are a new prediction, not an update.

    The archive keeps both, so the difference between the two is a measurement —
    which is what makes element-set age usable as a feature rather than an
    assertion.
    """
    assert network
    generate_passes(rollback, RecordingOrbitService(), HORIZON)

    with rollback.cursor() as cur:
        _insert_element_set(
            cur,
            VHF_SATELLITE,
            HORIZON_START - timedelta(hours=1),
            line2=REFITTED_LINE2,
        )

    report = generate_passes(rollback, RecordingOrbitService(), HORIZON)

    assert report.passes_stored == 1
    stored = find_passes_in_horizon(rollback, VHF_STATION, HORIZON.start, HORIZON.end)
    assert len({one.element_set_id for one in stored}) == 2


# --- the boundary, and the real propagator ------------------------------------


def test_a_naive_horizon_is_refused_before_anything_is_read(
    rollback: Any, network: dict[str, int]
) -> None:
    """CLAUDE.local.md §6: a naive datetime is a bug, not a tolerance."""
    assert network
    naive = GenerationHorizon(start=HORIZON.start.replace(tzinfo=None), end=HORIZON.end)

    with pytest.raises(ValueError, match="naive"):
        generate_passes(rollback, RecordingOrbitService(), naive)


def test_the_real_propagator_produces_passes_and_repeats_them_exactly(
    rollback: Any, network: dict[str, int]
) -> None:
    """The whole path, once, with real orbital geometry.

    A job only ever exercised against a stub has not been shown to work. This
    also re-proves idempotence where it is hardest: the acquisitions come from
    a bisection over propagated elevations, and they repeat to the microsecond
    only because the coarse scan grid is anchored rather than measured from the
    horizon start (D-063).
    """
    assert network
    orbit = SkyfieldOrbitService()

    first = generate_passes(rollback, orbit, HORIZON)
    second = generate_passes(rollback, orbit, HORIZON)

    assert first.passes_stored > 0
    assert second.passes_computed == first.passes_computed
    assert second.passes_stored == 0
