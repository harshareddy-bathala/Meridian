"""``/healthz`` says whether the platform and its database are up, in its body.

The tunnel is pointed at it first and the public-reachability check reads it,
and CI's bring-up step greps it. This pins the body and the status both ways:
a reachable database is ``200`` and ``ok``; an unreachable one is ``503`` and
``degraded``, answered rather than raised, because a health check that fails
with its dependency is a second outage, not a report of the first.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``: what
is pinned is the wire behaviour of a running platform.

Reference: docs/DECISIONS.md D-254; docs/OPERATIONS.md § Failure recovery.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from meridian import __version__
from meridian.api.app import create_app


def _health(monkeypatch: pytest.MonkeyPatch, url: str) -> tuple[int, object]:
    monkeypatch.setenv("DATABASE_URL", url)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.get("/healthz")
    return response.status_code, response.json()


def test_a_reachable_database_is_ok(
    monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    assert _health(monkeypatch, database_url) == (
        200,
        {"status": "ok", "version": __version__, "database": "ok"},
    )


def test_an_unreachable_database_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Port 1 is reserved and nothing listens on it."""
    nowhere = "postgresql://meridian:meridian@127.0.0.1:1/meridian"

    assert _health(monkeypatch, nowhere) == (
        503,
        {"status": "degraded", "version": __version__, "database": "unreachable"},
    )
