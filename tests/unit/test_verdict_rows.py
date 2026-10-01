"""``read_receptions`` — the verdict's inputs, read from a raw snapshot's files.

Reference: docs/DECISIONS.md D-145, D-250, D-261.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from meridian.datasets.row_fields import MalformedSnapshotError
from meridian.prediction.verdict_inputs import ReceptionInputs, inputs_sha256
from meridian.prediction.verdict_rows import read_receptions


def jsonl(*rows: dict[str, object]) -> bytes:
    return b"".join(json.dumps(one).encode() + b"\n" for one in rows)


def observation(assignment_id: str, **changes: object) -> dict[str, object]:
    return {
        "assignment_id": assignment_id,
        "revision": 1,
        "station_id": "st_001",
        "satellite_id": "norad:57166",
        "started_at": "2026-08-14T09:00:00Z",
        "outcome": "decoded",
        "signal_detected": True,
        "peak_snr_db": 14.5,
        "frames_decoded": 4800,
        "decoder": "satdump",
        "decoder_version": "1.2.2",
        "simulated": False,
    } | changes


def snapshot(*observations: dict[str, object]) -> dict[str, bytes]:
    return {
        "passes.jsonl": jsonl(
            {
                "id": 1,
                "satellite_id": "norad:57166",
                "aos": "2026-08-14T09:00:00Z",
                "los": "2026-08-14T09:10:00Z",
            }
        ),
        "transmitters.jsonl": jsonl(
            {
                "id": 1,
                "satellite_id": "norad:57166",
                "centre_freq_hz": 137100000,
                "mode": "lrpt",
                "deleted_at": None,
                "frame_interval_s": 0.113778,
            }
        ),
        "assignments.jsonl": jsonl(
            *(
                {
                    "assignment_id": name,
                    "pass_id": 1,
                    "centre_freq_hz": 137100000,
                    "mode": "lrpt",
                }
                for name in ("as_a", "as_b")
            )
        ),
        "listening.jsonl": jsonl(
            {"assignment_id": "as_a", "listening_confirmed": True}
        ),
        "observations.jsonl": jsonl(*observations),
    }


def test_a_reception_reads_every_input() -> None:
    (one,) = read_receptions(snapshot(observation("as_a")))

    assert one.assignment_id == "as_a"
    assert one.started_at == datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    assert one.band == "vhf"
    assert one.simulated is False
    assert one.inputs == ReceptionInputs(
        outcome="decoded",
        signal_detected=True,
        peak_snr_db=14.5,
        frames_decoded=4800,
        frames_expected=5273,
        decoder="satdump",
        decoder_version="1.2.2",
        listening_confirmed=True,
        mode="lrpt",
    )


def test_an_assignment_never_asked_about_reads_as_not_confirmed() -> None:
    (one,) = read_receptions(snapshot(observation("as_b")))

    assert one.inputs.listening_confirmed is False


def test_absent_evidence_stays_absent() -> None:
    (one,) = read_receptions(
        snapshot(
            observation(
                "as_a",
                outcome="no_signal",
                signal_detected=False,
                peak_snr_db=None,
                frames_decoded=None,
                decoder=None,
                decoder_version=None,
            )
        )
    )

    assert one.inputs.peak_snr_db is None
    assert one.inputs.frames_decoded is None
    assert one.inputs.decoder is None


def test_a_whole_snr_reads_as_the_float_the_database_holds() -> None:
    """A JSON ``12`` and a ``double precision`` 12.0 must hash the same."""
    (one,) = read_receptions(snapshot(observation("as_a", peak_snr_db=12)))

    assert isinstance(one.inputs.peak_snr_db, float)
    assert inputs_sha256(one.inputs) == inputs_sha256(
        ReceptionInputs(
            outcome="decoded",
            signal_detected=True,
            peak_snr_db=12.0,
            frames_decoded=4800,
            frames_expected=5273,
            decoder="satdump",
            decoder_version="1.2.2",
            listening_confirmed=True,
            mode="lrpt",
        )
    )


def test_simulated_receptions_are_read_and_marked() -> None:
    (one,) = read_receptions(snapshot(observation("as_a", simulated=True)))

    assert one.simulated is True


def test_receptions_come_back_oldest_first() -> None:
    later = observation("as_a", started_at="2026-08-14T10:00:00Z")
    earlier = observation("as_b")

    found = read_receptions(snapshot(later, earlier))

    assert [one.assignment_id for one in found] == ["as_b", "as_a"]


def test_an_observation_of_an_unknown_assignment_is_refused() -> None:
    with pytest.raises(MalformedSnapshotError, match="as_x"):
        read_receptions(snapshot(observation("as_x")))


def test_a_missing_listening_file_is_refused_by_name() -> None:
    held = snapshot(observation("as_a"))
    del held["listening.jsonl"]

    with pytest.raises(MalformedSnapshotError, match=r"listening\.jsonl"):
        read_receptions(held)
