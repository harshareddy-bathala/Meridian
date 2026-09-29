"""Every compose service is hardened, and only the public file trusts the edge (D-206).

Read from the files, with YAML anchors and merges resolved as compose resolves
them. The CI image job brings the hardened stack up, which is what proves the
services still start; this pins that the hardening is there, so a service added
later without it fails here with its name rather than passing unnoticed.

Marked as a unit test by living in ``tests/unit``: reads files, runs nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
COMPOSE_FILES = (
    "docker-compose.yml",
    "docker-compose.secrets.yml",
    "docker-compose.public.yml",
)


class _ComposeLoader(yaml.SafeLoader):
    """A safe loader that also reads compose's ``!reset`` and ``!override`` tags."""


def _tagged(loader: yaml.SafeLoader, node: yaml.Node) -> dict[str, Any]:
    if isinstance(node, yaml.SequenceNode):
        value: Any = loader.construct_sequence(node)
    elif isinstance(node, yaml.MappingNode):
        value = loader.construct_mapping(node)
    else:
        value = loader.construct_scalar(node)
    return {"__tag__": node.tag, "value": value}


_ComposeLoader.add_constructor("!reset", _tagged)
_ComposeLoader.add_constructor("!override", _tagged)


def _services(name: str) -> dict[str, dict[str, Any]]:
    document = yaml.load((DEPLOY / name).read_text(), Loader=_ComposeLoader)
    return document["services"]


def _defining_files() -> list[tuple[str, str, dict[str, Any]]]:
    """Every service a file *defines*: it names an image or merges the platform's."""
    defined = []
    for name in COMPOSE_FILES:
        for service, body in _services(name).items():
            if "image" in body:
                defined.append((name, service, body))
    return defined


@pytest.mark.parametrize(
    ("file", "service", "body"),
    _defining_files(),
    ids=[f"{f}:{s}" for f, s, _ in _defining_files()],
)
def test_every_service_drops_capabilities_and_writes_nothing_to_its_root(
    file: str, service: str, body: dict[str, Any]
) -> None:
    assert body.get("cap_drop") == ["ALL"], f"{file}: {service}"
    assert "no-new-privileges:true" in body.get("security_opt", []), service
    assert body.get("read_only") is True, service
    assert "cap_add" not in body, service


def test_every_service_of_the_main_file_is_checked() -> None:
    """Each names an image, directly or through the platform anchor, so the test
    above sees them all rather than only the ones it happened to recognise."""
    main = _services("docker-compose.yml")
    assert all("image" in body for body in main.values())


def test_the_database_runs_as_its_owner_from_the_start() -> None:
    """Root would need CHOWN, SETUID and SETGID to step down; the owner needs none."""
    assert _services("docker-compose.yml")["db"]["user"] == "70:70"


def test_the_public_file_removes_the_api_port_and_trusts_the_edge() -> None:
    main_api = _services("docker-compose.yml")["api"]
    public_api = _services("docker-compose.public.yml")["api"]

    assert main_api["ports"], "the laptop and CI still reach the API on the host"
    assert public_api["ports"] == {"__tag__": "!reset", "value": []}
    assert public_api["environment"]["CLIENT_ADDRESS_HEADER"] == "CF-Connecting-IP"
    assert public_api["environment"]["MERIDIAN_PUBLIC"] == "1"


def test_only_the_file_without_a_port_trusts_a_client_address_header() -> None:
    """D-051: the header is forgeable wherever the port is published."""
    for name in ("docker-compose.yml", "docker-compose.secrets.yml"):
        text = (DEPLOY / name).read_text()
        assert "CLIENT_ADDRESS_HEADER" not in text, name


def test_the_tunnel_exists_only_with_the_public_file() -> None:
    assert "tunnel" not in _services("docker-compose.yml")
    tunnel = _services("docker-compose.public.yml")["tunnel"]
    assert tunnel["environment"]["TUNNEL_TOKEN_FILE"].endswith("/tunnel_token")
    assert "TUNNEL_TOKEN" not in tunnel["environment"]
