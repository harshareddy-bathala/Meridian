"""One station's configuration file, and what it refuses to start from.

A station reads this once at start-up. Everything it gets wrong here it would
otherwise discover at the end of a pass, which never repeats — so the tests are
mostly refusals, each naming what was not recognised.

Marked as a unit test by living in ``tests/unit``.

Reference: docs/DECISIONS.md D-124, D-125, D-127.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian_client.credentials import StationCredentials
from meridian_client.reception.protocols import StationClocks
from meridian_client.reception.synthetic_receivers import (
    FileReplayReceiver,
    SimulatedReceiver,
)
from meridian_client.station_config import (
    ConfigError,
    StationPaths,
    load_station_config,
)
from meridian_client.station_wiring import build_executor, build_receiver, build_setup

FULL = """
[station]
base_url = "https://platform.example"
state_dir = "state"

[receiver]
kind = "simulated"
sample_rate_hz = 2000

[decoders.lrpt]
argv = ["/usr/local/bin/meridian-satdump", "{recording}", "{report_path}"]
timeout_s = 900.0

[decoders.apt]
argv = ["/usr/local/bin/meridian-apt", "{recording}", "{report_path}"]

[policy]
snr_threshold_db = 4.5
minimum_coverage = 0.6

[disk]
bytes_per_second = 4096000
margin = 2.0
reserve_bytes = 2147483648

[retention]
keep_recordings = true
"""

REPLAY = """
[receiver]
kind = "replay"

[receiver.recordings.as_44b2]
path = "recordings/pass.cf32"
sample_rate_hz = 1000
sample_format = "cf32"
centre_freq_hz = 137900000
gain_db = 32.8
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "station.toml"
    path.write_text(text, encoding="utf-8")
    return path


def refusal(tmp_path: Path, text: str) -> str:
    with pytest.raises(ConfigError) as refused:
        load_station_config(write(tmp_path, text))
    return str(refused.value)


# --- what a full file means ---------------------------------------------------


def test_every_setting_is_read_as_written(tmp_path: Path) -> None:
    config = load_station_config(write(tmp_path, FULL))

    assert config.base_url == "https://platform.example"
    assert config.paths == StationPaths.under(tmp_path / "state")
    assert config.receiver.kind == "simulated"
    assert config.receiver.sample_rate_hz == 2000
    assert sorted(config.decoders) == ["apt", "lrpt"]
    assert config.decoders["lrpt"].timeout_s == 900.0
    assert config.decoders["apt"].timeout_s == 900.0
    assert config.policy.snr_threshold_db == 4.5
    assert config.policy.minimum_coverage == 0.6
    assert config.disk.bytes_per_second == 4_096_000
    assert config.keep_recordings is True


def test_an_empty_file_is_a_station_with_defaults(tmp_path: Path) -> None:
    """Every table is optional; what is left out is the documented default."""
    config = load_station_config(write(tmp_path, ""))

    assert config.base_url == "http://localhost:8000"
    assert config.paths.state_dir == tmp_path / "state"
    assert config.receiver.kind == "simulated"
    assert config.decoders == {}
    assert config.policy.snr_threshold_db == 3.0
    assert config.disk.reserve_bytes == 1024**3
    assert config.keep_recordings is False


def test_paths_are_resolved_against_the_configuration_file(tmp_path: Path) -> None:
    """A configuration and the files it names travel together."""
    (tmp_path / "recordings").mkdir()
    config = load_station_config(write(tmp_path, REPLAY))

    source = config.receiver.recordings["as_44b2"]
    assert source.path == tmp_path / "recordings" / "pass.cf32"
    assert source.centre_freq_hz == 137_900_000
    assert source.gain_db == 32.8


# --- what it refuses ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[stations]\n", "unknown table: stations"),
        ("[station]\nbase_ur1 = 'x'\n", "unknown station key: base_ur1"),
        ("[policy]\nthreshold = 3\n", "unknown policy key: threshold"),
        ("[disk]\nreserve = 1\n", "unknown disk key: reserve"),
        ("[retention]\nkeep = true\n", "unknown retention key: keep"),
        ("[receiver]\nkind = 'sdr'\n", "no physical receiver adapter ships yet"),
        ("[receiver]\nkind = 'replay'\n", "no recordings are named"),
        ("[receiver]\nsample_rate_hz = 0\n", "positive whole number"),
        ("[receiver]\nsample_rate_hz = true\n", "positive whole number"),
        ("[station]\nbase_url = 8000\n", "non-empty string"),
        ("[retention]\nkeep_recordings = 'yes'\n", "true or false"),
        ("[policy]\nminimum_coverage = 0\n", "minimum_coverage"),
        ("[policy]\nsnr_threshold_db = 'loud'\n", "must be a number"),
        ("[disk]\nmargin = 0.5\n", "margin of 1 or more"),
        ("station = 'no'\n", "must be a table"),
    ],
)
def test_a_file_a_station_cannot_start_from_is_refused(
    tmp_path: Path, text: str, message: str
) -> None:
    assert message in refusal(tmp_path, text)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[decoders.lrpt]\nargv = '/bin/decode'\n", "array of strings"),
        ("[decoders.lrpt]\nargv = []\n", "needs a program"),
        ("[decoders.lrpt]\nargv = ['d', '{rec}']\n", "uses {rec}"),
        ("[decoders.lrpt]\nargv = ['d']\ntimeout_s = 0\n", "timeout_s"),
        ("[decoders.lrpt]\nargv = ['d']\nreties = 2\n", "unknown decoders.lrpt key"),
    ],
)
def test_a_decoder_command_is_validated_where_it_is_read(
    tmp_path: Path, text: str, message: str
) -> None:
    """D-124: a typo is refused at start-up, not at the end of the first pass."""
    refused = refusal(tmp_path, text)

    assert message in refused
    assert "decoders.lrpt" in refused


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[receiver.recordings.as_a]\nsample_rate_hz = 1\n", "non-empty string"),
        ("[receiver.recordings.as_a]\npath = 'p'\n", "positive whole number"),
        (
            "[receiver.recordings.as_a]\npath = 'p'\nsample_rate_hz = 1\n"
            "centre_freq_hz = 137900000\nsample_format = 'wav'\n",
            "sample_format",
        ),
        (
            "[receiver.recordings.as_a]\npath = 'p'\nrate = 1\n",
            "unknown receiver.recordings key",
        ),
    ],
)
def test_a_recording_this_client_cannot_read_is_refused(
    tmp_path: Path, text: str, message: str
) -> None:
    assert message in refusal(tmp_path, text)


def test_a_missing_or_unparseable_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="could not be read"):
        load_station_config(tmp_path / "absent.toml")

    assert "not valid TOML" in refusal(tmp_path, "[station\n")


# --- what the configuration builds -------------------------------------------


def credentials(*, simulated: bool) -> StationCredentials:
    return StationCredentials("st_7fa3c1", "a-token", "a-key", 30, simulated=simulated)


def test_the_configured_receiver_is_the_one_that_gets_built(tmp_path: Path) -> None:
    (tmp_path / "recordings").mkdir()
    clocks = StationClocks()

    simulated = build_receiver(load_station_config(write(tmp_path, FULL)), clocks)
    replayed = build_receiver(load_station_config(write(tmp_path, REPLAY)), clocks)

    assert isinstance(simulated, SimulatedReceiver)
    assert isinstance(replayed, FileReplayReceiver)


def test_the_setup_carries_the_policy_disk_and_retention_it_was_given(
    tmp_path: Path,
) -> None:
    config = load_station_config(write(tmp_path, FULL))

    setup = build_setup(config, StationClocks())

    assert setup.policy.snr_threshold_db == 4.5
    assert setup.disk.margin == 2.0
    assert setup.keep_recordings is True
    assert setup.decoder.supports("lrpt")
    assert setup.folders.folder_for("as_a") == tmp_path / "state" / "captures" / "as_a"


def test_a_station_that_did_not_register_as_simulated_refuses_a_synthetic_receiver(
    tmp_path: Path,
) -> None:
    """D-125, reached through the configuration a station actually starts from.

    The flag comes from the credentials, so no edit to this file can turn a
    simulated receiver's output into a measured station's observations.
    """
    config = load_station_config(write(tmp_path, FULL))

    with pytest.raises(ValueError, match="does not hear the sky"):
        build_executor(config, credentials(simulated=False), StationClocks())

    executor = build_executor(config, credentials(simulated=True), StationClocks())
    assert executor.status(None).state == "idle"
