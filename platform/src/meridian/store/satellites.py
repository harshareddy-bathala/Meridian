"""Tracked objects and their downlinks — the SQL layer under pass generation.

Reads ``satellites`` and ``satellite_transmitters``
(``deploy/migrations/sql/0003_satellites.sql``). Like the rest of
``meridian.store``, this module makes no decision about *which* satellites are
worth tracking or which downlink a station should be pointed at — it returns the
catalogue as it stands and lets the caller choose.

A satellite is a row here because someone entered it; the element sets that make
it predictable are ``meridian.store.element_sets``' table, and the two are
deliberately separate. A satellite with no element set is a tracked object we
cannot currently propagate, which is a gap worth seeing rather than an
inconsistency worth preventing.

Storage only, no propagation and no capability matching: deciding whether a
station can receive one of these transmitters is
``meridian.registry.capability_match``'s question.

Reference: docs/DATA-MODEL.md; docs/DECISIONS.md D-021 (transmitters are a child
table, so a frequency range is an indexable predicate rather than a JSON probe).
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "StoredTransmitter",
    "find_active_transmitters",
    "find_satellite_priorities",
]


@dataclass(frozen=True, slots=True)
class StoredTransmitter:
    """One downlink of one satellite, as read back.

    Carries ``satellite_id`` rather than being nested under a satellite object:
    the pass-generation job groups these itself, and a flat row is what the
    query returns. ``polarisation`` and ``bandwidth_hz`` are not read — they
    affect how well a pass is received, not whether it can be attempted, and no
    caller decides anything with them yet.
    """

    satellite_id: str
    centre_freq_hz: int
    """Nominal, unshifted, in Hz as an integer — never MHz and never a float."""

    mode: str
    """The demodulator needed, lowercase free text in Phase 1."""


def find_active_transmitters(conn: Connection) -> list[StoredTransmitter]:
    """Every live downlink of every live satellite.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.

    Returns:
        The matching transmitters ordered by satellite then frequency, empty
        when the catalogue holds none. Ordered rather than arbitrary because the
        pass-generation job walks this list to decide what to propagate, and a
        job whose output depends on the planner's row order is not reproducible
        — which CLAUDE.md requires every published number to be.

    Note:
        **Two independent kinds of "gone" are both excluded.** ``deleted_at``
        means the row was withdrawn from the catalogue; ``active = false`` means
        the object is still tracked but its transmitter is not expected to be
        heard — a decommissioned payload, or a satellite switched off. Both are
        filtered here, on both tables, because a pass computed for a silent
        transmitter would enter the completeness denominator as an opportunity
        nobody could ever have taken. That is EVALUATION.md §5's
        silent-satellite confound, and the cheapest place to keep it out is
        here, before anything is propagated.
    """
    with conn.cursor(row_factory=class_row(StoredTransmitter)) as cur:
        cur.execute(
            """
            select t.satellite_id, t.centre_freq_hz, t.mode
            from satellite_transmitters t
            join satellites s on s.satellite_id = t.satellite_id
            where t.active and t.deleted_at is null
              and s.active and s.deleted_at is null
            order by t.satellite_id asc, t.centre_freq_hz asc, t.id asc
            """
        )
        return cur.fetchall()


@dataclass(frozen=True, slots=True)
class _SatellitePriority:
    """One row of the priority query, so the two columns arrive typed."""

    satellite_id: str
    priority: float


def find_satellite_priorities(conn: Connection) -> dict[str, float]:
    """Every live satellite's operator weighting, keyed by satellite id.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.

    Returns:
        One entry per live satellite. A satellite nobody has weighted carries
        1.0, the column default, at which configuration B reduces exactly to
        configuration A.

    Note:
        Read from ``satellites``, never from ``assignments.priority``. That
        column records what a past decision *used*, so deriving the next run's
        weighting from it would mean the first run had nothing to read and every
        run after it quoted itself.
    """
    with conn.cursor(row_factory=class_row(_SatellitePriority)) as cur:
        cur.execute(
            """
            select satellite_id, priority
            from satellites
            where active and deleted_at is null
            """
        )
        return {row.satellite_id: row.priority for row in cur.fetchall()}
