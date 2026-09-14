"""What each round of scheduled work records about itself.

Served by the jobs process on its own listener (D-109), so the API never has to
guess whether scheduling is running: Prometheus can see when the last round
succeeded, how long it took, and how many passes it had to choose from.

The label is ``task``, not ``job``. Prometheus attaches ``job`` to every series
from the name of the scrape configuration, and a metric label of the same name
would be renamed ``exported_job`` on ingestion — and every alert written against
it would silently match nothing.

Reference: docs/DECISIONS.md D-109, D-110, D-111.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

__all__ = [
    "LAST_SUCCESS",
    "PASSES_COMPUTED",
    "PASS_GENERATION",
    "SCHEDULE",
    "SCHEDULER_CANDIDATES",
    "TASKS",
    "TASK_DURATION",
    "TASK_FAILURES",
]

PASS_GENERATION = "pass_generation"
SCHEDULE = "schedule"
TASKS = (PASS_GENERATION, SCHEDULE)
"""The two tasks a round runs, in order, and the only values ``task`` takes."""

TASK_DURATION = Histogram(
    "meridian_job_duration_seconds",
    "Wall time one task took in a round, whether it succeeded or failed.",
    ["task"],
    # A round over six hours of passes for one station takes about a second; for
    # fifty simulated stations, minutes. The last bucket is the round interval,
    # past which rounds overlap and the interval itself is the problem.
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

TASK_FAILURES = Counter(
    "meridian_job_failures",
    "Tasks that raised instead of completing, by task.",
    ["task"],
)

LAST_SUCCESS = Gauge(
    "meridian_job_last_success_timestamp_seconds",
    "Unix time at which each task last completed.",
    ["task"],
)
"""Absent for a task until its first success, never zero.

A zero would read as "last succeeded in 1970" and fire the scheduler alert on a
process that has simply not finished its first round yet; absence lets the
alert's own ``absent()`` clause decide how long to wait.
"""

PASSES_COMPUTED = Gauge(
    "meridian_passes_computed",
    "Passes the most recent pass-generation task computed over its horizon.",
)

SCHEDULER_CANDIDATES = Gauge(
    "meridian_scheduler_candidates",
    "Passes the most recent scheduling task had to choose from.",
)
