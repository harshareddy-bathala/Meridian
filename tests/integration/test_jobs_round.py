"""One round of the real tasks, against a real schema, rolled back afterwards.

``tests/unit/test_jobs_rounds.py`` covers the supervision with stubs. This proves
the wiring those stubs stand in for: that :class:`DatabaseRoundWork` drives the
same pass generation and scheduler the CLI does, through a connection it opens
per task, and that a round over a registered station completes both tasks.

The connection it is given is the test's own, inside a savepoint, so nothing the
round writes outlives the test.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-063, D-066, D-110.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.jobs.rounds import DatabaseRoundWork, RoundPlan, run_round  # noqa: E402
from meridian.orbit.skyfield_service import SkyfieldOrbitService  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes."""
    with conn.transaction(force_rollback=True):
        yield conn


def test_a_round_completes_both_tasks_against_the_schema(
    rollback: Any, schedule_rows: Any
) -> None:
    """Both tasks return a report; neither raised on the real queries."""
    schedule_rows.station("st_jobs_round", simulated=True)
    schedule_rows.satellite()

    @contextmanager
    def connect() -> Iterator[Any]:
        with rollback.transaction():
            yield rollback

    work = DatabaseRoundWork(connect, SkyfieldOrbitService())
    plan = RoundPlan(horizon=timedelta(hours=6), model_config="A", turnaround_s=0.0)

    outcome = run_round(work, plan, datetime.now(UTC))

    assert outcome.generated is not None
    assert outcome.scheduled is not None
    assert outcome.scheduled.model_config == "A"
