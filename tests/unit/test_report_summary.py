"""``meridian report summary``: an archive run's model figures, copied for publication.

Built end to end through ``meridian report build`` over a hand-built archive
world sourced from ``satnogs_network``, with every socket and database
refused. The summary must say whose figures they are, carry only what the run
sealed, refuse a run it cannot label, and match the shape the dashboard's
reader validates strictly (D-325, D-332).

Reference: docs/DECISIONS.md D-086, D-156, D-160, D-322, D-325.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian.cli import main
from meridian.datasets.canonical import canonical_line
from meridian.datasets.manifest import Manifest, SourceEntry, content_sha256, file_entry
from meridian.datasets.publish import publish_directory, read_directory
from meridian.reports.config import parse_report_config
from meridian.reports.summary import SCHEMA, NotSummarisableError, model_summary

SINCE = datetime(2026, 9, 1, tzinfo=UTC)
AS_OF = datetime(2026, 9, 23, 6, tzinfo=UTC)
FIRST_DAY = datetime(2026, 9, 2, tzinfo=UTC)
PEAKS = (15.0, 30.0, 50.0, 75.0)
SATNOGS = SourceEntry(
    source_id="satnogs_network",
    licence="CC BY-SA 4.0",
    terms_url="https://network.satnogs.org/",
    attribution_entry="SatNOGS Network observations",
    records=1,
)
TABLES = (
    "passes",
    "assignments",
    "observations",
    "heartbeats",
    "element_sets",
    "stations",
    "capabilities",
    "satellites",
    "transmitters",
    "archive_observations",
    "archive_stations",
    "ingest_records",
    "listening",
    "archive_passes",
    "pass_tracks",
    "environment_samples",
    "areas_of_interest",
    "pass_ground_tracks",
)
SETTINGS = """
[prediction]
population = "archive"
folds = 2
train_until = 2026-09-13T00:00:00Z
validate_until = 2026-09-17T00:00:00Z
resamples = 200
"""
TOP_KEYS = {
    "schema",
    "run_sha256",
    "method",
    "label",
    "source",
    "simulated",
    "completeness_ratio",
    "split",
    "configurations",
}
COMPUTABLE_KEYS = {
    "name",
    "n_test",
    "n_decoded",
    "brier",
    "brier_interval",
    "base_rate",
    "base_brier",
    "skill",
    "skill_interval",
    "computable",
    "bins",
}
BIN_KEYS = {"low", "high", "n", "mean_predicted", "observed", "observed_interval"}


def archive_world() -> dict[str, list[dict[str, object]]]:
    """Two archive stations, twenty days, four passes a day at four peaks.

    Higher passes decode more often, by a fixed rule with exceptions, so A has
    something to learn and both outcomes are in every span of the split. The
    10th, 20th, … pass is left unheard, so completeness is below one.
    """
    passes: list[dict[str, object]] = []
    heard: list[dict[str, object]] = []
    n = 0
    for station in (7, 8):
        for day in range(20):
            for slot, peak in enumerate(PEAKS):
                aos = FIRST_DAY + timedelta(days=day, hours=1 + slot * 5 + station)
                passes.append(
                    {
                        "archive_station_id": station,
                        "satellite_id": "norad:57166",
                        "aos": aos,
                        "los": aos + timedelta(minutes=12),
                        "max_elevation_deg": peak,
                    }
                )
                n += 1
                if n % 10 == 0:
                    continue
                decoded = (peak >= 50.0) != (n % 7 == 0)
                heard.append(
                    {
                        "archive_observation_id": n,
                        "archive_station_id": station,
                        "source_id": "satnogs_network",
                        "satellite_key": "norad:57166",
                        "satellite_key_kind": "norad",
                        "started_at": aos + timedelta(seconds=30),
                        "archive_outcome": "decoded" if decoded else "no_data",
                        "source_outcome": "status=good" if decoded else "status=bad",
                    }
                )
    return {
        "archive_stations": [
            {"archive_station_id": 7, "lat_deg": 12.5, "lon_deg": 77.25},
            {"archive_station_id": 8, "lat_deg": 48.25, "lon_deg": 11.5},
        ],
        "archive_passes": passes,
        "archive_observations": heard,
    }


def publish(root: Path, tables: Mapping[str, Sequence[Mapping[str, object]]]) -> Path:
    files = {
        f"{name}.jsonl": b"".join(canonical_line(row) for row in tables.get(name, ()))
        for name in TABLES
    }
    manifest = Manifest(
        kind="raw_snapshot",
        schema_revision="0032",
        since=SINCE,
        as_of=AS_OF,
        files=tuple(file_entry(name, data) for name, data in sorted(files.items())),
        created_at=AS_OF,
        sources=(SATNOGS,),
    )
    name = f"20260923T060000Z-{content_sha256(manifest).hex()[:12]}"
    return publish_directory(root / "snapshots", name, manifest, files).path


@pytest.fixture
def guarded(no_network: Any, network_guard: Any) -> Any:
    network_guard()
    return no_network


@pytest.fixture
def run(guarded: Any, datasets_root: Path, tmp_path: Path, capsys: Any) -> Path:
    """One sealed archive run."""
    del guarded
    snapshot = publish(datasets_root, archive_world())
    config = tmp_path / "archive.toml"
    config.write_text(SETTINGS, encoding="utf-8")
    code = main(
        [
            "report",
            "--root",
            str(datasets_root),
            "build",
            "--snapshot",
            str(snapshot),
            "--config",
            str(config),
            "--seed",
            "4471",
        ]
    )
    out = capsys.readouterr()
    assert code == 0, out.err
    return Path(out.out.splitlines()[0].split(": ", 1)[1].rsplit(" (", 1)[0])


def summarise(run: Path, output: Path, capsys: Any) -> tuple[int, str]:
    code = main(["report", "summary", str(run), "--output", str(output)])
    return code, capsys.readouterr().err


def test_an_archive_run_is_summarised_with_whose_figures_they_are(
    run: Path, tmp_path: Path, capsys: Any
) -> None:
    output = tmp_path / "published" / "model-summary.json"
    code, err = summarise(run, output, capsys)
    assert code == 0, err
    summary = json.loads(output.read_text("utf-8"))

    assert summary["schema"] == SCHEMA
    assert summary["run_sha256"] == content_sha256(read_directory(run).manifest).hex()
    assert re.fullmatch(r"report-[0-9]+", summary["method"])
    assert summary["label"].startswith(
        "Measured by the SatNOGS network, not by Meridian"
    )
    assert summary["simulated"] is False
    assert summary["source"] == {
        "name": "SatNOGS Network",
        "licence": "CC BY-SA 4.0",
        "terms_url": "https://network.satnogs.org/",
        "retrieved_from": SINCE.isoformat(),
        "retrieved_until": AS_OF.isoformat(),
    }
    assert 0.0 < summary["completeness_ratio"] < 1.0


def test_a_is_figures_and_the_others_are_a_reason_and_nothing_else(
    run: Path, tmp_path: Path, capsys: Any
) -> None:
    """D-156: an archive pass has an elevation and nothing else we compute."""
    output = tmp_path / "model-summary.json"
    summarise(run, output, capsys)
    configurations = json.loads(output.read_text("utf-8"))["configurations"]

    assert [one["name"] for one in configurations] == ["A", "C", "D"]
    first, *others = configurations
    assert first["computable"] is True
    assert first["n_test"] > 0
    assert 0.0 <= first["brier"] <= 1.0
    for one in others:
        assert set(one) == {"name", "computable", "reason"}
        assert one["computable"] is False
        assert "archive" in one["reason"]


def test_the_shape_is_exactly_what_the_dashboards_reader_accepts(
    run: Path, tmp_path: Path, capsys: Any
) -> None:
    """No key the reader refuses, ten bins, counts as integers (D-332)."""
    output = tmp_path / "model-summary.json"
    summarise(run, output, capsys)
    summary = json.loads(output.read_text("utf-8"))

    assert set(summary) == TOP_KEYS
    assert set(summary["split"]) == {"train_until", "validate_until", "as_of"}
    assert re.fullmatch(r"[0-9a-f]{64}", summary["run_sha256"])
    first = summary["configurations"][0]
    assert set(first) == COMPUTABLE_KEYS
    assert isinstance(first["n_test"], int)
    assert isinstance(first["n_decoded"], int)
    assert len(first["bins"]) == 10
    for one in first["bins"]:
        assert set(one) == BIN_KEYS
        assert isinstance(one["n"], int)


def test_the_summary_is_a_pure_function_of_the_run(
    run: Path, tmp_path: Path, capsys: Any
) -> None:
    first, again = tmp_path / "one.json", tmp_path / "two.json"
    summarise(run, first, capsys)
    summarise(run, again, capsys)
    assert first.read_bytes() == again.read_bytes()


def test_a_run_whose_files_no_longer_match_its_manifest_is_refused(
    run: Path, tmp_path: Path, capsys: Any
) -> None:
    target = run / "prediction.jsonl"
    target.chmod(0o600)
    target.write_bytes(target.read_bytes().replace(b'"fitted"', b'"FITTED"', 1))
    code, err = summarise(run, tmp_path / "out.json", capsys)

    assert code != 0
    assert "prediction.jsonl" in err
    assert not (tmp_path / "out.json").exists()


def test_a_run_of_our_own_stations_has_no_label_and_is_refused(
    run: Path,
) -> None:
    """No default label: our own figures are published only once one is decided."""
    sealed = read_directory(run)
    own = sealed.files["config.toml"].replace(b'"archive"', b'"own"')
    with pytest.raises(NotSummarisableError, match="only an archive run"):
        model_summary(
            type(sealed)(
                sealed.path, sealed.manifest, {**sealed.files, "config.toml": own}
            )
        )


def test_the_archive_example_parses_and_fits_with_its_dates_set(
    guarded: Any, datasets_root: Path, tmp_path: Path, capsys: Any
) -> None:
    """The shipped config is the one the real run starts from (D-325)."""
    del guarded
    example = Path(__file__).parents[2] / "analysis/configs/archive.toml.example"
    text = example.read_text("utf-8")
    parse_report_config(text.encode("utf-8"))
    dated = text.replace(
        "# train_until = 2026-10-19T00:00:00Z", "train_until = 2026-09-13T00:00:00Z"
    ).replace(
        "# validate_until = 2026-10-26T00:00:00Z",
        "validate_until = 2026-09-17T00:00:00Z\nresamples = 200",
    )
    config = tmp_path / "archive.toml"
    config.write_text(dated, encoding="utf-8")
    snapshot = publish(datasets_root, archive_world())
    code = main(
        [
            "report",
            "--root",
            str(datasets_root),
            "build",
            "--snapshot",
            str(snapshot),
            "--config",
            str(config),
            "--seed",
            "4471",
        ]
    )
    out = capsys.readouterr()
    assert code == 0, out.err
    run = Path(out.out.splitlines()[0].split(": ", 1)[1].rsplit(" (", 1)[0])
    (first, *_) = model_summary(read_directory(run))["configurations"]  # type: ignore[misc]
    assert first["computable"] is True
