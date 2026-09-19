"""One folder per reception, and the manifest that says how far it got.

Each assignment a station works gets ``<state>/captures/<assignment_id>/``,
beside ``held.json`` and ``outbox/``. The folder holds the recording (or refers
to one elsewhere), the decoder's output and logs, and ``manifest.json``: the
assignment, what was tuned and recorded, and the **phase** the reception reached.

The manifest is rewritten on every phase change, temp-then-rename (D-068), so a
power cut leaves either the old phase or the new one and never half of each. A
restart reads every manifest and resumes from the phase it finds (D-123) — which
is what makes a result that existed only in memory reconstructible, as D-073
anticipated.

This module stores and reads; the manifest's own rules are
:mod:`meridian_client.reception.manifest`'s. Neither decides anything about what
a phase means for an observation. That is the executor's, and the recovery's
(D-122, D-123).

Reference: docs/DECISIONS.md D-068, D-073, D-122, D-123.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from meridian_client.reception.manifest import (
    Manifest,
    manifest_from_json,
    manifest_to_json,
)

__all__ = [
    "MANIFEST_NAME",
    "CaptureFolders",
    "MalformedManifestError",
    "ScannedFolder",
]

MANIFEST_NAME = "manifest.json"

SAFE_ASSIGNMENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
"""The same rule the upload queue applies before an id becomes a file name."""


class MalformedManifestError(ValueError):
    """A manifest that cannot be read back into a :class:`Manifest`.

    Raised rather than treated as absent: a station that ignored an unreadable
    manifest would lose track of a reception it had started, silently.
    """


@dataclass(frozen=True, slots=True)
class ScannedFolder:
    """One capture folder found at start-up: its manifest, or why it has none."""

    assignment_id: str
    manifest: Manifest | None
    problem: str | None = None


class CaptureFolders:
    """The ``captures`` directory under a station's state directory.

    Args:
        root: ``<state>/captures``. Created on first write.
    """

    def __init__(self, root: Path) -> None:
        """Point at ``root``. Nothing is read or created yet."""
        self._root = root

    def folder_for(self, assignment_id: str) -> Path:
        """The folder for one assignment.

        Raises:
            ValueError: The id is not safe to use as a directory name. It comes
                from the platform, and a ``..`` in it must not become a path.
        """
        if not SAFE_ASSIGNMENT_ID.fullmatch(assignment_id):
            raise ValueError(
                f"assignment_id is not usable as a folder: {assignment_id!r}"
            )
        return self._root / assignment_id

    def write(self, manifest: Manifest) -> None:
        """Replace the manifest atomically, creating the folder if needed."""
        folder = self.folder_for(manifest.assignment.assignment_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / MANIFEST_NAME
        partial = path.with_name(path.name + ".partial")
        partial.write_text(
            json.dumps(manifest_to_json(manifest), indent=2) + "\n", encoding="utf-8"
        )
        os.replace(partial, path)  # noqa: PTH105 — Path.replace is the same call

    def read(self, assignment_id: str) -> Manifest | None:
        """The manifest for one assignment, or ``None`` if it has no folder yet.

        Raises:
            MalformedManifestError: The file exists and cannot be read back.
        """
        path = self.folder_for(assignment_id) / MANIFEST_NAME
        if not path.exists():
            return None
        return _read(path)

    def scan(self) -> tuple[ScannedFolder, ...]:
        """Every capture folder, in id order, with its manifest or its problem.

        A folder with an unreadable or missing manifest is reported rather than
        raised, so one corrupt reception cannot stop a station recovering the
        rest.
        """
        if not self._root.is_dir():
            return ()
        found = []
        for folder in sorted(self._root.iterdir()):
            if not folder.is_dir() or not SAFE_ASSIGNMENT_ID.fullmatch(folder.name):
                continue
            path = folder / MANIFEST_NAME
            try:
                found.append(ScannedFolder(folder.name, _read(path)))
            except FileNotFoundError:
                found.append(ScannedFolder(folder.name, None, "no manifest"))
            except MalformedManifestError as exc:
                found.append(ScannedFolder(folder.name, None, str(exc)))
        return tuple(found)


def _read(path: Path) -> Manifest:
    """Parse one manifest file, turning every way it can be wrong into one error."""
    text = path.read_text(encoding="utf-8")
    try:
        manifest = manifest_from_json(json.loads(text))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise MalformedManifestError(
            f"{path} is not a readable manifest: {exc}"
        ) from exc
    folder_id = path.parent.name
    if manifest.assignment.assignment_id != folder_id:
        raise MalformedManifestError(
            f"{path} describes {manifest.assignment.assignment_id}, not {folder_id}"
        )
    return manifest
