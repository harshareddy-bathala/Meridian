"""The front keeps only public views, and serves a copy only when the api fails (D-282).

Read from ``deploy/front/``, as text. CI's image job runs the front against a
stopped api, which is what proves nginx behaves this way; this pins that the
configuration still says so, so an edit that starts keeping MSP responses, or
serves a copy as current, fails here by name.

Marked as a unit test by living in ``tests/unit``: reads files, runs nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

FRONT = Path(__file__).resolve().parents[2] / "deploy" / "front"
SNAPSHOT_INCLUDE = "include /etc/nginx/meridian/snapshot.conf;"


def _directives(text: str) -> list[str]:
    """Each directive on one line, comments dropped."""
    lines = (line.split("#", 1)[0].strip() for line in text.splitlines())
    return [line for line in lines if line]


def _locations(text: str) -> dict[str, str]:
    """Every ``location`` block's argument and body. The blocks do not nest."""
    found = re.findall(r"location\s+([^{]+?)\s*\{([^}]*)\}", text)
    return {argument: body for argument, body in found}


def _conf() -> str:
    return "\n".join(_directives((FRONT / "nginx.conf").read_text()))


def _snapshot() -> list[str]:
    return _directives((FRONT / "snapshot.conf").read_text())


def test_only_the_dashboard_and_the_public_read_api_are_kept() -> None:
    locations = _locations(_conf())
    keeping = {arg for arg, body in locations.items() if SNAPSHOT_INCLUDE in body}
    assert keeping == {"= /", "/assets/", "/api/v1/"}


def test_nothing_else_names_the_cache() -> None:
    """MSP, /metrics and /healthz fall to the catch-all, which keeps nothing."""
    conf = _conf()
    assert "proxy_cache " not in conf.replace("proxy_cache_path", "")
    assert _locations(conf)["/"].strip() == "proxy_pass http://platform;"
    for path in ("/msp", "/metrics", "/healthz"):
        assert not any(arg.lstrip("=~ ").startswith(path) for arg in _locations(conf))


def test_a_copy_is_served_only_when_the_api_does_not_answer() -> None:
    snapshot = _snapshot()
    # Stale a second after it is stored: the next visitor asks the api again.
    validity = [d for d in snapshot if d.startswith("proxy_cache_valid")]
    assert validity == ["proxy_cache_valid 200 1s;"]
    use_stale = next(d for d in snapshot if d.startswith("proxy_cache_use_stale"))
    assert {"error", "timeout"} <= set(use_stale.rstrip(";").split()[1:])
    assert "updating" not in use_stale, "would serve a copy while the api answers"


def test_a_copy_says_what_it_is_and_when_it_was_made() -> None:
    snapshot = _snapshot()
    assert "add_header Meridian-Cache $upstream_cache_status always;" in snapshot
    assert "proxy_pass_header Date;" in snapshot


def test_a_dead_api_is_a_failure_in_seconds_not_a_hang() -> None:
    conf = _conf()
    connect = re.search(r"proxy_connect_timeout (\d+)s;", conf)
    read = re.search(r"proxy_read_timeout (\d+)s;", conf)
    assert connect is not None and int(connect.group(1)) <= 2
    assert read is not None and int(read.group(1)) <= 15


def test_the_api_is_found_by_name_whenever_it_appears() -> None:
    """``resolve`` lets nginx start, and recover, while the api is away."""
    conf = _conf()
    assert "server api:8000 resolve;" in conf
    assert re.search(r"resolver 127\.0\.0\.11 valid=\d+s", conf)


def test_the_readiness_route_answers_inside_the_container_only() -> None:
    body = _locations(_conf())["= /front-ready"]
    assert "allow 127.0.0.1;" in body and "deny all;" in body
