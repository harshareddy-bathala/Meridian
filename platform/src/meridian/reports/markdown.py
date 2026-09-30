"""How a report writes a number, a rate and a table — once, for every section.

Every section's text is rendered from its results rows by these functions, so
the same value is written the same way wherever it appears, and a reader who
finds ``0.250 [0.046, 0.699], n 4`` in one table knows how to read it in the
next. The formats are fixed, not locale-dependent, which is what makes
``report.md`` byte-identical on regeneration.

Reference: docs/DECISIONS.md D-235.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

__all__ = ["cell", "rate", "table"]

_NOTHING = "—"


def cell(value: object) -> str:
    """One value as a table cell: counts whole, reals to three places."""
    if isinstance(value, list | tuple):
        return " ".join(cell(one) for one in value) or _NOTHING
    if value is None:
        text = _NOTHING
    elif isinstance(value, bool):
        text = "yes" if value else "no"
    elif isinstance(value, float):
        text = f"{value:.3f}"
    else:
        text = str(value).replace("|", "\\|")
    return text


def rate(value: object) -> str:
    """A rate row — estimate, interval and n — or a dash when there was none.

    Args:
        value: ``{"estimate", "low", "high", "n"}`` as a results row holds it,
            or None.
    """
    if not isinstance(value, Mapping):
        return f"{_NOTHING} (nothing to count)"
    n = value["n"]
    count = f"{n:.1f}" if isinstance(n, float) and not n.is_integer() else f"{n:.0f}"
    return (
        f"{value['estimate']:.3f} [{value['low']:.3f}, {value['high']:.3f}], n {count}"
    )


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """A GitHub-flavoured markdown table, one line per row."""
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines
