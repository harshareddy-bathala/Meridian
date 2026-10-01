"""What a long run watches besides faults and alerts, and how it judges it. Stdlib only.

Stage 24's acceptance asks whether the platform *survives* seventy-two hours
unattended (D-257). Surviving is more than every container still running at the
end. A run also fails when:

- **a container is unhealthy** at the end, by its own healthcheck, though running;
- **memory climbs** — a container whose memory, fitted by least squares over
  the run's second half, grows faster than a stated bound. The first half is
  left out because caches and pools fill there, and that is not a leak;
- **disk runs short** — free space on Docker's data root below a stated floor
  at any sample;
- **the host throttled** — a Raspberry Pi that capped its clock for heat or
  power ran a different experiment from the one claimed;
- **an alert stayed silent** for a platform fault that lasted longer than the
  alert waits before firing. That is the positive control for the alerts, as
  ``StationOffline`` is for the stations' faults: a run in which nothing fired
  must not read as a clean one.

It also records, without judging, what a reader needs to believe the run: the
database's size and Prometheus's at each sample, the host's free memory and
swap, and its temperature.

Every rule here is a pure function of what was sampled, so
``tests/unit/test_long_run_watch.py`` pins each one without Docker.
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

MEMORY_SLOPE_MIB_PER_HOUR = 4.0
"""The fastest a container's memory may grow over the second half of a run.

Four MiB an hour is under 150 MiB over the half of a seventy-two hour run that
is judged, on a Pi with eight GiB: a leak that slow would still take weeks to
matter, and one faster is one to find before a deployment runs for months."""

DISK_FREE_FLOOR_GIB = 5.0
"""The least free space on Docker's data root a run may reach."""

MIN_SLOPE_SAMPLES = 8
"""Fewer samples than this in the second half and no slope is judged: a line
through four points of a two-hour rehearsal says nothing about a leak."""

MIN_LINE_POINTS = 2
"""A line needs two points."""

ALERT_MARGIN = timedelta(minutes=2)
"""Past an alert's ``for:``, the evaluation and scrape it needs to fire."""

BASE_GRACE = timedelta(minutes=10)
"""The least time after a fault closes that an alert it caused may still fire."""

PLATFORM_ALERTS = ("ApiUnavailable", "DatabaseUnavailable", "SchedulerUnavailable")
"""The alerts for the platform's own services, each owed when its fault lasts."""

THROTTLED_NOW = 0xF
"""``vcgencmd get_throttled``'s low bits: under-voltage, frequency capped,
throttled and soft temperature limit, each as it stands at the moment read."""


@dataclass(frozen=True, slots=True)
class Machine:
    """The machine at one sample, from ``/proc`` and ``/sys``; None if unreadable."""

    memory_available_mib: float | None = None
    swap_used_mib: float | None = None
    temperature_c: float | None = None
    throttled: int | None = None
    disk_free_gib: float | None = None


@dataclass
class Sizes:
    """What the stack holds on disk at one sample, in MiB; None if unreadable."""

    database_mib: float | None = None
    prometheus_mib: float | None = None


class Span(Protocol):
    """One stretch of time, as the ledger or Prometheus gives it."""

    @property
    def name(self) -> str:
        """What was open: an alert's name, or a fault's ``kind on target``."""

    @property
    def start(self) -> datetime:
        """When it opened."""

    @property
    def end(self) -> datetime | None:
        """When it closed, or None while open."""


@dataclass
class Resources:
    """The resource half of a run's record, and what failed it."""

    figures: dict[str, object] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)


# --- reading -------------------------------------------------------------------


def parse_meminfo(text: str) -> tuple[float | None, float | None]:
    """``/proc/meminfo``: memory available, and swap in use, in MiB."""
    values: dict[str, float] = {}
    for line in text.splitlines():
        found = re.match(r"^(\w+):\s+(\d+)\s*kB", line)
        if found:
            values[found.group(1)] = int(found.group(2)) / 1024
    available = values.get("MemAvailable")
    swap = None
    if "SwapTotal" in values and "SwapFree" in values:
        swap = values["SwapTotal"] - values["SwapFree"]
    return (
        None if available is None else round(available, 1),
        None if swap is None else round(swap, 1),
    )


def parse_temperature(text: str) -> float | None:
    """``/sys/class/thermal/thermal_zone0/temp``: millidegrees, as degrees."""
    stripped = text.strip()
    return round(int(stripped) / 1000, 1) if stripped.lstrip("-").isdigit() else None


def parse_throttled(text: str) -> int | None:
    """``vcgencmd get_throttled``: ``throttled=0x50000``, as its bits."""
    found = re.search(r"throttled=(0x[0-9a-fA-F]+)", text)
    return int(found.group(1), 16) if found else None


def parse_kib(text: str) -> float | None:
    """``du -sk``'s first field, in MiB."""
    first = text.split(maxsplit=1)[0] if text.split() else ""
    return round(int(first) / 1024, 1) if first.isdigit() else None


def parse_bytes(text: str) -> float | None:
    """A count of bytes, alone on a line, in MiB."""
    stripped = text.strip()
    return round(int(stripped) / (1024 * 1024), 1) if stripped.isdigit() else None


def read_machine(disk_root: Path | None, throttled_text: str | None) -> Machine:
    """The host as this process can see it; anything it cannot read is None."""
    available = swap = temperature = disk = None
    with contextlib.suppress(OSError):
        available, swap = parse_meminfo(Path("/proc/meminfo").read_text("utf-8"))
    with contextlib.suppress(OSError):
        zone = Path("/sys/class/thermal/thermal_zone0/temp")
        temperature = parse_temperature(zone.read_text("utf-8"))
    if disk_root is not None:
        disk = free_gib(disk_root)
    return Machine(
        memory_available_mib=available,
        swap_used_mib=swap,
        temperature_c=temperature,
        throttled=None if throttled_text is None else parse_throttled(throttled_text),
        disk_free_gib=disk,
    )


def free_gib(path: Path) -> float | None:
    """Free space under ``path``, in GiB, as an unprivileged process sees it."""
    try:
        stats = os.statvfs(path)
    except OSError:
        return None
    return round(stats.f_bavail * stats.f_frsize / 1024**3, 2)


def alert_waits(rules: str) -> dict[str, timedelta]:
    """Each alert's ``for:``, read from the rules file; the longest when repeated."""
    waits: dict[str, timedelta] = {}
    current = None
    for line in rules.splitlines():
        named = re.match(r"^\s*-\s*alert:\s*(\w+)", line)
        if named:
            current = named.group(1)
            waits.setdefault(current, timedelta(0))
            continue
        held = re.match(r"^\s*for:\s*(\d+)([smh])\s*$", line)
        if held and current:
            size = {"s": 1, "m": 60, "h": 3600}[held.group(2)]
            wait = timedelta(seconds=int(held.group(1)) * size)
            waits[current] = max(waits[current], wait)
    return waits


def grace_for(alert: str, waits: Mapping[str, timedelta]) -> timedelta:
    """How long after its cause closes an alert may still rise.

    Ten minutes, or the alert's own ``for:`` and five more where that is longer:
    an alert that waits thirty minutes before firing can first fire nearly
    thirty minutes after the fault that caused it.
    """
    return max(BASE_GRACE, waits.get(alert, timedelta(0)) + timedelta(minutes=5))


# --- judging -------------------------------------------------------------------


def slope_per_hour(points: Sequence[tuple[datetime, float]]) -> float | None:
    """The least-squares slope through ``points``, per hour; None below two."""
    if len(points) < MIN_LINE_POINTS:
        return None
    origin = points[0][0]
    xs = [(at - origin).total_seconds() / 3600 for at, _ in points]
    ys = [value for _, value in points]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    spread = sum((x - mean_x) ** 2 for x in xs)
    if spread == 0:
        return None
    return (
        sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / spread
    )


def memory_slopes(
    series: Mapping[str, Sequence[tuple[datetime, float]]],
) -> dict[str, float | None]:
    """Each container's memory slope over the second half of its samples.

    None where the half holds fewer than ``MIN_SLOPE_SAMPLES``: too few to say.
    """
    slopes: dict[str, float | None] = {}
    for name, points in sorted(series.items()):
        half = list(points[len(points) // 2 :])
        found = slope_per_hour(half) if len(half) >= MIN_SLOPE_SAMPLES else None
        slopes[name] = None if found is None else round(found, 3)
    return slopes


def missed_alerts(
    platform_faults: Iterable[Span],
    alerts: Sequence[Span],
    waits: Mapping[str, timedelta],
    causes: Mapping[str, frozenset[str]],
) -> list[str]:
    """Each platform fault that outlasted an alert's wait, with that alert silent.

    A fault of a kind an alert names among its causes, open longer than the
    alert's ``for:`` and the margin to evaluate it, owes that alert a firing
    between the fault opening and the grace after it closed.
    """
    missed: list[str] = []
    for fault in platform_faults:
        if fault.end is None:
            continue
        kind = fault.name.split(" on ", 1)[0]
        for alert in PLATFORM_ALERTS:
            if kind not in causes.get(alert, frozenset()):
                continue
            if fault.end - fault.start <= waits.get(alert, timedelta(0)) + ALERT_MARGIN:
                continue
            deadline = fault.end + grace_for(alert, waits)
            fired = any(
                one.name == alert
                and one.start <= deadline
                and (one.end or deadline) >= fault.start
                for one in alerts
            )
            if not fired:
                missed.append(f"{alert} for {fault.name} at {fault.start.isoformat()}")
    return missed


def judge_resources(
    memory: Mapping[str, Sequence[tuple[datetime, float]]],
    machines: Sequence[Machine],
    sizes: Sequence[Sizes],
    bounds: tuple[float, float] = (MEMORY_SLOPE_MIB_PER_HOUR, DISK_FREE_FLOOR_GIB),
) -> Resources:
    """The run's resources, summarised, and each bound it broke."""
    slope_bound, disk_floor = bounds
    found = Resources()
    slopes = memory_slopes(memory)
    peaks = {
        name: max(v for _, v in points) for name, points in memory.items() if points
    }
    disks = [one.disk_free_gib for one in machines if one.disk_free_gib is not None]
    throttled = [one.throttled for one in machines if one.throttled is not None]
    found.figures = {
        "peak_memory_mib": dict(sorted(peaks.items())),
        "memory_slope_mib_per_hour": slopes,
        "database_mib": _ends(one.database_mib for one in sizes),
        "prometheus_mib": _ends(one.prometheus_mib for one in sizes),
        "disk_free_gib_min": min(disks, default=None),
        "host_memory_available_mib_min": _least(
            one.memory_available_mib for one in machines
        ),
        "swap_used_mib_max": _most(one.swap_used_mib for one in machines),
        "temperature_c_max": _most(one.temperature_c for one in machines),
        "throttled_seen": any(bits & THROTTLED_NOW for bits in throttled)
        if throttled
        else None,
        "bounds": {
            "memory_slope_mib_per_hour": slope_bound,
            "disk_free_gib": disk_floor,
        },
    }
    found.failures += [
        f"memory of {name} grew {slope} MiB/h, over {slope_bound}"
        for name, slope in slopes.items()
        if slope is not None and slope > slope_bound
    ]
    if disks and min(disks) < disk_floor:
        found.failures.append(f"disk free fell to {min(disks)} GiB, under {disk_floor}")
    if found.figures["throttled_seen"]:
        found.failures.append("the host throttled its clock during the run")
    return found


def _ends(values: Iterable[float | None]) -> dict[str, float] | None:
    held = [one for one in values if one is not None]
    return {"first": held[0], "last": held[-1]} if held else None


def _least(values: Iterable[float | None]) -> float | None:
    return min((one for one in values if one is not None), default=None)


def _most(values: Iterable[float | None]) -> float | None:
    return max((one for one in values if one is not None), default=None)
