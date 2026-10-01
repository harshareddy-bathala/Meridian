"""The rating queue is blind: it reads and prints nothing the verdict reads.

D-260's label is independent of the verdict's inputs only if the person rating
never sees them. The queue's ``select`` decides what a rater sees, so its
columns are pinned here, and the printed queue is checked for every input.

Reference: docs/DECISIONS.md D-106, D-260.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from meridian.cli_verdict import queue_lines
from meridian.store import ratings
from meridian.store.ratings import QueuedProduct, QueuedReception

VERDICT_INPUTS = (
    "outcome",
    "signal_detected",
    "peak_snr_db",
    "snr_samples",
    "noise_floor_dbfs",
    "receiver_gain_db",
    "frames_decoded",
    "frames_failed",
    "decoder",
    "first_detection_at",
    "client_notes",
    "probability_usable",
    "reception_verdicts",
    "listening",
    "heartbeats",
)


def selected_columns(sql: str) -> list[str]:
    selected = re.search(r"select (.*?)\s+from ", sql, re.DOTALL)
    assert selected is not None
    return [one.strip() for one in selected.group(1).split(",")]


def test_the_queue_selects_identity_time_and_products_only() -> None:
    assert selected_columns(ratings._QUEUE) == [
        "o.assignment_id",
        "o.revision",
        "o.station_id",
        "o.satellite_id",
        "o.started_at",
        "p.kind",
        "p.sha256",
        "p.uri",
    ]


def test_the_queue_query_names_no_verdict_input() -> None:
    for name in VERDICT_INPUTS:
        assert name not in ratings._QUEUE, name


def test_the_printed_queue_holds_no_verdict_input() -> None:
    lines = queue_lines(
        [
            QueuedReception(
                assignment_id="as_a",
                revision=1,
                station_id="st_001",
                satellite_id="norad:57166",
                started_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
                products=(QueuedProduct("image", bytes(32), "file:///p/a.png"),),
            )
        ]
    )

    text = "\n".join(lines)
    assert "as_a  revision 1  st_001  norad:57166" in text
    assert "image  " + "00" * 32 + "  file:///p/a.png" in text
    for name in VERDICT_INPUTS:
        assert name not in text, name


def test_an_empty_queue_says_so() -> None:
    assert queue_lines([]) == [
        "nothing to rate: every measured reception with products is rated"
    ]
