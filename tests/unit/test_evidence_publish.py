"""The public cut of the evidence dataset, and publishing it for the public API.

Marked as a unit test by living in ``tests/unit``. Run in process through
``meridian.cli.main``, against raw snapshots published by hand
(``tests/unit/conftest.py``), with every database connection and socket
refused.

What the data page may serve (D-369):
- the public cut states every time to the minute and every angle to the
  degree, widening windows, and carries no SNR or Doppler series and nothing a
  diagnosis's tests found;
- a full-precision package is never published;
- the published manifest keeps the package's hash and drops the path it was
  built from;
- the index is a function of the packages alone, and is exactly the shape the
  API checks it against (``meridian.api.public.models.datasets``);
- a directory not named by its hash, or not a public cut, stops the index.

Reference: docs/DECISIONS.md D-082, D-093, D-310, D-313, D-332, D-369.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian.api.public.models.datasets import PublicDatasets
from meridian.cli import main
from meridian.datasets.evidence_index import NotPublishableError, write_index
from meridian.datasets.manifest import content_sha256, parse_manifest
from meridian.datasets.publish import read_directory

EXIT_FAILED = 1
SHIFT = timedelta(seconds=17, milliseconds=400)

pytestmark = pytest.mark.usefixtures("guarded")


@pytest.fixture
def guarded(no_network: Any, network_guard: Any) -> Any:
    network_guard()
    return no_network


@pytest.fixture
def snapshot(evidence_snapshot: Callable[[Any], Path], evidence_world: Any) -> Path:
    """The evidence world, with the first reception off the whole minute."""
    world = copy.deepcopy(evidence_world)
    for table in ("passes", "assignments", "observations"):
        for row in world[table]:
            if row.get("id") == 1 or row.get("assignment_id") == "as_1":
                for name, value in list(row.items()):
                    if name.endswith(("_at", "aos", "los")) and value is not None:
                        row[name] = value + SHIFT
                if table == "passes":
                    row["max_elevation_deg"] = 41.6
                    row["aos_azimuth_deg"] = 349.5
    return evidence_snapshot(world)


def config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / f"{abs(hash(text))}.toml"
    path.write_text(text, "utf-8")
    return path


def public(tmp_path: Path, populations: str = '["measured"]') -> Path:
    return config(
        tmp_path, f'[export]\npopulations = {populations}\nprecision = "public"\n'
    )


def export(root: Path, snapshot: Path, settings: Path) -> int:
    return main(
        [
            "dataset",
            "--root",
            str(root),
            "export",
            "--snapshot",
            str(snapshot),
            "--config",
            str(settings),
            "--seed",
            "4471",
        ]
    )


def publish(root: Path, package: Path, published: Path) -> int:
    return main(
        [
            "dataset",
            "--root",
            str(root),
            "publish",
            str(package),
            "--published",
            str(published),
        ]
    )


def package_path(printed: str) -> Path:
    """The directory the first line of an export or a publish names."""
    first = printed.splitlines()[0]
    return Path(first.split(": ", 1)[1].rsplit(" (", 1)[0])


def exported(
    root: Path, snapshot: Path, settings: Path, capsys: pytest.CaptureFixture[str]
) -> Path:
    assert export(root, snapshot, settings) == 0
    return package_path(capsys.readouterr().out)


def rows(package: Path, name: str = "receptions.jsonl") -> dict[str, Any]:
    held = read_directory(package).files[name]
    return {
        one["assignment_id"]: one
        for one in (json.loads(line) for line in held.splitlines())
    }


def test_the_public_cut_states_times_to_the_minute_and_angles_to_the_degree(
    snapshot: Path,
    datasets_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    full = rows(
        exported(datasets_root, snapshot, config(tmp_path, "[export]\n"), capsys)
    )["as_1"]
    cut = rows(exported(datasets_root, snapshot, public(tmp_path), capsys))["as_1"]

    assert full["window_start"].endswith(":17.400Z")
    assert cut["window_start"] == full["window_start"][:17] + "00.000Z"
    assert cut["window_end"] > full["window_end"]
    assert cut["window_end"].endswith(":00.000Z")
    assert cut["pass"]["aos"] <= full["pass"]["aos"] < full["pass"]["los"]
    assert cut["pass"]["los"] >= full["pass"]["los"]
    assert (cut["pass"]["max_elevation_deg"], cut["pass"]["aos_azimuth_deg"]) == (
        42,
        350,
    )
    observation = cut["observation"]
    for name in ("started_at", "ended_at", "first_detection_at", "submitted_at"):
        assert observation[name].endswith(":00.000Z"), name
    assert observation["snr_samples"] is None
    assert observation["doppler_samples"] is None
    assert observation["peak_snr_db"] == full["observation"]["peak_snr_db"]
    assert all(one["submitted_at"].endswith(":00.000Z") for one in cut["lineage"])
    assert cut["verdicts"] == full["verdicts"]


def test_a_diagnosis_keeps_its_causes_but_not_what_each_test_found(
    snapshot: Path,
    datasets_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    [diagnosis] = rows(exported(datasets_root, snapshot, public(tmp_path), capsys))[
        "as_2"
    ]["diagnoses"]

    assert diagnosis["cause"] == "station_not_listening"
    assert diagnosis["candidates_json"] == [
        {"cause": "station_not_listening", "fired": True, "support": None}
    ]


def test_the_datasheet_says_it_is_the_public_cut_and_full_precision_does_not(
    snapshot: Path,
    datasets_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cut = read_directory(exported(datasets_root, snapshot, public(tmp_path), capsys))
    full = read_directory(
        exported(datasets_root, snapshot, config(tmp_path, "[export]\n"), capsys)
    )

    assert b"## The public cut" in cut.files["datasheet.md"]
    assert cut.manifest.parameters["precision"] == "public"
    assert b"public cut" not in full.files["datasheet.md"]
    assert "precision" not in full.manifest.parameters


def test_a_full_precision_package_is_never_published(
    snapshot: Path,
    datasets_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = exported(datasets_root, snapshot, config(tmp_path, "[export]\n"), capsys)
    published = datasets_root / "published"

    assert publish(datasets_root, package, published) == EXIT_FAILED
    assert 'precision = "public"' in capsys.readouterr().err
    assert not (published / "evidence" / "index.json").exists()


def test_a_published_package_keeps_its_hash_and_drops_where_it_was_built(
    snapshot: Path,
    datasets_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = exported(datasets_root, snapshot, public(tmp_path), capsys)
    published = datasets_root / "published"

    assert publish(datasets_root, package, published) == 0
    copy_ = package_path(capsys.readouterr().out)
    original = read_directory(package).manifest
    held = read_directory(copy_)

    assert content_sha256(held.manifest) == content_sha256(original)
    assert copy_.name == content_sha256(original).hex()[:12]
    assert "snapshot_path" in original.environment
    assert "snapshot_path" not in held.manifest.environment
    assert set(held.manifest.environment) == {"code", "python", "dependencies"}
    assert str(snapshot) not in (copy_ / "manifest.json").read_text("utf-8")


def test_the_index_is_the_shape_the_api_checks_and_every_digest_is_true(
    snapshot: Path,
    datasets_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    published = datasets_root / "published"
    for settings in (public(tmp_path), public(tmp_path, '["measured", "simulated"]')):
        package = exported(datasets_root, snapshot, settings, capsys)
        assert publish(datasets_root, package, published) == 0
        capsys.readouterr()
    index = published / "evidence" / "index.json"

    served = PublicDatasets.model_validate_json(index.read_bytes())

    assert len(served.packages) == 2
    for one in served.packages:
        held = index.parent / one.name
        manifest = parse_manifest((held / "manifest.json").read_bytes())
        assert one.content_sha256 == content_sha256(manifest).hex()
        assert one.content_sha256 in one.citation
        for file in one.files:
            data = (held / file.name).read_bytes()
            assert (file.sha256, file.size_bytes) == (
                hashlib.sha256(data).hexdigest(),
                len(data),
            )
    both = next(one for one in served.packages if len(one.populations) == 2)
    assert [(one.population, one.simulated) for one in both.populations] == [
        ("measured", False),
        ("simulated", True),
    ]


def test_publishing_again_lists_the_package_once(
    snapshot: Path,
    datasets_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = exported(datasets_root, snapshot, public(tmp_path), capsys)
    published = datasets_root / "published"
    assert publish(datasets_root, package, published) == 0
    first = (published / "evidence" / "index.json").read_bytes()

    assert publish(datasets_root, package, published) == 0
    assert "already published, identically" in capsys.readouterr().out
    assert (published / "evidence" / "index.json").read_bytes() == first


def test_a_directory_not_named_by_its_hash_stops_the_index(
    snapshot: Path,
    datasets_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = exported(datasets_root, snapshot, public(tmp_path), capsys)
    published = datasets_root / "published"
    assert publish(datasets_root, package, published) == 0
    copy_ = package_path(capsys.readouterr().out)
    shutil.copytree(copy_, copy_.parent / "renamed")

    with pytest.raises(NotPublishableError, match="not by its hash"):
        write_index(published)


def test_a_full_precision_directory_placed_by_hand_stops_the_index(
    snapshot: Path,
    datasets_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    full = exported(datasets_root, snapshot, config(tmp_path, "[export]\n"), capsys)
    published = datasets_root / "published"
    (published / "evidence").mkdir(parents=True)
    shutil.copytree(full, published / "evidence" / full.name.rsplit("-", 1)[-1])

    with pytest.raises(NotPublishableError, match="not a public cut"):
        write_index(published)
