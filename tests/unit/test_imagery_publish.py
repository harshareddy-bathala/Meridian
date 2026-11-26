"""Publishing scenes: named by their manifest, kept under a cap, made again (D-375).

Every scene here is ours, written by the test: a small Sentinel‑2-shaped
archive near Bengaluru, searched end to end through the same seams the search
tests use, then built and published into a temporary directory. Nothing is
reached: the archive is local files and a fixture retriever.
"""

from __future__ import annotations

import json
import math
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

rasterio = pytest.importorskip("rasterio")
np = pytest.importorskip("numpy")

from rasterio.transform import from_origin  # noqa: E402 — after the extra is known
from rasterio.warp import transform_bounds  # noqa: E402

from meridian_ingest import cli, cli_imagery_run  # noqa: E402
from meridian_ingest.config import load_settings  # noqa: E402
from meridian_ingest.imagery import publish as publish_module  # noqa: E402
from meridian_ingest.imagery.areas import read_published_areas  # noqa: E402
from meridian_ingest.imagery.build import (  # noqa: E402
    BuildError,
    SceneRequest,
    build_scene,
)
from meridian_ingest.imagery.caption import TEMPLATE_SHA256  # noqa: E402
from meridian_ingest.imagery.model import (  # noqa: E402
    Bounds,
    ImageryManifest,
    IndexEntry,
    manifest_sha256,
)
from meridian_ingest.imagery.outcome import SceneSearch  # noqa: E402
from meridian_ingest.imagery.pixels import LocalPixels  # noqa: E402
from meridian_ingest.imagery.publish import (  # noqa: E402
    INDEX_NAME,
    MANIFEST_NAME,
    Published,
    publish_scene,
    read_index,
    read_manifest,
    retain,
    scratch_directory,
)
from meridian_ingest.imagery.search import SearchContext, search_area  # noqa: E402
from meridian_ingest.imagery.sources import (  # noqa: E402
    SENTINEL2_SOURCE,
    platform_name,
)
from meridian_ingest.imagery.stac import search_artefact  # noqa: E402
from meridian_ingest.politeness import RequestBudget  # noqa: E402
from meridian_ingest.provenance import Provenance  # noqa: E402
from meridian_ingest.raw_store import RawStore  # noqa: E402
from meridian_ingest.retrieval import MappedFixtureRetriever  # noqa: E402

UTM = "EPSG:32643"
BOX = Bounds(77.58, 12.96, 77.60, 12.98)
NOW = datetime(2026, 11, 18, 6, 0, tzinfo=UTC)
AREA_ID = 3
REPORT = "ab" * 32
CAP = 10**9


# --- an archive the test writes ----------------------------------------------------


def _write(path: Path, *, resolution: float, fill: int, bands: int) -> Path:
    """A GeoTIFF on a grid aligned to its pixel size, a margin around the box."""
    left, bottom, right, top = transform_bounds(
        "EPSG:4326", UTM, *BOX.to_list(), densify_pts=21
    )
    x0 = math.floor((left - 500) / resolution) * resolution
    y0 = math.ceil((top + 500) / resolution) * resolution
    width = round(
        (math.ceil((right + 500) / resolution) * resolution - x0) / resolution
    )
    y1 = math.floor((bottom - 500) / resolution) * resolution
    height = round((y0 - y1) / resolution)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=bands,
        dtype="uint8",
        crs=UTM,
        transform=from_origin(x0, y0, resolution, resolution),
        nodata=0,
    ) as target:
        target.write(np.full((bands, height, width), fill, dtype="uint8"))
    return path


class Archive:
    """A catalogue answer and the scenes it points at."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.items: list[dict[str, object]] = []
        self.files: dict[str, Path] = {}

    def scene(self, item_id: str, *, days_ago: float, colour: int = 120) -> None:
        when = NOW - timedelta(days=days_ago)
        base = f"https://cogs.example.invalid/{item_id}"
        self.items.append(
            {
                "type": "Feature",
                "id": item_id,
                "bbox": [77.0, 12.5, 78.0, 13.5],
                "properties": {
                    "datetime": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "platform": "sentinel-2b",
                    "proj:epsg": 32643,
                    "s2:datatake_id": item_id,
                    "eo:cloud_cover": 3.0,
                    "updated": "2026-11-17T00:00:00Z",
                },
                "assets": {
                    name: {"href": f"{base}/{name}.tif"} for name in ("visual", "scl")
                },
            }
        )
        self.files[f"{base}/scl.tif"] = _write(
            self.root / f"{item_id}-scl.tif", resolution=20.0, fill=4, bands=1
        )
        self.files[f"{base}/visual.tif"] = _write(
            self.root / f"{item_id}-visual.tif", resolution=10.0, fill=colour, bands=3
        )

    def context(self, store: RawStore, *, clock: datetime = NOW) -> SearchContext:
        answer = self.root / "search.json"
        answer.write_text(
            json.dumps({"type": "FeatureCollection", "features": self.items}),
            encoding="utf-8",
        )
        return SearchContext(
            retriever=MappedFixtureRetriever(
                {search_artefact(SENTINEL2_SOURCE, BOX, NOW).url: answer}
            ),
            pixels=LocalPixels(self.files),
            store=store,
            budget=RequestBudget(100),
            now=NOW,
            clock=lambda: clock,
        )


def write_summary(path: Path, *, report: str = REPORT) -> Path:
    body = {
        "schema": "areas-summary-1",
        "report_sha256": report,
        "areas": [
            {
                "area_id": AREA_ID,
                "label": "Bengaluru urban",
                "area_km2": 4.8,
                "bbox": BOX.to_list(),
            }
        ],
        "changes": [],
    }
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def put_fires(
    store: RawStore, day: datetime, rows: list[tuple[float, float, float]]
) -> str:
    """A FIRMS day over Karnataka, retrieved the next morning."""
    lines = [
        "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
        "instrument,confidence,version,bright_ti5,frp,daynight"
    ]
    lines += [
        f"{lat:.5f},{lon:.5f},330.1,0.39,0.36,{day:%Y-%m-%d},0530,N,VIIRS,h,"
        f"2.0NRT,290.2,{frp:.2f},D"
        for lat, lon, frp in rows
    ]
    provenance = Provenance(
        source_id="nasa_firms",
        original_identifier="nasa_firms/synthetic",
        source_version='"synthetic"',
        payload_kind="data",
        retrieved_at=day + timedelta(days=1, hours=len(store.scan("nasa_firms"))),
        media_type="text/csv",
        valid_from=day,
        valid_to=day + timedelta(days=1),
        spatial_extent={"west": 74.0, "south": 11.5, "east": 78.6, "north": 18.5},
    )
    return store.publish(provenance, [("\n".join(lines) + "\n").encode()]).raw_path


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore(tmp_path / "raw")


@pytest.fixture
def summary(tmp_path: Path) -> Path:
    return write_summary(tmp_path / "areas-summary.json")


def searched(
    archive: Archive, store: RawStore, *, clock: datetime = NOW
) -> SceneSearch:
    area = read_published_areas(write_summary(archive.root / "s.json")).areas[0]
    return search_area(SENTINEL2_SOURCE, area, archive.context(store, clock=clock))


def request_for(found: SceneSearch, summary: Path) -> SceneRequest:
    published = read_published_areas(summary)
    assert found.chosen is not None
    return SceneRequest(
        area=published.areas[0],
        published=published,
        summary_path=summary,
        raw_paths=(
            *((found.search_path,) if found.search_path else ()),
            *found.chosen.raw_paths,
            *found.windows,
        ),
    )


def published(
    root: Path,
    store: RawStore,
    request: SceneRequest,
    *,
    now: datetime = NOW,
    max_bytes: int = CAP,
) -> tuple[ImageryManifest, Published]:
    scratch = scratch_directory(root)
    manifest = build_scene(store, request, scratch)
    return manifest, publish_scene(
        root, scratch, manifest, now=now, max_bytes=max_bytes
    )


def scene_directories(root: Path) -> list[str]:
    return sorted(one.name for one in root.iterdir() if len(one.name) == 12)


# --- building a scene -----------------------------------------------------------------


def test_a_scene_is_built_from_its_raw_paths_and_names_each_of_them(
    tmp_path: Path, store: RawStore, summary: Path, network_guard: Callable[[], None]
) -> None:
    archive = Archive(tmp_path / "archive")
    archive.scene("S2B_one", days_ago=2)
    found = searched(archive, store)
    acquired = NOW - timedelta(days=2)
    put_fires(store, acquired.replace(hour=0, minute=0), [(12.97, 77.59, 6.5)])
    network_guard()

    manifest = build_scene(store, request_for(found, summary), tmp_path / "scene")

    assert manifest.platform == "Sentinel-2B"
    assert manifest.acquired_at == acquired
    assert found.chosen is not None
    assert manifest.cloud_fraction == found.chosen.cloud_fraction == 0.0
    assert [one.kind for one in manifest.annotations] == ["area", "fire"]
    kinds = [one.kind for one in manifest.inputs]
    assert kinds == [
        "catalogue",
        "classification",
        "scene",
        "areas_summary",
        "firms",
        "template",
    ]
    assert manifest.inputs[-1].sha256 == TEMPLATE_SHA256
    assert "1 active fire detection by NASA FIRMS" in manifest.caption
    on_disk = sorted(
        str(one.relative_to(tmp_path / "scene"))
        for one in (tmp_path / "scene").rglob("*")
        if one.is_file()
    )
    assert on_disk == sorted(one.path for one in manifest.files)


def test_a_stac_scene_without_its_cloud_layer_is_refused(
    tmp_path: Path, store: RawStore, summary: Path
) -> None:
    archive = Archive(tmp_path / "archive")
    archive.scene("S2B_one", days_ago=2)
    found = searched(archive, store)
    request = request_for(found, summary)
    assert found.chosen is not None
    without = tuple(p for p in request.raw_paths if p not in found.chosen.raw_paths)

    with pytest.raises(BuildError, match="scl windows its cloud is counted from"):
        build_scene(store, replace(request, raw_paths=without), tmp_path / "scene")


def test_satellites_are_named_as_their_operators_name_them() -> None:
    assert platform_name("sentinel-2b") == "Sentinel-2B"
    assert platform_name("landsat-9") == "Landsat 9"
    assert platform_name("NOAA-20") == "NOAA-20"


# --- publishing --------------------------------------------------------------------


def test_a_scene_is_published_under_its_manifests_digest_and_sealed(
    tmp_path: Path, store: RawStore, summary: Path
) -> None:
    archive = Archive(tmp_path / "archive")
    archive.scene("S2B_one", days_ago=2)
    root = tmp_path / "imagery"

    manifest, done = published(
        root, store, request_for(searched(archive, store), summary)
    )

    assert done.written and done.said == "published"
    assert done.hash == manifest_sha256(manifest)[:12]
    assert read_manifest(root, done.hash) == manifest
    index = read_index(root)
    assert [one.hash for one in index.scenes] == [done.hash]
    entry = index.scenes[0]
    assert (entry.area_id, entry.source_class, entry.published_at) == (
        AREA_ID,
        "high_resolution",
        NOW,
    )
    directory = root / done.hash
    sizes = sum(one.stat().st_size for one in directory.rglob("*") if one.is_file())
    assert entry.size_bytes == sizes
    for path in [directory, *directory.rglob("*")]:
        assert not path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    assert not list((root / ".incoming").iterdir())


def test_publishing_the_same_inputs_twice_gives_one_directory(
    tmp_path: Path, store: RawStore, summary: Path
) -> None:
    archive = Archive(tmp_path / "archive")
    archive.scene("S2B_one", days_ago=2)
    root = tmp_path / "imagery"
    request = request_for(searched(archive, store), summary)

    _, first = published(root, store, request)
    _, second = published(root, store, request, now=NOW + timedelta(days=1))

    assert second.hash == first.hash and not second.written
    assert scene_directories(root) == [first.hash]
    assert read_index(root).scenes[0].published_at == NOW, "first publication kept"


def test_a_new_fetch_of_a_scene_already_shown_publishes_nothing(
    tmp_path: Path, store: RawStore, summary: Path
) -> None:
    """Tomorrow's refresh reads the same scene into new raw paths (D-375)."""
    archive = Archive(tmp_path / "archive")
    archive.scene("S2B_one", days_ago=2)
    root = tmp_path / "imagery"
    _, first = published(root, store, request_for(searched(archive, store), summary))

    tomorrow = searched(archive, store, clock=NOW + timedelta(days=1))
    again, second = published(root, store, request_for(tomorrow, summary))

    assert manifest_sha256(again)[:12] != first.hash, "its inputs are new"
    assert (second.hash, second.written) == (first.hash, False)
    assert second.said.startswith(f"already held as {first.hash}")
    assert scene_directories(root) == [first.hash]


def test_a_scene_whose_markers_changed_replaces_it_and_the_old_one_goes(
    tmp_path: Path, store: RawStore, summary: Path
) -> None:
    archive = Archive(tmp_path / "archive")
    archive.scene("S2B_one", days_ago=2)
    root = tmp_path / "imagery"
    request = request_for(searched(archive, store), summary)
    _, first = published(root, store, request)

    day = (NOW - timedelta(days=2)).replace(hour=0, minute=0)
    put_fires(store, day, [(12.97, 77.59, 6.5)])
    _, second = published(root, store, request)

    assert second.written and second.hash != first.hash
    assert second.removed == (first.hash,)
    assert scene_directories(root) == [second.hash]
    assert [one.hash for one in read_index(root).scenes] == [second.hash]


def test_the_index_is_rewritten_before_a_directory_is_removed(
    tmp_path: Path, store: RawStore, summary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = Archive(tmp_path / "archive")
    archive.scene("S2B_one", days_ago=2)
    root = tmp_path / "imagery"
    request = request_for(searched(archive, store), summary)
    _, first = published(root, store, request)
    put_fires(store, (NOW - timedelta(days=2)).replace(hour=0), [(12.97, 77.59, 1.0)])
    listed_when_removed: list[list[str]] = []
    remove = publish_module._remove

    def watched(path: Path) -> None:
        if path.name == first.hash:
            listed_when_removed.append([one.hash for one in read_index(root).scenes])
        remove(path)

    monkeypatch.setattr(publish_module, "_remove", watched)
    published(root, store, request)

    assert listed_when_removed and first.hash not in listed_when_removed[0]


def test_an_interrupted_publish_leaves_no_index_entry_and_no_scratch(
    tmp_path: Path, store: RawStore, summary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = Archive(tmp_path / "archive")
    archive.scene("S2B_one", days_ago=2)
    root = tmp_path / "imagery"

    def disk_full(directory: Path) -> None:
        raise OSError(28, "No space left on device", str(directory))

    monkeypatch.setattr(publish_module, "_seal", disk_full)
    with pytest.raises(OSError, match="No space"):
        published(root, store, request_for(searched(archive, store), summary))

    assert not (root / INDEX_NAME).exists()
    assert scene_directories(root) == []
    assert not list((root / ".incoming").iterdir())


def test_the_cap_removes_the_oldest_but_never_an_areas_latest(
    tmp_path: Path, store: RawStore, summary: Path
) -> None:
    root = tmp_path / "imagery"
    older = Archive(tmp_path / "older")
    older.scene("S2B_older", days_ago=9, colour=90)
    _, first = published(root, store, request_for(searched(older, store), summary))
    newer = Archive(tmp_path / "newer")
    newer.scene("S2B_newer", days_ago=3, colour=150)

    _, second = published(
        root, store, request_for(searched(newer, store), summary), max_bytes=1
    )

    assert second.written
    assert second.removed == (first.hash,)
    assert [one.hash for one in read_index(root).scenes] == [second.hash]


def _entry(name: str, area: int, days_ago: int, size: int = 100) -> IndexEntry:
    return IndexEntry(
        hash=name * 12,
        area_id=area,
        source_class="high_resolution",
        source_id="sentinel2_l2a",
        acquired_at=NOW - timedelta(days=days_ago),
        published_at=NOW,
        size_bytes=size,
    )


def test_retention_drops_the_oldest_acquisitions_first() -> None:
    entries = [_entry("a", 1, 30), _entry("b", 1, 20), _entry("c", 1, 10)]

    assert [one.hash[0] for one in retain(entries, 200)] == ["c", "b"]
    assert [one.hash[0] for one in retain(entries, 300)] == ["c", "b", "a"]


def test_retention_keeps_each_areas_latest_above_the_cap() -> None:
    entries = [_entry("a", 1, 30, 900), _entry("b", 2, 40, 900), _entry("c", 1, 50)]

    kept = retain(entries, 1)

    assert [one.hash[0] for one in kept] == ["a", "b"]


def test_a_damaged_index_is_never_replaced_on_a_guess(
    tmp_path: Path, store: RawStore, summary: Path
) -> None:
    archive = Archive(tmp_path / "archive")
    archive.scene("S2B_one", days_ago=2)
    root = tmp_path / "imagery"
    root.mkdir()
    (root / INDEX_NAME).write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="is not JSON"):
        published(root, store, request_for(searched(archive, store), summary))

    assert (root / INDEX_NAME).read_text(encoding="utf-8") == "{not json"


# --- the command -----------------------------------------------------------------


@pytest.fixture
def config(tmp_path: Path, summary: Path) -> Path:
    path = tmp_path / "ingest.toml"
    path.write_text(
        f'raw_root = "raw"\n[imagery]\nsummary = "{summary.name}"\n'
        'published = "imagery"\nsources = ["sentinel2_l2a"]\n',
        encoding="utf-8",
    )
    return path


def test_render_list_and_verify_with_no_socket(  # noqa: PLR0913, PLR0917 — fixtures
    tmp_path: Path,
    store: RawStore,
    config: Path,
    summary: Path,
    network_guard: Callable[[], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = Archive(tmp_path / "archive")
    archive.scene("S2B_one", days_ago=2)
    raw = request_for(searched(archive, store), summary).raw_paths
    network_guard()
    args = ["--config", str(config), "imagery"]

    assert cli.main([*args, "render", "--area", str(AREA_ID), *raw]) == 0
    (name,) = scene_directories(tmp_path / "imagery")
    assert cli.main([*args, "list"]) == 0
    assert name in capsys.readouterr().out

    # A FIRMS file arriving later is not one this scene was made from.
    put_fires(store, (NOW - timedelta(days=2)).replace(hour=0), [(12.97, 77.59, 9.0)])
    assert cli.main([*args, "verify", name]) == 0
    assert "to the same manifest" in capsys.readouterr().out


def test_verify_says_which_file_no_longer_matches(
    tmp_path: Path,
    store: RawStore,
    config: Path,
    summary: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = Archive(tmp_path / "archive")
    archive.scene("S2B_one", days_ago=2)
    root = tmp_path / "imagery"
    _, done = published(root, store, request_for(searched(archive, store), summary))
    world = root / done.hash / "scene.pgw"
    (root / done.hash).chmod(0o755)
    world.chmod(0o644)
    world.write_text("0\n0\n0\n0\n0\n0\n", encoding="ascii")

    code = cli.main(["--config", str(config), "imagery", "verify", done.hash])

    assert code == 1
    assert f"{done.hash}/scene.pgw no longer matches" in capsys.readouterr().err


def test_verify_needs_the_summary_the_scene_was_made_from(
    tmp_path: Path,
    store: RawStore,
    config: Path,
    summary: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = Archive(tmp_path / "archive")
    archive.scene("S2B_one", days_ago=2)
    root = tmp_path / "imagery"
    _, done = published(root, store, request_for(searched(archive, store), summary))
    later = write_summary(tmp_path / "later.json", report="ef" * 32)

    code = cli.main(
        [
            "--config",
            str(config),
            "imagery",
            "verify",
            done.hash,
            "--summary",
            str(later),
        ]
    )

    assert code == 1
    assert "areas summary of report abababababab" in capsys.readouterr().err


def test_a_refresh_round_publishes_each_areas_scene(
    tmp_path: Path,
    store: RawStore,
    config: Path,
    summary: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = Archive(tmp_path / "archive")
    archive.scene("S2B_one", days_ago=2)

    @contextmanager
    def local(settings: object, now: datetime) -> Iterator[SearchContext]:
        del settings, now
        yield archive.context(store)

    monkeypatch.setattr(cli_imagery_run, "searching", local)
    settings = load_settings(config)

    failed = cli_imagery_run.refresh_round(
        settings, (SENTINEL2_SOURCE,), summary, None, now=NOW
    )

    assert not failed
    (entry,) = read_index(tmp_path / "imagery").scenes
    assert (tmp_path / "imagery" / entry.hash / MANIFEST_NAME).is_file()


def test_list_runs_without_the_imagery_extra_and_says_when_none_is_published(
    tmp_path: Path, config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "imagery").mkdir()

    assert cli.main(["--config", str(config), "imagery", "list"]) == 0
    assert "no scene is published" in capsys.readouterr().out
