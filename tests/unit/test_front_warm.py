"""The front's warmer asks for what a browser would, keyed as a browser would (D-283).

The front keeps a copy under the exact path and query it forwarded, so a route
warmed under any other spelling is a copy no visitor will ever be served. These
pin the warmer's encoding against the browser's, its walk of the directory, and
its reading of the answers. The routes file itself is checked against the
dashboard's requests by ``dashboard/src/snapshotRoutes.test.ts``.

Marked as a unit test by living in ``tests/unit``: one local socket at most, no
Docker. Files are written only under ``tmp_path``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import ClassVar

import pytest

TOOL = Path(__file__).resolve().parents[2] / "deploy/tools/front_warm.py"


@pytest.fixture(scope="module")
def warm() -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("front_warm", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["front_warm"] = module  # a dataclass looks its module up
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop("front_warm", None)


# What the browser gives for the same strings, from Node:
#   encodeURIComponent(s) and new URLSearchParams({x: s}).toString()
@pytest.mark.parametrize(
    ("value", "in_path", "in_query"),
    [
        ("st_0123456789ab", "st_0123456789ab", "st_0123456789ab"),
        ("st 1", "st%201", "st+1"),
        ("st/1", "st%2F1", "st%2F1"),
        ("a~b*'()! /é", "a~b*'()!%20%2F%C3%A9", "a%7Eb*%27%28%29%21+%2F%C3%A9"),
    ],
)
def test_a_station_id_is_encoded_as_the_browser_encodes_it(
    warm: ModuleType, value: str, in_path: str, in_query: str
) -> None:
    assert warm.path_segment(value) == in_path
    assert warm.query_value(value) == in_query


def test_each_route_is_encoded_where_the_station_sits(warm: ModuleType) -> None:
    assert (
        warm.expand("/api/v1/stations/{station}/uptime?hours=48", "st 1")
        == "/api/v1/stations/st%201/uptime?hours=48"
    )
    assert (
        warm.expand("/api/v1/observations?limit=10&station_id={station}", "st 1")
        == "/api/v1/observations?limit=10&station_id=st+1"
    )


def test_the_routes_file_lists_only_public_views(warm: ModuleType) -> None:
    routes = warm.read_routes()
    assert routes[0] == warm.DIRECTORY
    assert all(route.startswith("/api/v1/") for route in routes)
    assert len(routes) == len(set(routes))


def test_the_assets_a_page_and_a_stylesheet_name_are_found_once(
    warm: ModuleType,
) -> None:
    html = (
        b'<script type="module" src="/assets/index-a1.js"></script>'
        b'<link rel="stylesheet" href="/assets/index-b2.css">'
        b'<link rel="icon" href="/assets/index-b2.css">'
    )
    assert warm.assets_of(html) == ["/assets/index-a1.js", "/assets/index-b2.css"]
    css = (
        b"@font-face{src:url(/assets/inter-c3.woff2)}"
        b" a{background:url('/assets/x.svg')}"
    )
    assert warm.assets_of(css) == ["/assets/inter-c3.woff2", "/assets/x.svg"]


def _site(warm: ModuleType) -> dict[str, bytes]:
    """Two directory pages, a page with one script, and every route answering."""
    return {
        "/": b'<script src="/assets/index-a1.js"></script>',
        "/assets/index-a1.js": b"",
        warm.DIRECTORY: json.dumps(
            {"items": [{"station_id": "st_1"}], "next_cursor": "c 1"}
        ).encode(),
        f"{warm.DIRECTORY}&cursor=c+1": json.dumps(
            {"items": [{"station_id": "st_2"}], "next_cursor": None}
        ).encode(),
    }


def test_a_round_walks_the_directory_and_asks_for_every_station(
    warm: ModuleType,
) -> None:
    bodies = _site(warm)
    asked: list[str] = []

    def fetch(path: str) -> object:
        asked.append(path)
        return warm.Answer(path, 200, "MISS", bodies.get(path, b"{}"))

    result = warm.warm(fetch, warm.read_routes())

    assert result.stations == 2
    assert result.ok
    assert asked[:4] == [
        "/",
        "/assets/index-a1.js",
        warm.DIRECTORY,
        f"{warm.DIRECTORY}&cursor=c+1",
    ]
    for station in ("st_1", "st_2"):
        assert f"/api/v1/stations/{station}/profiles" in asked
        assert f"/api/v1/observations?limit=10&station_id={station}" in asked
    assert asked.count(warm.DIRECTORY) == 1
    assert len(asked) == len(set(asked))


def test_a_round_answered_from_copies_is_not_a_good_round(warm: ModuleType) -> None:
    """The front served its copies: the api did not answer, nothing was refreshed."""
    bodies = _site(warm)
    result = warm.warm(
        lambda path: warm.Answer(path, 200, "STALE", bodies.get(path, b"{}")),
        warm.read_routes(),
    )
    assert not result.ok
    assert "the api did not answer" in result.summary()


def test_a_failed_view_is_named(warm: ModuleType) -> None:
    bodies = _site(warm)

    def fetch(path: str) -> object:
        status = 504 if path.endswith("/profiles") else 200
        return warm.Answer(path, status, "MISS", bodies.get(path, b"{}"))

    result = warm.warm(fetch, warm.read_routes())
    assert not result.ok
    assert "504 /api/v1/stations/st_1/profiles" in result.summary()


def test_the_status_file_is_replaced_whole(warm: ModuleType, tmp_path: Path) -> None:
    status = tmp_path / "front-warm.json"
    result = warm.Round(answers=[warm.Answer("/", 200, "MISS")], stations=0)
    warm.write_status(status, result)
    written = json.loads(status.read_text())
    assert written["ok"] is True and written["views"] == 1
    assert not (tmp_path / "front-warm.json.partial").exists()


class _Front(BaseHTTPRequestHandler):
    """Refuses the first request with a 429, as the api's limiter would."""

    refused: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        if not self.refused:
            self.refused.append(self.path)
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Meridian-Cache", "EXPIRED")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format: str, *args: object) -> None:
        pass


def test_a_rate_limited_request_is_waited_out_once(warm: ModuleType) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Front)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        answer = warm.http_fetch(base, timeout_s=5, pace_s=0)("/api/v1/reliability")
    finally:
        server.shutdown()
        server.server_close()
    assert _Front.refused == ["/api/v1/reliability"]
    assert (answer.status, answer.cache) == (200, "EXPIRED")


def test_an_unreachable_front_is_a_failed_view_not_a_crash(warm: ModuleType) -> None:
    answer = warm.http_fetch("http://127.0.0.1:1", timeout_s=2, pace_s=0)("/")
    assert answer.status == 0


def test_an_answer_is_reported_without_its_body_for_the_drill(
    warm: ModuleType,
) -> None:
    """``--json`` is what ``front_drill.py`` reads: the label, the Date, the time."""
    answer = warm.Answer(
        "/",
        200,
        "STALE",
        b"<html>",
        date="Fri, 02 Oct 2026 15:08:12 GMT",
        seconds=2.0041,
    )

    assert answer.report() == {
        "path": "/",
        "status": 200,
        "cache": "STALE",
        "date": "Fri, 02 Oct 2026 15:08:12 GMT",
        "seconds": 2.004,
    }
