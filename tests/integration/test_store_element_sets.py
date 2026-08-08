"""``meridian.store.element_sets`` against real TimescaleDB.

The archive is append-only and the series *is* the measurement, so most of these
tests assert that something was **kept** rather than that something worked. The
three-row table in D-057 is written out here as three tests, because the
decision was chosen by comparing exactly those outcomes and a reader should be
able to check the claim rather than take it.

Marked ``integration`` by the directory hook in ``tests/conftest.py``. Same
``rollback`` fixture pattern as ``test_store_heartbeats.py``.

Reference: docs/DECISIONS.md D-049, D-057; docs/DATA-MODEL.md.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.element_sets import (  # noqa: E402 — after importorskip
    NewElementSet,
    find_element_set_current_at,
    find_element_sets_in_epoch_range,
    insert_element_set,
)

pytestmark = pytest.mark.integration

SATELLITE_ID = "norad:57166"

# Meteor-M N2-3. Real lines, so the fixed-width ASCII claim the migration's
# IMMUTABLE justification rests on is exercised rather than asserted.
LINE1 = "1 57166U 23091A   26226.50000000  .00001234  00000-0  12345-4 0  9991"
LINE2 = "2 57166  98.6416 247.4627 0006703 130.5360 325.0288 14.22377579123456"

# Differs from LINE1 in the drag term only — a plausible re-fit for the same
# epoch, which is precisely the case the old (satellite, epoch, source) key
# discarded.
REFIT_LINE1 = "1 57166U 23091A   26226.50000000  .00009999  00000-0  54321-4 0  9998"

EPOCH = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_invites.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture(autouse=True)
def satellite(rollback: Any) -> None:
    """The catalogue row every element set references."""
    with rollback.cursor() as cur:
        cur.execute(
            "insert into satellites (satellite_id, name) values (%s, %s)",
            (SATELLITE_ID, "Meteor-M N2-3"),
        )


def element_set(
    *,
    epoch: datetime = EPOCH,
    line1: str = LINE1,
    source: str = "manual",
) -> NewElementSet:
    """One set, varying only what a given test is about."""
    return NewElementSet(
        satellite_id=SATELLITE_ID,
        epoch=epoch,
        line1=line1,
        line2=LINE2,
        source=source,
    )


def row_count(conn: Any) -> int:
    """How many sets the archive holds for the test satellite."""
    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from element_sets where satellite_id = %s",
            (SATELLITE_ID,),
        )
        row = cur.fetchone()
    assert row is not None
    return int(row[0])


# --- D-057's table, one test per row ------------------------------------------


def test_the_same_lines_from_the_same_source_are_one_row(rollback: Any) -> None:
    """Re-retrieving an unchanged set is a no-op, and says so.

    This is what makes an import safe to re-run, and the return value is how a
    caller tells "archived" from "already had it" — the two are the same on the
    wire and different in every count derived from them.
    """
    assert insert_element_set(rollback, element_set()) is True
    assert insert_element_set(rollback, element_set()) is False

    assert row_count(rollback) == 1


def test_different_lines_at_one_epoch_are_two_rows(rollback: Any) -> None:
    """The defect D-057 exists to fix.

    Before migration 0009 this was one row: the second set was rejected by
    `unique (satellite_id, epoch, source)` and the series silently lost a
    measurement. A catalogue correction or a re-fit after a manoeuvre produces
    exactly this shape, and losing it is losing the data point that explains why
    a prediction from that epoch was wrong.
    """
    assert insert_element_set(rollback, element_set()) is True
    assert insert_element_set(rollback, element_set(line1=REFIT_LINE1)) is True

    assert row_count(rollback) == 2


def test_the_same_lines_from_two_sources_keep_both_provenances(
    rollback: Any,
) -> None:
    """`source` stays in the key deliberately.

    D-049 makes `source` how this table records provenance, so two retrievals of
    identical lines from different sources are two facts, not one. Collapsing
    them would discard whichever arrived second — and with it the evidence that
    the two catalogues agreed, which is the only cheap cross-check available.
    """
    assert insert_element_set(rollback, element_set(source="celestrak")) is True
    assert insert_element_set(rollback, element_set(source="spacetrack")) is True

    assert row_count(rollback) == 2


# --- the generated hash -------------------------------------------------------


def test_the_stored_hash_is_the_sha256_of_the_newline_joined_lines(
    rollback: Any,
) -> None:
    """The database's definition, checked against an independent one.

    Computed here with `hashlib` rather than by calling the SQL function, so the
    two agree because they are both right and not because they are the same
    expression. The store layer will need to reproduce this hash in Python to
    ask "do we hold this?" without a round trip, and this is the check that it
    can.
    """
    insert_element_set(rollback, element_set())
    expected = hashlib.sha256(f"{LINE1}\n{LINE2}".encode()).digest()

    with rollback.cursor() as cur:
        cur.execute(
            "select content_sha256 from element_sets where satellite_id = %s",
            (SATELLITE_ID,),
        )
        row = cur.fetchone()
    assert row is not None
    assert bytes(row[0]) == expected


def test_the_hash_cannot_disagree_with_the_lines(rollback: Any) -> None:
    """A generated column, so no caller can supply a hash of its own.

    If this ever becomes an ordinary column, a row whose hash does not match its
    lines becomes representable — and the unique constraint then guards nothing,
    because two identical sets could carry different hashes.
    """
    insert_element_set(rollback, element_set())

    with pytest.raises(psycopg.errors.GeneratedAlways), rollback.cursor() as cur:
        cur.execute(
            "update element_sets set content_sha256 = %s where satellite_id = %s",
            (bytes(32), SATELLITE_ID),
        )


# --- selecting a set ----------------------------------------------------------


def test_current_at_returns_the_set_current_then_not_the_newest(
    rollback: Any,
) -> None:
    """The distinction the archive exists for.

    A later set is inserted *after* the earlier one, so a query that ordered by
    `retrieved_at`, or simply took the newest row, would return it. Recomputing
    a pass from an earlier instant must use the set that was current then, or
    the timing error measured against it is attributed to an element set that
    did not exist yet.
    """
    older = EPOCH - timedelta(days=2)
    insert_element_set(rollback, element_set(epoch=older))
    insert_element_set(rollback, element_set(epoch=EPOCH, line1=REFIT_LINE1))

    found = find_element_set_current_at(
        rollback, SATELLITE_ID, older + timedelta(hours=1)
    )

    assert found is not None
    assert found.epoch == older


def test_current_at_accepts_a_set_whose_epoch_is_exactly_the_instant(
    rollback: Any,
) -> None:
    """A set is current from its own epoch, not from one second after it."""
    insert_element_set(rollback, element_set())

    found = find_element_set_current_at(rollback, SATELLITE_ID, EPOCH)

    assert found is not None
    assert found.epoch == EPOCH


def test_current_at_is_none_when_every_set_is_from_the_future(
    rollback: Any,
) -> None:
    """Not the earliest available set.

    Falling back to a later set would silently predict the past with an element
    set fitted after it, which reads as an unusually accurate prediction — the
    most misleading failure available here.
    """
    insert_element_set(rollback, element_set())

    assert (
        find_element_set_current_at(rollback, SATELLITE_ID, EPOCH - timedelta(days=1))
        is None
    )


def test_current_at_is_none_for_a_satellite_with_no_sets(rollback: Any) -> None:
    assert find_element_set_current_at(rollback, SATELLITE_ID, EPOCH) is None


# --- ranges -------------------------------------------------------------------


def test_the_epoch_range_is_half_open_and_ascending(rollback: Any) -> None:
    """Half-open so two adjacent ranges cannot both claim one set, and ascending
    because `element_set_divergence` compares each set against the one before
    it — a descending series inverts every sign without changing a magnitude."""
    for days, line1 in ((0, LINE1), (1, REFIT_LINE1)):
        insert_element_set(
            rollback, element_set(epoch=EPOCH + timedelta(days=days), line1=line1)
        )

    found = find_element_sets_in_epoch_range(
        rollback, SATELLITE_ID, EPOCH, EPOCH + timedelta(days=1)
    )

    assert [f.epoch for f in found] == [EPOCH]


def test_the_epoch_range_returns_every_set_in_order(rollback: Any) -> None:
    for days, line1 in ((0, LINE1), (1, REFIT_LINE1)):
        insert_element_set(
            rollback, element_set(epoch=EPOCH + timedelta(days=days), line1=line1)
        )

    found = find_element_sets_in_epoch_range(
        rollback, SATELLITE_ID, EPOCH, EPOCH + timedelta(days=7)
    )

    assert [f.epoch for f in found] == [EPOCH, EPOCH + timedelta(days=1)]


def test_an_empty_range_is_an_empty_list(rollback: Any) -> None:
    insert_element_set(rollback, element_set())

    found = find_element_sets_in_epoch_range(
        rollback, SATELLITE_ID, EPOCH + timedelta(days=30), EPOCH + timedelta(days=60)
    )

    assert found == []
