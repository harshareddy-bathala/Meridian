"""The latest published conditions, as the public API states them.

Reads ``environment_samples`` (D-221) for the two quantities the conditions
feature group uses (D-224): the planetary K index and cloud cover. For each, the
newest value published before ``now`` describing an interval begun by then,
latest revision first, as D-222's rule chooses a value for a pass. Kp is one
series; cloud cover is one per point an operator asked about.

**A published gap is returned as the gap.** A row with no value and a
``missing_reason`` says the source published nothing for that interval, which
is a different fact from never having asked (D-221).

Each row carries its source's name, licence and terms, so a reader can credit
it (D-134).

**Read live, for display only.** The scheduler never reads this: its conditions
come from a snapshot (D-224), and nothing here is a feature value.

Reference: docs/DECISIONS.md D-134, D-221, D-222, D-224, D-331.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = ["CLOUD_COVER", "KP_INDEX", "PublishedSample", "find_latest_samples"]

KP_INDEX = "kp_index"
CLOUD_COVER = "cloud_cover"


@dataclass(frozen=True, slots=True)
class PublishedSample:
    """One published value, or published gap, with the source it came from."""

    quantity: str
    value: float | None
    missing_reason: str | None
    value_unit: str
    observed_from: datetime
    observed_to: datetime
    published_at: datetime
    published_basis: str
    product: str
    lat_deg: float | None
    lon_deg: float | None
    source_id: str
    source_name: str
    licence: str
    terms_url: str


def find_latest_samples(
    conn: Connection, quantity: str, *, now: datetime, covering: bool
) -> list[PublishedSample]:
    """The newest value of ``quantity`` at each point, as of ``now``.

    Args:
        conn: An open connection. Read-only.
        quantity: ``kp_index`` or ``cloud_cover``.
        now: Only values published before this, describing an interval begun
            by then, are candidates.
        covering: Only an interval that still contains ``now``, as a forecast
            for the hour must; otherwise the latest interval begun.

    Returns:
        One row per point, a sample with no point counting as one, nearest
        point first by latitude then longitude.
    """
    with conn.cursor(row_factory=class_row(PublishedSample)) as cur:
        cur.execute(
            """
            select distinct on (e.lat_deg, e.lon_deg)
                   e.quantity, e.value, e.missing_reason, e.value_unit,
                   e.observed_from, e.observed_to, e.published_at,
                   e.published_basis, e.product, e.lat_deg, e.lon_deg,
                   s.source_id, s.name as source_name, s.licence, s.terms_url
            from environment_samples e
            join ingest_sources s on s.source_id = e.source_id
            where e.quantity = %(quantity)s
              and e.published_at < %(now)s
              and e.observed_from <= %(now)s
              and (not %(covering)s or e.observed_to >= %(now)s)
            order by e.lat_deg, e.lon_deg, e.observed_from desc,
                     e.published_at desc, e.sample_id desc
            """,
            {"quantity": quantity, "now": now, "covering": covering},
        )
        return cur.fetchall()
