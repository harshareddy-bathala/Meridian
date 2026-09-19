"""The reception layer: a receiver, a decoder and a rotator below one executor.

:class:`meridian_client.execution.PassExecutor` is the station loop's only view
of reception. Everything in this package sits below it — the narrower protocols
it is composed of, the adapters that implement them, and the capture folder each
reception keeps on disk (D-120, D-123).

**The station never transmits.** No protocol here has a transmit-shaped method,
no adapter opens a device for writing, and a test holds the surface to that
(D-126).

Reference: docs/DECISIONS.md D-120 to D-126.
"""
