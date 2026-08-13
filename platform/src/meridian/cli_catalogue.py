"""``meridian catalogue load`` — the operator's way to say what this deployment sees.

Reads one local catalogue document, opens a short-lived connection, and writes
the satellites, downlinks and element sets it describes. Parsing is
``meridian.catalogue_file``'s and the writing is ``meridian.store``'s; everything
here is argument handling, exit codes and rendering — the same division
``cli_passes`` uses.

Split out of ``meridian.cli`` for that module's sake: the command tree and its
parsers already sit close to the 400-line limit, and ``cli`` keeps the whole
command surface in one readable file by keeping none of the work.

Nothing here reaches a network. The catalogue is what an operator holds, which is
what lets a deployment schedule and receive with every external service down
(D-079).

Reference: docs/DECISIONS.md D-079; deploy/catalogue/README.md.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

from meridian.catalogue_file import (
    CatalogueDocument,
    MalformedCatalogueError,
    read_catalogue,
)
from meridian.config import load_settings
from meridian.store.element_sets import insert_element_set
from meridian.store.pool import CONNECT_TIMEOUT_S
from meridian.store.satellites import insert_satellite, insert_transmitter
from meridian.store.stations import Connection

__all__ = ["LoadTally", "load_document", "run_catalogue"]

EXIT_FAILED = 1
"""Matches ``meridian.cli.EXIT_FAILED``. Imported from there would be a cycle:
``cli`` imports this module to dispatch to it."""


@dataclass(frozen=True, slots=True)
class LoadTally:
    """How much of one document was new.

    Three counts rather than one total, because they answer different questions:
    a re-run writes nothing anywhere, a routine element-set refresh writes only
    the third, and a first load writes all three.
    """

    satellites_written: int
    transmitters_written: int
    element_sets_written: int

    @property
    def wrote_nothing(self) -> bool:
        """Whether the database already held every row in the document."""
        return not (
            self.satellites_written
            or self.transmitters_written
            or self.element_sets_written
        )


def load_document(conn: Connection, document: CatalogueDocument) -> LoadTally:
    """Write one parsed catalogue, skipping whatever is already held.

    Args:
        conn: An open connection. This function owns one transaction across the
            whole document.
        document: The rows to write, already validated.

    Returns:
        How many rows of each kind were new.

    Note:
        **One transaction for the whole file.** A document is one statement
        about what this deployment can see, and half of it is not a smaller
        version of it — a satellite written without its transmitter is an object
        that produces no passes, which looks exactly like a satellite nobody
        added.

        Satellites are written before the rows that reference them, which is the
        order :class:`~meridian.catalogue_file.CatalogueDocument` already holds
        them in, so the foreign keys are satisfied without this function
        knowing why.
    """
    with conn.transaction():
        satellites = sum(insert_satellite(conn, one) for one in document.satellites)
        transmitters = sum(
            insert_transmitter(conn, one) for one in document.transmitters
        )
        element_sets = sum(
            insert_element_set(conn, one) for one in document.element_sets
        )

    return LoadTally(
        satellites_written=satellites,
        transmitters_written=transmitters,
        element_sets_written=element_sets,
    )


def _print_tally(tally: LoadTally) -> None:
    """Write one load's outcome to stdout, in the order an operator reads it."""
    print(f"  satellites:   {tally.satellites_written} new")  # noqa: T201
    print(f"  transmitters: {tally.transmitters_written} new")  # noqa: T201
    print(f"  element sets: {tally.element_sets_written} new")  # noqa: T201
    if tally.wrote_nothing:
        # Said out loud because it is the expected result of a re-run and the
        # alarming result of a first load, and three zeroes look the same either
        # way.
        print("  nothing was new; the catalogue already held all of it")  # noqa: T201


def run_catalogue(args: argparse.Namespace) -> int:
    """Run ``meridian catalogue load``, the subcommand's only action.

    Args:
        args: The parsed command line, carrying ``file`` as typed.

    Returns:
        ``0`` on a completed load — including one that wrote nothing, which is
        the expected result of re-running an unchanged document.
        ``meridian.cli.EXIT_FAILED`` when the file cannot be read or parsed, or
        the database cannot be reached.
    """
    path = Path(args.file)
    try:
        document = read_catalogue(path)
    except MalformedCatalogueError as exc:
        print(f"meridian catalogue load: {exc}", file=sys.stderr)  # noqa: T201
        return EXIT_FAILED
    except OSError as exc:
        print(f"meridian catalogue load: {exc}", file=sys.stderr)  # noqa: T201
        return EXIT_FAILED

    settings = load_settings()
    try:
        conn = psycopg.connect(settings.psycopg_url, connect_timeout=CONNECT_TIMEOUT_S)
    except (psycopg.Error, OSError) as exc:
        print(  # noqa: T201 — this is a CLI; stderr is the interface
            f"meridian catalogue: cannot reach the database: {exc}", file=sys.stderr
        )
        return EXIT_FAILED

    with conn:
        tally = load_document(conn, document)

    print(f"Loaded {path}")  # noqa: T201
    _print_tally(tally)
    return 0
