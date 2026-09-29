"""``meridian-ingest load`` — the one verb that writes to the database.

Split from :mod:`meridian_ingest.cli` so ``follow`` can load after it fetches
without importing the command tree it is part of.

Reference: docs/DECISIONS.md D-140, D-141, D-225.
"""

from __future__ import annotations

from meridian.config import load_settings as load_platform_settings
from meridian.store.pool import DatabaseUnreachableError, connect_once
from meridian_ingest.config import IngestSettings
from meridian_ingest.console import refuse, say, warn
from meridian_ingest.load import load_source
from meridian_ingest.raw_store import RawStore

__all__ = ["run_load"]


def run_load(
    settings: IngestSettings, sources: tuple[str, ...], *, new_only: bool = False
) -> int:
    """Load every stored artefact of each source into the database.

    Args:
        settings: For the raw store's root.
        sources: Which sources, already resolved.
        new_only: Only artefacts not yet recorded; see ``load_source``.

    Returns:
        0, or 1 when the database cannot be reached.
    """
    store = RawStore(settings.raw_root)
    try:
        conn = connect_once(load_platform_settings())
    except DatabaseUnreachableError as exc:
        return refuse(f"meridian-ingest load: {exc}")
    # Autocommit, so each artefact's `conn.transaction()` in `load_artefact` is
    # a real transaction that commits when it closes. Without it the first
    # statement opens one run-wide transaction, every artefact becomes a
    # savepoint inside it, and a failure in the last artefact rolls back all
    # the ones that had finished — the opposite of "resumed by running again".
    conn.autocommit = True
    with conn:
        for source_id in sources:
            report = load_source(conn, store, source_id, new_only=new_only)
            say(
                f"{source_id}: {report.records_written} artefacts, "
                f"{report.stations_written} stations, "
                f"{report.receptions_written} receptions written"
            )
            if report.samples_written or report.samples_already_held:
                say(
                    f"    {report.samples_written} published values written, "
                    f"{report.samples_already_held} already held"
                )
            if report.receptions_already_held:
                say(
                    f"    {report.receptions_already_held} receptions were already "
                    "held, identically"
                )
            for skipped in report.skipped:
                say(f"    {skipped.raw_path}: {skipped.skipped}, nothing derived")
            for reverted in report.reverted:
                warn(
                    f"    {reverted.raw_path} matches record {reverted.record_id}, "
                    f"which record {reverted.reverted_past} superseded: the source "
                    "went back to an earlier version, and superseded_by still "
                    "names the later one (D-141)"
                )
            if report.wrote_nothing():
                say("    nothing new — this tree was already loaded")
    return 0
