"""What the platform tells Prometheus, and who may ask.

Two processes serve metrics — the API and the scheduled jobs — and this package
holds what both need: the bearer-token check, and the assembly of one scrape out
of the process's own counters and any collectors that read the database when the
scrape arrives.

**The metrics themselves are not defined here.** Each is declared beside the code
that increments it, so the owner of an event is also the owner of its counter,
and there is no catch-all module of names for every change to touch.

Every metric name starts with ``meridian_`` and every label has a small, fixed
set of values. No station, satellite, assignment or token identifier is ever a
label, and a series that could mix simulated and measured stations carries
``simulated`` (D-111).

Reference: docs/DECISIONS.md D-087, D-109, D-111.
"""
