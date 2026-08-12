"""Pydantic models for MSP §4.4's ``observation`` request and its acknowledgement.

Parses and validates the wire message and translates it into
``meridian.observations.ingest.Submission``. Holds no business logic and touches
no database: whether the submission is a first report, a retry or a correction is
the ingest service's decision, and this module only decides whether the body is
a well-formed observation at all.

It does **not** carry ``satellite_id`` or ``simulated``. Neither is on the wire,
and neither would be believed if it were — both are derived from the platform's
own records (D-048).

The one validation rule that lives at the route instead is D-013's window on
``started_at``: it needs the platform clock, and a model that read one could not
be tested at a fixed instant (D-072).

Reference: docs/MSP-SPEC.md §4.4, §6; docs/DECISIONS.md D-010, D-018, D-029,
D-032, D-072.
"""

from __future__ import annotations

import json
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from meridian.observations.ingest import Submission
from meridian.store.observations import DopplerSample

__all__ = [
    "MAX_DOPPLER_SAMPLES",
    "ObservationAckBody",
    "ObservationRequestBody",
    "SignalBlock",
]

MAX_DOPPLER_SAMPLES = 512
"""MSP §6's cap, taken from the specification's table (D-032).

One sample every 1.75 seconds across a fifteen-minute pass — beyond what any
receiver in this project produces and beyond what is useful on a curve this
smooth. At roughly 50 bytes each the array is about 25 KiB, comfortably inside
the 256 KiB body the middleware already enforces.
"""

DETECTION_REQUIRED = ("decoded", "signal_no_decode")
"""Outcomes that assert a signal was there. Both are meaningless without one."""

DETECTION_FORBIDDEN = ("no_signal", "not_attempted")
"""Outcomes that assert the opposite.

``no_signal`` is a station that listened and heard nothing — data.
``not_attempted`` is a station that took the work and never began — an
operational failure. MSP §4.4 says the two must never be conflated, and neither
can carry a detection without contradicting itself.
"""


class DopplerSampleBlock(BaseModel):
    """One entry of MSP §4.4's ``doppler_samples`` array."""

    t: AwareDatetime
    offset_hz: int
    """Observed minus nominal, in whole hertz. Frequencies are never floats."""

    def to_doppler_sample(self) -> DopplerSample:
        """The store layer's shape for this sample."""
        return DopplerSample(sampled_at=self.t, offset_hz=self.offset_hz)


class SignalBlock(BaseModel):
    """MSP §4.4's ``signal`` block — what the receiver heard, if anything."""

    detected: bool

    first_detection_at: AwareDatetime | None = None
    """Present exactly when ``detected``, and the reason this block matters.

    Its difference from the predicted acquisition time, against element-set age,
    is this project's primary measurement of orbital data quality.
    """

    peak_snr_db: float | None = Field(default=None, allow_inf_nan=False)
    """Non-finite refused: JSON has no ``NaN`` literal, but a parser returns one
    for input like ``1e400``, and no such value belongs in a measurement."""

    doppler_samples: list[DopplerSampleBlock] | None = Field(
        default=None, max_length=MAX_DOPPLER_SAMPLES
    )
    """Optional, and ``null`` is not ``[]``.

    A station with no frequency reference sends nothing; one that measured and
    found nothing worth reporting sends an empty array. They are different
    claims and are stored as different values.
    """

    @model_validator(mode="after")
    def _detection_and_its_timestamp_agree(self) -> Self:
        """The pair the ``observation_detection_consistent`` CHECK enforces.

        Restated here because a constraint violation surfaces as a ``500``, and
        telling a station its own body was malformed is both true and more useful
        than telling it the platform broke.
        """
        if self.detected and self.first_detection_at is None:
            raise ValueError("signal.detected requires signal.first_detection_at")
        if not self.detected and self.first_detection_at is not None:
            raise ValueError("signal.first_detection_at requires signal.detected")
        return self


class ObservationRequestBody(BaseModel):
    """MSP §4.4's full ``observation`` payload."""

    assignment_id: str
    station_id: str

    # AwareDatetime throughout: a naive timestamp would be stored as though it
    # were UTC, and `started_at` is the hypertable's partitioning column, so a
    # station in another zone would place its chunk hours away from the pass.
    started_at: AwareDatetime
    ended_at: AwareDatetime

    outcome: Literal[
        "decoded", "signal_no_decode", "no_signal", "aborted", "not_attempted"
    ]
    """MSP §4.4's five values, exactly, and MSP is the authority (D-010)."""

    signal: SignalBlock | None = None
    """Absent means nothing was heard — the shape a ``not_attempted`` report takes."""

    products: list[dict[str, object]] = Field(default_factory=list)
    """Metadata only, stored verbatim (D-018, D-029).

    Deliberately unvalidated beyond "a list of objects". MSP §4.4 says a product
    carries ``kind``, ``uri``, ``sha256`` "and whatever else the product type
    warrants", so a schema invented before the first receiver exists would reject
    a station we have not built yet. Its size is bounded by the 256 KiB body cap
    the middleware applies ahead of parsing.
    """

    client_notes: str | None = None

    @model_validator(mode="after")
    def _the_products_are_strict_json(self) -> Self:
        """D-072's "no non-finite float anywhere", applied inside ``products``.

        The array is unvalidated by design, but "any object" still has to mean
        one the platform can write down. ``1e400`` parses to ``inf``, which has
        no JSON literal — so it survives to the digest, which refuses it, and to
        the ``jsonb`` cast, which refuses it too. Both refusals arrive after the
        body was accepted and surface as a ``500``, which tells a station the
        platform broke when in fact its own body did.

        Re-serialising is the check rather than a walk over the tree: it is the
        same rule, applied by the same function, that the two later steps apply.
        """
        try:
            json.dumps(self.products, allow_nan=False)
        except ValueError as exc:
            raise ValueError(f"products is not strict JSON: {exc}") from exc
        return self

    @model_validator(mode="after")
    def _the_window_runs_forwards(self) -> Self:
        """A pass that ended before it started is not a clock skew, it is a bug."""
        if self.started_at > self.ended_at:
            raise ValueError("started_at is after ended_at")
        return self

    @model_validator(mode="after")
    def _the_outcome_agrees_with_the_signal(self) -> Self:
        """D-072: an outcome that contradicts its own signal block is malformed.

        ``aborted`` is in neither list: a station can abort before it heard
        anything or part-way through a decode, and both are honest reports.
        """
        detected = self.signal is not None and self.signal.detected
        if self.outcome in DETECTION_REQUIRED and not detected:
            raise ValueError(f"outcome {self.outcome} requires signal.detected")
        if self.outcome in DETECTION_FORBIDDEN and detected:
            raise ValueError(f"outcome {self.outcome} cannot carry a detection")
        return self

    def to_submission(self) -> Submission:
        """The ingest service's shape for this body.

        Everything the station is trusted for, and nothing it is not: the
        service adds ``satellite_id`` and ``simulated`` from the platform's own
        records once it has locked the assignment.
        """
        signal = self.signal
        samples = None if signal is None else signal.doppler_samples
        return Submission(
            assignment_id=self.assignment_id,
            started_at=self.started_at,
            ended_at=self.ended_at,
            outcome=self.outcome,
            signal_detected=signal is not None and signal.detected,
            first_detection_at=None if signal is None else signal.first_detection_at,
            peak_snr_db=None if signal is None else signal.peak_snr_db,
            doppler_samples=(
                None
                if samples is None
                else tuple(one.to_doppler_sample() for one in samples)
            ),
            products=tuple(self.products),
            client_notes=self.client_notes,
        )


class ObservationAckBody(BaseModel):
    """MSP §4.4's acknowledgement — three flat fields, and no others."""

    observation_id: str
    assignment_id: str
    superseded: bool
