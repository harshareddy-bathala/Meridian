"""The public read API — everything the dashboard and a curious stranger can see.

The endpoints live under ``/api/v1``, versioned in the path and carrying **no
version header**: the dashboard ships from the same image as the platform, so a
version skew between them is not a state this deployment can reach, and a header
would break ``curl https://dash.meridian.org.in/api/v1/stations`` for no benefit
(D-083).

It is a different surface from ``meridian.api.msp``, which is a published
protocol that strangers implement and which makes them a deprecation promise in
MSP §7. This one promises only that the dashboard at this commit works against
it. A breaking change becomes ``/api/v2``, served beside ``v1`` for as long as
anything third-party is known to use it.

Every module here is **read-only and thin**: it queries through the store layer,
hands the rows to a model, and returns. Nothing in this package writes.

The assembled router is :mod:`meridian.api.public.surface`, not this file, and
that module explains why. This one imports nothing on purpose.

Reference: docs/DECISIONS.md D-083, D-084, D-085, D-086; docs/PROJECT.md §13.
"""

from __future__ import annotations

__all__: list[str] = []
