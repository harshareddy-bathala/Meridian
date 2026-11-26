"""``deploy/tools/demo_imagery.py`` addresses the demo stack, and ingest can read it.

The tool runs only against a live demo stack, which the rehearsal does. These
pin the parts that would fail quietly there: the compose files it names, and
the settings file it writes for ``imagery demo``, read by ingest's own loader.

Marked as a unit test by living in ``tests/unit``: imports the tool, runs nothing.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from meridian_ingest.config import load_settings

TOOL = Path(__file__).resolve().parents[2] / "deploy/tools/demo_imagery.py"


@pytest.fixture(scope="module")
def tool() -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("demo_imagery", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["demo_imagery"] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop("demo_imagery", None)


def test_it_addresses_the_demo_stack_and_ingest_only_when_asked(
    tool: ModuleType,
) -> None:
    args = argparse.Namespace(project_name="meridian-s38", env_file="x.env")

    plain = tool.compose(args, "ps")
    ingest = tool.compose(args, "build", "ingest-imagery", ingest=True)

    assert [part for part in plain if part.endswith(".yml")] == [
        str(tool.REPO / "deploy/docker-compose.yml"),
        str(tool.REPO / "deploy/docker-compose.demo.yml"),
    ]
    ingest_file = str(tool.REPO / "deploy/docker-compose.ingest.yml")
    assert ingest_file in ingest and ingest_file not in plain
    assert plain[-1] == "ps" and plain[-5:-3] == ["-p", "meridian-s38"]


def test_the_settings_it_writes_are_ones_ingest_reads(
    tool: ModuleType, tmp_path: Path
) -> None:
    path = tmp_path / "ingest.toml"
    path.write_text(tool.INGEST_TOML, encoding="utf-8")

    settings = load_settings(path)

    assert settings.raw_root == Path("/work/raw")
    assert settings.imagery.summary == Path("/published/areas.json")
    assert settings.imagery.published == Path("/published/imagery")
    assert settings.imagery.sources == (), "no archive is searched"


def test_the_demo_override_points_the_api_where_the_tool_writes() -> None:
    text = (TOOL.parents[1] / "docker-compose.demo.yml").read_text(encoding="utf-8")

    assert "AREAS_SUMMARY: ${AREAS_SUMMARY:-/published/areas.json}" in text
    assert "IMAGERY_INDEX: ${IMAGERY_INDEX:-/published/imagery/index.json}" in text
