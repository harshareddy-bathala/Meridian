"""An evaluation dataset's labelled passes, as the reliability indicators read them.

``meridian snapshot reliability`` counts the same figures the live report
does, from ``labels.jsonl`` instead of ``pass_classifications``. A label is the
same classification (D-180), so this is a change of shape and nothing else.

**What a snapshot cannot say is said, not guessed** (D-184):

* station availability — the export keeps heartbeats only inside assignment
  windows, so the time between passes is not in it;
* submission delay — the dataset keeps each report's outcome, not when it
  arrived.

Reference: docs/DECISIONS.md D-180, D-184.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from meridian.datasets.labels import LabelledPass
from meridian.reliability.slis import PassRecord

__all__ = [
    "NO_AVAILABILITY",
    "NO_SUBMISSION_DELAY",
    "passes_in_window",
]

NO_AVAILABILITY = (
    "a snapshot keeps heartbeats only inside assignment windows, so it cannot "
    "say how long a station was up between them"
)
NO_SUBMISSION_DELAY = "a snapshot keeps each report's outcome, not when it arrived"


def passes_in_window(
    labelled: Iterable[LabelledPass], *, as_of: datetime, window_days: int
) -> list[PassRecord]:
    """The labelled passes whose windows closed inside the report's window.

    Args:
        labelled: Every row of ``labels.jsonl``.
        as_of: The snapshot's own instant; the window ends there.
        window_days: How far back the window reaches.

    Returns:
        One record per labelled pass whose loss of signal falls in
        ``[as_of − window_days, as_of)``. Excluded rows (a window still open,
        nothing scheduled) are not classified passes and are left out.
    """
    start = as_of - timedelta(days=window_days)
    return [
        PassRecord(
            reference=f"pass:{one.pass_id}",
            station_id=one.station_id,
            window_end=one.los,
            classification=one.label,
            listening_confirmed=bool(one.listening_confirmed),
            outcome=one.source_outcome,
            simulated=one.simulated,
        )
        for one in labelled
        if one.label is not None and start <= one.los < as_of
    ]
