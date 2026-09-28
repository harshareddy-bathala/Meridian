"""Back up the deployment, then keep only what the retention policy says. Stdlib only.

    python deploy/tools/scheduled_backup.py --dir backups

What `deploy/systemd/meridian-backup.timer` runs every night (D-209). It writes
`meridian-<UTC timestamp>.dump` and its manifest with `backup.py`, checks the
file it wrote against the manifest, and then deletes the dumps the policy no
longer keeps:

- every dump from the last `--keep-daily` days (7);
- the newest dump of each of the last `--keep-weekly` ISO weeks (4);
- the newest dump of each of the last `--keep-monthly` months (6).

About 17 dumps at most. Only files named `meridian-<timestamp>.dump`, as this
script names them, are ever deleted, so a dump taken by hand under another name
is left alone, and the newest dump is never deleted whatever the policy says.

A dump on the same disk as the database protects against a mistake, not against
the disk. Copying `backups/` off the host is the operator's step, and
`docs/OPERATIONS.md` § Backup and restore says how.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backup import backup
from compose_db import (
    Compose,
    Manifest,
    ToolError,
    add_compose_arguments,
    compose_from,
    manifest_path,
    sha256_of,
)

NAME_FORMAT = "meridian-%Y-%m-%dT%H%MZ.dump"
"""A dump's name is when it was taken, to the minute, so names sort by time."""


def dump_name(now: datetime) -> str:
    """The name of a dump taken at `now`."""
    return now.astimezone(UTC).strftime(NAME_FORMAT)


def taken_at(name: str) -> datetime | None:
    """When a dump this script named was taken, or ``None`` for any other name."""
    try:
        return datetime.strptime(name, NAME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def kept(
    stamps: list[datetime], now: datetime, *, daily: int, weekly: int, monthly: int
) -> set[datetime]:
    """The dumps the policy keeps, of those taken at `stamps`.

    The newest in each week or month is the one kept for it, because it holds
    the most. The newest overall is always kept.
    """
    keep = {s for s in stamps if now - s < timedelta(days=daily)}
    newest_in: dict[tuple[str, int, int], datetime] = {}
    for stamp in stamps:
        year, week, _ = stamp.isocalendar()
        for key in (("week", year, week), ("month", stamp.year, stamp.month)):
            if key not in newest_in or stamp > newest_in[key]:
                newest_in[key] = stamp
    this_monday = _monday(*now.isocalendar()[:2])
    for (unit, year, number), stamp in newest_in.items():
        if unit == "week":
            # Counted between Mondays, so a 53-week ISO year is counted right.
            if (this_monday - _monday(year, number)).days // 7 < weekly:
                keep.add(stamp)
        elif (now.year - year) * 12 + now.month - number < monthly:
            keep.add(stamp)
    if stamps:
        keep.add(max(stamps))
    return keep


def _monday(year: int, week: int) -> datetime:
    return datetime.fromisocalendar(year, week, 1)


def prune(directory: Path, now: datetime, **policy: int) -> list[Path]:
    """Delete the dumps and manifests the policy does not keep; return them."""
    named = {p: taken_at(p.name) for p in directory.glob("meridian-*.dump")}
    dumps = {p: stamp for p, stamp in named.items() if stamp is not None}
    keep = kept(list(dumps.values()), now, **policy)
    removed = []
    for path, stamp in sorted(dumps.items()):
        if stamp not in keep:
            path.unlink()
            manifest_path(path).unlink(missing_ok=True)
            removed.append(path)
    return removed


def backup_and_check(compose: Compose, out: Path) -> Manifest:
    """Back up to `out`, then read the file back against its manifest."""
    manifest = backup(compose, out)
    if sha256_of(out) != manifest.sha256:
        raise ToolError(f"{out} does not match the manifest written for it")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dir", type=Path, default=Path("backups"))
    parser.add_argument("--keep-daily", type=int, default=7)
    parser.add_argument("--keep-weekly", type=int, default=4)
    parser.add_argument("--keep-monthly", type=int, default=6)
    add_compose_arguments(parser)
    args = parser.parse_args(argv)
    now = datetime.now(UTC)
    out = args.dir / dump_name(now)
    try:
        manifest = backup_and_check(compose_from(args), out)
    except (ToolError, OSError) as failed:
        # Nothing is pruned after a failed backup: the old dumps may be all
        # there is.
        print(f"scheduled backup: {failed}", file=sys.stderr)
        return 1
    removed = prune(
        args.dir,
        now,
        daily=args.keep_daily,
        weekly=args.keep_weekly,
        monthly=args.keep_monthly,
    )
    print(f"wrote {out} ({manifest.size_bytes} bytes); removed {len(removed)} old")
    for path in removed:
        print(f"  removed {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
