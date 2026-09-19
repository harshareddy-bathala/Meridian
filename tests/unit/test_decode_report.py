"""Meridian's decode report, read strictly.

A lenient reader counts wrongly with nothing to show it, so every way a report
can be wrong is refused here, and every refusal becomes a failed decode — an
``aborted`` pass rather than a guessed number (D-122, D-124).

Marked as a unit test by living in ``tests/unit``.

Reference: docs/DECISIONS.md D-117, D-122, D-124.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meridian_client.reception import decode_report
from meridian_client.reception.decode_report import (
    DecodeReport,
    DecodeReportError,
    SnrPoint,
    parse_decode_report,
    read_decode_report,
)

DURATION_S = 660.0

FULL = {
    "format": 1,
    "decoder": "satdump",
    "decoder_version": "1.2.2",
    "frames_decoded": 412,
    "frames_failed": 37,
    "first_frame_offset_s": 35.2,
    "snr": [{"offset_s": 35.0, "snr_db": 3.1}, {"offset_s": 326.0, "snr_db": 11.4}],
    "noise_floor_dbfs": -52.3,
}


def parse(report: object) -> DecodeReport:
    return parse_decode_report(report, recording_duration_s=DURATION_S)


def with_(**changes: object) -> dict[str, object]:
    report = dict(FULL)
    report.update(changes)
    return report


def test_a_full_report_is_read_as_written() -> None:
    assert parse(FULL) == DecodeReport(
        decoder="satdump",
        decoder_version="1.2.2",
        frames_decoded=412,
        frames_failed=37,
        first_frame_offset_s=35.2,
        snr=(SnrPoint(35.0, 3.1), SnrPoint(326.0, 11.4)),
        noise_floor_dbfs=-52.3,
    )


def test_what_a_decoder_does_not_report_is_none_not_zero() -> None:
    """D-122: unknown is absent. A zero is a measurement."""
    report = parse({"format": 1, "decoder": "gr-satellites"})

    assert report.frames_decoded is None
    assert report.snr is None
    assert report.noise_floor_dbfs is None


def test_an_empty_snr_series_is_a_measurement_of_nothing() -> None:
    assert parse(with_(snr=[])).snr == ()


def test_integral_numbers_are_read_as_floats() -> None:
    assert parse(with_(noise_floor_dbfs=-52)).noise_floor_dbfs == -52.0


@pytest.mark.parametrize(
    ("report", "message"),
    [
        ([], "JSON object"),
        (with_(format=2), "format must be 1"),
        ({"decoder": "satdump"}, "format must be 1"),
        (with_(frames_decodde=3), "unknown keys"),
        (with_(decoder=""), "name the decoder"),
        ({"format": 1}, "name the decoder"),
        (with_(decoder_version=122), "must be a string"),
        (with_(frames_decoded=True), "whole number of frames"),
        (with_(frames_decoded=-1), "whole number of frames"),
        (with_(frames_failed=3.0), "whole number of frames"),
        (with_(noise_floor_dbfs="low"), "must be a number"),
        (with_(noise_floor_dbfs=False), "must be a number"),
        (with_(noise_floor_dbfs=float("nan")), "must be finite"),
        (with_(first_frame_offset_s=-0.5), "outside"),
        (with_(first_frame_offset_s=DURATION_S + 1), "outside"),
        (with_(frames_decoded=0), "must be 1 or more"),
        (with_(frames_decoded=None), "must be 1 or more"),
        (with_(snr={"offset_s": 1.0}), "must be an array"),
        (with_(snr=[{"offset_s": 1.0}]), "an snr point"),
        (with_(snr=[{"offset_s": 1.0, "snr_db": 2.0, "t": 3}]), "an snr point"),
        (with_(snr=[{"offset_s": 900.0, "snr_db": 2.0}]), "outside"),
        (with_(snr=[{"offset_s": None, "snr_db": 2.0}]), "needs both"),
        (with_(snr=[{"offset_s": 1.0, "snr_db": float("inf")}]), "must be finite"),
        (
            with_(
                snr=[{"offset_s": 9.0, "snr_db": 2.0}, {"offset_s": 3.0, "snr_db": 2.0}]
            ),
            "time order",
        ),
    ],
)
def test_a_report_that_breaks_the_contract_is_refused(
    report: object, message: str
) -> None:
    """Never clamped, never ignored: the pass is `aborted` instead (D-122)."""
    with pytest.raises(DecodeReportError, match=message):
        parse(report)


def test_a_runaway_snr_series_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(decode_report, "MAX_SNR_POINTS", 2)

    with pytest.raises(DecodeReportError, match="over 2"):
        parse(with_(snr=[{"offset_s": 1.0, "snr_db": 1.0}] * 3))


# --- reading the file ---------------------------------------------------------


def test_a_report_file_is_read_and_checked(tmp_path: Path) -> None:
    path = tmp_path / "decode_report.json"
    path.write_text(json.dumps(FULL), encoding="utf-8")

    report = read_decode_report(path, recording_duration_s=DURATION_S)

    assert report.frames_decoded == 412


def test_a_non_finite_token_in_the_file_is_refused(tmp_path: Path) -> None:
    """Python's JSON reader accepts `Infinity`; the contract does not."""
    path = tmp_path / "decode_report.json"
    path.write_text('{"format": 1, "decoder": "x", "noise_floor_dbfs": -Infinity}')

    with pytest.raises(DecodeReportError, match="finite"):
        read_decode_report(path, recording_duration_s=DURATION_S)


@pytest.mark.parametrize(
    ("contents", "message"),
    [(None, "wrote no report"), (b"{not json", "not JSON"), (b"\xff\xfe", "not JSON")],
)
def test_a_missing_or_unparseable_file_is_refused(
    tmp_path: Path, contents: bytes | None, message: str
) -> None:
    path = tmp_path / "decode_report.json"
    if contents is not None:
        path.write_bytes(contents)

    with pytest.raises(DecodeReportError, match=message):
        read_decode_report(path, recording_duration_s=DURATION_S)


def test_an_oversized_file_is_refused_unread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(decode_report, "MAX_REPORT_BYTES", 10)
    path = tmp_path / "decode_report.json"
    path.write_text(json.dumps(FULL), encoding="utf-8")

    with pytest.raises(DecodeReportError, match="over 10"):
        read_decode_report(path, recording_duration_s=DURATION_S)
