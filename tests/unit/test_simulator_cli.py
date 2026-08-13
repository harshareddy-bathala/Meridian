"""``meridian_sim.station`` — how a run is resolved, before anything registers.

No marker: this is argument parsing. It is separated from the subprocess tests
in ``test_executables.py`` because those prove the module survives being run and
these prove it resolved the right run — a process that starts cleanly against
the wrong platform is the failure that costs an afternoon.

Reference: docs/DECISIONS.md D-075, D-076, D-080.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian_sim.station import _build_parser, _config_from, _read_invites, main


def resolved(argv: list[str] | None = None) -> object:
    """The run configuration a command line and the environment produce."""
    return _config_from(_build_parser().parse_args(argv or []))


def test_the_defaults_are_the_ones_the_documentation_names() -> None:
    """A run whose seed the process invented at startup is not reproducible."""
    config = resolved()

    assert config.master_seed == 4471  # type: ignore[attr-defined]
    assert config.station_count == 1  # type: ignore[attr-defined]
    assert config.scenario == "clean"  # type: ignore[attr-defined]


def test_compose_variables_reach_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """compose passes these and no flags at all, so they have to be read."""
    monkeypatch.setenv("MERIDIAN_BASE_URL", "http://api:8000")
    monkeypatch.setenv("SIMULATOR_SEED", "9137")
    monkeypatch.setenv("SIMULATOR_STATION_COUNT", "5")
    monkeypatch.setenv("SIMULATOR_SCENARIO", "faulty")
    monkeypatch.setenv("SIMULATOR_STATE_DIR", "/srv/sim")

    config = resolved()

    assert config.base_url == "http://api:8000"  # type: ignore[attr-defined]
    assert config.master_seed == 9137  # type: ignore[attr-defined]
    assert config.station_count == 5  # type: ignore[attr-defined]
    assert config.scenario == "faulty"  # type: ignore[attr-defined]
    assert config.state_dir == Path("/srv/sim")  # type: ignore[attr-defined]


def test_a_flag_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator debugging a compose stack overrides one setting from a shell."""
    monkeypatch.setenv("SIMULATOR_SEED", "9137")

    assert resolved(["--seed", "22"]).master_seed == 22  # type: ignore[attr-defined]


def test_the_run_id_is_derived_from_the_seed_when_it_is_not_given() -> None:
    """Two runs at one seed should be comparable, and a random id would stop that."""
    assert resolved().run_id == resolved().run_id  # type: ignore[attr-defined]
    assert "4471" in resolved().run_id  # type: ignore[attr-defined]


def test_a_misspelled_scenario_is_refused_by_the_parser() -> None:
    """argparse names the valid ones, which is better than a KeyError later."""
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["--scenario", "netwrok"])


def test_a_station_count_below_one_is_refused() -> None:
    """A fleet of zero would start, report nothing and look like it was working."""
    assert main(["--count", "0"]) == 1


def test_no_invites_file_means_no_invites() -> None:
    """A restarted fleet needs none, so absent is normal rather than an error."""
    assert _read_invites("") == []


def test_invites_are_read_one_per_line(tmp_path: Path) -> None:
    """The shape `meridian invite create --count N` prints."""
    path = tmp_path / "invites.txt"
    path.write_text("alpha\nbeta\n\ngamma\n", encoding="utf-8")

    assert _read_invites(str(path)) == ["alpha", "beta", "gamma"]


def test_a_named_invites_file_that_cannot_be_read_is_reported(tmp_path: Path) -> None:
    """An operator who passed the flag meant it.

    Starting with none instead would fail one station at a time, with a message
    about registration rather than about the file that was missing.
    """
    assert main(["--invites", str(tmp_path / "absent.txt")]) == 1
