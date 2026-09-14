"""The platform's scheduled work: pass generation, then scheduling, on a timer.

``meridian jobs run`` is the long-running process that keeps a deployment's
horizon filled without anyone running a command (D-110). Before it, only the
simulator profile's shell loop did this, so a deployment without the simulator
scheduled nothing — which failed the independence test in ``CLAUDE.md``.

- :mod:`meridian.jobs.rounds` — one round, and the loop that repeats it until
  told to stop.
- :mod:`meridian.jobs.job_metrics` — what a round records about itself.
- :mod:`meridian.jobs.metrics_listener` — the token-guarded endpoint Prometheus
  scrapes, inside the compose network only (D-109).

The work each round does is not here. It is ``meridian.pass_generation`` and
``meridian.scheduler``, exactly as ``meridian passes generate`` and
``meridian schedule`` call them.

Reference: docs/DECISIONS.md D-063, D-066, D-109, D-110.
"""
