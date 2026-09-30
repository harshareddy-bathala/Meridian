"""One master seed, and every component's seed derived from it by name.

A report runs several seeded things — model folds, the solver, three bootstraps
— and each must be regenerable. Recording one seed per component would let two
of them be set to the same value by accident, and would give a reader eight
numbers to copy instead of one. So a run takes one master seed, and each
component's seed is the first eight bytes of
``sha256(f"{master}:{component}")``: different for every name, stable across
machines and Python versions, and written into the run's manifest beside the
master so a reader can see what each part drew from (D-236).

``random.Random`` and HiGHS both accept a 64-bit integer, so eight bytes are
used whole. Here rather than in ``meridian.reports`` because the regional
report derives its per-area seeds the same way, and both read snapshots
through this package already.

Reference: docs/DECISIONS.md D-236.
"""

from __future__ import annotations

import hashlib

__all__ = ["MASTER_SEED_MAX", "derive"]

MASTER_SEED_MAX = 2**32 - 1
"""The largest master seed accepted, as the regional configuration's seed is."""


def derive(master: int, component: str) -> int:
    """The seed ``component`` draws from, under master seed ``master``.

    Args:
        master: The run's master seed.
        component: A name, such as ``bootstrap.scheduling``. Names are the
            interface: renaming a component changes what it draws.

    Returns:
        A non-negative integer below 2**64.
    """
    digest = hashlib.sha256(f"{master}:{component}".encode()).digest()
    return int.from_bytes(digest[:8], "big")
