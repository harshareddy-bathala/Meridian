"""Two receivers that do not hear the sky: a simulated one, and a replayed file.

Both exist so the whole reception chain — capture folder, decoder subprocess,
outcome rules, queue — can run with no radio attached: in tests, on a simulated
station, and offline against a recording someone made earlier. Neither may ever
report for a station registered as measuring the real sky, and each declares
``hears_the_sky = False`` so the executor can refuse that pairing before any
pass (D-125, hard rule 5).

Reference: docs/DECISIONS.md D-056, D-122, D-123, D-125.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from meridian_client.reception.doppler_tolerance import doppler_tolerance_hz
from meridian_client.reception.protocols import (
    BYTES_PER_SAMPLE,
    CapturePlan,
    CaptureRefusedError,
    Recording,
    StationClocks,
    Tuning,
    first_sample_at,
)

__all__ = [
    "SIMULATED_RECORDING_NAME",
    "FileReplayReceiver",
    "RecordingSource",
    "SimulatedReceiver",
]

SIMULATED_RECORDING_NAME = "recording.u8"

_SILENCE = bytes([127, 127])
"""One ``u8`` sample at mid-scale in both I and Q: nothing, deterministically."""

_WRITE_CHUNK_SAMPLES = 32_768


class SimulatedReceiver:
    """Records silence for as long as it is asked to, on the injected clock.

    Args:
        clocks: Where "now" comes from. A test advances it; nothing here sleeps.
        sample_rate_hz: Deliberately low — a thousand samples a second writes
            about two megabytes for a widened pass, where a real SDR's two
            million would write gigabytes of silence to prove nothing.

    Note:
        The recording is written when capture stops, in one bounded pass of
        about two megabytes, so :meth:`start` and :meth:`alive` cost nothing.
        Its length follows the clock, which is what lets a test place a decoder's
        offsets on a known timeline.
    """

    hears_the_sky = False

    def __init__(self, clocks: StationClocks, *, sample_rate_hz: int = 1_000) -> None:
        """Build an idle receiver."""
        if sample_rate_hz <= 0:
            raise ValueError(f"sample_rate_hz must be positive, not {sample_rate_hz}")
        self._clocks = clocks
        self._sample_rate_hz = sample_rate_hz
        self._plan: CapturePlan | None = None
        self._started_at: datetime | None = None

    def start(self, plan: CapturePlan) -> Tuning:
        """Begin a capture. Refused only if one is already running."""
        if self._plan is not None:
            raise CaptureRefusedError(
                f"already capturing {self._plan.assignment_id}; one at a time"
            )
        plan.folder.mkdir(parents=True, exist_ok=True)
        self._plan = plan
        self._started_at = self._clocks.wall()
        return Tuning(
            plan.centre_freq_hz,
            self._sample_rate_hz,
            gain_db=None,
            recording_path=plan.folder / SIMULATED_RECORDING_NAME,
            sample_format="u8",
        )

    def alive(self) -> bool:
        """Whether a capture is running. A simulated receiver never dies."""
        return self._plan is not None

    def stop(self) -> Recording:
        """Write the silence the clock says was recorded, and describe it.

        Raises:
            RuntimeError: Nothing was started — a bug in the caller, not a
                reception outcome.
        """
        if self._plan is None or self._started_at is None:
            raise RuntimeError("stop() with no capture running")
        plan, started_at = self._plan, self._started_at
        self._plan, self._started_at = None, None

        stopped_at = max(self._clocks.wall(), started_at)
        elapsed_s = (stopped_at - started_at).total_seconds()
        sample_count = int(elapsed_s * self._sample_rate_hz)
        path = plan.folder / SIMULATED_RECORDING_NAME
        _write_silence(path, sample_count)
        return Recording(
            path=path,
            sample_rate_hz=self._sample_rate_hz,
            sample_format="u8",
            centre_freq_hz=plan.centre_freq_hz,
            sample_count=sample_count,
            first_sample_at=first_sample_at(
                stopped_at, sample_count, self._sample_rate_hz
            ),
            stopped_at=stopped_at,
            gain_db=None,
            interrupted=False,
            notes="simulated receiver, no antenna",
        )


@dataclass(frozen=True, slots=True)
class RecordingSource:
    """A recording an operator supplies for one assignment to be replayed from."""

    path: Path
    sample_rate_hz: int
    sample_format: str
    centre_freq_hz: int
    """The frequency the recording was tuned to, checked against the assignment's
    within D-056's tolerance."""

    gain_db: float | None = None

    def __post_init__(self) -> None:
        """Refuse a description no recording could match."""
        if self.sample_rate_hz <= 0:
            raise ValueError(f"sample_rate_hz must be positive: {self.sample_rate_hz}")
        if self.sample_format not in BYTES_PER_SAMPLE:
            raise ValueError(
                f"sample_format must be one of {tuple(BYTES_PER_SAMPLE)}, "
                f"not {self.sample_format!r}"
            )


class FileReplayReceiver:
    """Replays a recording from disk as if it had just been captured.

    Args:
        sources: The recording for each assignment id it may replay.
        clocks: Where "now" comes from.

    Note:
        **The file is referenced in place, never copied** (D-123): a pass is about
        a gigabyte, and the operator's file is the operator's.

        **The recording is placed on the capture's timeline**: its first sample
        is taken to be the instant :meth:`start` was called, and its length is
        the file's, however long the capture was held open. A replay has no
        sky-time of its own that could be honestly reported against a new
        assignment, and the result says it was a replay (D-125).
    """

    hears_the_sky = False

    def __init__(
        self, sources: Mapping[str, RecordingSource], clocks: StationClocks
    ) -> None:
        """Build an idle receiver over a fixed set of recordings."""
        self._sources = dict(sources)
        self._clocks = clocks
        self._source: RecordingSource | None = None
        self._started_at: datetime | None = None
        self._sample_count = 0

    def start(self, plan: CapturePlan) -> Tuning:
        """Check the recording can stand for this pass, then begin replaying it.

        Raises:
            CaptureRefusedError: A capture is already running; no recording is
                named for this assignment; the file is missing, empty or not a
                whole number of samples; or it was tuned outside D-056's
                tolerance of the assignment's frequency.
        """
        if self._source is not None:
            raise CaptureRefusedError("already replaying; one at a time")
        source = self._sources.get(plan.assignment_id)
        if source is None:
            raise CaptureRefusedError(
                f"no recording to replay for {plan.assignment_id}"
            )
        self._sample_count = _whole_samples(source)
        _require_same_target(source, plan)
        self._source = source
        self._started_at = self._clocks.wall()
        return Tuning(
            source.centre_freq_hz,
            source.sample_rate_hz,
            source.gain_db,
            recording_path=source.path,
            sample_format=source.sample_format,
        )

    def alive(self) -> bool:
        """Whether a replay is running. The file is already whole, so it cannot die."""
        return self._source is not None

    def stop(self) -> Recording:
        """Describe the replayed file as the recording this capture made.

        Raises:
            RuntimeError: Nothing was started.
        """
        if self._source is None or self._started_at is None:
            raise RuntimeError("stop() with no replay running")
        source, started_at = self._source, self._started_at
        self._source, self._started_at = None, None

        duration = timedelta(seconds=self._sample_count / source.sample_rate_hz)
        return Recording(
            path=source.path,
            sample_rate_hz=source.sample_rate_hz,
            sample_format=source.sample_format,
            centre_freq_hz=source.centre_freq_hz,
            sample_count=self._sample_count,
            first_sample_at=started_at,
            stopped_at=started_at + duration,
            gain_db=source.gain_db,
            interrupted=False,
            # The file's name, not its path: an operator's directory layout is
            # nothing an observation needs to carry.
            notes=f"replay of {source.path.name}, first sample placed at capture start",
        )


def _whole_samples(source: RecordingSource) -> int:
    """The recording's sample count, refusing a file that cannot be one."""
    if not source.path.is_file():
        raise CaptureRefusedError(f"recording {source.path.name} does not exist")
    size = source.path.stat().st_size
    per_sample = BYTES_PER_SAMPLE[source.sample_format]
    if size == 0 or size % per_sample != 0:
        raise CaptureRefusedError(
            f"recording {source.path.name} is {size} bytes, not a whole number "
            f"of {source.sample_format} samples"
        )
    return size // per_sample


def _require_same_target(source: RecordingSource, plan: CapturePlan) -> None:
    """Refuse a recording tuned to a different transmitter than the assignment's."""
    tolerance = doppler_tolerance_hz(plan.centre_freq_hz)
    if abs(source.centre_freq_hz - plan.centre_freq_hz) > tolerance:
        raise CaptureRefusedError(
            f"recording {source.path.name} is tuned to {source.centre_freq_hz} Hz, "
            f"outside ±{tolerance} Hz of the assignment's {plan.centre_freq_hz} Hz"
        )


def _write_silence(path: Path, sample_count: int) -> None:
    """Write ``sample_count`` mid-scale samples in bounded chunks."""
    remaining = sample_count
    with path.open("wb") as recording:
        while remaining > 0:
            chunk = min(remaining, _WRITE_CHUNK_SAMPLES)
            recording.write(_SILENCE * chunk)
            remaining -= chunk
