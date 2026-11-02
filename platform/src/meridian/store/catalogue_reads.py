"""What the catalogue holds, read back whole for ``meridian catalogue export``.

``find_active_transmitters`` reads only what pass generation needs; an export
has to be loadable into an empty database and describe the same catalogue, so
it reads every field ``meridian catalogue load`` writes. Active and undeleted
rows only: the loader has no way to write an inactive row, so exporting one
would have it come back active (D-321).

Reference: docs/DECISIONS.md D-079, D-321; ``meridian.catalogue_file``.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "CatalogueSatellite",
    "CatalogueTransmitter",
    "find_catalogue_satellites",
    "find_catalogue_transmitters",
]


@dataclass(frozen=True, slots=True)
class CatalogueSatellite:
    """One tracked satellite, as the catalogue file describes it."""

    satellite_id: str
    name: str
    orbital_regime: str


@dataclass(frozen=True, slots=True)
class CatalogueTransmitter:
    """One downlink, with every field the catalogue file carries."""

    satellite_id: str
    centre_freq_hz: int
    mode: str
    polarisation: str | None
    bandwidth_hz: int | None
    frame_interval_s: float | None


def find_catalogue_satellites(conn: Connection) -> list[CatalogueSatellite]:
    """Every active, undeleted satellite, in id order."""
    with conn.cursor(row_factory=class_row(CatalogueSatellite)) as cur:
        cur.execute(
            """
            select satellite_id, name, orbital_regime
            from satellites
            where active and deleted_at is null
            order by satellite_id
            """
        )
        return cur.fetchall()


def find_catalogue_transmitters(conn: Connection) -> list[CatalogueTransmitter]:
    """Every active, undeleted downlink of those satellites, in a stable order."""
    with conn.cursor(row_factory=class_row(CatalogueTransmitter)) as cur:
        cur.execute(
            """
            select t.satellite_id, t.centre_freq_hz, t.mode, t.polarisation,
                   t.bandwidth_hz, t.frame_interval_s
            from satellite_transmitters t
            join satellites s on s.satellite_id = t.satellite_id
            where t.active and t.deleted_at is null
              and s.active and s.deleted_at is null
            order by t.satellite_id, t.centre_freq_hz, t.mode, t.id
            """
        )
        return cur.fetchall()
