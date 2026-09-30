"""Timing error against element-set age: every detection, each regime's line, the prior.

One point per detection §6.1 keeps, at its element set's age and its absolute
timing error, coloured by orbital regime. A detection whose error is inside its
clock's uncertainty — the one §6.1 discards — is drawn hollow and grey, so the
rule's effect is visible rather than hidden. Each regime's least-squares line
is drawn in its colour over the ages it has, and the published 1σ prior is the
dashed curve: points under it are inside the uncertainty the scheduler assumed.

Colours are the reference palette's first three categorical slots, the ones
that stay distinguishable for colour-blind readers when every pair is on screen
at once; a fourth regime folds into the third's colour with its name in the
legend, which is always drawn when there are two or more. Same determinism and
style as :mod:`meridian.reports.svg` (D-235).

Reference: docs/DECISIONS.md D-235, D-239.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from xml.sax.saxutils import escape

from meridian.reports.svg import (
    AXIS,
    GRID,
    INK,
    INK_SECONDARY,
    MUTED,
    SURFACE,
    Ink,
    Stroke,
    badge,
    document,
    line,
    text,
)
from meridian.reports.svg_intervals import nice_step

__all__ = ["REGIME_COLOURS", "timing_scatter"]

REGIME_COLOURS = ("#2a78d6", "#eb6834", "#1baf7a")
"""Categorical slots 1 to 3, validated all-pairs for colour-vision deficiency."""

_WIDTH, _HEIGHT = 560, 440
_LEFT, _TOP, _RIGHT, _BOTTOM = 72, 84, 24, 64
_TICKS = 5

Row = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _Scale:
    x_top: float
    y_top: float

    def at(self, age: float, error: float) -> tuple[float, float]:
        width = _WIDTH - _LEFT - _RIGHT
        height = _HEIGHT - _TOP - _BOTTOM
        return (
            _LEFT + age / self.x_top * width,
            _HEIGHT - _BOTTOM - error / self.y_top * height,
        )


def timing_scatter(
    heading: tuple[str, str],
    points: Sequence[Row],
    lines: Sequence[Row],
    prior: Sequence[Row],
    *,
    simulated: bool,
) -> bytes:
    """The figure, from the section's ``detection``, ``regime`` and ``prior`` rows.

    Args:
        heading: The title, and a subtitle with the counts behind it.
        points: ``detection`` rows with an ``error_s``.
        lines: ``regime`` rows with a slope and intercept.
        prior: ``prior`` rows: the 1σ curve's ``age_days`` and ``sigma_s``.
        simulated: Whether the passes were simulated.
    """
    regimes = sorted({str(one["regime"]) for one in points})
    colour = {
        name: REGIME_COLOURS[min(index, len(REGIME_COLOURS) - 1)]
        for index, name in enumerate(regimes)
    }
    scale = _scale(points, prior)
    title, subtitle = heading
    parts = [
        *document(
            (_WIDTH, _HEIGHT),
            title,
            f"{subtitle}. Absolute timing error against element-set age, one point"
            " per detection; lines are each regime's least-squares fit; the dashed"
            " curve is the published 1σ prior.",
        ),
        text((16, 26), title, Ink(INK, 14, "start", bold=True)),
        text((16, 44), subtitle, Ink(INK_SECONDARY, anchor="start")),
        *_axes(scale),
        _prior(scale, prior),
        *(_fit(scale, one, colour) for one in lines if one["regime"] in colour),
        *(_point(scale, one, colour) for one in points),
        *_legend(colour),
    ]
    if simulated:
        parts.extend(badge(_WIDTH - 12))
    parts.append("</svg>")
    return ("\n".join(parts) + "\n").encode("utf-8")


def _scale(points: Sequence[Row], prior: Sequence[Row]) -> _Scale:
    ages = [_real(one["age_days"]) for one in (*points, *prior)] or [1.0]
    errors = [abs(_real(one["error_s"])) for one in points]
    errors += [_real(one["sigma_s"]) for one in prior]
    return _Scale(_top(max(ages)), _top(max(errors, default=1.0)))


def _top(value: float) -> float:
    step = nice_step(max(value, 1e-6) / (_TICKS - 1))
    return math.ceil(max(value, 1e-6) / step) * step


def _axes(scale: _Scale) -> list[str]:
    parts = []
    bottom, right = _HEIGHT - _BOTTOM, _WIDTH - _RIGHT
    for index in range(_TICKS):
        age = scale.x_top * index / (_TICKS - 1)
        error = scale.y_top * index / (_TICKS - 1)
        x, _ = scale.at(age, 0.0)
        _, y = scale.at(0.0, error)
        parts.append(line((x, _TOP), (x, bottom), Stroke(GRID)))
        parts.append(line((_LEFT, y), (right, y), Stroke(GRID)))
        parts.append(text((x, bottom + 16), f"{age:.3g}", Ink(MUTED, 10)))
        parts.append(text((_LEFT - 8, y + 3.5), f"{error:.3g}", Ink(MUTED, 10, "end")))
    parts.append(line((_LEFT, bottom), (right, bottom), Stroke(AXIS)))
    parts.append(line((_LEFT, _TOP), (_LEFT, bottom), Stroke(AXIS)))
    centre = (_LEFT + right) / 2
    parts.append(text((centre, bottom + 38), "element-set age, days", Ink(MUTED)))
    middle = (_TOP + bottom) / 2
    parts.append(
        f'<text x="20" y="{middle:.1f}" fill="{MUTED}" font-size="11"'
        f' text-anchor="middle" transform="rotate(-90 20 {middle:.1f})">'
        "|timing error|, s</text>"
    )
    return parts


def _prior(scale: _Scale, prior: Sequence[Row]) -> str:
    if not prior:
        return ""
    drawn = " ".join(
        "{:.1f},{:.1f}".format(*scale.at(_real(one["age_days"]), _real(one["sigma_s"])))
        for one in prior
    )
    return (
        f'<polyline points="{drawn}" fill="none" stroke="{MUTED}"'
        ' stroke-width="1" stroke-dasharray="4 4"/>'
    )


def _fit(scale: _Scale, one: Row, colour: Mapping[str, str]) -> str:
    slope, intercept = one.get("slope_s_per_day"), one.get("intercept_s")
    if slope is None or intercept is None:
        return ""
    low, high = _real(one["age_min_days"]), _real(one["age_max_days"])
    start = scale.at(low, max(_real(intercept) + _real(slope) * low, 0.0))
    end = scale.at(high, max(_real(intercept) + _real(slope) * high, 0.0))
    return line(start, end, Stroke(colour[str(one["regime"])], 2.0))


def _point(scale: _Scale, one: Row, colour: Mapping[str, str]) -> str:
    x, y = scale.at(_real(one["age_days"]), abs(_real(one["error_s"])))
    kept = one["excluded"] is None
    fill = colour[str(one["regime"])] if kept else SURFACE
    ring = SURFACE if kept else MUTED
    tip = (
        f"{one['assignment_id']}: {_real(one['error_s']):+.3f} s at"
        f" {_real(one['age_days']):.2f} d, 1σ {_real(one['sigma_s']):.3f} s"
        + ("" if kept else f", {one['excluded']}")
    )
    return (
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{fill}" stroke="{ring}"'
        f' stroke-width="{2 if kept else 1.5}"><title>{escape(tip)}</title></circle>'
    )


def _legend(colour: Mapping[str, str]) -> list[str]:
    y = _TOP - 18
    parts = [
        f'<circle cx="{_LEFT + 4}" cy="{y - 3.5:.1f}" r="4" fill="{SURFACE}"'
        f' stroke="{MUTED}" stroke-width="1.5"/>',
        text((_LEFT + 14, y), "inside clock uncertainty (§6.1 discards)", _SMALL),
        line(
            (_LEFT + 232, y - 3.5), (_LEFT + 256, y - 3.5), Stroke(MUTED, dashed=True)
        ),
        text((_LEFT + 262, y), "1σ prior", _SMALL),
    ]
    if len(colour) < 2:  # noqa: PLR2004 — one series needs no legend box
        return parts
    x = float(_LEFT + 330)
    for name, hue in colour.items():
        parts.append(f'<circle cx="{x:.1f}" cy="{y - 3.5:.1f}" r="4" fill="{hue}"/>')
        parts.append(text((x + 10, y), name, _SMALL))
        x += 18 + 7 * len(name)
    return parts


def _real(value: object) -> float:
    return float(str(value))


_SMALL = Ink(MUTED, 10, "start")
