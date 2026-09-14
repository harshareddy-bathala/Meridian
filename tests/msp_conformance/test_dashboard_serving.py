"""The dashboard beside the API, on the real application (D-081, D-091).

A stand-in build is written to a temporary directory — the tests are about what
the platform serves and what it refuses to shadow, not about what Vite emits, so
they must not depend on Node having run.

The shadowing tests are the ones that matter. A static mount at ``/`` is the
first thing anyone reaches for, and it passes every "the page loads" test while
quietly turning D-090's ``not_found`` and ``method_not_allowed`` into whatever the
mount answers.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``: what
is pinned is the wire behaviour of a running platform.

Reference: docs/DECISIONS.md D-081, D-090, D-091.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app
from meridian.api.dashboard import DASHBOARD_DIR_ENV

INDEX = "<!doctype html><title>Meridian</title><div id=root></div>"
SCRIPT = "console.log('built');"
NOT_FOUND = {"error": "not_found", "message": "No such endpoint."}


def _client(monkeypatch: pytest.MonkeyPatch, directory: Path | None) -> TestClient:
    # DATABASE_URL points nowhere, as in the metrics tests: serving files has
    # nothing to do with the database, and needing one would hide that.
    monkeypatch.setenv("DATABASE_URL", "postgresql://meridian:meridian@127.0.0.1:1/x")
    if directory is None:
        monkeypatch.delenv(DASHBOARD_DIR_ENV, raising=False)
    else:
        monkeypatch.setenv(DASHBOARD_DIR_ENV, str(directory))
    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture
def build(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(INDEX)
    (tmp_path / "assets" / "index-3f9a.js").write_text(SCRIPT)
    return tmp_path


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, build: Path) -> Iterator[TestClient]:
    with _client(monkeypatch, build) as started:
        yield started


def test_the_root_serves_the_built_index(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["content-type"].startswith("text/html")
    # A cached index.html pins a browser to the previous build's asset names.
    assert response.headers["cache-control"] == "no-cache"


def test_a_built_asset_is_served_under_assets(client: TestClient) -> None:
    response = client.get("/assets/index-3f9a.js")

    assert response.status_code == 200
    assert response.text == SCRIPT


def test_a_missing_asset_is_the_platforms_own_404(client: TestClient) -> None:
    """Starlette's static 404 must still leave in D-090's envelope."""
    response = client.get("/assets/index-0000.js")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


@pytest.mark.parametrize("path", ["/stations", "/api/v1/statoins", "/msp/v0/nope"])
def test_an_unrouted_path_is_not_handed_the_dashboard(
    client: TestClient, path: str
) -> None:
    """No single-page-app fallback: an unknown URL is an error, not a page."""
    response = client.get(path)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_a_wrong_method_on_an_msp_route_is_still_405(client: TestClient) -> None:
    """The case a root mount breaks without anyone noticing (D-091).

    `/msp/v0/register` only accepts POST, so a GET is a partial match — and
    Starlette lets a later full match win over a partial one.
    """
    response = client.get("/msp/v0/register")

    assert response.status_code == 405
    assert response.json()["error"] == "method_not_allowed"


@pytest.mark.parametrize("path", ["/healthz", "/api/v1/stations", "/metrics"])
def test_the_api_still_answers_its_own_paths(client: TestClient, path: str) -> None:
    """Each answers as the API, never with the dashboard's HTML."""
    response = client.get(path)

    assert INDEX not in response.text
    assert response.headers["content-type"].split(";")[0] != "text/html"


def test_without_a_build_the_platform_serves_the_api_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A checkout without Node still starts; the root is simply unrouted."""
    with _client(monkeypatch, tmp_path / "absent") as started:
        assert started.get("/").json() == NOT_FOUND
        assert started.get("/healthz").status_code in {200, 503}


def test_with_the_variable_unset_nothing_is_mounted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _client(monkeypatch, None) as started:
        assert started.get("/").status_code == 404
