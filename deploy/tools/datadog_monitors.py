"""Apply deploy/datadog/monitors.json to Datadog through pup. Stdlib only.

    python deploy/tools/datadog_monitors.py            # say what would change
    python deploy/tools/datadog_monitors.py --apply    # change it

Each monitor in the file mirrors a Prometheus rule (D-353), and the file is the
record: a monitor is matched to its live copy by name, created when absent and
updated when present. A live monitor tagged ``managed:meridian`` that the file no
longer names is reported, and deleted only with ``--prune``.

**Recipients are not in the repository.** ``DATADOG_NOTIFY`` holds the handles a
monitor notifies, such as ``@you@example.org``, and is appended to every
message as it is sent; a monitor grouped by ``simulated`` notifies them only for
measured stations. With it unset the monitors are still created, and notify
nobody.

It runs Datadog's own CLI, ``pup``, signed in with ``pup auth login``, so this
tool never holds a key.

Exit status: 0 done (or nothing to do), 1 it failed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

MONITORS = Path(__file__).resolve().parents[1] / "datadog" / "monitors.json"
MANAGED = "managed:meridian"


class ToolError(Exception):
    """Something the operator has to fix before this can run."""


def desired(path: Path = MONITORS, notify: str = "") -> list[dict[str, Any]]:
    """The monitors the file names, each with the recipients appended."""
    monitors: list[dict[str, Any]] = json.loads(path.read_text())["monitors"]
    names = [monitor["name"] for monitor in monitors]
    if len(set(names)) != len(names):
        raise ToolError("two monitors in the file share a name")
    if notify.strip():
        for monitor in monitors:
            monitor["message"] = f"{monitor['message']}\n\n{addressed(monitor, notify)}"
    return monitors


def addressed(monitor: dict[str, Any], notify: str) -> str:
    """The recipients, held back for a group of simulated stations.

    The demonstration fleet injects faults on purpose (D-330), so a monitor
    grouped by ``simulated`` alerts on it every day. Its alerts are still
    recorded, as Prometheus records them; only a measured group sends anyone
    an email.
    """
    if "by {simulated}" not in monitor["query"]:
        return notify.strip()
    return (
        '{{#is_exact_match "simulated.name" "false"}}'
        f"{notify.strip()}"
        "{{/is_exact_match}}"
    )


def pup(*args: str) -> str:
    """Run pup, returning its output, or raise with what it said."""
    try:
        done = subprocess.run(
            ["pup", *args, "--no-envelope", "-o", "json"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ToolError("pup is not installed: docs/OPERATIONS.md § Datadog") from exc
    if done.returncode != 0:
        raise ToolError(f"pup {' '.join(args[:2])}: {done.stderr.strip()}")
    return done.stdout


def live() -> dict[str, dict[str, Any]]:
    """Every live monitor this file manages, by name."""
    out = pup("monitors", "list", "--tags", MANAGED, "--limit", "1000")
    # pup prints "No monitors found" to stderr and nothing to stdout.
    if not out.strip() or out.strip().startswith("No monitors"):
        return {}
    found = json.loads(out)
    if isinstance(found, dict):
        found = found.get("data", found.get("monitors", []))
    return {monitor["name"]: monitor for monitor in found}


def plan(
    want: list[dict[str, Any]], have: dict[str, dict[str, Any]]
) -> tuple[
    list[dict[str, Any]], list[tuple[int, dict[str, Any]]], list[dict[str, Any]]
]:
    """What to create, what to update (with its live id), and what to prune."""
    names = {monitor["name"] for monitor in want}
    create = [monitor for monitor in want if monitor["name"] not in have]
    update = [(int(have[m["name"]]["id"]), m) for m in want if m["name"] in have]
    prune = [monitor for name, monitor in have.items() if name not in names]
    return create, update, prune


def send(verb: str, monitor: dict[str, Any], monitor_id: int | None = None) -> None:
    """Create or update one monitor from a file pup reads."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(monitor, handle)
    try:
        extra = [str(monitor_id)] if monitor_id is not None else []
        pup("monitors", verb, "--file", handle.name, *extra)
    finally:
        Path(handle.name).unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="make the changes")
    parser.add_argument(
        "--prune", action="store_true", help="also delete managed monitors not named"
    )
    args = parser.parse_args(argv)
    try:
        want = desired(notify=os.environ.get("DATADOG_NOTIFY", ""))
        create, update, prune = plan(want, live())
        for monitor in create:
            print(f"create  {monitor['name']}")
        for _, monitor in update:
            print(f"update  {monitor['name']}")
        for monitor in prune:
            action = "delete" if args.prune else "unnamed (kept; --prune deletes)"
            print(f"{action}  {monitor['name']}")
        if not args.apply:
            print("\nnothing changed; --apply makes these changes")
            return 0
        for monitor in create:
            send("create", monitor)
        for monitor_id, monitor in update:
            send("update", monitor, monitor_id)
        if args.prune:
            for monitor in prune:
                pup("monitors", "delete", str(monitor["id"]), "--yes")
        print(f"\n{len(create)} created, {len(update)} updated")
    except (ToolError, OSError, json.JSONDecodeError) as exc:
        print(f"datadog_monitors: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
