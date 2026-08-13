"""Reading the local catalogue document: what this deployment can see.

Turns one JSON file into the three kinds of row `meridian catalogue load` writes
— satellites, their downlinks, and the element sets that make them predictable.
Parsing only: it opens no database and decides nothing about what is worth
tracking, which is why a malformed file can be caught in a unit test with no
infrastructure at all.

The document is a local file rather than a fetch from an element-set provider.
The standalone path comes first and external archives are enrichment on top of
it (`CLAUDE.md`, the independence test), so the catalogue a deployment runs on is
something an operator holds rather than something a third party has to be
reachable to supply.

Format, with every field shown::

    {
      "satellites": [
        {
          "satellite_id": "norad:57166",
          "name": "Meteor-M2-3",
          "orbital_regime": "leo",
          "transmitters": [
            {"centre_freq_hz": 137100000, "mode": "lrpt",
             "polarisation": "rhcp", "bandwidth_hz": 150000}
          ],
          "element_sets": [
            {"line1": "1 57166U ...", "line2": "2 57166 ..."}
          ]
        }
      ]
    }

``orbital_regime`` defaults to ``leo``; ``polarisation`` and ``bandwidth_hz`` are
optional. An element set carries no epoch — it is read out of ``line1``, because
the epoch is a property of the set rather than a claim the file gets to make.

Reference: docs/DECISIONS.md D-079; docs/DATA-MODEL.md.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from meridian.orbit.element_set_epoch import epoch_of
from meridian.store.element_sets import NewElementSet
from meridian.store.satellites import NewSatellite, NewTransmitter

__all__ = ["CatalogueDocument", "MalformedCatalogueError", "read_catalogue"]


class MalformedCatalogueError(ValueError):
    """The catalogue file is not the document this loader can read.

    Carries the path of the offending field — ``satellites[2].transmitters[0]``
    — because a catalogue is edited by hand and "expected int, got str" without
    a location is a message that sends someone hunting through a file.
    """


@dataclass(frozen=True, slots=True)
class CatalogueDocument:
    """One catalogue file, flattened into the rows it describes.

    Flat rather than nested, and in dependency order: satellites first, then the
    transmitters and element sets that reference them by foreign key. The loader
    then writes each list in turn without having to walk a tree while holding a
    transaction open.
    """

    satellites: tuple[NewSatellite, ...]
    transmitters: tuple[NewTransmitter, ...]
    element_sets: tuple[NewElementSet, ...]


def read_catalogue(path: Path) -> CatalogueDocument:
    """Read and validate one catalogue file.

    Args:
        path: The document to read, as UTF-8 JSON.

    Returns:
        Every row the file describes, in an order the loader can write directly.

    Raises:
        MalformedCatalogueError: The file is not JSON, is not shaped like a
            catalogue, or names a field with the wrong type. Reported with the
            path of the field rather than only its type.
        OSError: The file could not be read. Left uncaught: a missing catalogue
            is the operator's own argument being wrong, and the message the
            filesystem gives is already the right one.

    Note:
        **Nothing is checked against the database and nothing is checked for
        sense.** A satellite whose element set belongs to a different object,
        or a frequency no station can hear, is a valid document — the first is
        caught by propagation producing no passes and the second by capability
        matching producing no candidates. This function decides only whether the
        file can be read at all.
    """
    raw = _decode(path)
    entries = _require_list(raw.get("satellites"), "satellites")

    satellites: list[NewSatellite] = []
    transmitters: list[NewTransmitter] = []
    element_sets: list[NewElementSet] = []
    for index, entry in enumerate(entries):
        where = f"satellites[{index}]"
        satellite = _read_satellite(entry, where)
        satellites.append(satellite)
        transmitters.extend(_read_transmitters(entry, satellite.satellite_id, where))
        element_sets.extend(_read_element_sets(entry, satellite.satellite_id, where))

    return CatalogueDocument(
        satellites=tuple(satellites),
        transmitters=tuple(transmitters),
        element_sets=tuple(element_sets),
    )


def _decode(path: Path) -> Mapping[str, object]:
    """The file as a JSON object, or a refusal naming the file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise MalformedCatalogueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise MalformedCatalogueError(
            f"{path} is a {type(raw).__name__}, not a catalogue object"
        )
    return raw


def _read_satellite(entry: object, where: str) -> NewSatellite:
    """One satellite, without its children."""
    fields = _require_object(entry, where)
    return NewSatellite(
        satellite_id=_require_text(fields.get("satellite_id"), f"{where}.satellite_id"),
        name=_require_text(fields.get("name"), f"{where}.name"),
        orbital_regime=_optional_text(
            fields.get("orbital_regime"), f"{where}.orbital_regime"
        )
        or "leo",
    )


def _read_transmitters(
    entry: object, satellite_id: str, where: str
) -> list[NewTransmitter]:
    """Every downlink one satellite entry declares."""
    fields = _require_object(entry, where)
    listed = _require_list(fields.get("transmitters"), f"{where}.transmitters")
    return [
        _read_transmitter(one, satellite_id, f"{where}.transmitters[{index}]")
        for index, one in enumerate(listed)
    ]


def _read_transmitter(entry: object, satellite_id: str, where: str) -> NewTransmitter:
    """One downlink."""
    fields = _require_object(entry, where)
    return NewTransmitter(
        satellite_id=satellite_id,
        centre_freq_hz=_require_whole(
            fields.get("centre_freq_hz"), f"{where}.centre_freq_hz"
        ),
        mode=_require_text(fields.get("mode"), f"{where}.mode"),
        polarisation=_optional_text(
            fields.get("polarisation"), f"{where}.polarisation"
        ),
        bandwidth_hz=_optional_whole(
            fields.get("bandwidth_hz"), f"{where}.bandwidth_hz"
        ),
    )


def _read_element_sets(
    entry: object, satellite_id: str, where: str
) -> list[NewElementSet]:
    """Every element set one satellite entry carries."""
    fields = _require_object(entry, where)
    listed = _require_list(fields.get("element_sets"), f"{where}.element_sets")
    return [
        _read_element_set(one, satellite_id, f"{where}.element_sets[{index}]")
        for index, one in enumerate(listed)
    ]


def _read_element_set(entry: object, satellite_id: str, where: str) -> NewElementSet:
    """One element set, with its epoch read out of the lines rather than declared.

    ``source`` is fixed at ``manual`` — the file is what an operator put on
    disk, whatever they took it from, and claiming ``celestrak`` for a line
    somebody may have edited would put a provenance in the archive that nothing
    checked.
    """
    fields = _require_object(entry, where)
    line1 = _require_text(fields.get("line1"), f"{where}.line1")
    line2 = _require_text(fields.get("line2"), f"{where}.line2")
    try:
        epoch = epoch_of(line1, line2)
    except ValueError as exc:
        raise MalformedCatalogueError(f"{where} is not a readable element set") from exc
    return NewElementSet(
        satellite_id=satellite_id,
        epoch=epoch,
        line1=line1,
        line2=line2,
        source="manual",
    )


def _require_object(value: object, where: str) -> Mapping[str, object]:
    """``value`` as a JSON object, or a refusal naming ``where``."""
    if not isinstance(value, dict):
        raise MalformedCatalogueError(
            f"{where} is a {type(value).__name__}, not an object"
        )
    return value


def _require_list(value: object, where: str) -> Sequence[object]:
    """``value`` as a JSON array, or a refusal naming ``where``."""
    if not isinstance(value, list):
        raise MalformedCatalogueError(
            f"{where} is a {type(value).__name__}, not an array"
        )
    return value


def _require_text(value: object, where: str) -> str:
    """``value`` as a non-empty string, or a refusal naming ``where``."""
    if not isinstance(value, str) or not value.strip():
        raise MalformedCatalogueError(f"{where} is not a non-empty string: {value!r}")
    return value


def _optional_text(value: object, where: str) -> str | None:
    """:func:`_require_text`, but absent and ``null`` are both allowed."""
    if value is None:
        return None
    return _require_text(value, where)


def _require_whole(value: object, where: str) -> int:
    """``value`` as an integer, refusing the float a hertz value must never be."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise MalformedCatalogueError(f"{where} is not a whole number: {value!r}")
    return value


def _optional_whole(value: object, where: str) -> int | None:
    """:func:`_require_whole`, but absent and ``null`` are both allowed."""
    if value is None:
        return None
    return _require_whole(value, where)
