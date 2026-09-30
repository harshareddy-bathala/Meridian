"""An interval chart: one row per estimate, a point on its 95% interval.

The scheduling section compares things that share one unit — frames per
station-hour, or a gain relative to B — so they are drawn on one horizontal
axis, one row each, with zero drawn solid and a target, where there is one,
dashed and labelled. Labels sit in their own column to the left, in ink; the
points and whiskers carry the one series colour. Same style and the same
determinism as :mod:`meridian.reports.svg` (D-235).

Reference: docs/DECISIONS.md D-235, D-238.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from xml.sax.saxutils import escape

from meridian.reports.svg import (
    AXIS,
    GRID,
    INK,
    INK_SECONDARY,
    MUTED,
    SERIES,
    SURFACE,
    Ink,
    Stroke,
    badge,
    document,
    line,
    text,
)

__all__ = ["Estimate", "interval_chart"]

_WIDTH = 560
_LABELS = 170
_RIGHT = 24
_TOP = 84
_ROW = 34
_BOTTOM = 56
_TICKS = 5


@dataclass(frozen=True, slots=True)
class Estimate:
    """One row: what it is, its value, and its interval where it has one."""

    label: str
    value: float
    low: float | None = None
    high: float | None = None


@dataclass(frozen=True, slots=True)
class _Axis:
    low: float
    high: float
    ticks: tuple[float, ...]

    def x(self, value: float) -> float:
        span = self.high - self.low or 1.0
        return _LABELS + (value - self.low) / span * (_WIDTH - _LABELS - _RIGHT)


def interval_chart(
    heading: tuple[str, str],
    rows: Sequence[Estimate],
    *,
    unit: str,
    target: tuple[float, str] | None = None,
    simulated: bool = False,
) -> bytes:
    """Every estimate on one axis, with zero and an optional target marked.

    Args:
        heading: The title, and a subtitle carrying what a reader needs beside
            it: the sample and the resamples.
        rows: One estimate per row, top to bottom.
        unit: What the axis measures.
        target: A value to mark, and its label, e.g. ``(0.2, "SC-1 target")``.
        simulated: Whether the passes behind it were simulated.

    Returns:
        The SVG document, UTF-8, ending with a newline.
    """
    title, subtitle = heading
    height = _TOP + _ROW * max(len(rows), 1) + _BOTTOM
    axis = _axis(rows, target)
    bottom = _TOP + _ROW * len(rows)
    parts = [
        *document(
            (_WIDTH, height),
            title,
            f"{subtitle}. Each row is a point estimate on its 95% interval; the"
            " solid vertical line is zero.",
        ),
        text((16, 26), title, Ink(INK, 14, "start", bold=True)),
        text((16, 44), subtitle, Ink(INK_SECONDARY, anchor="start")),
        *_grid(axis, bottom, unit),
        *_target(axis, bottom, target),
        *(_row(axis, index, one) for index, one in enumerate(rows)),
        "</svg>",
    ]
    if simulated:
        parts[-1:-1] = badge(_WIDTH - 12)
    return ("\n".join(parts) + "\n").encode("utf-8")


def _axis(rows: Sequence[Estimate], target: tuple[float, str] | None) -> _Axis:
    """A range holding every value, zero and the target, on round ticks."""
    values = [0.0, *(target[:1] if target else ())]
    for one in rows:
        values.extend(v for v in (one.value, one.low, one.high) if v is not None)
    low, high = min(values), max(values)
    step = _step((high - low) / (_TICKS - 1) if high > low else 1.0)
    first = math.floor(low / step) * step
    last = math.ceil(high / step) * step
    count = round((last - first) / step)
    return _Axis(first, last, tuple(first + step * n for n in range(count + 1)))


def _step(raw: float) -> float:
    """The round step at or above ``raw``: 1, 2, 2.5 or 5 times a power of ten."""
    power = 10.0 ** math.floor(math.log10(raw))
    return next(one * power for one in (1, 2, 2.5, 5, 10) if one * power >= raw)


def _grid(axis: _Axis, bottom: float, unit: str) -> list[str]:
    parts = []
    for tick in axis.ticks:
        x = axis.x(tick)
        parts.append(line((x, _TOP - 8), (x, bottom), Stroke(GRID)))
        parts.append(text((x, bottom + 16), f"{tick:.4g}", Ink(MUTED, 10)))
    zero = axis.x(0.0)
    parts.append(line((zero, _TOP - 8), (zero, bottom), Stroke(AXIS, 1.5)))
    parts.append(line((_LABELS, bottom), (_WIDTH - _RIGHT, bottom), Stroke(AXIS)))
    centre = (_LABELS + _WIDTH - _RIGHT) / 2
    parts.append(text((centre, bottom + 36), unit, Ink(MUTED)))
    return parts


def _target(axis: _Axis, bottom: float, target: tuple[float, str] | None) -> list[str]:
    if target is None:
        return []
    value, label = target
    x = axis.x(value)
    right_half = x > (_LABELS + _WIDTH - _RIGHT) / 2
    anchor, nudge = ("end", 4.0) if right_half else ("start", -4.0)
    return [
        line((x, _TOP - 8), (x, bottom), Stroke(MUTED, dashed=True)),
        text((x + nudge, _TOP - 14), f"{label} {value:g}", Ink(MUTED, 10, anchor)),
    ]


def _row(axis: _Axis, index: int, one: Estimate) -> str:
    y = _TOP + _ROW * index + _ROW / 2
    parts = [text((_LABELS - 12, y + 4), one.label, Ink(INK, anchor="end"))]
    if one.low is not None and one.high is not None:
        parts.append(
            line((axis.x(one.low), y), (axis.x(one.high), y), Stroke(SERIES, 1.5))
        )
    interval = "" if one.low is None else f" [{one.low:.3f}, {one.high:.3f}]"
    parts.append(
        f'<circle cx="{axis.x(one.value):.1f}" cy="{y:.1f}" r="4" fill="{SERIES}"'
        f' stroke="{SURFACE}" stroke-width="2"><title>{escape(one.label)}:'
        f" {one.value:.3f}{interval}</title></circle>"
    )
    return "\n".join(parts)
