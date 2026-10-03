"""The station tier's backup: what goes in, what never does, and what restore refuses.

``deploy/tools/station_archive.py`` copies a station's own record — the reports
the platform has not acknowledged, and the products only the station holds
(D-281) — into one file with a manifest, and writes it back. The cases that
matter are the refusals: an archive that is not the one written must not be
restored, a station's identity must never be in one, and a restore must never
replace what a station already holds, because the station's copy is the
authority (D-129).

Marked as a unit test by living in ``tests/unit``: no network, no Docker. Files
are written only under ``tmp_path``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import io
import json
import sys
import tarfile
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from meridian_client.station_config import StationPaths

TOOL = Path(__file__).resolve().parents[2] / "deploy/tools/station_archive.py"
PRODUCT = "a" * 64
RECORDING = b"\x7f" * 4096


@pytest.fixture(scope="module")
def tool() -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("station_archive", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["station_archive"] = module  # a dataclass looks its module up
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop("station_archive", None)


@pytest.fixture
def state(tmp_path: Path) -> Path:
    """A station's state directory, as ``StationPaths.under`` lays it out."""
    root = tmp_path / "state"
    files = {
        "credentials.json": b'{"bearer_token": "secret"}',
        "registration_key": b"secret-key",
        "held.json": b'{"assignments": []}',
        "outbox/as_a.json": b'{"assignment_id": "as_a"}',
        "outbox/as_c.json.partial": b'{"assignment_id": "as_',
        "outbox/failed/as_b.json": b'{"assignment_id": "as_b"}',
        "captures/as_a/decoder.log": b"frames 412\n",
        "captures/as_a/recording.u8": RECORDING,
        f"products/{PRODUCT}": b"\x89PNG waterfall",
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    # The station wrote the path from its own working directory, not this one.
    manifest = {"phase": "handed_over", "recording": {"path": "st/as_a/recording.u8"}}
    (root / "captures/as_a/manifest.json").write_text(json.dumps(manifest))
    return root


def made(tool: ModuleType, state: Path, tmp_path: Path, **options: bool) -> Path:
    archive = tmp_path / "archives" / "station.tar"
    chosen = {"products": True, "recordings": False} | options
    tool.create(state, archive, **chosen)
    return archive


def names(archive: Path) -> set[str]:
    with tarfile.open(archive) as tar:
        return set(tar.getnames())


def test_a_stations_record_goes_in_and_its_identity_never_does(
    tool: ModuleType, state: Path, tmp_path: Path
) -> None:
    archive = made(tool, state, tmp_path)

    assert names(archive) == {
        "held.json",
        "outbox/as_a.json",
        "outbox/failed/as_b.json",
        "captures/as_a/decoder.log",
        "captures/as_a/manifest.json",
        f"products/{PRODUCT}",
    }
    assert b"secret" not in archive.read_bytes()


def test_what_is_left_out_is_said_when_the_archive_is_made(
    tool: ModuleType, state: Path, tmp_path: Path
) -> None:
    left_out = tool.read_manifest(made(tool, state, tmp_path)).left_out

    assert any(line.startswith("credentials.json") for line in left_out)
    assert any(line.startswith("registration_key") for line in left_out)
    assert any(
        line.startswith("captures/as_a/recording.u8") and "4096 bytes" in line
        for line in left_out
    )


def test_recordings_are_taken_only_when_asked_for(
    tool: ModuleType, state: Path, tmp_path: Path
) -> None:
    archive = made(tool, state, tmp_path, recordings=True)

    assert "captures/as_a/recording.u8" in names(archive)
    assert "credentials.json" not in names(archive)


def test_products_can_be_left_out_and_that_is_said(
    tool: ModuleType, state: Path, tmp_path: Path
) -> None:
    archive = made(tool, state, tmp_path, products=False)

    assert f"products/{PRODUCT}" not in names(archive)
    assert any("--no-products" in line for line in tool.read_manifest(archive).left_out)


def test_an_archive_restores_to_the_same_bytes_in_an_empty_state(
    tool: ModuleType, state: Path, tmp_path: Path
) -> None:
    archive = made(tool, state, tmp_path)
    elsewhere = tmp_path / "elsewhere"

    written, kept = tool.restore(archive, elsewhere)

    assert kept == []
    assert sorted(written) == sorted(names(archive))
    for name in written:
        assert (elsewhere / name).read_bytes() == (state / name).read_bytes()
    assert not (elsewhere / "credentials.json").exists()
    assert not list(elsewhere.rglob("*.partial"))


def test_a_restore_never_replaces_what_the_station_already_holds(
    tool: ModuleType, state: Path, tmp_path: Path
) -> None:
    """The station's copy is the authority; an archive is older by definition."""
    archive = made(tool, state, tmp_path)
    newer = b'{"assignment_id": "as_a", "revised": true}'
    (state / "outbox/as_a.json").write_bytes(newer)
    (state / "held.json").unlink()

    written, kept = tool.restore(archive, state)

    assert written == ["held.json"]
    assert "outbox/as_a.json" in kept
    assert (state / "outbox/as_a.json").read_bytes() == newer


def test_an_archive_that_changed_is_refused_and_nothing_is_written(
    tool: ModuleType, state: Path, tmp_path: Path
) -> None:
    archive = made(tool, state, tmp_path)
    with archive.open("ab") as handle:
        handle.write(b"x")
    elsewhere = tmp_path / "elsewhere"

    with pytest.raises(tool.ArchiveError, match="not the one that was archived"):
        tool.restore(archive, elsewhere)

    assert not elsewhere.exists()


def _repacked(tool: ModuleType, archive: Path, members: dict[str, bytes]) -> None:
    """Rewrite the archive with these members, and its sha256 in the manifest.

    What someone who altered an archive carefully would do: the manifest's own
    hash of the archive is put right, and the per-file entries are left alone.
    """
    with tarfile.open(archive, "w") as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    path = tool.manifest_path(archive)
    document = json.loads(path.read_text())
    document["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    path.write_text(json.dumps(document))


def _members(archive: Path) -> dict[str, bytes]:
    with tarfile.open(archive) as tar:
        return {
            member.name: tar.extractfile(member).read()  # type: ignore[union-attr]
            for member in tar
        }


def test_a_file_with_other_bytes_is_refused_even_under_a_corrected_hash(
    tool: ModuleType, state: Path, tmp_path: Path
) -> None:
    archive = made(tool, state, tmp_path)
    members = _members(archive)
    members["outbox/as_a.json"] = b'{"assignment_id": "as_z"}'
    _repacked(tool, archive, members)

    with pytest.raises(tool.ArchiveError, match=r"outbox/as_a\.json is not the file"):
        tool.verify(archive)


def test_a_file_the_manifest_does_not_name_is_refused(
    tool: ModuleType, state: Path, tmp_path: Path
) -> None:
    archive = made(tool, state, tmp_path)
    _repacked(tool, archive, _members(archive) | {"outbox/planted.json": b"{}"})

    with pytest.raises(tool.ArchiveError, match="unlisted"):
        tool.verify(archive)


def test_a_missing_file_is_refused(
    tool: ModuleType, state: Path, tmp_path: Path
) -> None:
    archive = made(tool, state, tmp_path)
    members = _members(archive)
    del members["held.json"]
    _repacked(tool, archive, members)

    with pytest.raises(tool.ArchiveError, match="lacks"):
        tool.verify(archive)


@pytest.mark.parametrize(
    "path",
    ["../outside.json", "/etc/passwd", "credentials.json", "outbox/../../x", "other/x"],
)
def test_a_manifest_naming_a_path_outside_a_state_is_refused(
    tool: ModuleType, state: Path, tmp_path: Path, path: str
) -> None:
    """A restore writes where the manifest says, so the manifest is checked first."""
    archive = made(tool, state, tmp_path)
    manifest = tool.manifest_path(archive)
    document = json.loads(manifest.read_text())
    document["files"][0]["path"] = path
    manifest.write_text(json.dumps(document))

    assert not tool.safe(path)
    with pytest.raises(tool.ArchiveError):
        tool.restore(archive, tmp_path / "elsewhere")
    assert not (tmp_path / "elsewhere").exists()


def test_an_archive_without_its_manifest_is_refused(
    tool: ModuleType, state: Path, tmp_path: Path
) -> None:
    archive = made(tool, state, tmp_path)
    tool.manifest_path(archive).unlink()

    with pytest.raises(tool.ArchiveError, match="no manifest"):
        tool.verify(archive)


def test_an_archive_is_never_written_over_another(
    tool: ModuleType, state: Path, tmp_path: Path
) -> None:
    archive = made(tool, state, tmp_path)
    before = archive.read_bytes()

    assert tool.main(["create", "--state", str(state), "--out", str(archive)]) == 1
    assert archive.read_bytes() == before


def test_a_state_with_nothing_but_its_identity_is_not_archived(
    tool: ModuleType, tmp_path: Path
) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "credentials.json").write_text("{}")

    with pytest.raises(tool.ArchiveError, match="nothing to archive"):
        tool.create(bare, tmp_path / "bare.tar", products=True, recordings=False)
    assert not (tmp_path / "bare.tar").exists()


def test_the_commands_run_end_to_end(
    tool: ModuleType, state: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = tmp_path / "station.tar"

    assert tool.main(["create", "--state", str(state), "--out", str(archive)]) == 0
    assert "not in this archive   credentials.json" in capsys.readouterr().out
    assert tool.main(["verify", str(archive)]) == 0
    assert "6 files, as archived" in capsys.readouterr().out
    assert tool.main(["restore", str(archive), "--state", str(state)]) == 0
    assert "the station already holds it" in capsys.readouterr().out


def test_the_tool_knows_every_path_a_station_keeps(
    tool: ModuleType, tmp_path: Path
) -> None:
    """A path added to a station's state is archived, or refused, on purpose.

    The tool is a host script and cannot import the client, so it names the
    layout itself. This is where the two are held together.
    """
    paths = StationPaths.under(tmp_path)
    kept = {
        getattr(paths, one.name).name
        for one in dataclasses.fields(paths)
        if one.name != "state_dir"
    }

    assert kept == set(tool.TOP_LEVEL) | set(tool.NEVER)
