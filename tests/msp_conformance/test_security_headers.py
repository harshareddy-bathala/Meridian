"""The content-security policy, the security headers, and no CORS (D-208).

Pinned on the running application, against a stand-in dashboard build written
to a temporary directory, as ``test_dashboard_serving.py`` does. The policy is
written out here rather than imported: a test that reads its expectation from
the constant under test passes when both are wrong together.

The single-origin decision is pinned from the attacker's side: a browser on
another origin asking, by preflight and by a plain request, gets no
``Access-Control-Allow-Origin``, so it cannot read what the platform answers.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-036, D-081, D-091, D-092, D-208.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

from meridian.api.app import create_app
from meridian.api.dashboard import DASHBOARD_DIR_ENV

REPO_ROOT = Path(__file__).resolve().parents[2]

POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data: https://tile.openstreetmap.org; font-src 'self'; "
    "connect-src 'self'; manifest-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)
HEADERS = {
    "content-security-policy": POLICY,
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "strict-transport-security": "max-age=31536000",
}
ELSEWHERE = "https://somewhere-else.example"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>")
    (tmp_path / "assets" / "index-3f9a.js").write_text("console.log('built');")
    monkeypatch.setenv("DATABASE_URL", "postgresql://meridian:meridian@127.0.0.1:1/x")
    monkeypatch.setenv(DASHBOARD_DIR_ENV, str(tmp_path))
    with TestClient(create_app(), raise_server_exceptions=False) as started:
        yield started


@pytest.mark.parametrize(
    "path",
    ["/", "/assets/index-3f9a.js", "/api/v1/no-such-thing", "/msp/v0/time", "/metrics"],
)
def test_every_response_carries_the_policy_and_headers(
    client: TestClient, path: str
) -> None:
    """The page, its assets, the API, MSP and a refusal alike."""
    response = client.get(path, headers={"MSP-Version": "0.1"})

    for name, value in HEADERS.items():
        assert response.headers.get(name) == value, (path, name)
    assert "permissions-policy" in response.headers


def test_a_refusal_before_routing_carries_them_too(client: TestClient) -> None:
    response = client.get("/api/v1/stations", params={"q": "x" * 3000})

    assert response.status_code == 400
    assert response.headers["content-security-policy"] == POLICY


def test_the_policy_allows_nothing_inline_and_nothing_elsewhere() -> None:
    """No 'unsafe-inline', no 'unsafe-eval', no wildcard, one foreign origin."""
    assert "unsafe" not in POLICY
    assert "*" not in POLICY
    foreign = set(re.findall(r"https?://[^\s;]+", POLICY))
    assert foreign == {"https://tile.openstreetmap.org"}


def test_the_tile_origin_is_the_one_the_map_uses() -> None:
    """D-092's tiles: the map and the policy must name the same host."""
    station_map = (REPO_ROOT / "dashboard/src/StationMap.tsx").read_text()
    tiles = re.search(r'OSM_TILES = "(https://[^/"]+)/', station_map)

    assert tiles is not None
    assert tiles.group(1) in POLICY


def test_the_page_source_has_no_inline_script_or_style() -> None:
    """What the policy forbids, the page must not contain, or it breaks."""
    page = (REPO_ROOT / "dashboard/index.html").read_text()

    for script in re.findall(r"<script\b[^>]*>", page):
        assert "src=" in script, script
    assert "<style" not in page
    assert "style=" not in page


def test_no_other_origin_is_allowed_to_read_the_api(client: TestClient) -> None:
    """D-208: the dashboard is same-origin, so nothing needs CORS or gets it."""
    preflight = client.options(
        "/api/v1/stations",
        headers={"Origin": ELSEWHERE, "Access-Control-Request-Method": "GET"},
    )
    plain = client.get("/api/v1/no-such-thing", headers={"Origin": ELSEWHERE})

    for response in (preflight, plain):
        assert not any(h.startswith("access-control-") for h in response.headers)
    assert preflight.status_code == 405


def test_no_cors_middleware_is_installed() -> None:
    app = create_app()

    assert all(m.cls is not CORSMiddleware for m in app.user_middleware)
