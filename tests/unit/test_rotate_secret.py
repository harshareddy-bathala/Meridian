"""``deploy/tools/rotate_secret.py``: the files it writes and the order it keeps.

The tool writes the files ``deploy/docker-compose.secrets.yml`` mounts (D-201).
What it must get right is small and unforgiving: the modes, carrying a value
over from ``.env`` rather than inventing a new one, and keeping the old pepper
when rotating it, since a pepper replaced outright strands every station.

Files are written only under ``tmp_path``.
"""

from __future__ import annotations

import importlib.util
import stat
from pathlib import Path
from types import ModuleType

import pytest

TOOL = Path(__file__).resolve().parents[2] / "deploy/tools/rotate_secret.py"


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rotate_secret", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_init_writes_every_file_the_override_mounts(
    tool: ModuleType, tmp_path: Path
) -> None:
    secrets = tmp_path / "secrets"
    tool.init(secrets, {})

    override = (TOOL.parents[1] / "docker-compose.secrets.yml").read_text()
    mounted = {
        line.split("./secrets/")[1].strip()
        for line in override.splitlines()
        if "source: ./secrets/" in line
    }
    assert mounted == {p.name for p in secrets.iterdir()}
    assert _mode(secrets) == 0o700
    assert {_mode(p) for p in secrets.iterdir()} == {0o444}
    assert (secrets / "token_hash_pepper_previous").read_text() == ""
    assert len((secrets / "token_hash_pepper").read_text().strip()) == 64


def test_init_carries_real_values_over_from_env(
    tool: ModuleType, tmp_path: Path
) -> None:
    """Moving the pepper to a file must not change it, or every station 401s."""
    tool.init(
        tmp_path, {"TOKEN_HASH_PEPPER": "the-pepper", "METRICS_TOKEN": "change-me"}
    )

    assert (tmp_path / "token_hash_pepper").read_text().strip() == "the-pepper"
    assert (tmp_path / "metrics_token").read_text().strip() != "change-me"


def test_init_leaves_existing_files_alone(tool: ModuleType, tmp_path: Path) -> None:
    tool.init(tmp_path, {})
    before = (tmp_path / "metrics_token").read_text()
    assert tool.init(tmp_path, {}) == []
    assert (tmp_path / "metrics_token").read_text() == before


def test_rotating_the_pepper_keeps_the_old_one_as_previous(
    tool: ModuleType, tmp_path: Path
) -> None:
    tool.init(tmp_path, {"TOKEN_HASH_PEPPER": "old-pepper"})

    tool.rotate(tmp_path, "token_hash_pepper")

    assert (tmp_path / "token_hash_pepper_previous").read_text().strip() == "old-pepper"
    new = (tmp_path / "token_hash_pepper").read_text().strip()
    assert new not in {"", "old-pepper"}
    assert _mode(tmp_path / "token_hash_pepper") == 0o444


def test_a_second_pepper_rotation_waits_for_the_first_to_retire(
    tool: ModuleType, tmp_path: Path
) -> None:
    """Two rotations in a row would drop the pepper some stations still use."""
    tool.init(tmp_path, {})
    tool.rotate(tmp_path, "token_hash_pepper")

    with pytest.raises(tool.ToolError, match="already in progress"):
        tool.rotate(tmp_path, "token_hash_pepper")

    tool.retire(tmp_path, "token_hash_pepper")
    assert (tmp_path / "token_hash_pepper_previous").read_text() == ""
    tool.rotate(tmp_path, "token_hash_pepper")


def test_rotating_the_metrics_token_keeps_no_previous(
    tool: ModuleType, tmp_path: Path
) -> None:
    tool.init(tmp_path, {})
    before = (tmp_path / "metrics_token").read_text()

    tool.rotate(tmp_path, "metrics_token")

    assert (tmp_path / "metrics_token").read_text() != before
    assert (tmp_path / "token_hash_pepper_previous").read_text() == ""


def test_the_printed_command_recreates_every_reader(tool: ModuleType) -> None:
    command = tool.recreate_command("metrics_token")
    assert "docker-compose.secrets.yml" in command
    assert command.endswith("--force-recreate api jobs prometheus")


def test_a_public_deployment_is_recreated_with_the_public_file(
    tool: ModuleType, tmp_path: Path
) -> None:
    """Without it the api would publish its port and drop MERIDIAN_PUBLIC."""
    tool.init(tmp_path, {})
    private = tool.run("rotate", "metrics_token", tmp_path, tmp_path / ".env")
    tool.set_secret(tmp_path, "tunnel_token", "eyJhIjoi...")

    public = tool.run("rotate", "metrics_token", tmp_path, tmp_path / ".env")

    assert "docker-compose.public.yml" not in private
    assert "-f deploy/docker-compose.public.yml up -d" in public


def test_env_values_ignores_comments_and_quotes(
    tool: ModuleType, tmp_path: Path
) -> None:
    env = tmp_path / ".env"
    env.write_text('# TOKEN_HASH_PEPPER=nope\nTOKEN_HASH_PEPPER="yes"\n\nX\n')
    assert tool.env_values(env) == {"TOKEN_HASH_PEPPER": "yes"}


def test_the_tunnel_token_is_set_from_what_cloudflare_issued(
    tool: ModuleType, tmp_path: Path
) -> None:
    """Cloudflare issues it, so it is stored rather than generated (D-206)."""
    tool.set_secret(tmp_path / "secrets", "tunnel_token", "eyJhIjoi...\n")

    stored = tmp_path / "secrets" / "tunnel_token"
    assert stored.read_text().strip() == "eyJhIjoi..."
    assert _mode(stored) == 0o444
    command = tool.recreate_command("tunnel_token")
    assert "-f deploy/docker-compose.public.yml" in command
    assert command.endswith("--force-recreate tunnel")


def test_only_the_tunnel_token_is_set_by_hand(tool: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(tool.ToolError, match="rotate the others"):
        tool.set_secret(tmp_path, "token_hash_pepper", "chosen-by-a-person")
    with pytest.raises(tool.ToolError, match="nothing was given"):
        tool.set_secret(tmp_path, "tunnel_token", "  \n")


def test_the_public_file_mounts_the_file_set_writes() -> None:
    public = (TOOL.parents[1] / "docker-compose.public.yml").read_text()
    assert "source: ./secrets/tunnel_token" in public
