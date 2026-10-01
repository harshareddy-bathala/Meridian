"""The schedule configuration: strict, hashed on its values (D-168, D-169)."""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.scheduler.schedule_config import (
    ScheduleConfig,
    ScheduleConfigError,
    check_model,
    load_schedule_config,
    model_path,
    parse_schedule_config,
    schedule_config_sha256,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "deploy" / "schedule.toml.example"


def test_no_file_is_a_with_the_elevation_proxy() -> None:
    config = load_schedule_config(None)

    assert config == ScheduleConfig()
    assert (config.configuration, config.model, config.frames) == (
        "A",
        None,
        "duration",
    )


def test_the_example_file_is_accepted_as_it_stands() -> None:
    assert load_schedule_config(EXAMPLE).configuration == "A"


def test_every_key_is_read() -> None:
    config = parse_schedule_config(
        'configuration = "D"\nmodel = "models/abc"\nframes = "none"\n'
        "time_limit_s = 2.5\nturnaround_s = 30\nseed = 7\n"
    )

    assert config.parameters() == {
        "configuration": "D",
        "model": "models/abc",
        "frames": "none",
        "time_limit_s": 2.5,
        "turnaround_s": 30.0,
        "seed": 7,
    }


@pytest.mark.parametrize(
    ("text", "refusal"),
    [
        ('configuration = "E"', "configuration must be one of"),
        ('configuration = "oracle"', "configuration must be one of"),
        ('configuration = "C"', "is a learned model: name its model"),
        ('configuration = "D"', "is a learned model: name its model"),
        ('model = ""', "must be a directory's path"),
        ("model = 3", "must be a directory's path"),
        ('frames = "bytes"', "frames must be one of"),
        ("time_limit_s = 0", "time_limit_s must be seconds above 0"),
        ("time_limit_s = inf", "time_limit_s must be seconds above 0"),
        ("time_limit_s = 3601", "time_limit_s must be seconds above 0"),
        ("time_limit_s = true", "time_limit_s must be seconds above 0"),
        ("turnaround_s = -1", "turnaround_s must be seconds at least 0"),
        ("seed = -1", "outside 0..2147483647"),
        ("seed = 2147483648", "outside 0..2147483647"),
        ("seed = 1.5", "seed must be a whole number"),
        ("horizon = 6", "unknown schedule settings ['horizon']"),
        ("configuration = ", "not TOML"),
    ],
)
def test_a_setting_that_cannot_be_obeyed_is_refused(text: str, refusal: str) -> None:
    with pytest.raises(ScheduleConfigError, match=refusal.replace("[", r"\[")):
        parse_schedule_config(text)


def test_a_turnaround_of_zero_is_allowed() -> None:
    """Positive control for the refusal above: zero is the honest value."""
    assert parse_schedule_config("turnaround_s = 0").turnaround_s == 0


def test_the_hash_is_of_the_values_not_the_text() -> None:
    plain = parse_schedule_config('configuration = "B"\nseed = 3\n')
    commented = parse_schedule_config('# ours\nseed = 3\nconfiguration = "B"\n')

    assert schedule_config_sha256(plain) == schedule_config_sha256(commented)
    assert schedule_config_sha256(plain) != schedule_config_sha256(ScheduleConfig())


def test_a_model_path_is_under_the_root_unless_absolute(tmp_path: Path) -> None:
    relative = ScheduleConfig(configuration="D", model="models/abc")
    absolute = ScheduleConfig(configuration="D", model=str(tmp_path / "m"))

    assert model_path(relative, tmp_path / "root") == tmp_path / "root/models/abc"
    assert model_path(absolute, tmp_path / "root") == tmp_path / "m"
    assert model_path(ScheduleConfig(), tmp_path) is None


@pytest.mark.parametrize(
    ("configuration", "model", "allowed"),
    [
        ("A", "A", True),
        ("A", "B", True),
        ("B", "A", True),
        ("C", "C", True),
        ("D", "D", True),
        ("B", "D", False),
        ("C", "D", False),
        ("D", "C", False),
        ("D", "A", False),
    ],
)
def test_a_model_must_be_its_configuration_s(
    configuration: str, model: str, allowed: bool
) -> None:
    config = ScheduleConfig(configuration=configuration, model="models/x")

    if allowed:
        check_model(config, model)
    else:
        with pytest.raises(ScheduleConfigError, match="is scored by a model of"):
            check_model(config, model)
