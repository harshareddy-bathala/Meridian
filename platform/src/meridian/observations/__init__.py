"""Ingest, normalisation, deduplication — the system of record.

Everything downstream reads what this module writes. Every reliability figure,
every model label and every published number is computed from the ``observations``
table, so the rules below are what those numbers rest on.

Records are **immutable once written**; corrections are additive, carried by a
``revision`` counter on the natural key ``(assignment_id, revision)``
(docs/DECISIONS.md D-015). A resubmission appends ``revision + 1`` and the
highest revision is current — exposed by the ``observations_current`` view.
D-015 considered a self-referencing ``supersedes_id`` pointer first and rejected
it: ``revision`` orders the lineage explicitly rather than leaving it to be
reconstructed by walking a chain.

Two modules, and the split is the point. :mod:`~meridian.observations.ingest` is
where the five invariants below land together, in one function with one test
file, because they are one decision about one submission rather than five checks
that happen to run in sequence. :mod:`~meridian.observations.canonical_body` is
pure computation, so the rule that tells a retry from a correction is checkable
without a database.

This module serves no HTTP and does not decide what counts as a missed pass. The
first belongs to ``meridian.api``, the second to ``meridian.reliability``.

``ingest`` holds these five invariants:

1. **Ownership** — a station may only submit observations for assignments issued
   to it (MSP §3), else ``not_owner``.
2. **Provenance** — ``simulated`` is copied from the station's registry record and
   never read from the payload. Station input is untrusted, and provenance is the
   last field to take on trust (D-048).
3. **Timestamp sanity** — ``started_at`` outside ``[now − 30d, now + 1h]`` is
   ``malformed``. It is the hypertable's partitioning column, and a station with a
   dead clock would otherwise place a chunk in 1970 that every retention and
   compression policy then mishandles forever. Checked at the route, which is
   where the platform clock is (D-072).
4. **Idempotency** — a resubmission whose canonical body hashes the same writes
   nothing and returns the original acknowledgement; one whose content changed
   appends a superseding revision (MSP §6, D-070).
5. **State** — the assignment transitions to ``reported`` (D-008), unless it has
   already expired, in which case the observation is stored and the state is left
   as the evidence that the station stopped naming the work (D-071).
"""
