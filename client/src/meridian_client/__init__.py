"""Reference MSP station client.

Polls heartbeat, receives assignments, drives the receiver and rotator, decodes,
submits observations.

**Knows nothing about the database.** This distribution depends on ``httpx`` and
nothing else, so that rule is enforced when the package is built rather than when
a reviewer notices (docs/DECISIONS.md D-012).

A complete station is ``station_loop.StationLoop`` over four things: a
``transport`` to the platform, the ``credentials`` that identify it, the
``held_assignments`` record of what it has accepted, and an ``execution``
implementation that receives. ``registration`` gets it admitted in the first
place; ``clock``, ``heartbeat`` and ``assignment_message`` are the protocol it
speaks once it is.

Submitting observations is Stage 9, and the receiver and decoder are Stage 13 —
until then ``execution.NullExecutor`` occupies a pass's window and receives
nothing, which is enough for a station to hold work and report listening.

Must survive three things, all of them tested:

* **network loss mid-pass** — reception is never blocked on the platform being
  reachable. Work is started from the on-disk record rather than from a
  response, so an outage changes nothing the station does.
* **power loss** — rejoins cleanly with no operator intervention. Its identity,
  its recovery key and the assignments it holds are all on disk before it claims
  any of them.
* **clock skew** — estimates its offset against the platform and reports both
  the offset and its own uncertainty, never zero for unknown.

**The station never transmits.** There are no transmit code paths here and there
never will be.
"""

__version__ = "0.1.0"
