"""Fixtures shared by the whole suite, and the directory-to-marker rule.

The database fixtures live here rather than in one test module because every
integration test needs the same connection and the same skip behaviour, and a
second copy of them is a second place for the skip logic to be wrong.

Tests run against real TimescaleDB, never SQLite. The schema uses ``timestamptz``,
arrays, ``CHECK`` constraints, generated columns and hypertables; a suite that
passes on SQLite says nothing about what runs on the Pi.

``network_guard`` is here for the same reason the database fixtures are: Stage
14's completion gate is asserted from two directories, and a guard copied into
each is a guard that can stop guarding in one of them without anybody noticing.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = Path(__file__).resolve().parent

DIRECTORY_MARKERS = {
    "integration": "integration",
    "msp_conformance": "msp_conformance",
    "e2e": "e2e",
}
"""Which directory implies which marker.

``unit/`` is deliberately absent: it is the default and carries no marker, so
``-m "not integration and not e2e and not msp_conformance"`` selects it.
"""


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply each directory's marker to every test collected beneath it.

    **A hook, not ``pytestmark`` in the directory's conftest.** ``pytestmark`` is
    honoured in test *modules* only; setting it in a ``conftest.py`` is silently
    ignored, which is the worst possible failure here — the marker looks applied,
    `-m e2e` selects nothing, and the e2e tests run inside the unit job instead.
    Verified by ``tests/unit/test_marker_wiring.py``, which exists because that
    mistake is invisible until a compose bring-up appears in a job that should
    take two seconds.

    Marking by directory rather than per module means a new test file cannot
    forget to declare what it needs.
    """
    for item in items:
        try:
            relative = Path(str(item.fspath)).resolve().relative_to(TESTS_ROOT)
        except ValueError:  # pragma: no cover — collected from outside tests/
            continue
        if not relative.parts:  # pragma: no cover
            continue
        marker = DIRECTORY_MARKERS.get(relative.parts[0])
        if marker is not None:
            item.add_marker(getattr(pytest.mark, marker))


@pytest.fixture(scope="session")
def database_url() -> str:
    """The libpq URL for the test database, or skip.

    Normalised through ``meridian.config`` so a ``DATABASE_URL`` carrying the
    ``+psycopg`` suffix Alembic wants still works here — one variable, two
    consumers (D-033).
    """
    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        pytest.skip("DATABASE_URL is not set; start TimescaleDB and export it")

    from meridian.config import libpq_url

    return libpq_url(raw)


@pytest.fixture(scope="session")
def conn(database_url: str) -> Iterator[Any]:
    """One session-scoped connection against the migrated schema."""
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(database_url) as connection:
        yield connection


@pytest.fixture
def scalar(conn: Any):
    """Run a query and return its first column, or ``None``."""

    def _scalar(sql: str, *args: object) -> Any:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            row = cur.fetchone()
        return row[0] if row else None

    return _scalar


class NetworkAccessError(AssertionError):
    """Something opened, or tried to open, a connection under the guard.

    An ``AssertionError`` rather than a ``RuntimeError``: it is a test failing,
    not a program misbehaving, and the message names the layer that reached.
    """


NETWORK_LAYERS = ("socket", "httpx", "psycopg")
"""The layers :func:`network_guard` can patch, and can be asked to leave alone."""


@pytest.fixture
def network_reached() -> type[AssertionError]:
    """What :func:`network_guard` raises, for the tests that assert on it.

    A fixture rather than an import: ``conftest.py`` is not a module any test
    can import — four directories hold one each, none of them are packages, and
    the suite runs under ``--import-mode=importlib``.
    """
    return NetworkAccessError


@pytest.fixture
def network_guard(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install a guard that fails the test if anything reaches for a network.

    Lives here rather than in the one module that needs it because Stage 14's
    completion gate has two halves — ``tests/unit/test_ingest_gate.py`` and its
    integration twin — and a second copy of the guard is a second thing that
    can quietly stop guarding. The positive control in the unit half therefore
    exercises the same code the integration half installs.

    Returns:
        A callable taking ``allow``: layer names from :data:`NETWORK_LAYERS` to
        leave unpatched.

    Note:
        **The socket patches are on ``socket.socket`` itself**, not on a module
        binding, so a module that did ``from socket import socket`` at import
        time still receives an object whose ``connect`` raises.

        **It does not make a database unreachable.** See :func:`_guard_psycopg`.
    """

    def install(*, allow: Iterable[str] = ()) -> None:
        exempt = frozenset(allow)
        unknown = exempt - set(NETWORK_LAYERS)
        if unknown:
            message = f"no network layer named {sorted(unknown)}"
            raise ValueError(message)
        if "socket" not in exempt:
            _guard_sockets(monkeypatch)
        if "httpx" not in exempt:
            _guard_httpx(monkeypatch)
        if "psycopg" not in exempt:
            _guard_psycopg(monkeypatch)

    return install


def _guard_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """The layer that actually guards: everything in Python reaches a network here."""
    import socket

    monkeypatch.setattr(socket.socket, "connect", _refuses("socket.socket.connect"))
    monkeypatch.setattr(
        socket.socket, "connect_ex", _refuses("socket.socket.connect_ex")
    )
    monkeypatch.setattr(socket, "getaddrinfo", _refuses("socket.getaddrinfo"))
    monkeypatch.setattr(
        socket, "create_connection", _refuses("socket.create_connection")
    )


def _guard_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name httpx in the failure, above the socket patch that would catch it anyway."""
    import httpx

    monkeypatch.setattr(httpx.Client, "send", _refuses("httpx.Client.send"))


def _guard_psycopg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Name psycopg in the failure — and nothing more than that.

    ``psycopg[binary]`` talks to Postgres through libpq, which opens its own
    sockets in C where no patch of Python's ``socket`` module can see them.
    Asserted by ``test_the_guard_cannot_see_inside_libpq`` rather than assumed,
    because assuming it the other way round is how a guard comes to be trusted
    for something it never did.

    So this patch makes an attempt *say* which layer it was, and the integration
    half of the gate exempts it to keep its own connection.
    """
    import psycopg

    monkeypatch.setattr(psycopg, "connect", _refuses("psycopg.connect"))


def _refuses(what: str) -> Any:
    """A replacement for ``what`` that fails the test instead of connecting."""

    def refuse(*_args: object, **_kwargs: object) -> object:
        message = (
            f"{what} was called while the network guard was installed. "
            "Whatever is under test reached for a network it must not need."
        )
        raise NetworkAccessError(message)

    return refuse
