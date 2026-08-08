"""Ingest, normalisation, deduplication — the system of record.

Records are **immutable once written**; corrections are additive, carried by a
``revision`` counter on the natural key ``(assignment_id, revision)``
(docs/DECISIONS.md D-015). A resubmission appends ``revision + 1`` and the
highest revision is current. D-015 considered a self-referencing
``supersedes_id`` pointer first and rejected it: ``revision`` orders the
lineage explicitly rather than leaving it to be reconstructed by walking a
chain. Every record carries provenance and a ``simulated`` flag propagated
from MSP registration — read from the station's row via
``store.stations.find_station_provenance``, never from the payload (D-048).

This module does not serve HTTP and does not decide what counts as a missed
pass. The first belongs to ``meridian.api``, the second to
``meridian.reliability`` — which is the sole authority on absence, and calls
``Registry.was_listening`` and nothing else to reach it.

``ingest`` is where five invariants land together, which is why they belong in one
function with one test file rather than spread across the API layer:

1. **Ownership** — a station may only submit observations for assignments issued
   to it (MSP §3), else ``not_owner``.
2. **Provenance** — ``simulated`` is copied from the station's registry record and
   never read from the payload. Station input is untrusted, and provenance is the
   last field to take on trust.
3. **Timestamp sanity** — ``started_at`` outside ``[now − 30d, now + 1h]`` is
   ``malformed``. It is the hypertable's partitioning column, and a station with a
   dead clock would otherwise place a chunk in 1970 that every retention and
   compression policy then mishandles forever.
4. **Idempotency** — a resubmission for an assignment that already has an
   observation appends a superseding row rather than duplicating or overwriting
   (MSP §6).
5. **State** — the assignment transitions to ``reported`` (D-008).
"""
