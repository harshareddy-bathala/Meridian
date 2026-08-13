"""A deterministic meteor shower: where each streak starts, ends and when.

Real meteors in a shower travel on near-parallel tracks, because they are all
entering the atmosphere along the same orbit — which is why they appear to
radiate from one point in the sky. So these are not scattered at random
angles: they share a heading, jittered by a few degrees, and vary instead in
where they start, how fast they cross and how long they wait between passes.
Scattered headings look like static; parallel ones look like a sky.

Each meteor also carries its own period and phase, so they arrive irregularly
rather than in a visible cycle.

It is pure computation, seeded, and returns plain values in the caller's
coordinates. It draws nothing and knows nothing about SVG.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

# The same number as the star field's seed, drawn from a separate generator so
# that changing the meteors does not reshuffle the stars behind them.
METEOR_SEED = 137

# Nine tracks against the periods below start a meteor about every two seconds,
# each lasting under a second and a half, which leaves rather less than one on
# screen at any instant. A realistic shower is far sparser than that and would
# mean a reader who glances at the banner sees nothing move.
#
# Nine different tracks is also what keeps it from reading as a loop: each one
# repeats every twelve to twenty-six seconds, but never where the last one was.
METEOR_COUNT = 9

# Meteors fall to the lower right. The jitter is what keeps them from looking
# like a ruled hatch, and is small because a shower's tracks really are close
# to parallel.
HEADING_DEG = 27.0
HEADING_JITTER_DEG = 9.0

# How long a streak is on screen, and how long it is. A meteor crosses in
# under two seconds; the wait between them is what makes one feel like an
# event rather than like a loading spinner.
CROSSING_S = (0.9, 1.9)
PERIOD_S = (11.0, 26.0)
TRAIL_LENGTH = (95.0, 195.0)
TRAVEL_LENGTH = (520.0, 900.0)
HEAD_RADIUS = (1.4, 2.3)
PEAK_OPACITY = (0.55, 0.95)

# Every track is placed through a point inside the frame rather than from an
# entry point on an edge. Choosing an entry point is the obvious way to do it
# and produces meteors that clip a corner and leave: a track is only worth
# animating if it crosses sky somebody is looking at.
CROSSING_INSET = 60.0

# Those crossing points are stratified across the width — one per band, jittered
# inside it — rather than drawn independently. Nine independent draws leave gaps
# and clumps often enough that the layout becomes a property of the seed, and
# the first seed tried put all nine in the right half of the frame.
#
# The bands stop short of the right edge because the globe is there. A meteor
# over the wireframe is legible but busy, and the tracks run down and to the
# right anyway, so they arrive at the globe on their own.
CROSSING_FIELD_RIGHT = 1180.0

# How much of the track is spent getting to that point. Varying it is what
# stops every meteor being at the same place at the same moment of its own
# cycle.
LEAD_FRACTION = (0.30, 0.62)


@dataclass(frozen=True)
class Meteor:
    """One streak: a straight track, a length, and a schedule.

    Attributes:
        start_x: Where the head enters, banner units. Off-frame for most.
        start_y: Where the head enters, banner units.
        end_x: Where the head leaves, banner units.
        end_y: Where the head leaves, banner units.
        heading_deg: Direction of travel, clockwise from the +x axis. The
            trail is drawn along this, so the caller rotates by it rather
            than computing the trail's endpoints itself.
        trail_length: Length of the visible tail behind the head.
        head_radius: Radius of the bright head.
        peak_opacity: Brightest the streak gets, mid-crossing.
        crossing_s: Seconds from entering to leaving.
        period_s: Seconds between one crossing and the next.
        phase_s: Offset into the cycle at load, so they do not all start
            together on the first frame.
    """

    start_x: float
    start_y: float
    end_x: float
    end_y: float
    heading_deg: float
    trail_length: float
    head_radius: float
    peak_opacity: float
    crossing_s: float
    period_s: float
    phase_s: float


def meteor_shower(height: float, seed: int = METEOR_SEED) -> list[Meteor]:
    """Place the whole shower.

    Takes no width: the band the crossings are spread across ends at
    `CROSSING_FIELD_RIGHT`, short of the frame's own right edge, to leave the
    globe alone.

    Args:
        height: Frame height in banner units.
        seed: Anything reproducible. Defaults to the project's own.

    Returns:
        `METEOR_COUNT` meteors, in no particular order.
    """
    rng = random.Random(seed)
    return [_one_meteor(rng, band, height) for band in range(METEOR_COUNT)]


def _one_meteor(rng: random.Random, band: int, height: float) -> Meteor:
    """One meteor, crossing the frame inside its own vertical band.

    Args:
        rng: The shower's generator.
        band: Which of the `METEOR_COUNT` bands across the width this one
            crosses in.
        height: Frame height in banner units.

    Returns:
        The placed meteor.
    """
    heading_deg = HEADING_DEG + rng.uniform(-HEADING_JITTER_DEG, HEADING_JITTER_DEG)
    heading_rad = heading_deg * math.pi / 180
    travel = rng.uniform(*TRAVEL_LENGTH)

    band_width = (CROSSING_FIELD_RIGHT - CROSSING_INSET) / METEOR_COUNT
    crosses_x = CROSSING_INSET + band_width * (band + rng.random())
    crosses_y = rng.uniform(CROSSING_INSET, height - CROSSING_INSET)
    lead = travel * rng.uniform(*LEAD_FRACTION)
    period_s = rng.uniform(*PERIOD_S)

    return Meteor(
        start_x=crosses_x - lead * math.cos(heading_rad),
        start_y=crosses_y - lead * math.sin(heading_rad),
        end_x=crosses_x + (travel - lead) * math.cos(heading_rad),
        end_y=crosses_y + (travel - lead) * math.sin(heading_rad),
        heading_deg=heading_deg,
        trail_length=rng.uniform(*TRAIL_LENGTH),
        head_radius=rng.uniform(*HEAD_RADIUS),
        peak_opacity=rng.uniform(*PEAK_OPACITY),
        crossing_s=rng.uniform(*CROSSING_S),
        period_s=period_s,
        # Drawn from this meteor's own period, so the shower is already
        # scattered through its cycle on the very first frame rather than
        # starting with everything at once.
        phase_s=rng.uniform(0, period_s),
    )
