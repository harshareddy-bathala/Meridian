"""Before seventy-two hours: is this host ready to run them? Stdlib only.

``long_run.py --preflight`` asks these questions and starts nothing (D-257).
Each answer is ``pass``, ``fail``, ``warn`` or ``skip``, and any ``fail`` exits 1:

- **architecture** — the acceptance run is the Pi's, so anything but arm64 is
  a rehearsal and says so;
- **the database's disk** — Docker's data root on the NVMe, where the
  architecture puts Postgres. On a Pi an SD card fails; elsewhere it warns;
- **the clock** — synchronised by NTP. A Pi has no real-time clock, and a clock
  that steps forward mid-run reads as a host that slept, which fails the run;
- **free disk** — at least four times the run's floor;
- **the image** — ``MERIDIAN_IMAGE`` pinned to a ``sha-<commit>`` tag or a
  digest (D-256), and present here or pullable. A run of ``:main`` cannot say
  which software survived;
- **Compose** — 2.24.4 or later, which the deployment's ``!reset`` needs;
- **cooling** — on a Pi, not throttled and under 70 °C before the load starts;
- **the metrics token** — ``deploy/prometheus/metrics_token`` readable by the
  Prometheus container, which runs as ``nobody``. A token file kept at mode
  600 stops every scrape, and every platform alert then fires for the whole
  run, as Stage 24's own rehearsal found;
- **a fresh start** — ``--out`` holds no earlier run.

Pure where it decides anything, so ``tests/unit/test_long_run_watch.py`` pins it.
"""

from __future__ import annotations

import platform
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from long_run_state import refuse_taken
from long_run_watch import THROTTLED_NOW, free_gib, parse_temperature, parse_throttled

ARM = frozenset({"aarch64", "arm64"})
COMPOSE_LEAST = (2, 24, 4)
HOT_C = 70.0
READABLE_BY_OTHERS = 0o004

MOUNT_FIELDS = 2
"""A mount line's device and mount point, at least."""

DISK_HEADROOM = 4.0
"""Free disk asked for at the start, as a multiple of the run's floor."""

Run = Callable[[list[str]], tuple[int, str]]


@dataclass(frozen=True, slots=True)
class Check:
    """One question, its answer, and what the answer rests on."""

    name: str
    status: str
    detail: str


def architecture(machine: str) -> Check:
    """arm64 is the Pi's run; anything else is a rehearsal."""
    if machine in ARM:
        return Check("architecture", "pass", machine)
    return Check("architecture", "warn", f"{machine}: a rehearsal, not the Pi's run")


def mount_source(mounts: str, path: Path) -> str | None:
    """The device ``/proc/mounts`` says holds ``path``: the longest mount above it."""
    best, source = -1, None
    target = str(path)
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) < MOUNT_FIELDS:
            continue
        point = parts[1]
        inside = target == point or target.startswith(point.rstrip("/") + "/")
        if inside and len(point) > best:
            best, source = len(point), parts[0]
    return source


def database_disk(source: str | None, machine: str) -> Check:
    """Docker's data root on the NVMe: required on a Pi, advised elsewhere."""
    if source is None:
        return Check("database disk", "warn", "Docker's data root could not be found")
    if "nvme" in source:
        return Check("database disk", "pass", source)
    status = "fail" if machine in ARM else "warn"
    return Check("database disk", status, f"{source}, not the NVMe")


def clock(synchronised: str | None) -> Check:
    """``timedatectl``'s ``NTPSynchronized``: a clock that steps reads as sleep."""
    if synchronised is None:
        return Check("clock", "fail", "timedatectl did not answer")
    if synchronised.strip() == "yes":
        return Check("clock", "pass", "synchronised by NTP")
    return Check("clock", "fail", "not synchronised: a step mid-run reads as sleep")


def free_disk(free: float | None, floor_gib: float) -> Check:
    """Room for the run: four times the floor it must stay above."""
    wanted = floor_gib * DISK_HEADROOM
    if free is None:
        return Check("free disk", "fail", "free space could not be read")
    status = "pass" if free >= wanted else "fail"
    return Check("free disk", status, f"{free} GiB free, {wanted} GiB wanted")


def image(reference: str | None, present: bool) -> Check:
    """The platform image, pinned to a commit and here or pullable (D-256)."""
    if not reference:
        return Check("image", "fail", "MERIDIAN_IMAGE is not set")
    if not re.search(r":sha-[0-9a-f]{7,}|@sha256:[0-9a-f]{64}", reference):
        return Check("image", "fail", f"{reference} is not pinned to a commit")
    if not present:
        return Check("image", "fail", f"{reference} is neither here nor pullable")
    return Check("image", "pass", reference)


def compose(version: str | None) -> Check:
    """Compose recent enough for the deployment's ``!reset``."""
    found = re.match(r"v?(\d+)\.(\d+)\.(\d+)", version or "")
    if not found:
        return Check("compose", "fail", f"version {version!r} could not be read")
    held = tuple(int(one) for one in found.groups())
    status = "pass" if held >= COMPOSE_LEAST else "fail"
    return Check(
        "compose", status, f"{version}, {'.'.join(map(str, COMPOSE_LEAST))} wanted"
    )


def cooling(throttled: str | None, temperature: str | None) -> Check:
    """A Pi neither throttled nor hot before the load begins."""
    if throttled is None:
        return Check("cooling", "skip", "not a Raspberry Pi")
    bits = parse_throttled(throttled)
    degrees = parse_temperature(temperature or "")
    if bits is None or bits & THROTTLED_NOW:
        return Check("cooling", "fail", f"throttled ({throttled.strip()})")
    if degrees is not None and degrees >= HOT_C:
        return Check("cooling", "fail", f"{degrees} °C before any load")
    return Check("cooling", "pass", f"not throttled, {degrees} °C")


def metrics_token(path: Path) -> Check:
    """The token file Prometheus scrapes with, readable by the user it runs as."""
    try:
        mode = path.stat().st_mode
    except OSError:
        return Check("metrics token", "fail", f"{path} does not exist")
    if not mode & READABLE_BY_OTHERS:
        return Check(
            "metrics token",
            "fail",
            f"{path} is mode {mode & 0o777:o}; Prometheus runs as nobody and"
            " cannot read it, so every scrape fails: chmod 644",
        )
    return Check("metrics token", "pass", f"{path} is readable by Prometheus")


def fresh(out: Path) -> Check:
    """``--out`` holds no earlier run."""
    refused = refuse_taken(out)
    return Check("fresh start", "fail" if refused else "pass", refused or str(out))


def preflight(
    run: Run, out: Path, image_ref: str | None, floor_gib: float, token: Path
) -> list[Check]:
    """Every question, asked of this host through ``run``; nothing is started."""

    def answer(command: list[str]) -> str | None:
        status, output = run(command)
        return output.strip() if status == 0 else None

    machine = platform.machine()
    root = answer(["docker", "info", "--format", "{{.DockerRootDir}}"])
    mounts = _read("/proc/mounts")
    source = mount_source(mounts, Path(root)) if root and mounts else None
    if source and source.startswith("/dev/mapper/"):
        # An encrypted or logical volume: the disk is what lies beneath it.
        beneath = answer(["lsblk", "-s", "-n", "-l", "-o", "NAME", source])
        source = f"{source} on {' '.join((beneath or '').split()[1:]) or 'unknown'}"
    present = bool(image_ref) and (
        answer(["docker", "image", "inspect", "--format", "{{.Id}}", str(image_ref)])
        is not None
        or answer(["docker", "manifest", "inspect", str(image_ref)]) is not None
    )
    pi = answer(["vcgencmd", "get_throttled"])
    return [
        architecture(machine),
        database_disk(source, machine),
        clock(answer(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])),
        free_disk(free_gib(Path(root)) if root else None, floor_gib),
        image(image_ref, present),
        compose(answer(["docker", "compose", "version", "--short"])),
        cooling(pi, _read("/sys/class/thermal/thermal_zone0/temp")),
        metrics_token(token),
        fresh(out),
    ]


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text("utf-8")
    except OSError:
        return None
