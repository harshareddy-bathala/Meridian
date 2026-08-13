"""The banner's lockup: the mark, the wordmark and the subline, measured and drawn.

Everything here is the identity block that sits in the middle of the banner.
Its proportions are the ones `make-images.py` uses for the raster lockup — the
wordmark's em is 0.80 of the mark's radius, the gap between them is 1.50 of it,
and the letters are tracked out by 0.38 em — so the two exports are the same
drawing at different sizes rather than two drawings that resemble each other.

The lockup is measured and then centred, never positioned by eye. Tracking of
0.38 em across eight letters is nearly a third of the wordmark's total width,
so its width cannot be guessed from the point size, and a guess overruns the
frame.

It returns SVG fragments as strings and takes colours as arguments, so it holds
no opinion about which theme it is being drawn in and writes nothing to disk.

Coordinates are the banner's own units, y down. Text arrives from
wordmark_outlines as outlines in font units with y up, and is flipped here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wordmark_outlines import Wordmark, typeset

FONTS = Path(__file__).resolve().parent.parent / "fonts"

MARK_RADIUS = 70.0
WORDMARK_EM_PER_MARK_RADIUS = 0.80
LOCKUP_GAP_PER_MARK_RADIUS = 1.50
WORDMARK_TRACKING_EM = 0.38
LOCKUP_CENTRE_Y = 225.0

# The subline is the first sentence of what the project is, in the same words
# as the site's own front page and social card.
TAGLINE = "Predictive scheduling and reliability for satellite ground stations."
TAGLINE_EM = 21.0
TAGLINE_TRACKING_EM = 0.02
TAGLINE_BASELINE_Y = 352.0

Box = tuple[float, float, float, float]  # left, top, right, bottom


@dataclass(frozen=True)
class Lockup:
    """Where the mark, the wordmark and the subline go, once measured.

    Attributes:
        mark_centre_x: Centre of the globe mark, banner units.
        wordmark: The typeset letters, in font units.
        wordmark_x: Left edge of the letters, banner units.
        wordmark_scale: Font units to banner units.
        tagline: The typeset subline, in font units.
        tagline_x: Left edge of the subline, banner units.
        tagline_scale: Font units to banner units.
    """

    mark_centre_x: float
    wordmark: Wordmark
    wordmark_x: float
    wordmark_scale: float
    tagline: Wordmark
    tagline_x: float
    tagline_scale: float

    def covered_boxes(self, margin: float) -> tuple[Box, Box]:
        """The two rectangles this lockup occupies, grown by `margin`.

        The caller draws the background and needs to know what will be painted
        over it. Measured from the typeset text rather than declared as a
        constant: a fixed box wide enough to be safe is wide enough to be seen
        as a hole in the sky behind it.

        Args:
            margin: Clear space to add on every side, banner units.

        Returns:
            The mark-and-wordmark line, then the subline.
        """
        wordmark_width = self.wordmark.advance_width_font_units * self.wordmark_scale
        tagline_width = self.tagline.advance_width_font_units * self.tagline_scale
        return (
            (
                self.mark_centre_x - MARK_RADIUS - margin,
                LOCKUP_CENTRE_Y - MARK_RADIUS - margin,
                self.wordmark_x + wordmark_width + margin,
                LOCKUP_CENTRE_Y + MARK_RADIUS + margin,
            ),
            (
                self.tagline_x - margin,
                TAGLINE_BASELINE_Y - TAGLINE_EM - margin,
                self.tagline_x + tagline_width + margin,
                TAGLINE_BASELINE_Y + margin,
            ),
        )


def measure_lockup(frame_width: float) -> Lockup:
    """Typeset both lines and centre the result in a frame of `frame_width`."""
    wordmark = typeset(
        FONTS / "IBMPlexSans-Medium.woff2", "MERIDIAN", WORDMARK_TRACKING_EM
    )
    wordmark_scale = MARK_RADIUS * WORDMARK_EM_PER_MARK_RADIUS / wordmark.units_per_em
    wordmark_width = wordmark.advance_width_font_units * wordmark_scale

    gap = MARK_RADIUS * LOCKUP_GAP_PER_MARK_RADIUS
    left = (frame_width - (2 * MARK_RADIUS + gap + wordmark_width)) / 2

    tagline = typeset(FONTS / "IBMPlexSans-Regular.woff2", TAGLINE, TAGLINE_TRACKING_EM)
    tagline_scale = TAGLINE_EM / tagline.units_per_em
    tagline_width = tagline.advance_width_font_units * tagline_scale

    return Lockup(
        mark_centre_x=left + MARK_RADIUS,
        wordmark=wordmark,
        wordmark_x=left + 2 * MARK_RADIUS + gap,
        wordmark_scale=wordmark_scale,
        tagline=tagline,
        tagline_x=(frame_width - tagline_width) / 2,
        tagline_scale=tagline_scale,
    )


def mark_markup(centre_x: float, centre_y: float, radius: float, colour: str) -> str:
    """The limb and one meridian — the same two strokes as favicon.svg.

    Stroke weights are the proportions of the 64-unit viewBox the source vector
    uses, so this and every raster export stay one drawing. The arc is the left
    half of an ellipse about the same centre, which is what a meridian looks
    like under orthographic projection.

    Args:
        centre_x: Centre of the mark, banner units.
        centre_y: Centre of the mark, banner units.
        radius: Outer radius of the limb, banner units.
        colour: Stroke colour for both strokes.

    Returns:
        Two SVG elements, as one string.
    """
    limb_width = radius * 4 / 22
    meridian_width = radius * 3.5 / 22
    meridian_rx = radius * 10.5 / 22
    return (
        f'<circle cx="{centre_x:.1f}" cy="{centre_y:.1f}" r="{radius:.1f}" '
        f'fill="none" stroke="{colour}" stroke-width="{limb_width:.2f}"/>'
        f'<path d="M{centre_x:.1f} {centre_y - radius:.1f}'
        f"A{meridian_rx:.1f} {radius:.1f} 0 0 0 "
        f'{centre_x:.1f} {centre_y + radius:.1f}" fill="none" '
        f'stroke="{colour}" stroke-width="{meridian_width:.2f}" '
        'stroke-linecap="round"/>'
    )


def lockup_markup(lockup: Lockup, ink: str, muted: str) -> str:
    """The mark, the wordmark and the subline, placed.

    Both text runs are outlines in font units with y up, so each is flipped
    back by a negative y scale about its own baseline.

    Args:
        lockup: The measured lockup.
        ink: Colour of the mark and the wordmark.
        muted: Colour of the subline.

    Returns:
        The whole identity block, as one string of SVG elements.
    """
    baseline_y = LOCKUP_CENTRE_Y + (
        lockup.wordmark.cap_height_font_units * lockup.wordmark_scale / 2
    )
    return (
        mark_markup(lockup.mark_centre_x, LOCKUP_CENTRE_Y, MARK_RADIUS, ink)
        + f'<path fill="{ink}" transform="translate({lockup.wordmark_x:.1f} '
        f"{baseline_y:.1f}) scale({lockup.wordmark_scale:.5f} "
        f'{-lockup.wordmark_scale:.5f})" d="{lockup.wordmark.path_data}"/>'
        + f'<path fill="{muted}" transform="translate({lockup.tagline_x:.1f} '
        f"{TAGLINE_BASELINE_Y:.1f}) scale({lockup.tagline_scale:.5f} "
        f'{-lockup.tagline_scale:.5f})" d="{lockup.tagline.path_data}"/>'
    )
