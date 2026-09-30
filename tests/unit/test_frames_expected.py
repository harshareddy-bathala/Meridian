"""Frames expected: the platform's one definition, and reading it from a snapshot.

No marker: both halves are pure. The snapshot is built by hand in the shape
``meridian snapshot export`` writes, with only the three files the reader needs.

Reference: docs/DECISIONS.md D-103, D-104, D-250.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from meridian.datasets.frames_expected import read_frames_expected
from meridian.datasets.row_fields import MalformedSnapshotError
from meridian.observations.frames_expected import frames_expected

AOS = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
LRPT_INTERVAL_S = 0.113778
"""Meteor LRPT: a 1024-byte frame at 72 kbit/s, as the development catalogue
states it."""


def test_a_ten_minute_lrpt_pass_expects_about_five_thousand_frames() -> None:
    """600 s over 8192 bits at 72 kbit/s — the figure a decoder's count is read
    against."""
    assert frames_expected(AOS, AOS + timedelta(minutes=10), LRPT_INTERVAL_S) == 5273


def test_a_frame_the_pass_ended_inside_is_not_expected() -> None:
    """Rounded down: a frame cut off by loss of signal was never sent whole."""
    assert frames_expected(AOS, AOS + timedelta(seconds=9.9), 2.0) == 4


def test_an_unknown_interval_gives_no_count_rather_than_a_guess() -> None:
    """The verdict omits the ratio, which it can only do if this says so (D-104)."""
    assert frames_expected(AOS, AOS + timedelta(minutes=10), None) is None


@pytest.mark.parametrize("interval", [0.0, -0.1])
def test_an_interval_that_is_not_positive_is_a_bug(interval: float) -> None:
    """The catalogue and the table both refuse one, so reaching here is a bug."""
    with pytest.raises(ValueError, match="positive"):
        frames_expected(AOS, AOS + timedelta(minutes=10), interval)


def test_a_pass_that_ends_before_it_begins_is_a_bug() -> None:
    with pytest.raises(ValueError, match="before it begins"):
        frames_expected(AOS, AOS - timedelta(seconds=1), LRPT_INTERVAL_S)


def _jsonl(rows: list[dict[str, Any]]) -> bytes:
    return "\n".join(json.dumps(one) for one in rows).encode()


def _transmitter(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": 1,
        "satellite_id": "norad:57166",
        "centre_freq_hz": 137100000,
        "mode": "lrpt",
        "polarisation": "rhcp",
        "bandwidth_hz": 150000,
        "active": True,
        "source": "manual",
        "deleted_at": None,
        "frame_interval_s": 2.0,
    }
    row.update(overrides)
    return row


def _snapshot(
    transmitters: list[dict[str, Any]],
    assignments: list[dict[str, Any]] | None = None,
) -> dict[str, bytes]:
    """A raw snapshot holding one ten-minute pass and what the reader needs."""
    passes = [
        {
            "id": 7,
            "satellite_id": "norad:57166",
            "station_id": "st_one",
            "aos": "2026-09-30T09:00:00Z",
            "los": "2026-09-30T09:10:00Z",
        }
    ]
    if assignments is None:
        assignments = [
            {
                "assignment_id": "as_one",
                "pass_id": 7,
                "centre_freq_hz": 137100000,
                "mode": "lrpt",
            }
        ]
    return {
        "passes.jsonl": _jsonl(passes),
        "assignments.jsonl": _jsonl(assignments),
        "transmitters.jsonl": _jsonl(transmitters),
    }


def test_the_reader_computes_each_assignment_from_its_pass_and_downlink() -> None:
    """Acquisition to loss, over the interval of the downlink it was pointed at."""
    assert read_frames_expected(_snapshot([_transmitter()])) == {"as_one": 300}


def test_a_downlink_whose_interval_nobody_stated_expects_nothing() -> None:
    files = _snapshot([_transmitter(frame_interval_s=None)])

    assert read_frames_expected(files) == {"as_one": None}


def test_a_snapshot_exported_before_the_column_existed_reads_as_unknown() -> None:
    """A pre-0026 snapshot is still readable; its ratios are simply absent."""
    old = _transmitter()
    del old["frame_interval_s"]

    assert read_frames_expected(_snapshot([old])) == {"as_one": None}


def test_another_frequency_of_the_same_satellite_is_not_this_downlink() -> None:
    """Identity is satellite, frequency and mode, as the catalogue's is."""
    files = _snapshot([_transmitter(centre_freq_hz=137900000)])

    assert read_frames_expected(files) == {"as_one": None}


def test_a_live_row_is_preferred_to_a_withdrawn_one_whatever_the_order() -> None:
    """Withdrawn first in the file and lower-numbered, and still not chosen."""
    withdrawn = _transmitter(
        id=1, deleted_at="2026-09-01T00:00:00Z", frame_interval_s=1.0
    )
    live = _transmitter(id=2, frame_interval_s=2.0)

    assert read_frames_expected(_snapshot([withdrawn, live])) == {"as_one": 300}
    assert read_frames_expected(_snapshot([live, withdrawn])) == {"as_one": 300}


def test_an_assignment_whose_pass_is_not_held_is_refused_by_name() -> None:
    orphan = [
        {
            "assignment_id": "as_orphan",
            "pass_id": 99,
            "centre_freq_hz": 137100000,
            "mode": "lrpt",
        }
    ]

    with pytest.raises(MalformedSnapshotError, match="as_orphan"):
        read_frames_expected(_snapshot([_transmitter()], orphan))


def test_an_interval_that_is_not_positive_is_refused() -> None:
    with pytest.raises(MalformedSnapshotError, match="frame_interval_s"):
        read_frames_expected(_snapshot([_transmitter(frame_interval_s=0)]))


def test_a_snapshot_without_its_transmitters_is_refused_by_name() -> None:
    files = _snapshot([_transmitter()])
    del files["transmitters.jsonl"]

    with pytest.raises(MalformedSnapshotError, match=r"transmitters\.jsonl"):
        read_frames_expected(files)
