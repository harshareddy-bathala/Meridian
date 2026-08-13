"""``meridian invite create`` — one invite, and a fleet's worth.

Tests the handler rather than the process: ``_invite_create`` takes an open
connection, which is the seam that lets these run inside the ``rollback``
fixture instead of against a database a subprocess would commit to.

The output split is the thing under test as much as the rows are. Tokens go to
stdout and everything else to stderr, so ``--count 50 > invites.txt`` produces
fifty tokens and nothing else — which is how the simulator's stations are
provisioned without anybody parsing a sentence.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-020, D-034, D-079.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.cli_invite import _invite_create  # noqa: E402 — after importorskip
from meridian.store.invites import (  # noqa: E402
    find_invite_by_hash,
    hash_invite_token,
    list_invites,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_invites.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


def arguments(**overrides: Any) -> argparse.Namespace:
    """The parsed command line ``meridian invite create`` hands its handler."""
    values: dict[str, Any] = {
        "label": "station-001",
        "expires_in_days": None,
        "for_station": None,
        "count": 1,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def tokens_from(captured: Any) -> list[str]:
    """The tokens a run printed, which is stdout and only stdout."""
    return [line for line in captured.out.splitlines() if line]


def test_one_invite_prints_one_bare_token(
    rollback: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stdout is the token alone, so the single case reads like the many case."""
    assert _invite_create(rollback, arguments()) == 0

    printed = tokens_from(capsys.readouterr())
    assert len(printed) == 1
    assert find_invite_by_hash(rollback, hash_invite_token(printed[0])) is not None


def test_the_reassurance_goes_to_stderr_where_it_cannot_be_redirected_into_a_file(
    rollback: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """A token file with a sentence in it admits no stations."""
    _invite_create(rollback, arguments())

    captured = capsys.readouterr()
    assert "Shown once" in captured.err
    assert "Shown once" not in captured.out


def test_a_count_prints_that_many_distinct_tokens(
    rollback: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """One line per station, and no two stations sharing a credential.

    An invite is single-use and binds to the station that consumes it, so a
    fleet needs one each — a repeated token would admit the first station and
    lock out every station after it.
    """
    assert _invite_create(rollback, arguments(count=4)) == 0

    printed = tokens_from(capsys.readouterr())
    assert len(printed) == 4
    assert len(set(printed)) == 4
    assert all(
        find_invite_by_hash(rollback, hash_invite_token(one)) is not None
        for one in printed
    )


def test_several_invites_are_numbered_so_one_can_be_revoked(rollback: Any) -> None:
    """``label`` is how an operator later finds a specific invite.

    Fifty rows sharing a name cannot be told apart, and revocation is by label.
    """
    _invite_create(rollback, arguments(label="sim", count=3))

    labels = {one.label for one in list_invites(rollback)}
    assert {"sim-1", "sim-2", "sim-3"} <= labels


def test_a_single_invite_keeps_the_label_exactly_as_given(rollback: Any) -> None:
    """Numbering a fleet must not rename the one-station case to ``station-001-1``."""
    _invite_create(rollback, arguments(label="station-001"))

    assert "station-001" in {one.label for one in list_invites(rollback)}
