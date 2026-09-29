"""``meridian.reliability.report`` — two populations that never meet.

Reference: docs/DECISIONS.md D-086, D-184, D-185; CLAUDE.md rule 5.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

from meridian.reliability.config import SloConfig
from meridian.reliability.report import (
    NotMeasured,
    ReliabilityReport,
    build_report,
    report_lines,
    slo_results,
)
from meridian.reliability.slis import PassRecord, Share

END = datetime(2026, 9, 20, tzinfo=UTC)
WINDOW = (END - timedelta(days=30), END)


def record(classification: str, *, simulated: bool, station: str, n: int) -> PassRecord:
    return PassRecord(
        reference=f"as_{n}",
        station_id=station,
        window_end=END - timedelta(hours=n),
        classification=classification,
        listening_confirmed=True,
        outcome="decoded" if classification == "successful_reception" else None,
        simulated=simulated,
    )


AVAILABILITY = {
    "st_real": (False, Share(3000.0, 4000.0)),
    "st_sim": (True, Share(100.0, 100.0)),
}


def build(
    availability: Mapping[str, tuple[bool, Share]] | NotMeasured = AVAILABILITY,
    submission_delays: Mapping[bool, Sequence[float]] | NotMeasured = MappingProxyType(
        {False: [60.0, 120.0]}
    ),
) -> ReliabilityReport:
    passes = [
        record("successful_reception", simulated=False, station="st_real", n=1),
        record("confirmed_miss", simulated=False, station="st_real", n=2),
        record("successful_reception", simulated=True, station="st_sim", n=3),
    ]
    return build_report(
        passes,
        header=("live", WINDOW, "classification-1", "ab" * 32),
        slo=SloConfig(),
        availability=availability,
        submission_delays=submission_delays,
    )


def test_measured_and_simulated_are_counted_apart() -> None:
    report = build()

    assert report.measured.capture.numerator == 1
    assert report.measured.capture.denominator == 2
    assert report.simulated.capture.denominator == 1
    assert report.measured.availability == Share(3000.0, 4000.0)
    assert [one.station_id for one in report.measured.stations] == ["st_real"]


def test_a_figure_the_source_cannot_give_is_said_not_zeroed() -> None:
    report = build(
        availability=NotMeasured("no heartbeats between passes"),
        submission_delays=NotMeasured("no arrival times"),
    )
    printed = "\n".join(report_lines(report))

    assert "not measured — no heartbeats between passes" in printed
    assert "not measured — no arrival times" in printed
    assert "failure detection: not measured" in printed


def test_each_target_is_judged_and_named_for_its_claim() -> None:
    report = build()
    results = {one.name: one for one in slo_results(report.measured, SloConfig())}

    capture = results["pass capture rate"]
    assert (capture.claim, capture.value, capture.met) == ("SC-4", 0.5, False)
    assert results["failure detection (s)"].claim == "SC-5"
    assert results["failure detection (s)"].met is None
    assert results["confirmed miss rate"].claim is None
    assert results["submission delay p95 (s)"].met is True


def test_the_printed_report_names_its_window_method_and_budget() -> None:
    printed = "\n".join(report_lines(build()))

    assert "reliability from live" in printed
    assert "classification-1" in printed
    assert "measured: 2 classified passes" in printed
    assert "simulated: 1 classified passes" in printed
    assert "pass capture rate          1/2 = 50.0%" in printed
    assert "EXHAUSTED" in printed
    assert "confirmed_miss" in printed
    assert "NOT MET" in printed
