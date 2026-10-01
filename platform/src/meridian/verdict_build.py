"""Writing the reception verdict: every closed reception, scored once per method.

Stage 26's runtime half. For each observation revision with no verdict by the
model's method (:func:`meridian.store.verdicts.unscored_receptions`), it:
1. builds the inputs, the same nine values the snapshot reader builds
   (:mod:`meridian.prediction.verdict_rows`);
2. scores them by the route their evidence allows;
3. appends the row.

The inputs come from:
- the observation's own evidence;
- frames expected from the pass and the transmitter's interval, by the one
  definition (D-250);
- listening from :meth:`Registry.was_listening`, the sole authority (rule 7),
  asked the question the snapshot export asks.

**Every reception, measured and simulated.** A simulated reception's verdict
is labelled simulated and is never a label or a training row (D-078). Stage 27
reads it to tell a partial reception from a decoded one on a simulated fleet.

**Idempotent.** A re-run finds nothing unscored and writes nothing. A new
model is a new method, so it scores everything again beside the old verdicts
(D-104, D-262).

Reference: docs/DECISIONS.md D-078, D-104, D-145, D-250, D-261, D-263.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from meridian.observations.frames_expected import frames_expected
from meridian.prediction.verdict_inputs import ReceptionInputs
from meridian.prediction.verdict_score import VerdictModel, score_reception
from meridian.registry import ListeningQuery
from meridian.store.stations import Connection
from meridian.store.verdicts import (
    NewVerdict,
    UnscoredReception,
    insert_verdict,
    unscored_receptions,
)

__all__ = ["Listening", "VerdictBuildReport", "apply_verdicts", "inputs_of"]


class Listening(Protocol):
    """What :func:`apply_verdicts` asks of the registry, and nothing else."""

    def was_listening(self, query: ListeningQuery) -> bool:
        """Whether the station was confirmed listening for this assignment."""
        ...


@dataclass(frozen=True, slots=True)
class VerdictBuildReport:
    """What one application of a verdict model did."""

    method: str
    scored: int
    written: int
    routes: dict[str, int] = field(default_factory=dict)
    simulated: int = 0


def inputs_of(reception: UnscoredReception, *, listening: bool) -> ReceptionInputs:
    """The verdict's inputs for one stored reception."""
    return ReceptionInputs(
        outcome=reception.outcome,
        signal_detected=reception.signal_detected,
        peak_snr_db=reception.peak_snr_db,
        frames_decoded=reception.frames_decoded,
        frames_expected=frames_expected(
            reception.aos, reception.los, reception.frame_interval_s
        ),
        decoder=reception.decoder,
        decoder_version=reception.decoder_version,
        listening_confirmed=listening,
        mode=reception.mode,
    )


def apply_verdicts(
    conn: Connection,
    registry: Listening,
    model: VerdictModel,
    *,
    now: datetime,
    limit: int | None = None,
) -> VerdictBuildReport:
    """Score every closed reception the model has not scored, and write each.

    Args:
        conn: An open connection; the caller commits.
        registry: Answers ``was_listening`` over the same connection.
        model: The fitted verdict model.
        now: Windows ending at or before this are closed.
        limit: At most this many receptions, the oldest; ``None`` for all.

    Returns:
        How many were scored and written, by route, and how many simulated.
    """
    pending = unscored_receptions(conn, method=model.method, now=now, limit=limit)
    routes: Counter[str] = Counter()
    written = simulated = 0
    for one in pending:
        listening = registry.was_listening(
            ListeningQuery(
                station_id=one.station_id,
                satellite_id=one.satellite_id,
                centre_freq_hz=one.centre_freq_hz,
                mode=one.mode,
                window=(one.start_at, one.end_at),
            )
        )
        verdict = score_reception(model, inputs_of(one, listening=listening))
        routes[verdict.route] += 1
        simulated += one.simulated
        written += insert_verdict(
            conn,
            NewVerdict(
                assignment_id=one.assignment_id,
                revision=one.revision,
                observation_started_at=one.started_at,
                station_id=one.station_id,
                probability_usable=verdict.probability_usable,
                method=verdict.method,
                route=verdict.route,
                inputs_sha256=verdict.inputs_sha256,
                partial_below=model.partial_below,
                simulated=one.simulated,
            ),
        )
    return VerdictBuildReport(
        method=model.method,
        scored=len(pending),
        written=written,
        routes=dict(sorted(routes.items())),
        simulated=simulated,
    )
