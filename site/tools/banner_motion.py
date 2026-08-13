"""The banner's stylesheet: everything that moves, and how to switch it off.

CSS animation rather than SMIL. Both survive being embedded as an image, and
only CSS can be switched off by prefers-reduced-motion — an <img> renders no
script, so there is nothing else to gate it with.

Every duration here is long. The banner sits at the top of a README that
people scroll past on their way to reading something, and motion that catches
the eye twice is motion that gets the image deleted.

Meteors need a keyframe each, because each one travels between its own pair of
points, so most of this module is generating those from the tracks that
meteor_tracks.py places. The trick they all use: the crossing itself takes
about a second, but the animation's period is twenty or thirty, so the streak
occupies the first few percent of its timeline and the rest is spent waiting
off-frame at zero opacity. That is what makes them arrive irregularly instead
of marching past in step.

It returns CSS as a string and touches nothing else.
"""

from __future__ import annotations

from meteor_tracks import Meteor

# Motion that is always present, whatever is drawn over it.
BASE_CSS = """
@keyframes twinkle { 0%, 100% { opacity: 1 } 50% { opacity: 0.25 } }
@keyframes drift {
  from { transform: translate(0, 0) scale(1) }
  to   { transform: translate(28px, -16px) scale(1.06) }
}
@keyframes glow { from { opacity: 0.06 } to { opacity: 0.18 } }
.twinkle { animation: twinkle 7s ease-in-out infinite }
.drift { animation: drift 64s ease-in-out infinite alternate;
         transform-origin: 750px 250px }
.limb-glow { animation: glow 11s ease-in-out infinite alternate }
"""

# A meteor holds at its end point, invisible, for the rest of its period. The
# two inner stops are where it reaches full brightness and where it begins to
# burn out, as fractions of the crossing rather than of the whole period.
BRIGHTEN_AT = 0.16
FADE_FROM = 0.62

REDUCED_MOTION_CSS = """
@media (prefers-reduced-motion: reduce) {
  .twinkle, .drift, .limb-glow, .meteor { animation: none }
  .meteor { opacity: 0 }
}
"""


def motion_css(meteors: list[Meteor]) -> str:
    """The whole stylesheet, with the reduced-motion override last so it wins.

    The fixed rules first, then one keyframe per meteor.

    Args:
        meteors: The placed shower.

    Returns:
        CSS, ready to go inside a <style> element.
    """
    per_meteor = "".join(
        _meteor_css(index, meteor) for index, meteor in enumerate(meteors)
    )
    return f"{BASE_CSS}{per_meteor}{REDUCED_MOTION_CSS}"


def _meteor_css(index: int, meteor: Meteor) -> str:
    """One meteor's keyframe and the rule that binds it."""
    crossing_fraction = meteor.crossing_s / meteor.period_s
    brighten_pct = 100 * crossing_fraction * BRIGHTEN_AT
    fade_pct = 100 * crossing_fraction * FADE_FROM
    gone_pct = 100 * crossing_fraction

    start = f"translate({meteor.start_x:.0f}px, {meteor.start_y:.0f}px)"
    end = f"translate({meteor.end_x:.0f}px, {meteor.end_y:.0f}px)"
    turn = f"rotate({meteor.heading_deg:.1f}deg)"

    return (
        f"@keyframes meteor-{index} {{"
        f"0% {{ transform: {start} {turn}; opacity: 0 }}"
        f"{brighten_pct:.2f}% {{ opacity: {meteor.peak_opacity:.2f} }}"
        f"{fade_pct:.2f}% {{ opacity: {meteor.peak_opacity:.2f} }}"
        f"{gone_pct:.2f}%, 100% {{ transform: {end} {turn}; opacity: 0 }}"
        f"}}"
        f".meteor-{index} {{ animation: meteor-{index} "
        f"{meteor.period_s:.1f}s linear infinite; "
        f"animation-delay: {-meteor.phase_s:.1f}s }}"
    )
