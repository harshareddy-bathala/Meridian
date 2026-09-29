"""Products — what a station declared it holds from one reception.

Writes ``products`` (``deploy/migrations/sql/0020_deferred_storage.sql``). An
observation revision's ``products`` array is stored verbatim in
``observations.products_json`` (D-018), and each element that names a kind and a
sha256 also becomes a row here, in the same transaction (D-176).

The rows are read from the stored revision, not from the submission, by the
query migration 0020 backfilled every earlier observation with. So ingest and
the backfill hold one rule for which elements are products:
- an object whose ``kind`` is a non-empty string and whose ``sha256`` is 64 hex
  digits;
- ``size_bytes`` and ``uri`` are taken where they have the right type, and left
  null where they do not.

An element that fails the rule stays in ``products_json``. MSP §4.4 leaves a
product's shape open, so it is not an error; it is counted and gets no row.

Reference: docs/DECISIONS.md D-018, D-029, D-176.
"""

from __future__ import annotations

from dataclasses import dataclass

from meridian.store.stations import Connection

__all__ = ["RecordedProducts", "record_observation_products"]

_RECORD_PRODUCTS = """
    insert into products (
        assignment_id, revision, observation_started_at, station_id, element_index,
        kind, sha256, size_bytes, uri, simulated
    )
    select o.assignment_id, o.revision, o.started_at, o.station_id,
           (e.position - 1)::integer,
           e.element ->> 'kind',
           decode(lower(e.element ->> 'sha256'), 'hex'),
           case
               when jsonb_typeof(e.element -> 'size_bytes') = 'number'
                    and (e.element ->> 'size_bytes') ~ '^[0-9]{1,18}$'
               then (e.element ->> 'size_bytes')::bigint
           end,
           case
               when jsonb_typeof(e.element -> 'uri') = 'string'
               then e.element ->> 'uri'
           end,
           o.simulated
    from observations o
    cross join lateral jsonb_array_elements(
        case when jsonb_typeof(o.products_json) = 'array'
             then o.products_json else '[]'::jsonb end
    ) with ordinality as e(element, position)
    where o.assignment_id = %(assignment_id)s
      and o.revision = %(revision)s
      and jsonb_typeof(e.element) = 'object'
      and jsonb_typeof(e.element -> 'kind') = 'string'
      and e.element ->> 'kind' <> ''
      and jsonb_typeof(e.element -> 'sha256') = 'string'
      and e.element ->> 'sha256' ~ '^[0-9a-fA-F]{64}$'
"""


@dataclass(frozen=True, slots=True)
class RecordedProducts:
    """How many of a revision's products became rows, and how many could not."""

    written: int
    skipped: int
    """Elements kept in ``products_json`` with no row: no kind, or no valid hash."""


def record_observation_products(
    conn: Connection, *, assignment_id: str, revision: int, submitted: int
) -> RecordedProducts:
    """Write a row for each product one stored observation revision declares.

    Args:
        conn: An open connection, inside the transaction that wrote the
            revision, so the rows commit or roll back with it.
        assignment_id: The observation's assignment.
        revision: The revision just written.
        submitted: How many elements its ``products`` array held.

    Returns:
        The rows written, and the elements that could not be one.
    """
    with conn.cursor() as cur:
        cur.execute(
            _RECORD_PRODUCTS, {"assignment_id": assignment_id, "revision": revision}
        )
        written = max(cur.rowcount, 0)
    return RecordedProducts(written=written, skipped=submitted - written)
