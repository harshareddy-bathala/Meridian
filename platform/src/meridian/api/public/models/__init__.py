"""Response bodies for the public read API.

Every shape the dashboard receives is declared here as a Pydantic model, and each
one is built from a store row by a classmethod on the model itself. That is where
the two rules the public surface has to keep live: a station's coordinates are
coarsened to its declared precision (D-082), and liveness is derived from a
heartbeat timestamp rather than read from a column (D-054).

Putting both in the model rather than in the route means an endpoint cannot
serialise a station without applying them — there is no path from a store row to
a response that skips this package.

`meridian.api.public.models.*` is exempt from mypy's ``disallow_any_explicit`` in
``pyproject.toml``, for the same reason ``meridian.api.models.*`` is: a bare
``class X(BaseModel)`` inherits ``Any`` from Pydantic's own public surface.
"""

from __future__ import annotations

from meridian.api.public.models.stations import (
    Page,
    PublicStation,
    PublishedLocation,
    StationLiveness,
)

__all__ = [
    "Page",
    "PublicStation",
    "PublishedLocation",
    "StationLiveness",
]
