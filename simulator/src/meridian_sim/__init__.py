"""Virtual stations speaking real MSP over the real network stack.

**Not a mock.** It is a client implementation — built on ``meridian_client``, the
same transport the reference station uses — and it is what makes software-first
development possible. A mock would prove the platform works against a mock.

Deterministic from a seed: the same seed produces a byte-identical sequence of
observations, asserted by hash in the test suite. That is docs/EVALUATION.md §9's
reproducibility requirement made executable rather than aspirational.

Simulated stations register with ``simulated: true`` plus ``simulator_run_id`` and
``seed``, and the platform propagates that flag to every derived record, every API
response and every dashboard element. Simulated results are never aggregated with
measured ones in any reported figure.

Geometry is real: the simulator propagates real catalogue element sets, so only
its *outcomes* are synthetic. A simulator with fabricated orbits would test the
platform against passes that do not exist.
"""

__version__ = "0.1.0"
