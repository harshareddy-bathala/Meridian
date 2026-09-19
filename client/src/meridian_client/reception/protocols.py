"""What a receiver, a decoder and a rotator must do, and the values between them.

Narrower than :class:`~meridian_client.execution.PassExecutor`, and deliberately
so: an executor drives a receiver, a decoder and possibly a rotator, and each of
those is replaceable on its own (D-069, D-120). A physical SDR, a simulated one
and a recording replayed from disk are three receivers behind one protocol.

**No call blocks for the length of a pass.** The shape is checked against the
receiver this protocol has to admit later, an ``rtl_sdr`` subprocess writing a
file: :meth:`Receiver.start` launches it, :meth:`Receiver.alive` polls it, and
:meth:`Receiver.stop` interrupts it and waits a bounded few seconds.

**Nothing here transmits** (D-126). The receiver's methods start, poll and stop a
recording; the decoder's read one; the rotator's point and release an antenna.

Reference: docs/DECISIONS.md D-069, D-120, D-122, D-124, D-125, D-126.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from meridian_client.reception.decode_report import DecodeFailure, DecodeReport

__all__ = [
    "BYTES_PER_SAMPLE",
    "CapturePlan",
    "CaptureRefusedError",
    "DecodeJob",
    "DecodeRun",
    "Decoder",
    "Receiver",
    "Recording",
    "RotatorController",
    "StationClocks",
    "Tuning",
    "first_sample_at",
]

BYTES_PER_SAMPLE = {
    "u8": 2,
    "s8": 2,
    "s16": 4,
    "cf32": 8,
}
"""Bytes per complex sample for each recording format this layer accepts.

Every format is interleaved I and Q, so one sample is two numbers: ``u8`` is
``rtl_sdr``'s native output, ``cf32`` is what SatDump and GNU Radio write. A size
that is not a whole number of samples is a truncated or mislabelled file.
"""


class CaptureRefusedError(Exception):
    """A capture that could not start, with the reason an operator should read.

    Raised by :meth:`Receiver.start` and :meth:`RotatorController.prepare`. The
    executor records the reason and reports the pass ``not_attempted`` — the
    station took the work and could not begin it, which is an operational
    failure and never ``no_signal`` (MSP §4.4, D-122).
    """


@dataclass(frozen=True, slots=True)
class StationClocks:
    """The two clocks the reception layer reads, injected so tests need no waiting.

    ``wall`` stamps instants that are reported; ``monotonic`` measures durations
    such as a decoder's timeout, which an NTP step must not stretch or cut short.
    """

    wall: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    monotonic: Callable[[], float] = field(default=time.monotonic)


@dataclass(frozen=True, slots=True)
class CapturePlan:
    """One capture a receiver is asked to make."""

    assignment_id: str
    satellite_id: str
    centre_freq_hz: int
    mode: str
    opens_at: datetime
    closes_at: datetime
    folder: Path
    """The capture folder. A receiver that writes its own recording writes it
    here; one that replays a file leaves that file where it is (D-123)."""


@dataclass(frozen=True, slots=True)
class Tuning:
    """What a receiver actually set, which is what the heartbeat reports."""

    centre_freq_hz: int
    sample_rate_hz: int
    gain_db: float | None
    """``None`` under automatic gain, or when the receiver cannot say. Not zero:
    zero decibels is a setting (D-117)."""

    recording_path: Path
    """Where the samples are going. Written to the manifest when capture starts,
    so a restart after a power cut can find a recording that never reached
    :meth:`Receiver.stop` (D-123)."""

    sample_format: str
    """One of :data:`BYTES_PER_SAMPLE`'s keys."""


@dataclass(frozen=True, slots=True)
class Recording:
    """A finished capture, referenced where it lies rather than copied (D-123)."""

    path: Path
    sample_rate_hz: int
    sample_format: str
    """One of :data:`BYTES_PER_SAMPLE`'s keys."""

    centre_freq_hz: int
    sample_count: int
    first_sample_at: datetime
    """When the first sample was taken — the origin every decoder offset is added
    to (D-122). See :func:`first_sample_at`."""

    stopped_at: datetime
    gain_db: float | None
    interrupted: bool
    """``True`` when capture ended before it was asked to — a receiver that died,
    or a recording found half-written after a restart. Its pass is ``aborted``."""

    notes: str | None = None
    """What an observation's ``client_notes`` should say about this recording's
    origin — a replay names the file it replayed (D-125)."""


def first_sample_at(
    stopped_at: datetime, sample_count: int, sample_rate_hz: int
) -> datetime:
    """The instant of a recording's first sample, counted back from its last.

    Args:
        stopped_at: When capture stopped, from the station's wall clock.
        sample_count: Samples in the recording.
        sample_rate_hz: Samples per second.

    Returns:
        ``stopped_at`` less the recording's duration.

    Note:
        **Counted back from the stop, not forward from the launch** (D-122). A
        receiver process takes a variable fraction of a second to open the
        device and deliver its first sample, and an instant stamped at launch
        would bias every timing figure by that latency — an error the station's
        ``clock_uncertainty_s`` does not describe.
    """
    return stopped_at - timedelta(seconds=sample_count / sample_rate_hz)


class Receiver(Protocol):
    """Owns an SDR, or stands in for one, for one capture at a time."""

    @property
    def hears_the_sky(self) -> bool:
        """Whether this receiver's recordings come from an antenna.

        ``False`` for the simulated and replay receivers, and an executor built
        for a station registered as not simulated refuses such a receiver before
        any pass (D-125): its observations would be simulated results presented
        as measured.
        """
        ...

    def start(self, plan: CapturePlan) -> Tuning:
        """Begin recording, returning as soon as samples are flowing.

        Raises:
            CaptureRefusedError: The capture cannot begin.
        """
        ...

    def alive(self) -> bool:
        """Whether the capture started last is still recording. Never blocks."""
        ...

    def stop(self) -> Recording:
        """End the capture and describe what was recorded. Waits seconds at most."""
        ...


class RotatorController(Protocol):
    """Points an antenna for a capture, or does nothing for a fixed one (D-126)."""

    def prepare(self, plan: CapturePlan) -> None:
        """Make the antenna ready for ``plan``. Never blocks for a slew.

        Raises:
            CaptureRefusedError: The antenna cannot be made ready.
        """
        ...

    def release(self) -> None:
        """Let the antenna go once capture has stopped."""
        ...


@dataclass(frozen=True, slots=True)
class DecodeJob:
    """One recording to decode, and where its output belongs."""

    recording: Recording
    mode: str
    """The assignment's mode, which chooses the decoder (D-124)."""

    folder: Path
    """The capture folder. The decoder's output, report and logs go inside it."""


class DecodeRun(Protocol):
    """One decode in progress, polled rather than waited on."""

    def poll(self) -> DecodeReport | DecodeFailure | None:
        """``None`` while running; the outcome, the same on every call, after."""
        ...

    def cancel(self) -> None:
        """Stop the decode. Waits seconds at most."""
        ...


class Decoder(Protocol):
    """Turns a finished recording into a decode report (D-124)."""

    def supports(self, mode: str) -> bool:
        """Whether a decoder is configured for ``mode``.

        Asked before capture, so a pass nothing could decode is refused at
        ``begin`` and reported ``not_attempted`` rather than recorded for
        nothing (D-124).
        """
        ...

    def start(self, job: DecodeJob) -> DecodeRun:
        """Begin decoding, returning at once.

        A decoder that cannot even start returns a run whose first poll is the
        failure, so the executor has one path for every way a decode goes wrong.
        """
        ...
