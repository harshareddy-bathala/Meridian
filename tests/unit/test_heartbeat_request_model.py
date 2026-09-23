"""``HeartbeatRequestBody`` — the clock figures a station reports must be numbers.

A non-finite clock offset is not a measurement, and one stored would stop every
dataset snapshot whose window holds it: canonical JSON has no ``NaN`` (D-070,
D-144). So it is refused where it arrives, as the observation's floats are.

Reference: docs/MSP-SPEC.md §4.2; docs/DECISIONS.md D-025, D-070.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from meridian.api.models.heartbeat import HeartbeatRequestBody

BODY: dict[str, Any] = {
    "station_id": "st_a",
    "sent_at": "2026-09-23T06:00:00Z",
    "state": "idle",
    "held_assignments": [],
}


def test_a_measured_clock_is_accepted() -> None:
    body = HeartbeatRequestBody.model_validate(
        BODY | {"clock_offset_s": 0.012, "clock_uncertainty_s": 0.004}
    )

    assert (body.clock_offset_s, body.clock_uncertainty_s) == (0.012, 0.004)


@pytest.mark.parametrize("field", ["clock_offset_s", "clock_uncertainty_s"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_clock_figure_that_is_not_a_number_is_refused(
    field: str, value: float
) -> None:
    with pytest.raises(ValidationError, match="finite"):
        HeartbeatRequestBody.model_validate(BODY | {field: value})
