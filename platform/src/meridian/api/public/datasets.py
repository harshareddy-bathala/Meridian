"""``/api/v1/datasets`` — the published evidence packages, and their files.

``/datasets`` is the index ``EVIDENCE_INDEX`` names, written by ``meridian
dataset publish`` and checked whole as ``evidence-index-1`` (D-369). Unnamed,
the answer is ``not_yet_computed``; named and refused, a ``server_error``
naming the setting, never part of the file, as ``/insights/model`` answers
(D-332).

``/datasets/{name}/{file}`` is one file of one listed package, from the
directory beside the index. **Only a name and a file the index lists are
served**, so a request cannot reach any other path, and **only bytes that still
have the digest and size the index states**: a file changed after it was
published is refused, never served under the index's hash. It is an
attachment, for a reader to keep.

A package's name is its content hash, and a sealed directory is never
rewritten, so a file served under a name is the same file for as long as the
name is listed. That is what lets the front keep it longer than a view's copy
(``deploy/front/nginx.conf``).

Every package is a public cut: to the minute and the degree, with no SNR or
Doppler series (D-093, D-369).

Reference: docs/DECISIONS.md D-086, D-093, D-310, D-313, D-332, D-369.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi import Path as PathParam
from fastapi.responses import Response
from pydantic import Field, ValidationError

from meridian.api.dependencies import get_settings
from meridian.api.public.envelope import NOT_FOUND, SERVER_ERROR, PublicError
from meridian.api.public.models.datasets import PublicDatasets
from meridian.api.public.models.not_yet_computed import NotYetComputed
from meridian.config import Settings

__all__ = ["DATASETS_STAGE", "Datasets", "router"]

DATASETS_STAGE = 37
"""Stage 37 — the dashboard for everyone, whose data page lists the packages."""

Datasets = Annotated[PublicDatasets | NotYetComputed, Field(discriminator="status")]
"""What the index read serves; ``status`` says which."""

MEDIA_TYPES = {
    ".jsonl": "application/x-ndjson",
    ".json": "application/json",
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".toml": "application/toml",
}
"""What each kind of file in a package is served as."""

_log = logging.getLogger(__name__)

router = APIRouter()

_REFUSED = (
    "The evidence index EVIDENCE_INDEX names was refused; the platform's log says why."
)


@router.get("/datasets")
def datasets(settings: Settings = Depends(get_settings)) -> Datasets:
    """Every published package, or ``not_yet_computed`` when no index is named.

    Raises:
        PublicError: ``server_error`` when the named index cannot be read or is
            not an ``evidence-index-1`` index.
    """
    if not settings.evidence_index:
        return NotYetComputed(
            reason="No evidence package has been published yet.",
            available_from_stage=DATASETS_STAGE,
        )
    return _index(settings.evidence_index)


@router.get("/datasets/{name}/{file}")
def dataset_file(
    name: Annotated[str, PathParam(pattern=r"^[0-9a-f]{12}$")],
    file: Annotated[str, PathParam(pattern=r"^[a-z0-9_]+\.[a-z]+$")],
    settings: Settings = Depends(get_settings),
) -> Response:
    """One file of one published package, as an attachment.

    Raises:
        PublicError: ``not_found`` for a package or a file the index does not
            list; ``server_error`` when the index is refused, or the file no
            longer has the digest the index states.
    """
    if not settings.evidence_index:
        raise PublicError(NOT_FOUND, "No evidence package has been published yet.")
    package = _index(settings.evidence_index).package(name)
    listed = None if package is None else package.file(file)
    if package is None or listed is None:
        raise PublicError(NOT_FOUND, f"No published package lists {name}/{file}.")
    path = Path(settings.evidence_index).parent / name / listed.name
    try:
        data = path.read_bytes()
    except OSError:
        _log.warning("published package file unreadable: %s", path, exc_info=True)
        raise PublicError(SERVER_ERROR, _damaged(name, file)) from None
    if len(data) != listed.size_bytes or hashlib.sha256(data).hexdigest() != (
        listed.sha256
    ):
        _log.warning("published package file does not match its index: %s", path)
        raise PublicError(SERVER_ERROR, _damaged(name, file))
    return Response(
        content=data,
        media_type=MEDIA_TYPES.get(path.suffix, "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{name}-{file}"'},
    )


def _index(path: str) -> PublicDatasets:
    try:
        return PublicDatasets.model_validate_json(Path(path).read_bytes())
    except (OSError, ValidationError):
        _log.warning("evidence index refused", exc_info=True)
        raise PublicError(SERVER_ERROR, _REFUSED) from None


def _damaged(name: str, file: str) -> str:
    return (
        f"{name}/{file} does not match the evidence index; the platform's log says why."
    )
