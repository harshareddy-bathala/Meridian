"""Figures as SVG, written by hand so the same rows always give the same bytes.

A figure is part of a run and is hashed with it, so it must be a function of
its results rows and nothing else: no font metrics, no library version, no
date in its metadata. A plotting library would put all three in, and a figure
would change its hash when the library was upgraded. So the few forms a report
needs are drawn here from primitives, with every coordinate written to one
decimal place (D-235).

**One style for every figure,** from the reference data-visualisation palette:
a light surface, recessive hairline grid and axes, muted tick labels, text in
ink rather than in the series colour, one blue for a single series, markers of
8 px with a 2 px ring in the surface colour, and thin interval whiskers. A
figure drawn from simulated data carries a ``SIMULATED`` badge (rule 5). Each
figure has a ``<title>`` and a ``<desc>``, and the table in ``report.md``
beside it holds every number it plots.

Reference: docs/DECISIONS.md D-235, D-237.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from xml.sax.saxutils import escape

__all__ = ["reliability_diagram"]

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SERIES = "#2a78d6"
SERIES_LIGHT = "#86b6ef"
CRITICAL = "#d03b3b"
FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"

_WIDTH, _HEIGHT = 440, 500
_LEFT, _TOP, _SIZE = 64, 80, 320
_STRIP_TOP, _STRIP = _TOP + _SIZE + 56, 36
_TICKS = (0.0, 0.25, 0.5, 0.75, 1.0)

Row = Mapping[str, object]


def reliability_diagram(
    title: str, subtitle: str, bins: Sequence[Row], *, simulated: bool
) -> bytes:
    """A reliability diagram: observed frequency against predicted probability.

    Args:
        title: What the figure is of, e.g. ``Configuration D``.
        subtitle: The numbers a reader needs beside it: n and the Brier scores.
        bins: The model's ``bin`` rows: ``low``, ``high``, ``n``,
            ``mean_predicted`` and ``observed`` with its interval.
        simulated: Whether the passes behind it were simulated.

    Returns:
        The SVG document, UTF-8, ending with a newline.
    """
    parts = [
        *_frame(title, subtitle, simulated),
        *_grid(),
        _line((_x(0.0), _y(0.0)), (_x(1.0), _y(1.0)), Stroke(MUTED, dashed=True)),
        *_whiskers(bins),
        *_strip(bins),
        "</svg>",
    ]
    return ("\n".join(parts) + "\n").encode("utf-8")


def _frame(title: str, subtitle: str, simulated: bool) -> list[str]:
    description = (
        "Each point is one bin of predicted probability: its mean prediction"
        " across, its observed decode frequency up, with a Wilson 95% interval."
        " The dashed diagonal is perfect calibration. Bars below count the"
        " passes in each bin."
    )
    head = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_WIDTH}"'
        f' height="{_HEIGHT}" viewBox="0 0 {_WIDTH} {_HEIGHT}"'
        f' font-family="{FONT}" role="img">',
        f"<title>{escape(title)}: reliability diagram</title>",
        f"<desc>{escape(description)}</desc>",
        f'<rect width="{_WIDTH}" height="{_HEIGHT}" fill="{SURFACE}"/>',
        _text((_LEFT - 40, 24), title, Ink(INK, 14, "start", bold=True)),
        _text((_LEFT - 40, 42), subtitle, Ink(INK_SECONDARY, anchor="start")),
        _text((_x(0.5), _TOP + _SIZE + 32), "predicted probability", Ink(MUTED)),
        *_key(),
        f'<text x="18" y="{_y(0.5):.1f}" fill="{MUTED}" font-size="11"'
        f' text-anchor="middle" transform="rotate(-90 18 {_y(0.5):.1f})">'
        "observed decode frequency</text>",
    ]
    if simulated:
        head.extend(
            [
                f'<rect x="{_WIDTH - 96}" y="12" width="84" height="20" rx="4"'
                f' fill="{SURFACE}" stroke="{CRITICAL}" stroke-width="1.5"/>',
                _text((_WIDTH - 54, 26), "SIMULATED", Ink(CRITICAL, bold=True)),
            ]
        )
    return head


def _key() -> list[str]:
    """What the marks mean, above the plot rather than on top of the data."""
    y = _TOP - 16
    return [
        _line((_LEFT + 4, y - 10), (_LEFT + 4, y + 3), Stroke(SERIES, 1.5)),
        f'<circle cx="{_LEFT + 4:.1f}" cy="{y - 3.5:.1f}" r="4" fill="{SERIES}"'
        f' stroke="{SURFACE}" stroke-width="2"/>',
        _text((_LEFT + 14, y), "observed, with 95% interval", _SMALL_START),
        _line(
            (_LEFT + 180, y - 3.5), (_LEFT + 204, y - 3.5), Stroke(MUTED, dashed=True)
        ),
        _text((_LEFT + 210, y), "perfect calibration", _SMALL_START),
    ]


def _grid() -> list[str]:
    lines = []
    for tick in _TICKS:
        lines.append(_line((_x(tick), _y(0.0)), (_x(tick), _y(1.0)), Stroke(GRID)))
        lines.append(_line((_x(0.0), _y(tick)), (_x(1.0), _y(tick)), Stroke(GRID)))
        label = f"{tick:g}"
        lines.append(_text((_x(tick), _y(0.0) + 16), label, _SMALL))
        lines.append(_text((_x(0.0) - 8, _y(tick) + 3.5), label, _SMALL_END))
    lines.append(_line((_x(0.0), _y(0.0)), (_x(1.0), _y(0.0)), Stroke(AXIS)))
    lines.append(_line((_x(0.0), _y(0.0)), (_x(0.0), _y(1.0)), Stroke(AXIS)))
    return lines


def _whiskers(bins: Sequence[Row]) -> list[str]:
    marks = []
    for one in bins:
        observed = one.get("observed")
        predicted = one.get("mean_predicted")
        if not isinstance(observed, Mapping) or not isinstance(predicted, float):
            continue
        x = _x(predicted)
        marks.append(
            _line(
                (x, _y(observed["low"])),
                (x, _y(observed["high"])),
                Stroke(SERIES, 1.5),
            )
        )
        marks.append(
            f'<circle cx="{x:.1f}" cy="{_y(observed["estimate"]):.1f}" r="4"'
            f' fill="{SERIES}" stroke="{SURFACE}" stroke-width="2">'
            f"<title>{escape(_tooltip(one, observed))}</title></circle>"
        )
    return marks


def _strip(bins: Sequence[Row]) -> list[str]:
    counts = [one["n"] if isinstance(one["n"], int) else 0 for one in bins]
    largest = max(counts, default=0) or 1
    bars = [
        _text((_LEFT - 8, _STRIP_TOP + _STRIP - 2), "passes", _SMALL_END),
        _line(
            (_x(0.0), _STRIP_TOP + _STRIP),
            (_x(1.0), _STRIP_TOP + _STRIP),
            Stroke(AXIS),
        ),
    ]
    for one, count in zip(bins, counts, strict=True):
        if not count:
            continue
        low, high = float(str(one["low"])), float(str(one["high"]))
        height = max(_STRIP * count / largest, 1.0)
        bars.append(
            f'<rect x="{_x(low) + 1:.1f}" y="{_STRIP_TOP + _STRIP - height:.1f}"'
            f' width="{_x(high) - _x(low) - 2:.1f}" height="{height:.1f}" rx="2"'
            f' fill="{SERIES_LIGHT}"><title>{count} passes predicted'
            f" {low:g} to {high:g}</title></rect>"
        )
    bars.append(
        _text((_x(1.0), _STRIP_TOP - 4), f"most in one bin: {largest}", _SMALL_END)
    )
    return bars


def _tooltip(one: Row, observed: Mapping[str, object]) -> str:
    return (
        f"predicted {one['low']:g}–{one['high']:g}: mean {one['mean_predicted']:.3f},"
        f" observed {observed['estimate']:.3f}"
        f" [{observed['low']:.3f}, {observed['high']:.3f}], n {one['n']}"
    )


def _x(value: object) -> float:
    return _LEFT + float(str(value)) * _SIZE


def _y(value: object) -> float:
    return _TOP + (1.0 - float(str(value))) * _SIZE


@dataclass(frozen=True, slots=True)
class Stroke:
    """How a line is drawn."""

    colour: str
    width: float = 1.0
    dashed: bool = False


@dataclass(frozen=True, slots=True)
class Ink:
    """How text is set."""

    colour: str
    size: int = 11
    anchor: str = "middle"
    bold: bool = False


def _line(start: tuple[float, float], end: tuple[float, float], stroke: Stroke) -> str:
    dash = ' stroke-dasharray="4 4"' if stroke.dashed else ""
    return (
        f'<line x1="{start[0]:.1f}" y1="{start[1]:.1f}" x2="{end[0]:.1f}"'
        f' y2="{end[1]:.1f}" stroke="{stroke.colour}"'
        f' stroke-width="{stroke.width:g}"{dash}/>'
    )


def _text(at: tuple[float, float], content: str, ink: Ink) -> str:
    bold = ' font-weight="600"' if ink.bold else ""
    return (
        f'<text x="{at[0]:.1f}" y="{at[1]:.1f}" fill="{ink.colour}"'
        f' font-size="{ink.size}" text-anchor="{ink.anchor}"{bold}>'
        f"{escape(content)}</text>"
    )


_SMALL = Ink(MUTED, 10)
_SMALL_END = Ink(MUTED, 10, "end")
_SMALL_START = Ink(MUTED, 10, "start")
