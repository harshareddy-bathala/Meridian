"""The 72-hour run in ``report.md``: whether it counts, why not, and what it ran on.

Split from :mod:`meridian.reports.render_reliability`. Drawn from the
``long_run`` row alone, which carries the parts of the run's own record a
reader needs to believe it (D-257).
"""

from __future__ import annotations

from collections.abc import Mapping

from meridian.reports.markdown import cell

__all__ = ["long_run_lines"]

Row = Mapping[str, object]


def _table(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def long_run_lines(one: Row) -> list[str]:
    """The 72-hour run's paragraph: its status, why, and what it ran on."""
    title = f"**The {one['hours_required']}-hour run**"
    if one["status"] == "not run":
        return [f"{title}: not run — {one['reason']}.", ""]
    record = _table(one["record"])
    ran_on = _table(record.get("environment"))
    lines = [
        f"{title}: {one['status']}, `{str(one['sha256'])[:12]}`,"
        f" {cell(one['hours'])} hours from seed {one['seed']},"
        f" {record.get('stations', '—')} stations, on"
        f" {ran_on.get('arch') or 'an unrecorded machine'} running"
        f" {_image(ran_on)}.",
    ]
    reasons = one.get("reasons")
    if isinstance(reasons, list) and reasons:
        lines.append(
            "Why it is not the acceptance run: " + "; ".join(map(str, reasons)) + "."
        )
    interruptions = record.get("interruptions")
    if isinstance(interruptions, list) and interruptions:
        lines.append(
            f"The tool was stopped and resumed {len(interruptions)} time(s), for"
            f" {cell(one.get('interrupted_hours'))} hours in all, which the run's"
            " length leaves out: the platform ran on, but nothing injected a"
            " fault or looked."
        )
    lines.extend(_resources(_table(_table(record.get("resources")).get("figures"))))
    return [*lines, ""]


def _image(ran_on: Mapping[str, object]) -> str:
    digests = ran_on.get("image_digests")
    if isinstance(digests, list) and digests:
        return f"`{str(digests[0]).rsplit('@', 1)[-1][:19]}`"
    return f"`{ran_on.get('image') or 'an unrecorded image'}`"


def _resources(figures: Mapping[str, object]) -> list[str]:
    """Peak memory, its growth over the run's second half, and the database's."""
    if not figures:
        return []
    peaks = _table(figures.get("peak_memory_mib"))
    slopes = _table(figures.get("memory_slope_mib_per_hour"))
    database = _table(figures.get("database_mib"))
    growth = ", ".join(
        f"{name} {cell(slopes[name])} MiB/h"
        for name in sorted(slopes)
        if slopes[name] is not None
    )
    lines = [
        "Peak memory: "
        + (
            ", ".join(
                f"{name} {cell(peaks[name])} MiB"
                for name in sorted(peaks)
                # A one-shot service that had finished before any sample used none.
                if peaks[name]
            )
            or "—"
        )
        + ". Growth over the second half: "
        + (growth or "too few samples to fit")
        + "."
    ]
    if database:
        lines.append(
            f"Database {cell(database['first'])} MiB at the start,"
            f" {cell(database['last'])} MiB at the end."
        )
    return lines
