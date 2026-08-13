"""``meridian_sim.executor`` — the first thing here that produces an observation.

No marker: the executor takes no clock and touches no network, which is what
makes D-077's first assertion possible here rather than in an end-to-end test.
That assertion — two runs at one seed producing byte-identical bodies — is the
strongest determinism claim this stage makes, and it is checked against the real
`meridian_client.build_observation_body`, not a local copy of it.

Reference: docs/DECISIONS.md D-073, D-077, D-078.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from meridian_client.assignment_message import Assignment, ElementSet
from meridian_client.execution import PassExecutor
from meridian_client.observation_message import build_observation_body
from meridian_sim.config import seed_for_station
from meridian_sim.executor import SimulatedExecutor
from meridian_sim.faults import RECEIVER_DOWN, FaultState

MASTER_SEED = 4471
STATION_SEED = seed_for_station(MASTER_SEED, 1)
STATION_ID = "st_7fa3c1"

WINDOW_START = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
LINE1 = "1 57166U 23091A   26223.50000000  .00000100  00000-0  50000-4 0  9990"
LINE2 = "2 57166  98.7041 210.4322 0002726  80.4113 279.7297 14.22000000160126"


def assignment(
    assignment_id: str = "as_44b2",
    *,
    elevation_deg: float = 61.4,
    minutes: int = 11,
) -> Assignment:
    """One assignment, with the two fields these tests vary exposed."""
    return Assignment(
        assignment_id=assignment_id,
        satellite_id="norad:57166",
        start_at=WINDOW_START,
        end_at=WINDOW_START + timedelta(minutes=minutes),
        centre_freq_hz=137_100_000,
        mode="lrpt",
        expected_max_elevation_deg=elevation_deg,
        predicted_yield=None,
        element_set=ElementSet(
            epoch=WINDOW_START - timedelta(hours=7), line1=LINE1, line2=LINE2
        ),
        timing_uncertainty_s=4.2,
        priority=1.0,
    )


def run_one(seed: int, work: Assignment) -> tuple[object, ...]:
    """Drive one executor through one whole pass and drain it."""
    executor = SimulatedExecutor(seed)
    executor.begin(work)
    executor.end(work)
    return executor.take_completed()


def body_digest(seed: int, work: Assignment) -> str:
    """The wire body one pass produces, hashed."""
    (result,) = run_one(seed, work)
    body = build_observation_body(result, STATION_ID)  # type: ignore[arg-type]
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_it_satisfies_the_executor_protocol() -> None:
    """Structurally, the way the reference client's own NullExecutor does."""
    executor: PassExecutor = SimulatedExecutor(STATION_SEED)

    assert executor.take_completed() == ()


def test_two_runs_at_one_seed_produce_byte_identical_bodies() -> None:
    """D-077's first assertion, and the real claim this stage makes.

    Checked over the wire body rather than the result object, because the body
    is what the platform stores and what an analysis later reads. A result that
    compared equal while serialising differently would satisfy a weaker test and
    break reproducibility anyway.
    """
    assert body_digest(STATION_SEED, assignment()) == body_digest(
        STATION_SEED, assignment()
    )


def test_the_digest_is_the_one_this_seed_has_always_produced() -> None:
    """A regression pin, so a change to the model cannot pass unnoticed.

    Any edit to `outcomes` or to how instants are placed moves this. That is the
    point: the value is not meaningful on its own, and a run whose observations
    changed without anyone deciding to change them is exactly what determinism
    is supposed to make impossible. Update it deliberately, in the commit that
    changed the model.
    """
    assert (
        body_digest(STATION_SEED, assignment())
        == "8a360b15d7f59bd7de5837415be65a3c9d0cf053446e08a1660b40dfe964860d"
    )


def test_a_different_station_hears_a_different_pass() -> None:
    """Two stations watching one satellite must not report the same thing."""
    other = seed_for_station(MASTER_SEED, 2)

    assert body_digest(STATION_SEED, assignment()) != body_digest(other, assignment())


def test_a_station_reaches_the_same_conclusion_after_a_restart() -> None:
    """A fresh executor over the same station seed and assignment agrees.

    This is what keying the pass seed on the assignment id buys: a station that
    restarts mid-run and executes the pass again does not quietly report
    something different from what it would have.
    """
    first = body_digest(STATION_SEED, assignment())

    assert body_digest(STATION_SEED, assignment()) == first


def test_the_reported_window_is_the_assigned_window() -> None:
    """A virtual station has no rotator to be slow, so it invents no drift."""
    (result,) = run_one(STATION_SEED, assignment())

    assert result.started_at == WINDOW_START  # type: ignore[attr-defined]
    assert result.ended_at == WINDOW_START + timedelta(minutes=11)  # type: ignore[attr-defined]


def test_nothing_is_produced_before_the_window_closes() -> None:
    """The drain hands over finished work, and a pass in progress is not that."""
    executor = SimulatedExecutor(STATION_SEED)
    executor.begin(assignment())

    assert executor.take_completed() == ()


def test_a_result_is_handed_over_exactly_once() -> None:
    """A result the loop has taken is the loop's from then on.

    Returning it again would submit the same observation on every tick for the
    rest of the station's life.
    """
    executor = SimulatedExecutor(STATION_SEED)
    executor.begin(assignment())
    executor.end(assignment())

    assert len(executor.take_completed()) == 1
    assert executor.take_completed() == ()


def test_a_pass_that_never_began_produces_nothing() -> None:
    """`end` without `begin` must not invent a reception.

    This is what lets the fault schedule express a station that took the work
    and never started: the honest report for that is `not_attempted`, and a
    `no_signal` from a receiver that never ran would be a measurement nothing
    measured.
    """
    executor = SimulatedExecutor(STATION_SEED)
    executor.end(assignment())

    assert executor.take_completed() == ()


def test_a_down_receiver_reports_not_attempted() -> None:
    """MSP §4.4 separates never began from listened and heard nothing.

    A dead SDR does not stop the client — the station goes on heartbeating and
    goes on holding the work — so the window closes on a pass nothing received.
    Reporting `no_signal` there would put a measurement in the store that no
    receiver produced; reporting nothing at all would look like a decline the
    station never made.
    """
    executor = SimulatedExecutor(
        STATION_SEED, FaultState(active=frozenset({RECEIVER_DOWN}))
    )
    executor.begin(assignment())
    executor.end(assignment())

    (result,) = executor.take_completed()
    assert result.outcome == "not_attempted"
    assert result.signal is None


def test_a_receiver_that_recovers_mid_pass_still_reports_not_attempted() -> None:
    """It is what happened at the start of the window that decides this.

    A radio that came back after the pass was missed did not receive the pass.
    """
    faults = FaultState(active=frozenset({RECEIVER_DOWN}))
    executor = SimulatedExecutor(STATION_SEED, faults)
    executor.begin(assignment())

    faults.active = frozenset()
    executor.end(assignment())

    (result,) = executor.take_completed()
    assert result.outcome == "not_attempted"


def test_a_working_receiver_never_reports_not_attempted() -> None:
    """The value is unreachable except through the fault schedule."""
    executor = SimulatedExecutor(STATION_SEED)
    for index in range(40):
        work = assignment(f"as_{index:04d}", elevation_deg=30.0)
        executor.begin(work)
        executor.end(work)

    assert all(one.outcome != "not_attempted" for one in executor.take_completed())


def test_several_passes_drain_together() -> None:
    """A tick that closed two windows hands over both."""
    executor = SimulatedExecutor(STATION_SEED)
    for one in ("as_a", "as_b"):
        executor.begin(assignment(one))
        executor.end(assignment(one))

    assert len(executor.take_completed()) == 2


@pytest.mark.parametrize("elevation_deg", [6.0, 20.0, 55.0, 88.0])
def test_every_result_is_a_body_the_platform_would_accept(elevation_deg: float) -> None:
    """The model refuses a body whose outcome contradicts its signal block.

    `build_observation_body` applies the same rules the endpoint does, so a
    result that could not be sent raises here rather than being queued and set
    aside after a `malformed` — one pass silently lost per inconsistency.
    """
    for index in range(40):
        (result,) = run_one(
            STATION_SEED, assignment(f"as_{index:04d}", elevation_deg=elevation_deg)
        )
        build_observation_body(result, STATION_ID)  # type: ignore[arg-type]


def test_a_detection_falls_inside_the_window_even_on_a_short_pass() -> None:
    """The offset suits an eight-to-fifteen minute pass; a two-minute one clamps.

    Nothing downstream validates a detection after the window closed, so an
    unclamped offset would produce a row every later reader has to puzzle over.
    """
    for index in range(60):
        (result,) = run_one(
            STATION_SEED, assignment(f"as_{index:04d}", elevation_deg=85.0, minutes=1)
        )
        if result.signal is not None and result.signal.first_detection_at is not None:  # type: ignore[attr-defined]
            assert result.signal.first_detection_at <= result.ended_at  # type: ignore[attr-defined]


def test_the_doppler_series_spans_the_window_in_order() -> None:
    """A time series, and the platform hashes two orderings differently (D-070)."""
    detected = _first_detected()
    samples = detected.signal.doppler_samples  # type: ignore[union-attr]
    assert samples is not None

    instants = [one.sampled_at for one in samples]
    assert instants == sorted(instants)
    assert instants[0] == WINDOW_START
    assert instants[-1] == WINDOW_START + timedelta(minutes=11)


def test_the_notes_carry_the_seed_the_row_came_from() -> None:
    """Every published number is regenerable from a snapshot, a config and a seed.

    Carrying the third with the row means one observation can be reproduced on
    its own, without re-running the fleet that produced it.
    """
    (result,) = run_one(STATION_SEED, assignment())

    assert "seed" in (result.client_notes or "")  # type: ignore[attr-defined]


def test_no_products_are_claimed() -> None:
    """MSP 0.x defines no transfer mechanism, and there is no artefact anyway."""
    (result,) = run_one(STATION_SEED, assignment())

    assert result.products == ()  # type: ignore[attr-defined]


def _first_detected() -> object:
    """The first pass in a sweep that heard something."""
    for index in range(60):
        (result,) = run_one(
            STATION_SEED, assignment(f"as_{index:04d}", elevation_deg=85.0)
        )
        if result.signal is not None:  # type: ignore[attr-defined]
            return result
    raise AssertionError("no detected pass in the sweep")
