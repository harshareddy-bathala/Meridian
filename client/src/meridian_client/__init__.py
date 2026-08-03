"""Reference MSP station client.

Polls heartbeat, receives assignments, drives the receiver and rotator, decodes,
submits observations.

**Knows nothing about the database.** This distribution depends on ``httpx`` and
nothing else, so that rule is enforced when the package is built rather than when
a reviewer notices (docs/DECISIONS.md D-012).

Must survive three things, all of which are tested rather than assumed:

* **network loss mid-pass** — reception is never blocked on the platform being
  reachable. The client continues executing assignments it already holds and
  queues observations, submitting them later with their *original* timestamps.
* **power loss** — rejoins cleanly with no operator intervention.
* **clock skew** — estimates its offset against ``GET /msp/v0/time`` and reports
  both the offset and its own uncertainty in the next heartbeat.

**The station never transmits.** There are no transmit code paths here and there
never will be.
"""

__version__ = "0.1.0"
