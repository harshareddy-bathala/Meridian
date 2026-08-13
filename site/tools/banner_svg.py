"""The README banner: the lockup over a star field, with Earth in the corner.

One drawing, generated in two themes and written to site/brand/. README.md
selects between them with a <picture> element, so the banner follows GitHub's
light and dark settings the way the site follows the browser's.

It is a single self-contained SVG per theme: no external font, no external
stylesheet, no script. That is not a preference — a README image is rendered
in a context that blocks all three, so anything the file does not carry, it
does not get. The text is therefore outlines rather than a font-family, and
the motion is CSS inside the document rather than anything a page supplies.

The globe is a real orthographic projection of a 15-degree graticule, from
orthographic_projection.py — the same construction as the social card and the
still globe on the home page, seen from the same camera. The star field is
seeded and reproducible, and the identity block in the middle is measured and
drawn by banner_lockup.py.

Frames: everything here is in the banner's own viewBox units, 1500 by 500,
y down.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from banner_lockup import Lockup, lockup_markup, measure_lockup
from orthographic_projection import (
    DEG,
    Camera,
    graticule,
    svg_path_data,
    visible_runs,
)
from starfield import Star, star_field

BRAND = Path(__file__).resolve().parent.parent / "brand"

# 3:1. The same ratio a header image is cropped to on most social platforms,
# so one asset serves the README, the repository's social preview and a
# profile header rather than three drifting variants of the same picture.
WIDTH, HEIGHT = 1500.0, 500.0

# Earth sits in the top-right corner with about a quarter of the disc in
# frame: centre beyond both edges, radius large enough that the visible arc
# reads as a planet's limb rather than as a circle that happens to be cut off.
GLOBE_CX, GLOBE_CY, GLOBE_R = 1470.0, 30.0, 330.0
GLOBE_CAMERA = Camera(18 * DEG, 102.6 * DEG)  # matches the home page's camera

# How close a star may come to a letterform or to the globe's limb. Enough to
# keep the type clean, and no more: a generous margin carves a visible empty
# rectangle out of the sky, which is more distracting than the stars were.
KEEP_OUT_MARGIN = 18.0


@dataclass(frozen=True)
class Theme:
    """One theme's palette. Every colour the banner uses is on here.

    Attributes:
        name: Goes in the filename, and nowhere else.
        ground: The background. Never pure black or pure white — both of the
            project's grounds are warm.
        ink: The mark and the wordmark.
        muted: The subline.
        wire_near: Graticule on the hemisphere facing us.
        wire_far: Graticule on the far side, showing through.
        limb: The globe's outline.
        has_deep_field: Whether to draw the nebula and stars. The light theme
            does not: a star field on warm paper reads as dust on the page,
            and the wireframe globe carries the drawing on its own.
    """

    name: str
    ground: str
    ink: str
    muted: str
    wire_near: str
    wire_far: str
    limb: str
    has_deep_field: bool


DARK = Theme(
    name="dark",
    ground="#08090D",
    ink="#E8E6E1",
    muted="#787E8A",
    wire_near="#2F3746",
    wire_far="#191E27",
    limb="#59637A",
    has_deep_field=True,
)

LIGHT = Theme(
    name="light",
    ground="#FAF9F7",
    ink="#14161B",
    muted="#5A6069",
    wire_near="#C9C2B6",
    wire_far="#E4DFD6",
    limb="#A79E8F",
    has_deep_field=False,
)

# ------------------------------------------------------------------ globe --


def globe_to_frame(right: float, up: float) -> tuple[float, float]:
    """Projected (right, up) to banner units. up is up, the frame's y is down."""
    return GLOBE_CX + right * GLOBE_R, GLOBE_CY - up * GLOBE_R


def globe_markup(theme: Theme) -> str:
    """Earth: far graticule, limb, near graticule, in that order.

    Sampled every 5 degrees along each line rather than the 15 the home page's
    inline globe uses. That globe is 86 units across and this one is 330, and
    at 15 degrees the polygon corners on an arc this large are visible: the
    departure from the true curve is R(1 - cos(step / 2)), which is 2.8 units
    here against 0.7 there.
    """
    lines = graticule(step_deg=5)
    far = svg_path_data(visible_runs(lines, GLOBE_CAMERA, globe_to_frame, near=False))
    near = svg_path_data(visible_runs(lines, GLOBE_CAMERA, globe_to_frame, near=True))
    limb = (
        f'<circle cx="{GLOBE_CX}" cy="{GLOBE_CY}" r="{GLOBE_R}" fill="none" '
        f'stroke="{theme.limb}" stroke-width="1.6"/>'
    )
    return (
        f'<g fill="none" stroke-width="1">'
        f'<path stroke="{theme.wire_far}" d="{far}"/>'
        f"{_limb_glow(theme)}{limb}"
        f'<path stroke="{theme.wire_near}" d="{near}"/>'
        f"</g>"
    )


def _limb_glow(theme: Theme) -> str:
    """Two wide, faint circles outside the limb — atmosphere, without a blur.

    A Gaussian filter would be the obvious way to do this and is the expensive
    one: it forces the whole globe onto a filter surface in every viewer that
    renders the file. Two strokes cost nothing and are close enough at this
    size.
    """
    if not theme.has_deep_field:
        return ""
    return (
        f'<circle class="limb-glow" cx="{GLOBE_CX}" cy="{GLOBE_CY}" '
        f'r="{GLOBE_R + 7:.0f}" fill="none" stroke="{theme.limb}" '
        'stroke-width="14" opacity="0.10"/>'
        f'<circle cx="{GLOBE_CX}" cy="{GLOBE_CY}" r="{GLOBE_R + 2:.0f}" '
        f'fill="none" stroke="{theme.limb}" stroke-width="4" opacity="0.22"/>'
    )


# -------------------------------------------------------------- deep field --


def keep_out_test(lockup: Lockup) -> Callable[[float, float], bool]:
    """Build the predicate saying where the sky is already occupied.

    Args:
        lockup: The measured lockup, which knows its own extent.

    Returns:
        A predicate on a position in banner units, true where no star goes:
        inside either of the lockup's boxes, or on the globe.
    """
    boxes = lockup.covered_boxes(KEEP_OUT_MARGIN)

    def is_covered(x: float, y: float) -> bool:
        for left, top, right, bottom in boxes:
            if left <= x <= right and top <= y <= bottom:
                return True
        return math.hypot(x - GLOBE_CX, y - GLOBE_CY) < GLOBE_R + 8

    return is_covered


def starfield_markup(theme: Theme, lockup: Lockup) -> str:
    """Every star as one circle, the brightest layer carrying a twinkle."""
    if not theme.has_deep_field:
        return ""
    stars = star_field(WIDTH, HEIGHT, keep_out_test(lockup))
    return f'<g fill="{theme.ink}">{"".join(_star_markup(s) for s in stars)}</g>'


def _star_markup(star: Star) -> str:
    """One star, with its twinkle delay if it has one.

    Resting brightness is fill-opacity; the twinkle animates the element's own
    opacity on top of it, so one keyframe serves every star.
    """
    twinkle = (
        f' class="twinkle" style="animation-delay:{-star.twinkle_delay_s:.2f}s"'
        if star.twinkle_delay_s is not None
        else ""
    )
    return (
        f'<circle cx="{star.x:.1f}" cy="{star.y:.1f}" r="{star.radius:.2f}" '
        f'fill-opacity="{star.opacity:.2f}"{twinkle}/>'
    )


def nebula_markup(theme: Theme) -> str:
    """Three overlapping washes, drifting as one.

    Deliberately almost colourless. The brand is two inks and three semantic
    signal colours that mean *above horizon*, *below horizon* and *predicted*,
    and none of those may appear in a logo treatment — so the nebula is built
    from near-black indigo and violet that read as depth rather than as a
    fourth brand colour.
    """
    if not theme.has_deep_field:
        return ""
    # Kept away from the middle of the frame: a wash directly behind the
    # wordmark reads as a stain on the type rather than as distance behind it.
    washes = (
        ("nebula-a", "#16203A", 210.0, 430.0, 620.0, 280.0),
        ("nebula-b", "#1D1B33", 1180.0, 90.0, 520.0, 300.0),
        ("nebula-c", "#20242E", 700.0, 470.0, 420.0, 150.0),
    )
    gradients = "".join(
        f'<radialGradient id="{name}">'
        f'<stop offset="0" stop-color="{colour}" stop-opacity="0.62"/>'
        f'<stop offset="1" stop-color="{colour}" stop-opacity="0"/>'
        "</radialGradient>"
        for name, colour, *_ in washes
    )
    ellipses = "".join(
        f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="url(#{name})"/>'
        for name, _, cx, cy, rx, ry in washes
    )
    return f'<defs>{gradients}</defs><g class="drift">{ellipses}</g>'


def transit_markup(theme: Theme) -> str:
    """One satellite crossing the frame, with a short trail behind it.

    Its static transform is where it rests when a viewer has asked for reduced
    motion: mid-sky and above the wordmark, rather than at the origin, which
    is the corner of the frame.
    """
    return (
        f'<g class="transit" transform="translate(760 60)" stroke="{theme.ink}" '
        f'fill="{theme.ink}" opacity="0.5">'
        '<line x1="-34" y1="1.3" x2="0" y2="0" stroke-width="1.1" opacity="0.45"/>'
        '<circle cx="0" cy="0" r="2.2"/>'
        "</g>"
    )


# ------------------------------------------------------------------ motion --
#
# CSS animation rather than SMIL. Both survive being embedded as an image, and
# only CSS can be switched off by prefers-reduced-motion — an <img> renders no
# script, so there is nothing else to gate it with.
#
# Every duration is long. This sits at the top of a README that people scroll
# past to read something, and motion that draws the eye twice is motion that
# gets the image deleted.

MOTION_CSS = """
@keyframes twinkle { 0%, 100% { opacity: 1 } 50% { opacity: 0.25 } }
@keyframes drift {
  from { transform: translate(0, 0) scale(1) }
  to   { transform: translate(28px, -16px) scale(1.06) }
}
@keyframes glow { from { opacity: 0.06 } to { opacity: 0.18 } }
@keyframes transit {
  0%   { transform: translate(-160px, 96px); opacity: 0 }
  7%   { opacity: 0.5 }
  93%  { opacity: 0.5 }
  100% { transform: translate(1680px, 26px); opacity: 0 }
}
.twinkle { animation: twinkle 7s ease-in-out infinite }
.drift { animation: drift 64s ease-in-out infinite alternate;
         transform-origin: 750px 250px }
.limb-glow { animation: glow 11s ease-in-out infinite alternate }
.transit { animation: transit 24s linear infinite }
@media (prefers-reduced-motion: reduce) {
  .twinkle, .drift, .limb-glow, .transit { animation: none }
}
"""


# ---------------------------------------------------------------- document --


def banner_svg(theme: Theme, lockup: Lockup) -> str:
    """The whole banner as one SVG document. Pure — no files are touched.

    Args:
        theme: The palette to draw in.
        lockup: The measured lockup, passed in rather than measured here so
            that generating both themes typesets the text once.

    Returns:
        A complete standalone SVG, ready to be written to disk.
    """
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH:.0f} '
        f'{HEIGHT:.0f}" width="{WIDTH:.0f}" height="{HEIGHT:.0f}" role="img" '
        'aria-label="Meridian — predictive scheduling and reliability for '
        'satellite ground stations">'
        "<title>Meridian</title>"
        # A double hyphen cannot appear inside an XML comment, which rules out
        # writing the flag itself here.
        "<!-- Generated by site/tools/make-images.py, the banner output. "
        "Do not edit by hand. -->"
        f"<style>{MOTION_CSS}</style>"
        f'<rect width="{WIDTH:.0f}" height="{HEIGHT:.0f}" fill="{theme.ground}"/>'
        f"{nebula_markup(theme)}"
        f"{starfield_markup(theme, lockup)}"
        f"{globe_markup(theme)}"
        f"{transit_markup(theme)}"
        f"{lockup_markup(lockup, theme.ink, theme.muted)}"
        "</svg>"
    )


def write_banners() -> list[Path]:
    """Write both themes to site/brand/. Idempotent: same bytes every run."""
    BRAND.mkdir(exist_ok=True)
    lockup = measure_lockup(WIDTH)
    written = []
    for theme in (DARK, LIGHT):
        path = BRAND / f"meridian-banner-{theme.name}.svg"
        markup = banner_svg(theme, lockup) + "\n"
        path.write_text(markup, encoding="utf-8", newline="\n")
        written.append(path)
    return written
