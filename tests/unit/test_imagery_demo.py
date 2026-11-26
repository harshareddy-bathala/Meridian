"""The demonstration's scene: synthetic, drawn by the real pipeline, labelled (D-378).

``imagery demo`` writes a small archive of its own and searches, counts,
renders and publishes it with the code a real scene goes through. These pin
that it opens no socket, that every layer says the scene is simulated, that a
second run on the same day publishes nothing new, that it can be verified like
any other scene, and that no network search can reach its source.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("rasterio")

from meridian_ingest import cli
from meridian_ingest.imagery.demo import utm_epsg
from meridian_ingest.imagery.publish import read_index, read_manifest

BOX = [77.58, 12.96, 77.60, 12.98]


@pytest.fixture
def config(tmp_path: Path) -> Path:
    (tmp_path / "areas.json").write_text(
        json.dumps(
            {
                "schema": "areas-summary-1",
                "report_sha256": "ab" * 32,
                "areas": [
                    {
                        "area_id": 3,
                        "label": "Bengaluru urban",
                        "area_km2": 4.8,
                        "bbox": BOX,
                    }
                ],
                "changes": [],
            }
        ),
        encoding="utf-8",
    )
    path = tmp_path / "ingest.toml"
    path.write_text(
        'raw_root = "raw"\n[imagery]\nsummary = "areas.json"\npublished = "imagery"\n',
        encoding="utf-8",
    )
    return path


def test_the_demo_publishes_a_scene_labelled_simulated_at_every_layer(
    tmp_path: Path,
    config: Path,
    network_guard: Callable[[], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    network_guard()

    assert cli.main(["--config", str(config), "imagery", "demo"]) == 0

    root = tmp_path / "imagery"
    (entry,) = read_index(root).scenes
    manifest = read_manifest(root, entry.hash)
    assert entry.simulated and manifest.simulated
    assert manifest.source_id == "simulated_scene"
    assert manifest.platform == "No satellite (simulated)"
    assert manifest.caption.startswith("Simulated image of Bengaluru urban")
    assert "not a satellite's" in manifest.caption
    assert manifest.credit.startswith("Simulated:")
    assert manifest.cloud_fraction == 0.0
    assert "published" in capsys.readouterr().out


def test_a_second_demo_on_the_same_day_publishes_nothing_new(
    tmp_path: Path, config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["--config", str(config), "imagery", "demo"]
    assert cli.main(args) == 0
    capsys.readouterr()

    assert cli.main(args) == 0

    assert "already held" in capsys.readouterr().out
    assert len(read_index(tmp_path / "imagery").scenes) == 1


def test_the_demo_scene_is_verified_like_any_other(
    tmp_path: Path, config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--config", str(config), "imagery", "demo"]) == 0
    (entry,) = read_index(tmp_path / "imagery").scenes

    assert cli.main(["--config", str(config), "imagery", "verify", entry.hash]) == 0
    assert "to the same manifest" in capsys.readouterr().out


def test_no_network_search_can_reach_the_simulated_source(
    config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for verb in ("search", "refresh"):
        args = ["--config", str(config), "imagery", verb, "--source", "simulated_scene"]
        assert cli.main(args) == 1
        assert "which no network holds" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("lon", "lat", "epsg"),
    [
        (77.59, 12.97, 32643),
        (76.4, 11.7, 32643),
        (-0.1, 51.5, 32630),
        (151.2, -33.9, 32756),
    ],
)
def test_the_demo_grid_is_the_points_utm_zone(
    lon: float, lat: float, epsg: int
) -> None:
    assert utm_epsg(lon, lat) == epsg
