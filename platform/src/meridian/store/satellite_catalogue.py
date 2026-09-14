"""The satellites the public API lists, and the downlinks it shows for one.

Reads ``satellites`` and ``satellite_transmitters`` for the dashboard's
catalogue: what is tracked, whether we still expect to hear it, and how recently
we obtained an element set for it. It is a different read from
``meridian.store.satellites``' ``find_active_transmitters`` — that one answers
"what should pass generation propagate", this one answers "what is in the
catalogue" — and the two differ on the question that matters most here.

**A silent satellite appears in this read, labelled.** ``find_active_transmitters``
excludes ``active = false`` deliberately: a pass computed for a transmitter
nobody expects to hear enters the completeness denominator as an opportunity that
was never real, which is EVALUATION.md §5's silent-satellite confound. That
argument is about what to *schedule*. A reader asking why a satellite produced no
data this week is asking exactly the opposite question, and an endpoint that hid
the answer would make the catalogue look smaller than it is.

Soft-deleted rows are excluded in both reads. Withdrawing a satellite from the
catalogue is a statement that we stopped tracking it, not that it went quiet.

Element-set epochs come back raw. Age is the interesting quantity and it is
derived against a single clock reading in the API layer, for the reason liveness
is (D-054): a list aged row by row measures each satellite against a slightly
different "now".

Reference: docs/DATA-MODEL.md; docs/DECISIONS.md D-021, D-066, D-085.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "CataloguedSatellite",
    "CataloguedTransmitter",
    "find_satellite",
    "find_satellites_after",
    "find_transmitters_for_satellite",
]

_CATALOGUE_COLUMNS = """
    s.satellite_id, s.name, s.orbital_regime, s.priority,
    s.active as is_active,
    (select max(e.epoch) from element_sets e
      where e.satellite_id = s.satellite_id) as latest_element_set_epoch
"""
"""Every column both reads select, written once so the two cannot diverge.

A satellite's detail page and its row in the list must agree about the satellite;
two column lists maintained separately is how a field appears in one and not the
other, which reads to a user as data that is missing rather than as a projection
that drifted.
"""


@dataclass(frozen=True, slots=True)
class CataloguedSatellite:
    """One tracked object as a reader may see it."""

    satellite_id: str
    """``norad:NNNNN`` as text, because objects without NORAD ids exist."""

    name: str
    orbital_regime: str
    """One of ``leo``, ``meo``, ``geo``, ``heo``, ``other``."""

    priority: float
    """The operator weighting the scheduler multiplies elevation by (D-066).

    Published because it is half the answer to "why was that pass chosen over
    this one", and an endpoint that serves scheduling decisions while withholding
    the weight behind them explains nothing. 1.0 is the default and the value at
    which configuration B reduces to configuration A exactly.
    """

    is_active: bool
    """Whether we still expect to hear this object transmitting.

    False is a claim about the satellite, not about our records: a
    decommissioned payload or one switched off is still tracked, still has a
    history, and still appears here. It is the column that keeps a silent
    satellite from being read as a prediction failure.
    """

    latest_element_set_epoch: datetime | None
    """The newest epoch held for this satellite, or ``None`` if we hold none.

    ``None`` means a tracked object we cannot currently propagate, which is a gap
    worth seeing rather than an inconsistency worth preventing — the catalogue
    and the element-set archive are deliberately separate tables. The epoch
    rather than the retrieval time: accuracy decays from the epoch, so that is
    the instant an age is measured from.
    """


def find_satellites_after(
    conn: Connection, satellite_id: str | None, limit: int
) -> list[CataloguedSatellite]:
    """One page of the satellite catalogue, ordered by id.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        satellite_id: The last id on the previous page, or ``None`` for the first
            page. Strictly exclusive, so no satellite is served twice.
        limit: The most rows to return. Callers ask for one more than the page
            needs and trim, which is how "is there another page" becomes a fact
            rather than a guess (D-085).

    Returns:
        The matching satellites in ascending ``satellite_id`` order, empty when
        the cursor has passed the last one.

    Note:
        The order is lexicographic over text, so ``norad:9`` sorts after
        ``norad:12345``. That is catalogue order, not a ranking and not
        alphabetical by name — a name is not unique, and a cursor over a
        non-unique column cannot promise each row is served once.
    """
    with conn.cursor(row_factory=class_row(CataloguedSatellite)) as cur:
        cur.execute(
            f"""
            select {_CATALOGUE_COLUMNS}
            from satellites s
            where s.deleted_at is null
              and (%s::text is null or s.satellite_id > %s)
            order by s.satellite_id asc
            limit %s
            """,
            (satellite_id, satellite_id, limit),
        )
        return cur.fetchall()


def find_satellite(conn: Connection, satellite_id: str) -> CataloguedSatellite | None:
    """One satellite by id, or ``None`` when there is no such satellite.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        satellite_id: The object to look up.

    Returns:
        The satellite, or ``None`` — which a soft-deleted satellite also
        produces, so a caller cannot tell one withdrawn from the catalogue from
        one that was never in it.
    """
    with conn.cursor(row_factory=class_row(CataloguedSatellite)) as cur:
        cur.execute(
            f"""
            select {_CATALOGUE_COLUMNS}
            from satellites s
            where s.satellite_id = %s
              and s.deleted_at is null
            """,
            (satellite_id,),
        )
        return cur.fetchone()


@dataclass(frozen=True, slots=True)
class CataloguedTransmitter:
    """One downlink of one satellite, as a reader may see it.

    Wider than ``meridian.store.satellites``' ``StoredTransmitter``, which reads
    the three columns pass generation needs. ``polarisation`` and
    ``bandwidth_hz`` describe how well a downlink is received rather than what is
    being received, so nothing in the scheduling path decides anything with them
    — and they are exactly what somebody deciding whether their own hardware
    could receive this satellite wants to read.
    """

    satellite_id: str
    centre_freq_hz: int
    """Nominal, unshifted, in Hz as an integer — never MHz and never a float."""

    mode: str
    """The demodulator needed, lowercase free text in Phase 1."""

    polarisation: str | None
    """One of ``rhcp``, ``lhcp``, ``linear_v``, ``linear_h``, ``linear``,
    ``none`` — or ``None`` where it is simply not known, which is a real record
    rather than a missing one."""

    bandwidth_hz: int | None
    """Occupied bandwidth, or ``None`` when unrecorded."""

    is_active: bool
    """Whether this downlink is expected to be heard, independent of whether the
    satellite is. A satellite can be alive with one of its transmitters off."""

    source: str
    """Where the entry came from — ``manual`` or ``simulator``. Published for the
    same reason ``simulated`` is: a catalogue entry invented for a simulator run
    must not be readable as a fact about a real spacecraft."""


def find_transmitters_for_satellite(
    conn: Connection, satellite_id: str
) -> list[CataloguedTransmitter]:
    """Every live downlink recorded for one satellite, silent ones included.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        satellite_id: The satellite whose downlinks to read.

    Returns:
        Its transmitters ordered by frequency then id, empty when none are
        recorded — which is also what an unknown satellite returns, so the
        caller checks the satellite exists rather than inferring it from this.

    Note:
        ``active = false`` rows are returned and labelled, unlike
        ``find_active_transmitters``. Ordered rather than arbitrary for the
        reason every read here is: a response whose field order depends on the
        query planner is not reproducible.
    """
    with conn.cursor(row_factory=class_row(CataloguedTransmitter)) as cur:
        cur.execute(
            """
            select satellite_id, centre_freq_hz, mode, polarisation,
                   bandwidth_hz, active as is_active, source
            from satellite_transmitters
            where satellite_id = %s
              and deleted_at is null
            order by centre_freq_hz asc, id asc
            """,
            (satellite_id,),
        )
        return cur.fetchall()
