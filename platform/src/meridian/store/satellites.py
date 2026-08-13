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
    "NewSatellite",
    "NewTransmitter",
    "StoredTransmitter",
    "find_active_transmitters",
    "find_satellite_priorities",
    "insert_satellite",
    "insert_transmitter",
]


@dataclass(frozen=True, slots=True)
class NewSatellite:
    """One tracked object, in insertable form.

    ``active`` and ``deleted_at`` are absent: a satellite being entered is one
    somebody wants tracked, and withdrawing it later is a different operation
    from adding it. ``priority`` is absent for the same reason — it is an
    operator's weighting (D-066), changed by whoever holds the opinion rather
    than supplied by whatever loaded the catalogue.
    """

    satellite_id: str
    """``norad:NNNNN`` as text, because objects without NORAD ids exist."""

    name: str
    orbital_regime: str = "leo"


@dataclass(frozen=True, slots=True)
class NewTransmitter:
    """One downlink of one satellite, in insertable form."""

    satellite_id: str
    centre_freq_hz: int
    """Nominal, unshifted, in Hz as an integer — never MHz and never a float."""

    mode: str
    polarisation: str | None = None
    bandwidth_hz: int | None = None
    source: str = "manual"


def insert_satellite(conn: Connection, satellite: NewSatellite) -> bool:
    """Add one tracked object, or report that the catalogue already held it.

    Args:
        conn: An open connection. This function owns its transaction, which
            nests as a savepoint inside the caller's.
        satellite: The object to track.

    Returns:
        True when a row was written, False when this ``satellite_id`` was
        already present — including when it was present with a different name.

    Note:
        **A repeat load leaves the existing row exactly as it was.** Nothing is
        updated, so a catalogue file that renames a satellite does not silently
        rewrite history that passes and observations already reference. Changing
        a tracked object is an operator action with its own consequences, not a
        side effect of re-running an import.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            insert into satellites (satellite_id, name, orbital_regime)
            values (%s, %s, %s)
            on conflict (satellite_id) do nothing
            """,
            (satellite.satellite_id, satellite.name, satellite.orbital_regime),
        )
        return cur.rowcount > 0


def insert_transmitter(conn: Connection, transmitter: NewTransmitter) -> bool:
    """Add one downlink, or report that this satellite already had it.

    Args:
        conn: An open connection. This function owns its transaction, which
            nests as a savepoint inside the caller's.
        transmitter: The downlink to record. Its satellite must already exist —
            the foreign key says so, and a transmitter belonging to nothing is
            not a gap worth tolerating.

    Returns:
        True when a row was written, False when a live row already carried this
        satellite, frequency and mode.

    Note:
        Identity is the satellite, the frequency and the mode, enforced by the
        partial unique index migration 0013 adds. ``polarisation`` and
        ``bandwidth_hz`` are deliberately outside it: they describe how well a
        downlink is received rather than what is being received, so two entries
        differing only in those are one downlink described twice.
    """
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            insert into satellite_transmitters
                (satellite_id, centre_freq_hz, mode, polarisation,
                 bandwidth_hz, source)
            values (%s, %s, %s, %s, %s, %s)
            on conflict (satellite_id, centre_freq_hz, mode)
                where deleted_at is null
                do nothing
            """,
            (
                transmitter.satellite_id,
                transmitter.centre_freq_hz,
                transmitter.mode,
                transmitter.polarisation,
                transmitter.bandwidth_hz,
                transmitter.source,
            ),
        )
        return cur.rowcount > 0


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
