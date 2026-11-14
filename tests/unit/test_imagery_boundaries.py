"""What keeps imagery a picture and never a measurement (D-133, D-371).

``meridian_ingest.imagery`` is the one package in the archive layer allowed to
decode an image. These rules are what make that safe, each with a control that
shows the scan would notice a crossing:

1. **No image library outside it.** ``test_public_sources`` holds every adapter
   and normaliser to that; this file holds the package's own edges.
2. **Nothing outside it imports it**, except the command that runs it. An
   adapter or a normaliser that could reach the renderer could hand a pixel to
   a sample.
3. **It imports no loader and no store.** It may *read* records — a FIRMS
   normaliser's output, a published summary — but has no path by which
   anything it computes from a pixel can be written as a sample.
4. **Its record-only modules import no image library either.** The model, the
   annotator and the caption are built from records, so a marker cannot be
   placed by reading the scene (D-374).
5. **GDAL is reached by one door.** ``rasterio`` is called as ``rasterio.open``
   and never bound early, and one module alone may write a ``/vsicurl/`` path,
   so the guard in ``tests/conftest.py`` stands where every network read enters.

Reference: docs/DECISIONS.md D-133, D-142, D-371.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INGEST = REPO_ROOT / "ingest" / "src" / "meridian_ingest"
IMAGERY = INGEST / "imagery"

IMAGE_LIBRARIES = ("PIL", "imageio", "cv2", "skimage", "matplotlib", "rasterio")
"""Every library that can turn bytes into pixels, as ``test_public_sources`` lists."""

MAY_IMPORT_IMAGERY = frozenset({"cli.py", "cli_imagery.py"})
"""The modules outside the package that may import it: the command and its verbs."""

WRITERS = ("meridian_ingest.load", "meridian_ingest.load_rows", "meridian.store")
"""Where a sample is written. ``load`` prefixes ``load_report`` and
``load_element_sets`` too, which is intended: none of them is imagery's."""

RECORD_ONLY = ("model", "available", "annotate", "caption", "gazetteer")
"""Imagery modules built from records alone, by name or name prefix."""

NETWORK_DOOR = "pixels.py"
"""The one imagery module allowed to write a ``/vsicurl/`` path (D-372)."""


def _imports(path: Path) -> Iterator[tuple[int, str]]:
    """Each absolute import in ``path``, as its line and full module name."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module
            for alias in node.names:
                yield node.lineno, f"{node.module}.{alias.name}"


def _names(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == prefix or module.startswith(prefix) for prefix in prefixes)


def _crossings_into_imagery(root: Path) -> list[str]:
    imagery = root / "imagery"
    found = []
    for path in sorted(root.rglob("*.py")):
        if path.is_relative_to(imagery) or path.name in MAY_IMPORT_IMAGERY:
            continue
        for line, module in _imports(path):
            if module.startswith("meridian_ingest.imagery"):
                found.append(f"{path.relative_to(root)}:{line} imports {module}")
    return found


def _writers_reached(imagery: Path) -> list[str]:
    return [
        f"{path.name}:{line} imports {module}"
        for path in sorted(imagery.rglob("*.py"))
        for line, module in _imports(path)
        if _names(module, WRITERS)
    ]


def _record_only_decoders(imagery: Path) -> list[str]:
    found = []
    for path in sorted(imagery.rglob("*.py")):
        if not path.stem.startswith(RECORD_ONLY):
            continue
        for line, module in _imports(path):
            if module.split(".")[0] in IMAGE_LIBRARIES or module.startswith("numpy"):
                found.append(f"{path.name}:{line} imports {module}")
    return found


def _doors(root: Path) -> list[str]:
    found = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for line, module in _imports(path):
            if module == "rasterio.open":
                found.append(f"{path.relative_to(root)}:{line} binds rasterio.open")
        if "/vsicurl" in text and path.name != NETWORK_DOOR:
            found.append(f"{path.relative_to(root)} writes a /vsicurl/ path")
    return found


def test_the_package_exists() -> None:
    assert (IMAGERY / "__init__.py").is_file()


def test_nothing_outside_imagery_imports_it() -> None:
    assert _crossings_into_imagery(INGEST) == []


def test_imagery_imports_no_loader_and_no_store() -> None:
    assert _writers_reached(IMAGERY) == []


def test_record_only_modules_import_no_image_library() -> None:
    assert _record_only_decoders(IMAGERY) == []


def test_gdal_is_reached_by_one_door() -> None:
    assert _doors(INGEST) == []


# --- each scan would notice a crossing --------------------------------------


def _package(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "meridian_ingest"
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_the_scans_would_notice_each_crossing(tmp_path: Path) -> None:
    root = _package(
        tmp_path,
        {
            "adapters/x.py": "from meridian_ingest.imagery import render\n",
            "cli_imagery.py": "import meridian_ingest.imagery\n",
            "imagery/render.py": (
                "from meridian.store import environment_samples\n"
                "from rasterio import open\n"
                'PATH = "/vsicurl/https://scene.invalid/a.tif"\n'
            ),
            "imagery/annotate.py": "import numpy as np\nfrom PIL import Image\n",
            "imagery/pixels.py": 'PATH = "/vsicurl/https://scene.invalid/a.tif"\n',
        },
    )

    assert _crossings_into_imagery(root) == [
        "adapters/x.py:1 imports meridian_ingest.imagery",
        "adapters/x.py:1 imports meridian_ingest.imagery.render",
    ]
    assert _writers_reached(root / "imagery") == [
        "render.py:1 imports meridian.store",
        "render.py:1 imports meridian.store.environment_samples",
    ]
    assert _record_only_decoders(root / "imagery") == [
        "annotate.py:1 imports numpy",
        "annotate.py:2 imports PIL",
        "annotate.py:2 imports PIL.Image",
    ]
    assert _doors(root) == [
        "imagery/render.py:2 binds rasterio.open",
        "imagery/render.py writes a /vsicurl/ path",
    ]


# --- the GDAL guard -----------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/vsicurl/https://scene.invalid/a.tif",
        "/vsizip//vsicurl/https://scene.invalid/a.zip/b.tif",
        "/vsis3/bucket/a.tif",
        "https://scene.invalid/a.tif",
        "s3://bucket/a.tif",
    ],
)
def test_the_guard_refuses_a_network_path(
    network_guard, network_reached: type[AssertionError], path: str
) -> None:
    """The positive control: without it a quiet guard would pass as a working one."""
    rasterio = pytest.importorskip("rasterio")
    network_guard()

    with pytest.raises(network_reached, match=r"rasterio\.open reached for"):
        rasterio.open(path)


def test_the_guard_lets_a_local_scene_through(network_guard, tmp_path: Path) -> None:
    rasterio = pytest.importorskip("rasterio")
    np = pytest.importorskip("numpy")
    from rasterio.transform import from_origin

    path = tmp_path / "scene.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=4,
        height=4,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_origin(77.5, 13.0, 0.01, 0.01),
    ) as dataset:
        dataset.write(np.full((1, 4, 4), 7, dtype="uint8"))
    network_guard()

    with rasterio.open(path) as dataset:
        assert int(dataset.read(1)[0, 0]) == 7


def test_gdal_is_a_layer_that_can_be_left_alone(network_guard) -> None:
    rasterio = pytest.importorskip("rasterio")
    real = rasterio.open
    network_guard(allow=("gdal",))

    assert rasterio.open is real
