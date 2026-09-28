"""Which decided passes a round decides again, and when that writes anything — D-171.

A round considers every pass in its horizon that is **open**:

* never decided under this configuration;
* skipped — a skip records why the pass lost, and a round has a reason to ask
  again when the pass it lost to has been declined, or its station has
  returned;
* revoked because its station was offline — the station is back, since only
  an available station's passes are decided, and did not name the assignment
  on its return, or it would have been reinstated.

Everything else is **closed**: work the station holds or has done, and an
assignment the station declined — offering a station the pass it just let go
would be asking it to decline again.

A decision about an open pass is written as a new revision only when it
changed: a skip that is skipped again for the same assignment says nothing
new, and a round writing it would fill the table with copies every five
minutes. Anything else — a skip now taken, a skip now blocked by something
else, a revoked pass decided at all — is a new row.

A leaf: no I/O and no clock.

Reference: docs/DECISIONS.md D-003, D-165, D-171.
"""

from __future__ import annotations

from meridian.store.schedule_reads import LatestDecision
from meridian.store.schedule_writes import NewAssignment

__all__ = ["next_revision", "reopens", "unchanged"]


def reopens(latest: LatestDecision | None) -> bool:
    """Whether a pass with this latest decision is decided again."""
    if latest is None or latest.decision == "skipped":
        return True
    return latest.state == "revoked" and latest.revoked_reason == "offline"


def next_revision(latest: LatestDecision | None) -> int:
    """The revision a new decision about the pass is written as."""
    return 0 if latest is None else latest.revision + 1


def unchanged(latest: LatestDecision | None, row: NewAssignment) -> bool:
    """Whether a new decision says only what the latest one already did."""
    return (
        latest is not None
        and latest.decision == "skipped"
        and row.decision == "skipped"
        and latest.conflicts_with_assignment_id == row.conflicts_with_assignment_id
    )
