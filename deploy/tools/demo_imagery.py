"""Give a demo stack one published area and a simulated scene of it, offline.

    python deploy/tools/demo_imagery.py [--project-name NAME] [--env-file FILE]

A rehearsal runs with no network, and still has to show the areas and the
Imagery page doing what they do (D-378). This does, against a running demo
stack, what ``OPERATIONS.md`` § Publishing areas and § Imagery describe for a
real deployment, with the demonstration's own synthetic archive in place of the
open ones:

1. **An area**, if none is registered: Bengaluru urban, the runbook's first.
2. **Its regional summary**, from a snapshot of the demo's own database, by
   ``meridian snapshot export``, ``regions report`` and ``regions summary``,
   written to ``<published>/areas.json``. With no public product loaded, each
   series says why it has no value, as the summary always does.
3. **A simulated scene of it**, by ``meridian-ingest imagery demo`` in the
   ingest image: the real search, cloud count, rendering and publishing, over
   a synthetic archive, and labelled simulated in its credit, its caption, its
   manifest, the index and the API (rule 5).

It changes no setting. It says how to publish what it wrote:
``AREAS_PUBLISHED=true`` and ``up -d api``, which the demo override file reads
with the two paths filled in.

Each container runs as the invoking user, so the files it writes under
``data/`` are the user's, and the platform's database is reached as
``meridian_api``, which may read and write rows and nothing else (D-207).

Exit status: 0 the area and the scene are published; 1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FILES = ("deploy/docker-compose.yml", "deploy/docker-compose.demo.yml")
INGEST_FILE = "deploy/docker-compose.ingest.yml"
AREA = ("Bengaluru urban", "77.46,12.83,77.75,13.14")
"""The runbook's first area (OPERATIONS.md § Publishing areas)."""

SNAPSHOT_DAYS = 7
"""How far back the snapshot reaches: a week of the demo fleet."""

INGEST_TOML = """\
# Written by deploy/tools/demo_imagery.py for one run of `imagery demo`.
raw_root = "/work/raw"

[imagery]
summary = "/published/areas.json"
published = "/published/imagery"
"""


class ToolError(Exception):
    """A step failed; the message is what the operator reads."""


def compose(args: argparse.Namespace, *rest: str, ingest: bool = False) -> list[str]:
    """`docker compose` for the demo project, then ``rest``."""
    command = ["docker", "compose"]
    for name in (*FILES, *((INGEST_FILE,) if ingest else ())):
        command += ["-f", str(REPO / name)]
    if args.project_name:
        command += ["-p", args.project_name]
    if args.env_file:
        command += ["--env-file", args.env_file]
    return [*command, *rest]


def run(command: list[str]) -> str:
    """Run one command; its output, or a :class:`ToolError` in its own words."""
    words = command
    for start in ("meridian", "meridian-ingest", "build"):
        if start in command:
            words = command[command.index(start) :]
            break
    print("->", " ".join(words), flush=True)
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise ToolError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def platform(
    args: argparse.Namespace, *command: str, mounts: tuple[str, ...] = ()
) -> str:
    """One ``meridian`` command in a one-off jobs container."""
    volumes = [part for mount in mounts for part in ("-v", mount)]
    user = f"{os.getuid()}:{os.getgid()}"
    return run(
        compose(
            args,
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--user",
            user,
            *volumes,
            "jobs",
            *command,
        )
    )


def found(pattern: str, text: str, what: str) -> str:
    match = re.search(pattern, text)
    if match is None:
        raise ToolError(f"could not find {what} in:\n{text}")
    return match.group(1)


def publish_area(args: argparse.Namespace, work: Path, published: Path) -> None:
    """Steps 1 and 2: an area, and its summary in ``published``."""
    listed = platform(args, "meridian", "regions", "list")
    if "active" not in listed:
        platform(
            args, "meridian", "regions", "add", "--label", AREA[0], "--bbox", AREA[1]
        )
    since = (datetime.now(tz=UTC) - timedelta(days=SNAPSHOT_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    mounts = (f"{work}:/work",)
    exported = platform(
        args,
        "meridian",
        "snapshot",
        "--root",
        "/work",
        "export",
        "--since",
        since,
        mounts=mounts,
    )
    snapshot = found(r"(/work/\S+)", exported, "the snapshot's directory")
    reported = platform(
        args,
        "meridian",
        "regions",
        "report",
        "--snapshot",
        snapshot,
        "--root",
        "/work",
        mounts=mounts,
    )
    report = found(r"(/work/\S+)", reported, "the report's directory")
    platform(
        args,
        "meridian",
        "regions",
        "summary",
        report.rstrip("."),
        "--output",
        "/published/areas.json",
        mounts=(*mounts, f"{published}:/published"),
    )


def publish_scene(args: argparse.Namespace, work: Path, published: Path) -> None:
    """Step 3: a simulated scene of each published area, by the ingest image."""
    run(compose(args, "build", "ingest-imagery", ingest=True))
    (work / "ingest.toml").write_text(INGEST_TOML, encoding="utf-8")
    (published / "imagery").mkdir(exist_ok=True)
    image = os.environ.get("MERIDIAN_INGEST_IMAGE", "meridian-ingest:local")
    said = run(
        [
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--network",
            "none",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-v",
            f"{work}:/work",
            "-v",
            f"{published}:/published",
            image,
            "meridian-ingest",
            "--config",
            "/work/ingest.toml",
            "imagery",
            "demo",
        ]
    )
    print(said, end="")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-p", "--project-name", default=None)
    parser.add_argument("--env-file", default=None)
    parser.add_argument(
        "--published",
        type=Path,
        default=REPO / "data" / "published",
        help="the host's PUBLISHED_DIR; default data/published",
    )
    parser.add_argument(
        "--work",
        type=Path,
        default=REPO / "data" / "demo-imagery",
        help="where the snapshot, the report and the raw store are kept",
    )
    args = parser.parse_args(argv)
    work, published = args.work.resolve(), args.published.resolve()
    work.mkdir(parents=True, exist_ok=True)
    published.mkdir(parents=True, exist_ok=True)
    if not os.access(published, os.W_OK):
        print(
            f"demo imagery: {published} is not writable by you. If Docker made it "
            "(owned by root, from a mount that found it missing), and it is empty, "
            "remove it and run this again.",
            file=sys.stderr,
        )
        return 1
    try:
        publish_area(args, work, published)
        publish_scene(args, work, published)
    except ToolError as exc:
        print(f"demo imagery: {exc}", file=sys.stderr)
        return 1
    print(
        "Published. Serve it with AREAS_PUBLISHED=true and `docker compose "
        "-f deploy/docker-compose.yml -f deploy/docker-compose.demo.yml "
        "--profile demo up -d api`."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
