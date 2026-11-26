"""The ``[imagery]`` table of the settings file: areas, sources and place names.

Split from :mod:`meridian_ingest.config` as the places a source is asked about
are (:mod:`meridian_ingest.config_extent`): one table, its own rules, read by
one command. It cannot check its source ids against the imagery package's
list, because nothing outside that package may import it (D-371), so
``meridian-ingest imagery`` checks them when it runs.

An area's place names come from GeoNames when its box is at most 50 km on its
longer side and from Natural Earth above that (D-374). ``[imagery.areas."<id>"]
gazetteer = "<source id>"`` overrides the rule for one area: a long, thin area
may want a town's names although its box is long.

Reference: docs/DECISIONS.md D-220, D-326, D-372, D-374.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from meridian_ingest.adapters.public.gazetteers import GEONAMES, NATURAL_EARTH
from meridian_ingest.provenance import SOURCE_ID

__all__ = [
    "DEFAULT_IMAGERY_BUDGET",
    "DEFAULT_MAX_BYTES",
    "GAZETTEERS",
    "ImagerySettings",
    "ImagerySettingsError",
    "parse_imagery",
]

DEFAULT_IMAGERY_BUDGET = 200
"""Requests one imagery source may make in one run, across every published
area: a search, a token, and one per window read (D-372)."""

DEFAULT_MAX_BYTES = 5 * 1024**3
"""What the published scenes may hold together before the oldest go: 5 GiB.
Each area's latest scene from each class is kept whatever this says (D-375)."""

_IMAGERY_KEYS = frozenset(
    {"summary", "sources", "request_budget", "areas", "published", "max_bytes"}
)
_AREA_KEYS = frozenset({"gazetteer"})
GAZETTEERS = (GEONAMES.source_id, NATURAL_EARTH.source_id)
"""The sources an area's place names may come from."""


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

    gazetteers: Mapping[int, str] = field(default_factory=dict)
    """An area's chosen gazetteer, by area id, where it overrides the rule."""

    published: Path | None = None
    """The directory scenes are published into, ``<PUBLISHED_DIR>/imagery``,
    which the API reads its index from. Nothing is published until it is
    named (D-375)."""

    max_bytes: int = DEFAULT_MAX_BYTES


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
    summary = _path(value.get("summary"), "summary", found)
    published = _path(value.get("published"), "published", found)
    sources = value.get("sources", [])
    if not isinstance(sources, list) or not all(
        isinstance(one, str) and SOURCE_ID.fullmatch(one) for one in sources
    ):
        message = f"imagery.sources must be a list of source ids, not {sources!r}"
        raise ImagerySettingsError(message)
    return ImagerySettings(
        summary=summary,
        sources=tuple(sources),
        request_budget=_positive(
            value.get("request_budget", DEFAULT_IMAGERY_BUDGET), "request_budget"
        ),
        gazetteers=_gazetteers(value.get("areas", {}), found),
        published=published,
        max_bytes=_positive(value.get("max_bytes", DEFAULT_MAX_BYTES), "max_bytes"),
    )


def _path(value: object, key: str, found: Path) -> Path | None:
    """A path key, resolved against the settings file's directory."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        message = f"imagery.{key} must be a non-empty path, not {value!r}"
        raise ImagerySettingsError(message)
    return (found.parent / value).resolve()


def _gazetteers(value: object, found: Path) -> dict[int, str]:
    """``[imagery.areas."<id>"]`` tables, as each area's gazetteer."""
    if not isinstance(value, dict):
        message = f"imagery.areas must be a table, not {type(value).__name__}"
        raise ImagerySettingsError(message)
    chosen = {}
    for key, table in value.items():
        if not key.isdigit() or int(key) < 1:
            message = f'imagery.areas."{key}": an area is named by its id, like "3"'
            raise ImagerySettingsError(message)
        if not isinstance(table, dict) or not set(table) <= _AREA_KEYS:
            known = ", ".join(sorted(_AREA_KEYS))
            message = f'{found} [imagery.areas."{key}"]: the only key is {known}'
            raise ImagerySettingsError(message)
        gazetteer = table.get("gazetteer")
        if gazetteer is None:
            continue
        if gazetteer not in GAZETTEERS:
            message = (
                f'imagery.areas."{key}".gazetteer must be one of '
                f"{', '.join(GAZETTEERS)}, not {gazetteer!r}"
            )
            raise ImagerySettingsError(message)
        chosen[int(key)] = gazetteer
    return chosen


def _positive(value: object, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        message = f"imagery.{key} must be a whole number of at least 1, not {value!r}"
        raise ImagerySettingsError(message)
    return value
