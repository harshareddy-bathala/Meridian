"""A deterministic star field, for the dark banner's background.

Stars are placed in three depth layers plus a faint diagonal band standing in
for a galactic plane, from a seeded generator: the same seed always produces
the same sky, so regenerating the banner does not silently produce a different
picture and a reviewer can tell a deliberate change from a re-run.

It is pure computation. It returns a list of placed stars in the caller's own
coordinates and knows nothing about SVG, files, or what the banner looks like.

The caller supplies a `keep_out` predicate — the wordmark and the globe are
drawn over this, and a star sitting inside a letterform or on the Earth's disc
reads as a speck of dirt rather than as depth.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass

# 137 is the band the first station receives at — an arbitrary number needs a
# provenance like any other constant, and this one at least belongs to the
# project. Changing it reshuffles the entire sky.
STAR_SEED = 137

# Layers, faintest and most numerous first: (count, min radius, max radius,
# min opacity, max opacity). Radii are viewBox units, which at the banner's
# 1500-unit width render at roughly 0.6 px each on a GitHub README.
LAYERS = (
    (190, 0.6, 1.0, 0.16, 0.34),
    (74, 1.0, 1.5, 0.34, 0.58),
    (18, 1.5, 2.1, 0.58, 0.88),
)

# The band runs corner to corner and is where a Milky Way would sit. Its width
# is a standard deviation, not an edge: stars thin out rather than stopping.
BAND_STAR_COUNT = 110
BAND_SPREAD = 78.0
BAND_START = (-60.0, 470.0)
BAND_END = (1560.0, 90.0)

# Only the brightest layer twinkles. A sky where everything moves is noise,
# and the effect is meant to be noticed on the second look, not the first.
TWINKLE_PERIOD_S = 7.0


@dataclass(frozen=True)
class Star:
    """One star, in the caller's coordinates.

    Attributes:
        x: Horizontal position, viewBox units.
        y: Vertical position, viewBox units, y down.
        radius: Drawn radius, viewBox units.
        opacity: Resting opacity, 0 to 1.
        twinkle_delay_s: Offset into the shared twinkle cycle, or None for a
            star that does not twinkle. Staggering the delays is what stops
            the whole sky pulsing in unison.
    """

    x: float
    y: float
    radius: float
    opacity: float
    twinkle_delay_s: float | None


def star_field(
    width: float,
    height: float,
    keep_out: Callable[[float, float], bool],
    seed: int = STAR_SEED,
) -> list[Star]:
    """Place the whole sky: three uniform layers, then the diagonal band.

    Args:
        width: Frame width in viewBox units.
        height: Frame height in viewBox units.
        keep_out: True for a position a star must not occupy. Called once per
            candidate; a rejected candidate is dropped rather than retried, so
            the excluded regions read as genuinely empty sky rather than as a
            rim of stars packed against their edges.
        seed: Anything reproducible. Defaults to the project's own.

    Returns:
        Stars in draw order, faintest first.
    """
    rng = random.Random(seed)
    stars: list[Star] = []
    for index, (count, min_r, max_r, min_o, max_o) in enumerate(LAYERS):
        brightest = index == len(LAYERS) - 1
        for _ in range(count):
            x, y = rng.uniform(0, width), rng.uniform(0, height)
            if keep_out(x, y):
                continue
            delay = rng.uniform(0, TWINKLE_PERIOD_S) if brightest else None
            radius = rng.uniform(min_r, max_r)
            stars.append(Star(x, y, radius, rng.uniform(min_o, max_o), delay))
    stars.extend(_band_stars(rng, keep_out))
    return stars


def _band_stars(
    rng: random.Random,
    keep_out: Callable[[float, float], bool],
) -> list[Star]:
    """The galactic band: small stars scattered normally about a diagonal."""
    dx = BAND_END[0] - BAND_START[0]
    dy = BAND_END[1] - BAND_START[1]
    length = math.hypot(dx, dy)
    normal_x, normal_y = -dy / length, dx / length

    stars = []
    for _ in range(BAND_STAR_COUNT):
        along = rng.random()
        offset = rng.gauss(0, BAND_SPREAD)
        x = BAND_START[0] + dx * along + normal_x * offset
        y = BAND_START[1] + dy * along + normal_y * offset
        if keep_out(x, y):
            continue
        # Fainter the further from the band's centre line, so the edges dissolve
        # instead of ending. The band is haze, not a stripe.
        falloff = math.exp(-((offset / BAND_SPREAD) ** 2))
        stars.append(Star(x, y, rng.uniform(0.5, 0.9), 0.10 + 0.22 * falloff, None))
    return stars
