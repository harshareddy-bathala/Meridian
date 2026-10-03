"""Copy a station's own record into one file, and put it back. Stdlib only.

    python deploy/tools/station_archive.py create --state state --out station.tar
    python deploy/tools/station_archive.py verify station.tar
    python deploy/tools/station_archive.py restore station.tar --state state

A station is authoritative for what it received (D-129), and some of that
exists nowhere else: a report the platform has not yet acknowledged, and every
decoded product, of which the platform holds only a hash and a size (D-281).
The database backup takes none of it. This is the station tier's backup.

**What goes in:** `held.json`; `outbox/`, with `outbox/failed/`; every capture
folder's manifest, decoder output and logs; and `products/`.

**What never goes in:** `credentials.json` and `registration_key`. They are the
station's identity, and an archive is something that gets copied to other
disks. So an archive restores a station's record, not the station: a station
that kept its registration key is given a new token by a bound invite (D-034)
and delivers what was restored, and one that lost both is a new station, whose
restored reports the platform refuses (OPERATIONS.md § Archiving a station).
**Recordings stay out unless asked for** with `--recordings`: a pass is
about a gigabyte, and the station deletes it once the result is handed over
(D-123). Everything left out is listed, so the gap is read when the archive is
made and not when it is needed.

Beside the archive goes `<archive>.manifest.json`: the archive's sha256, and
every file's path, size and sha256. `verify` checks all three against the bytes
in the archive. `restore` verifies first, then writes only files that do not
exist: **it never replaces anything a station already holds**, because the
station's copy is the authority and an archive is older by definition.

The station may be running. Every file it writes is renamed into place whole
(D-068), so what is read here is never half a file; a report delivered and
removed while the archive is made is simply not in it.

Exit status: 0 done, 1 refused or failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

FORMAT = "meridian-station-archive/1"
CHUNK_BYTES = 1 << 20

NEVER = frozenset({"credentials.json", "registration_key"})
"""The station's identity. Not in an archive under any option."""

TOP_LEVEL = ("held.json", "outbox", "captures", "products")
"""What a state directory holds besides its identity (`StationPaths.under`)."""


class ArchiveError(Exception):
    """A step failed or was refused; the message is what the operator reads."""


@dataclass(frozen=True, slots=True)
class Entry:
    """One file in the archive."""

    path: str
    """Relative to the state directory, with forward slashes."""
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class Manifest:
    """What an archive is, written beside it as JSON."""

    sha256: str
    size_bytes: int
    created_at: str
    files: list[Entry]
    left_out: list[str] = field(default_factory=list)
    """What was in the state directory and is not in the archive, and why."""
    format: str = FORMAT


class _Hashing:
    """A file that hashes what is read from it, so a file is read once."""

    def __init__(self, source: IO[bytes]) -> None:
        self._source = source
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        chunk = self._source.read(size)
        self.digest.update(chunk)
        return chunk


def manifest_path(archive: Path) -> Path:
    """Where the manifest for `archive` lives: beside it, with a suffix added."""
    return archive.with_name(archive.name + ".manifest.json")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def recordings_of(captures: Path) -> set[Path]:
    """The recording each capture folder's manifest names, where it has one."""
    found = set()
    for manifest in captures.glob("*/manifest.json"):
        try:
            recording = json.loads(manifest.read_text(encoding="utf-8"))["recording"]
            named = Path(recording["path"])
            found.add(named.resolve())
            # The station wrote the path from its own working directory, which
            # need not be this tool's: a file of that name in the folder is it.
            found.add((manifest.parent / named.name).resolve())
        except (OSError, ValueError, KeyError, TypeError):
            # No recording, or a manifest this tool cannot read: nothing to
            # leave out by name. The folder's files are archived as they are.
            continue
    return found


def select(
    state: Path, *, products: bool, recordings: bool
) -> tuple[list[Path], list[str]]:
    """Which files under `state` go in, and what is left out and why.

    Returns:
        The files, sorted by their path relative to `state`, and one line for
        each thing left out.
    """
    left_out = [
        f"{name} — the station's identity, never archived (D-287)"
        for name in sorted(NEVER)
        if (state / name).exists()
    ]
    if not products and (state / "products").is_dir():
        left_out.append("products/ — left out by --no-products")
    kept_recordings = recordings_of(state / "captures")
    chosen = []
    for top in TOP_LEVEL:
        root = state / top
        if top == "products" and not products:
            continue
        candidates = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in candidates:
            if not path.is_file() or path.is_symlink():
                continue
            if path.name.endswith(".partial"):
                continue  # a write in progress; the whole file follows it
            if not recordings and path.resolve() in kept_recordings:
                size = path.stat().st_size
                relative = path.relative_to(state).as_posix()
                left_out.append(
                    f"{relative} — a recording, {size} bytes; --recordings takes it"
                )
                continue
            chosen.append(path)
    return sorted(chosen, key=lambda p: p.relative_to(state).as_posix()), left_out


def create(state: Path, out: Path, *, products: bool, recordings: bool) -> Manifest:
    """Archive `state` to `out`, and write the manifest beside it."""
    if not state.is_dir():
        raise ArchiveError(f"{state} is not a station's state directory")
    partial = out.with_name(out.name + ".partial")
    for path in (out, manifest_path(out), partial):
        if path.exists():
            raise ArchiveError(f"{path} already exists; choose another --out")
    files, left_out = select(state, products=products, recordings=recordings)
    if not files:
        raise ArchiveError(f"{state} holds nothing to archive")

    out.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    with tarfile.open(partial, "w") as archive:
        for path in files:
            relative = path.relative_to(state).as_posix()
            try:
                handle = path.open("rb")
            except FileNotFoundError:
                # Delivered, or pruned, between the listing and now.
                left_out.append(f"{relative} — gone before it was read")
                continue
            with handle:
                info = archive.gettarinfo(arcname=relative, fileobj=handle)
                # The file's bytes and name; whose it was on this disk is not
                # the archive's to say, and a restore writes as whoever runs it.
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                hashing = _Hashing(handle)
                archive.addfile(info, hashing)
            entries.append(Entry(relative, info.size, hashing.digest.hexdigest()))
    partial.rename(out)
    manifest = Manifest(
        sha256=sha256_of(out),
        size_bytes=out.stat().st_size,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        files=entries,
        left_out=left_out,
    )
    manifest_path(out).write_text(
        json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def read_manifest(archive: Path) -> Manifest:
    """The manifest beside `archive`.

    Raises:
        ArchiveError: It is missing, is not JSON, or is another format. An
            archive without a trustworthy manifest is not restored on hope.
    """
    path = manifest_path(archive)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document["format"] != FORMAT:
            raise ArchiveError(f"{path} is not a {FORMAT} manifest")
        files = [Entry(**entry) for entry in document["files"]]
        return Manifest(
            sha256=str(document["sha256"]),
            size_bytes=int(document["size_bytes"]),
            created_at=str(document["created_at"]),
            files=files,
            left_out=list(document.get("left_out", [])),
        )
    except FileNotFoundError as missing:
        raise ArchiveError(f"no manifest at {path}") from missing
    except (ValueError, KeyError, TypeError) as broken:
        raise ArchiveError(f"manifest {path} cannot be read: {broken}") from broken


def safe(relative: str) -> bool:
    """Whether a path from a manifest may be written below a state directory."""
    parts = Path(relative).parts
    return (
        bool(parts)
        and not Path(relative).is_absolute()
        and ".." not in parts
        and parts[0] in TOP_LEVEL
        and Path(relative).name not in NEVER
    )


def verify(archive: Path) -> Manifest:
    """Check the archive, and every file in it, against the manifest.

    Raises:
        ArchiveError: The archive is not the one that was written, holds a file
            the manifest does not name, lacks one it does, or holds other bytes.
    """
    manifest = read_manifest(archive)
    found = sha256_of(archive)
    if found != manifest.sha256:
        raise ArchiveError(
            f"the archive's sha256 is {found}, the manifest says {manifest.sha256}:"
            " the file is not the one that was archived"
        )
    expected = {entry.path: entry for entry in manifest.files}
    unsafe = [path for path in expected if not safe(path)]
    if unsafe:
        raise ArchiveError(f"the manifest names paths outside a state: {unsafe}")
    seen = set()
    with tarfile.open(archive, "r") as tar:
        for member in tar:
            entry = expected.get(member.name)
            if entry is None or not member.isfile():
                raise ArchiveError(f"the archive holds {member.name!r}, unlisted")
            handle = tar.extractfile(member)
            assert handle is not None
            digest = hashlib.sha256()
            while chunk := handle.read(CHUNK_BYTES):
                digest.update(chunk)
            if digest.hexdigest() != entry.sha256 or member.size != entry.size_bytes:
                raise ArchiveError(f"{member.name} is not the file that was archived")
            seen.add(member.name)
    missing = sorted(expected.keys() - seen)
    if missing:
        raise ArchiveError(f"the archive lacks {missing}")
    return manifest


def restore(archive: Path, state: Path) -> tuple[list[str], list[str]]:
    """Write the archive's files below `state`, replacing nothing.

    Returns:
        The paths written, and the paths left alone because the station already
        holds a file there.
    """
    manifest = verify(archive)
    written, kept = [], []
    with tarfile.open(archive, "r") as tar:
        for entry in manifest.files:
            target = state / entry.path
            if target.exists():
                kept.append(entry.path)
                continue
            source = tar.extractfile(entry.path)
            assert source is not None
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".partial")
            with partial.open("wb") as handle:
                while chunk := source.read(CHUNK_BYTES):
                    handle.write(chunk)
            partial.rename(target)
            written.append(entry.path)
    return written, kept


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    making = commands.add_parser("create", help="archive a state directory")
    making.add_argument("--state", required=True, type=Path)
    making.add_argument("--out", required=True, type=Path, help="the archive to write")
    making.add_argument("--no-products", action="store_true", help="leave products out")
    making.add_argument("--recordings", action="store_true", help="take recordings too")
    checking = commands.add_parser(
        "verify", help="check an archive against its manifest"
    )
    checking.add_argument("archive", type=Path)
    putting = commands.add_parser("restore", help="write an archive's files back")
    putting.add_argument("archive", type=Path)
    putting.add_argument("--state", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command == "create":
            manifest = create(
                args.state,
                args.out,
                products=not args.no_products,
                recordings=args.recordings,
            )
            print(f"wrote {args.out} ({manifest.size_bytes} bytes)")
            print(f"  sha256     {manifest.sha256}")
            print(f"  files      {len(manifest.files)}")
            print(f"  manifest   {manifest_path(args.out)}")
            for line in manifest.left_out:
                print(f"  not in this archive   {line}")
        elif args.command == "verify":
            manifest = verify(args.archive)
            print(f"{args.archive}: {len(manifest.files)} files, as archived")
        else:
            written, kept = restore(args.archive, args.state)
            print(f"wrote {len(written)} files below {args.state}")
            for path in kept:
                print(f"  left alone   {path} — the station already holds it")
    except (ArchiveError, OSError, tarfile.TarError) as refused:
        print(f"station archive: {refused}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
