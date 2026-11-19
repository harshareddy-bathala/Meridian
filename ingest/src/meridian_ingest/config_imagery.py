"""The ``[imagery]`` table of the settings file: which areas, which sources.

Split from :mod:`meridian_ingest.config` as the places a source is asked about
are (:mod:`meridian_ingest.config_extent`): one table, its own rules, read by
one command. It cannot check its source ids against the imagery package's
list, because nothing outside that package may import it (D-371), so
``meridian-ingest imagery`` checks them when it runs.

Reference: docs/DECISIONS.md D-220, D-326, D-372.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from meridian_ingest.provenance import SOURCE_ID

__all__ = [
    "DEFAULT_IMAGERY_BUDGET",
    "ImagerySettings",
    "ImagerySettingsError",
    "parse_imagery",
]

DEFAULT_IMAGERY_BUDGET = 200
"""Requests one imagery source may make in one run, across every published
area: a search, a token, and one per window read (D-372)."""

_IMAGERY_KEYS = frozenset({"summary", "sources", "request_budget"})


class ImagerySettingsError(ValueError):
    """An ``[imagery]`` table that cannot be obeyed as written."""


@dataclass(frozen=True, slots=True)
class ImagerySettings:
    """What ``meridian-ingest imagery`` images, and from where (D-372).

    The imagery sources are not in the registry — they are never fetched by
    ``fetch`` and never loaded — so their ids are checked by the imagery
    command, against its own list, rather than here.
    """

    summary: Path | None = None
    """The published ``areas-summary-1`` file. Only an area in it is imaged,
    because only a published area may be shown (D-326)."""

    sources: tuple[str, ...] = ()
    """The imagery sources a run uses when none is named. Empty by default, as
    every real source is off until an operator turns it on (D-220)."""

    request_budget: int = DEFAULT_IMAGERY_BUDGET


def parse_imagery(value: object, found: Path) -> ImagerySettings:
    """The ``[imagery]`` table, its path resolved against the settings file.

    Raises:
        ImagerySettingsError: A key this version does not know, or a value
            it cannot obey. The caller turns it into a ``ConfigurationError``.
    """
    if not isinstance(value, dict):
        message = f"imagery must be a table, not {type(value).__name__}"
        raise ImagerySettingsError(message)
    unknown = sorted(set(value) - _IMAGERY_KEYS)
    if unknown:
        known = ", ".join(sorted(_IMAGERY_KEYS))
        message = f"{found} [imagery]: unknown key {unknown}; known: {known}"
        raise ImagerySettingsError(message)
    summary = value.get("summary")
    if summary is not None and (not isinstance(summary, str) or not summary.strip()):
        message = f"imagery.summary must be a non-empty path, not {summary!r}"
        raise ImagerySettingsError(message)
    sources = value.get("sources", [])
    if not isinstance(sources, list) or not all(
        isinstance(one, str) and SOURCE_ID.fullmatch(one) for one in sources
    ):
        message = f"imagery.sources must be a list of source ids, not {sources!r}"
        raise ImagerySettingsError(message)
    return ImagerySettings(
        summary=None if summary is None else (found.parent / summary).resolve(),
        sources=tuple(sources),
        request_budget=_budget(value.get("request_budget", DEFAULT_IMAGERY_BUDGET)),
    )


def _budget(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        message = (
            "imagery.request_budget must be a whole number of at least 1, "
            f"not {value!r}"
        )
        raise ImagerySettingsError(message)
    return value
