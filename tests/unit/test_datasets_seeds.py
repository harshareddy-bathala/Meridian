"""``meridian.datasets.seeds`` — one master seed, a stable seed per component.

Reference: docs/DECISIONS.md D-236.
"""

from __future__ import annotations

import hashlib

from meridian.datasets.seeds import derive
from meridian.regions.change import _seed as regional_seed


def test_a_components_seed_is_the_same_everywhere() -> None:
    """Pinned as a value: a change here moves every interval in every report."""
    digest = hashlib.sha256(b"4471:bootstrap.scheduling").digest()

    assert derive(4471, "bootstrap.scheduling") == int.from_bytes(digest[:8], "big")


def test_each_component_draws_its_own_seed() -> None:
    names = ["model.A", "model.C", "model.D", "solver", "bootstrap.orbit"]

    assert len({derive(4471, name) for name in names}) == len(names)


def test_another_master_moves_every_component() -> None:
    assert derive(4471, "solver") != derive(4472, "solver")


def test_the_regional_report_draws_what_it_drew_before() -> None:
    """Regions now call :func:`derive`; the seeds it hands out must not move."""
    before = hashlib.sha256(b"8:3:ndvi").digest()

    assert regional_seed(8, 3, "ndvi") == int.from_bytes(before[:8], "big")
