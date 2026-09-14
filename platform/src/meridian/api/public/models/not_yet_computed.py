"""The body an endpoint serves while nothing computes what it will publish.

D-086. Safety comes from *absence*: the body carries no numeric field of any kind,
so a chart reaching for a value finds nothing to draw. A ``null`` or a ``0`` would
leave a number-shaped hole exactly where a reader looks, and a zero that means
"not measured" is the failure this project exists to avoid.

``status`` is a ``Literal`` so it appears in the schema as a discriminator. The
real response will carry ``"status": "computed"`` beside its data, and a client
that branches on ``status`` survives the change without being rewritten.

``simulated`` is deliberately absent. There is no data, so there is no provenance
to state — CLAUDE.md rule 5 applies to every response that *has* results.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

__all__ = ["NotYetComputed"]


class NotYetComputed(BaseModel):
    """A placeholder that states what is missing and when it arrives."""

    status: Literal["not_yet_computed"] = "not_yet_computed"
    reason: str
    """One sentence a reader can act on: what has to exist before this does."""
    available_from_stage: int
    """The roadmap stage that computes it, in
    ``docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md``."""
