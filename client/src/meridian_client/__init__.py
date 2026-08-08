"""Reference MSP station client.

Polls heartbeat, receives assignments, drives the receiver and rotator, decodes,
submits observations.

**Knows nothing about the database.** This distribution depends on ``httpx`` and
nothing else, so that rule is enforced when the package is built rather than when
a reviewer notices (docs/DECISIONS.md D-012).

**Built so far:** ``transport`` (retrying MSP HTTP) and ``clock`` (offset
estimation against ``GET /msp/v0/time``). Registration, credential
persistence and the assignment loop are Stage 4.3's client half and Stage 8 —
which is why Stage 4's completion gate, a *client* gate, is not yet met.

Must survive three things. Clock skew is tested today; the other two are
tested when the code that has to survive them exists:

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
