"""The network's weekly report, as the public API serves it.

The report's text, rendered by template ``network-weekly-1`` from the week's
facts, with the two hashes that tell a reader whether a second read found the
same facts and gave the same words (D-302's pair, applied to a public report).

**Counts only.** The text names no station, satellite, pass or place, and the
body carries nothing beside it but the week, the template and the hashes
(D-210).

This module builds no SQL and opens no connection.

Reference: docs/DECISIONS.md D-210, D-302, D-303, D-367.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

__all__ = ["PublicNetworkWeekly"]


class PublicNetworkWeekly(BaseModel):
    """One settled week of the network, in plain words."""

    status: Literal["computed"] = "computed"
    template: str
    """The template version that wrote the text, ``network-weekly-1``."""

    period_start: datetime
    period_end: datetime
    """Monday 00:00 UTC to the next; a pass belongs to the week its window
    closed in."""

    previous_week: date | None
    """The Monday before, or ``null`` once the week reaches back before the
    first station still registered."""

    as_of: datetime
    """When the store was read."""

    includes_simulated: bool
    """Whether the text has a simulated section, which says so in its heading."""

    subject: str
    body: str
    """Plain text, one line per ``\\n``: nothing in it is markup."""

    inputs_sha256: str
    """The facts' sha256, as hex: equal on two reads that found the same facts."""

    content_sha256: str
    """The subject and body's sha256, as hex."""
