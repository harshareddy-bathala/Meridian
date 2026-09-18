"""The two programs an operator runs on a station.

``python -m meridian_client.replay`` puts one recording through the real
pipeline offline and prints the observation it would have produced;
``python -m meridian_client.station`` runs a registered station. Both are
exercised here in process, with the real configuration, the real executor and a
decoder program that really runs.

Marked as a unit test by living in ``tests/unit``: a temporary directory, a
child process, and a platform nothing can reach.

Reference: docs/DECISIONS.md D-023, D-125, D-127, D-128.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian.api.models.observation import ObservationRequestBody
from meridian_client import replay, station
from meridian_client.credentials import StationCredentials, save_credentials
from meridian_client.station_config import StationPaths

FAKE_DECODER = Path(__file__).parent / "reception_fakes" / "fake_decoder.py"
START_AT = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
LINE1 = "1 57166U 23091A   26223.50000000  .00000100  00000-0  50000-4 0  9990"
LINE2 = "2 57166  98.7041 210.4322 0002726  80.4113 279.7297 14.22000000160126"

REPORT = {
    "format": 1,
    "decoder": "fake-satdump",
    "decoder_version": "0.0.1",
    "frames_decoded": 412,
    "frames_failed": 37,
    "first_frame_offset_s": 35.0,
    "snr": [{"offset_s": 330.0, "snr_db": 11.4}],
    "noise_floor_dbfs": -52.3,
}


def assignment_file(tmp_path: Path) -> Path:
    """One MSP §4.3 assignment, as a heartbeat response carried it."""
    path = tmp_path / "assignment.json"
    path.write_text(
        json.dumps(
            {
                "assignment_id": "as_44b2",
                "satellite_id": "norad:57166",
                "start_at": START_AT.isoformat(),
                "end_at": (START_AT + timedelta(minutes=11)).isoformat(),
                "centre_freq_hz": 137_900_000,
                "mode": "lrpt",
                "expected_max_elevation_deg": 61.4,
                "predicted_yield": None,
                "element_set": {
                    "epoch": (START_AT - timedelta(hours=7)).isoformat(),
                    "line1": LINE1,
                    "line2": LINE2,
                },
                "timing_uncertainty_s": 4.2,
                "priority": 1.0,
            }
        ),
        encoding="utf-8",
    )
    return path


def config_file(tmp_path: Path, *, base_url: str = "http://127.0.0.1:1") -> Path:
    """A station whose decoder is the fake one, and whose platform is unreachable."""
    report = tmp_path / "report.json"
    report.write_text(json.dumps(REPORT), encoding="utf-8")
    argv = json.dumps(
        [sys.executable, str(FAKE_DECODER), "report", "{report_path}", str(report)]
    )
    path = tmp_path / "station.toml"
    path.write_text(
        f'[station]\nbase_url = "{base_url}"\nstate_dir = "state"\n\n'
        f"[decoders.lrpt]\nargv = {argv}\ntimeout_s = 30.0\n\n"
        "[disk]\nbytes_per_second = 800\nreserve_bytes = 1\n",
        encoding="utf-8",
    )
    return path


def recording_file(tmp_path: Path) -> Path:
    """700 seconds at 100 Hz in cf32 — long enough to cover an eleven-minute pass."""
    path = tmp_path / "pass.cf32"
    path.write_bytes(b"\0" * 8 * 100 * 700)
    return path


def replay_argv(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--config",
        str(config_file(tmp_path)),
        "--assignment",
        str(assignment_file(tmp_path)),
        "--recording",
        str(recording_file(tmp_path)),
        "--sample-rate-hz",
        "100",
        "--station-id",
        "st_7fa3c1",
        *extra,
    ]


# --- the replay runner --------------------------------------------------------


def test_a_replayed_pass_is_written_as_the_body_a_station_would_have_sent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = replay.main(replay_argv(tmp_path))

    body = json.loads(capsys.readouterr().out)
    ObservationRequestBody.model_validate(body)
    assert code == 0
    assert body["station_id"] == "st_7fa3c1"
    assert body["assignment_id"] == "as_44b2"
    assert body["outcome"] == "decoded"
    assert body["decode"]["frames_decoded"] == 412
    assert "replay of pass.cf32" in body["client_notes"]


def test_the_body_can_be_written_to_a_file_instead(tmp_path: Path) -> None:
    out = tmp_path / "body.json"

    code = replay.main(replay_argv(tmp_path, "--out", str(out)))

    assert code == 0
    assert json.loads(out.read_text())["outcome"] == "decoded"


def test_a_replay_leaves_the_stations_own_state_directory_alone(tmp_path: Path) -> None:
    """The capture folder is a temporary one: a replay is not a reception."""
    replay.main(replay_argv(tmp_path))

    assert not (tmp_path / "state" / "captures").exists()


def test_a_replay_that_cannot_use_its_recording_still_reports_a_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A recording tuned to another satellite is refused, and the pass is honest."""
    code = replay.main(replay_argv(tmp_path, "--centre-freq-hz", "137912500"))

    body = json.loads(capsys.readouterr().out)
    ObservationRequestBody.model_validate(body)
    assert code == 0
    assert body["outcome"] == "not_attempted"
    assert "outside" in body["client_notes"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("config", "unknown table"),
        ("assignment", "is not JSON"),
        ("station_id", "no station is registered"),
    ],
)
def test_a_replay_that_cannot_start_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], change: str, message: str
) -> None:
    argv = replay_argv(tmp_path)
    if change == "config":
        (tmp_path / "station.toml").write_text("[stations]\n", encoding="utf-8")
    elif change == "assignment":
        (tmp_path / "assignment.json").write_text("{", encoding="utf-8")
    else:
        argv = [one for one in argv if one not in {"--station-id", "st_7fa3c1"}]

    code = replay.main(argv)

    assert code == replay.EXIT_FAILED
    assert message in capsys.readouterr().err


def test_a_replay_that_runs_out_of_patience_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A decoder that never finishes ends as a sentence, not a hang."""
    monkeypatch.setattr(replay, "SLACK_S", -31.0)

    code = replay.main(replay_argv(tmp_path))

    assert code == replay.EXIT_FAILED
    assert "did not finish" in capsys.readouterr().err


def test_the_replay_runner_has_no_way_to_reach_the_platform() -> None:
    """D-125: an offline replay writes a body and never submits it.

    Checked against the source rather than a run, because the property is that
    no code path exists — not that this particular run did not take one.
    """
    source = Path(replay.__file__).read_text(encoding="utf-8")

    assert "MspTransport" not in source
    assert "ObservationQueue" not in source
    assert "observations" not in source


# --- the station runner -------------------------------------------------------


def register_a_station(tmp_path: Path, *, simulated: bool = True) -> None:
    paths = StationPaths.under(tmp_path / "state")
    save_credentials(
        paths.credentials,
        StationCredentials("st_7fa3c1", "a-token", "a-key", 30, simulated=simulated),
    )


def test_a_station_that_never_registered_says_so_rather_than_registering(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """D-023: registration consumes an invite, so it is never automatic."""
    code = station.main(["--config", str(config_file(tmp_path))])

    assert code == station.EXIT_FAILED
    assert "no station is registered" in capsys.readouterr().err


def test_a_station_whose_configuration_is_wrong_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = config_file(tmp_path)
    path.write_text("[receiver]\nkind = 'sdr'\n", encoding="utf-8")

    code = station.main(["--config", str(path)])

    assert code == station.EXIT_FAILED
    assert "no physical receiver adapter ships yet" in capsys.readouterr().err


def test_a_registered_station_ticks_and_survives_an_unreachable_platform(
    tmp_path: Path,
) -> None:
    """The platform is a closed port: the station keeps its work and exits cleanly."""
    path = config_file(tmp_path)
    register_a_station(tmp_path)

    code = station.main(["--config", str(path), "--ticks", "1"])

    assert code == 0
    assert (tmp_path / "state").is_dir()


def test_a_station_with_a_synthetic_receiver_and_no_simulated_flag_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """D-125 reaches the operator as a sentence, not a traceback."""
    path = config_file(tmp_path)
    register_a_station(tmp_path, simulated=False)

    code = station.main(["--config", str(path), "--ticks", "1"])

    assert code == station.EXIT_FAILED
    assert "does not hear the sky" in capsys.readouterr().err
