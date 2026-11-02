"""Writing the catalogue a deployment holds back out as a catalogue file.

``meridian catalogue export`` is the other half of ``load``: it writes the
satellites, downlinks and the element set current for each, in the format
:mod:`meridian.catalogue_file` reads. Its purpose is the independence test. A
machine that fetched current sets through ``meridian-ingest`` writes them to a
file, and a machine with no network brings a stack up from that file with
``meridian catalogue load`` (D-321).

**A set comes back as ``manual``.** The reader stamps every set it loads
``manual``, because a file is what an operator holds and a line in it may have
been edited (D-079). An exported set therefore records where it came from in
fields the reader ignores — ``exported_from``, with its source, epoch and when
it was retrieved — so a person reading the file can see its provenance and
nothing loaded from it can claim one.

**The file is not committed.** Its lines are another catalogue's data, which
the project does not republish (D-310, D-320), so the default place for it is
under ``data/``, which git ignores.

Reference: docs/DECISIONS.md D-079, D-320, D-321.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from meridian.store.catalogue_reads import (
    CatalogueSatellite,
    CatalogueTransmitter,
    find_catalogue_satellites,
    find_catalogue_transmitters,
)
from meridian.store.element_sets import (
    StoredElementSet,
    find_element_set_current_at,
)
from meridian.store.stations import Connection

__all__ = ["catalogue_document", "read_held_catalogue", "write_catalogue"]

Document = dict[str, object]


def read_held_catalogue(
    conn: Connection, at: datetime
) -> tuple[
    list[CatalogueSatellite],
    list[CatalogueTransmitter],
    dict[str, StoredElementSet],
]:
    """Everything an export writes, read in one place.

    Args:
        conn: An open connection. Read-only.
        at: The instant each satellite's element set must be current at.

    Returns:
        The satellites, their downlinks, and the set current at ``at`` for
        each satellite that has one.
    """
    satellites = find_catalogue_satellites(conn)
    transmitters = find_catalogue_transmitters(conn)
    sets = {}
    for satellite in satellites:
        current = find_element_set_current_at(conn, satellite.satellite_id, at)
        if current is not None:
            sets[satellite.satellite_id] = current
    return satellites, transmitters, sets


def catalogue_document(
    satellites: Sequence[CatalogueSatellite],
    transmitters: Sequence[CatalogueTransmitter],
    sets: Mapping[str, StoredElementSet],
) -> Document:
    """The catalogue file for these rows, as a JSON-ready mapping.

    Args:
        satellites: In the order to write them.
        transmitters: Grouped under their satellite, in the order given.
        sets: The one set to write per satellite; a satellite without one gets
            an empty list, which loads and propagates nothing.

    Returns:
        A document :func:`meridian.catalogue_file.read_catalogue` reads back
        into the same satellites, downlinks and lines.
    """
    entries = []
    for satellite in satellites:
        downlinks = [
            _transmitter(one)
            for one in transmitters
            if one.satellite_id == satellite.satellite_id
        ]
        held = sets.get(satellite.satellite_id)
        entries.append(
            {
                "satellite_id": satellite.satellite_id,
                "name": satellite.name,
                "orbital_regime": satellite.orbital_regime,
                "transmitters": downlinks,
                "element_sets": [] if held is None else [_element_set(held)],
            }
        )
    return {"satellites": entries}


def write_catalogue(path: Path, document: Document) -> None:
    """Write a document so a reader never sees half of it.

    Written beside the target and renamed into place, so a load started while
    an export runs reads the old file or the new one, never a truncated one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(f".{path.name}.partial")
    scratch.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    scratch.replace(path)


def _transmitter(one: CatalogueTransmitter) -> dict[str, object]:
    """One downlink, leaving out the optional fields the catalogue does not hold."""
    fields: dict[str, object] = {
        "centre_freq_hz": one.centre_freq_hz,
        "mode": one.mode,
    }
    optional = {
        "polarisation": one.polarisation,
        "bandwidth_hz": one.bandwidth_hz,
        "frame_interval_s": one.frame_interval_s,
    }
    fields.update({key: value for key, value in optional.items() if value is not None})
    return fields


def _element_set(held: StoredElementSet) -> dict[str, object]:
    """The two lines the reader loads, and their provenance, which it does not."""
    return {
        "line1": held.line1,
        "line2": held.line2,
        "exported_from": {
            "source": held.source,
            "epoch": held.epoch.isoformat(),
            "retrieved_at": held.retrieved_at.isoformat(),
        },
    }
