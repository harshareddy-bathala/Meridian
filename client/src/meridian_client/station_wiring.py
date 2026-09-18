"""Building the loop a configured station runs.

:mod:`meridian_client.station_config` reads what a station is; this assembles it.
One place holds the wiring, so the station runner, the offline replay runner and
the tests all build the same object graph rather than three that drift.

**The station's own provenance decides what it may receive with.** The
``simulated`` flag comes from the credentials written at registration, and the
executor refuses a receiver that does not hear the sky for a station without it
(D-125, D-127). Nothing here can override that.

Reference: docs/DECISIONS.md D-120 to D-127.
"""

from __future__ import annotations

import httpx

from meridian_client.credentials import StationCredentials
from meridian_client.held_assignments import AssignmentRecord
from meridian_client.observation_queue import ObservationQueue
from meridian_client.reception.capture_folder import CaptureFolders
from meridian_client.reception.null_rotator import NullRotator
from meridian_client.reception.protocols import Receiver, StationClocks
from meridian_client.reception.reception_executor import (
    ReceptionExecutor,
    ReceptionSetup,
)
from meridian_client.reception.subprocess_decoder import SubprocessDecoder
from meridian_client.reception.synthetic_receivers import (
    FileReplayReceiver,
    SimulatedReceiver,
)
from meridian_client.station_config import StationConfig
from meridian_client.station_loop import StationLoop, retry_policy_attempts_for
from meridian_client.transport import MspTransport, RetryPolicy

__all__ = [
    "build_executor",
    "build_loop",
    "build_receiver",
    "build_setup",
    "open_transport",
]


def build_receiver(config: StationConfig, clocks: StationClocks) -> Receiver:
    """The receiver the configuration names.

    Args:
        config: This station's configuration.
        clocks: Where the receiver reads "now".

    Returns:
        A simulated receiver, or one replaying the recordings the configuration
        named. Neither hears the sky, and the executor is what refuses that
        pairing for a station that is not simulated (D-125).
    """
    if config.receiver.kind == "replay":
        return FileReplayReceiver(config.receiver.recordings, clocks)
    return SimulatedReceiver(clocks, sample_rate_hz=config.receiver.sample_rate_hz)


def build_setup(config: StationConfig, clocks: StationClocks) -> ReceptionSetup:
    """The receiver, decoder, rotator and capture folders one station receives with.

    Args:
        config: This station's configuration.
        clocks: The wall clock for manifests, and the monotonic one for the
            decoder's timeout.

    Returns:
        The setup, with the null rotator every fixed antenna has (D-126).
    """
    return ReceptionSetup(
        receiver=build_receiver(config, clocks),
        decoder=SubprocessDecoder(config.decoders, clocks),
        rotator=NullRotator(),
        folders=CaptureFolders(config.paths.captures),
        policy=config.policy,
        disk=config.disk,
        keep_recordings=config.keep_recordings,
    )


def build_executor(
    config: StationConfig, credentials: StationCredentials, clocks: StationClocks
) -> ReceptionExecutor:
    """The executor for a registered station, resuming whatever it left unfinished.

    Args:
        config: This station's configuration.
        credentials: What registration wrote down, including whether the
            platform records this station as simulated.
        clocks: The station's clocks.

    Raises:
        ValueError: The configured receiver does not hear the sky and this
            station did not register as simulated (D-125).
    """
    return ReceptionExecutor(
        build_setup(config, clocks), clocks, simulated_station=credentials.simulated
    )


def open_transport(
    config: StationConfig,
    credentials: StationCredentials,
    http_transport: httpx.BaseTransport | None = None,
) -> MspTransport:
    """An authenticated transport whose retries fit inside one heartbeat interval.

    Args:
        config: This station's configuration.
        credentials: Its identity and the cadence registration asked for.
        http_transport: Where the bytes go. A test supplies one that reaches the
            platform in-process.

    Returns:
        The transport. The caller closes it, or uses it as a context manager.
    """
    interval_s = float(credentials.heartbeat_interval_s)
    return MspTransport(
        config.base_url,
        bearer_token=credentials.bearer_token,
        retry=RetryPolicy(attempts=retry_policy_attempts_for(interval_s)),
        http_transport=http_transport,
    )


def build_loop(
    config: StationConfig,
    credentials: StationCredentials,
    clocks: StationClocks,
    transport: MspTransport,
) -> StationLoop:
    """The whole station: transport, held record, reception and upload queue.

    Args:
        config: This station's configuration.
        credentials: Its identity.
        clocks: Its clocks.
        transport: From :func:`open_transport`, so the caller owns closing it.

    Returns:
        A loop ready to tick.
    """
    return StationLoop(
        transport,
        credentials,
        AssignmentRecord(config.paths.held_assignments),
        build_executor(config, credentials, clocks),
        ObservationQueue(config.paths.outbox),
    )
