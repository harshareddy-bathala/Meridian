"""Whose silence counts about the satellite: another station's, never the loss's own.

Stage 20 pools every assignment a station held of one physical pass into one
classification. The satellite's silence is judged on *other* stations' attempts
(D-276), so a second assignment of the same station and the same pass is not a
witness to it: it is the same loss, reported twice. One that heard the
satellite still counts, because the satellite was heard.

The store is stubbed: what is under test is which receptions are counted, not
how they are read.

Reference: docs/DECISIONS.md D-147, D-276.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from meridian.reliability import diagnosis_gather
from meridian.reliability.config import DiagnosisConfig
from meridian.reliability.diagnosis import diagnose
from meridian.reliability.diagnosis_gather import StationHistory, gather
from meridian.store.diagnosis_reads import ClockTraces, HistoryRow
from meridian.store.loss_diagnoses import DiagnosisSubject

T0 = datetime(2026, 8, 12, 10, tzinfo=UTC)
END = T0 + timedelta(minutes=10)
CONFIG = DiagnosisConfig()


def subject(*siblings: str) -> DiagnosisSubject:
    """A confirmed, high, unheard pass, pooled with ``siblings`` by Stage 20."""
    return DiagnosisSubject(
        assignment_id="as_lost",
        station_id="st_a",
        satellite_id="norad:57166",
        pass_id=1,
        element_set_id=1,
        start_at=T0,
        end_at=END,
        aos=T0,
        los=END,
        centre_freq_hz=137_900_000,
        mode="LRPT",
        timing_uncertainty_s=4.0,
        max_elevation_deg=62.0,
        simulated=False,
        classification_id=7,
        classification="confirmed_miss",
        classification_evidence={
            "assignments": [{"assignment_id": one} for one in ("as_lost", *siblings)],
            "heard_during_window": True,
            "listening_confirmed": True,
        },
        revision=1,
        observation_started_at=T0,
        observation_ended_at=END,
        outcome="no_signal",
        noise_floor_dbfs=-60.0,
        receiver_gain_db=30.0,
        snr_samples=None,
        probability_usable=None,
        partial_below=None,
    )


@pytest.fixture
def nearby(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """The store, stubbed: only the satellite's other receptions are set."""
    found: dict[str, list[str]] = {"signals": [], "silences": []}

    def other_receptions(*_: Any, **__: Any) -> tuple[list[str], list[str]]:
        return found["signals"], found["silences"]

    stubs: dict[str, Any] = {
        "satellite_evidence": other_receptions,
        "find_transmitter_active": lambda *_, **__: True,
        "find_site": lambda *_, **__: None,
        "find_noise_baseline": lambda *_, **__: (None, 0),
        "find_clock_traces": lambda *_, **__: ClockTraces(None, (), ()),
        "find_declared_floors": lambda *_, **__: [],
    }
    for name, stub in stubs.items():
        monkeypatch.setattr(diagnosis_gather, name, stub)
    return found


def cause_of(one: DiagnosisSubject) -> tuple[str, int, int]:
    evidence = gather(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        one,
        config=CONFIG,
        history=StationHistory(),
    )
    counts = evidence.satellite
    return diagnose(evidence, CONFIG).cause, counts.signals, counts.silences


def test_another_station_s_silence_names_the_satellite(
    nearby: dict[str, list[str]],
) -> None:
    nearby["silences"] = ["as_elsewhere"]

    assert cause_of(subject()) == ("satellite_silent", 0, 1)


def test_the_station_s_own_other_assignment_is_no_witness(
    nearby: dict[str, list[str]],
) -> None:
    """One station, one pass, two assignments, both silent: one loss, not two."""
    nearby["silences"] = ["as_sibling"]

    assert cause_of(subject("as_sibling")) == ("undetermined", 0, 0)


def test_its_own_other_assignment_that_heard_the_satellite_still_counts(
    nearby: dict[str, list[str]],
) -> None:
    nearby["signals"] = ["as_sibling"]
    nearby["silences"] = ["as_elsewhere"]

    assert cause_of(subject("as_sibling")) == ("undetermined", 1, 1)


def test_a_station_s_history_is_read_once_and_only_grown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run takes losses oldest first: each later loss of a station asks for a
    little more history, and only that stretch is read."""
    asked: list[tuple[datetime, datetime]] = []

    def stored(
        _conn: Any, *, station_id: str, between: tuple[datetime, datetime]
    ) -> list[HistoryRow]:
        asked.append(between)
        since, until = between
        hours = range(int((until - since) / timedelta(hours=1)))
        return [
            HistoryRow(
                f"{station_id}-{since + timedelta(hours=h):%d%H}",
                1,
                since + timedelta(hours=h),
                -60.0,
                30.0,
                [],
            )
            for h in hours
        ]

    monkeypatch.setattr(diagnosis_gather, "find_station_history", stored)
    history = StationHistory()
    day = timedelta(days=1)

    first = history.receptions(None, "st_a", (T0 - day, T0))  # type: ignore[arg-type]
    again = history.receptions(None, "st_a", (T0 - day, T0))  # type: ignore[arg-type]
    later = history.receptions(
        None,  # type: ignore[arg-type]
        "st_a",
        (T0 - day + timedelta(hours=2), T0 + timedelta(hours=2)),
    )

    assert asked == [(T0 - day, T0), (T0, T0 + timedelta(hours=2))]
    assert again == first
    assert len(first) == len(later) == 24
    assert later[0].started_at == T0 - day + timedelta(hours=2)
    assert later[-1].started_at == T0 + timedelta(hours=1)
