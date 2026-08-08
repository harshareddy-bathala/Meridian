"""Virtual stations speaking real MSP over the real network stack.

**Not a mock.** It is a client implementation — built on ``meridian_client``, the
same transport the reference station uses — and it is what makes software-first
development possible. A mock would prove the platform works against a mock.

**Stub only.** ``station.py`` is an entry-point shell that exits with a
message naming the stage that fills it in; nothing here speaks MSP yet. The
properties below are the contract it will be built to, not claims about code
that exists.

Deterministic from a seed: the same seed will produce a byte-identical
sequence of observations, to be asserted by hash in the test suite. That is
docs/EVALUATION.md §9's reproducibility requirement made executable rather
than aspirational — and the assertion is the part that makes it so, which is
why it is named here before there is anything to assert it about.

Simulated stations register with ``simulated: true`` plus ``simulator_run_id`` and
``seed``, and the platform propagates that flag to every derived record, every API
response and every dashboard element. Simulated results are never aggregated with
measured ones in any reported figure.

Geometry is real: the simulator propagates real catalogue element sets, so only
its *outcomes* are synthetic. A simulator with fabricated orbits would test the
platform against passes that do not exist.
"""

__version__ = "0.1.0"
