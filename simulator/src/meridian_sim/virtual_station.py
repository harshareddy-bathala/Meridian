"""Bringing one virtual station up: its files, its identity, and its loop.

Assembles a station out of the reference client — the transport, the held-work
record, the upload queue and :class:`~meridian_client.station_loop.StationLoop`
— with :class:`~meridian_sim.executor.SimulatedExecutor` in the one place a real
station puts a radio. Nothing here reimplements anything the client already
does; if it did, the simulator would be testing the platform against a second
client nobody runs.

**A station registers once.** On every start after the first it loads the
credentials it already has, which is what makes a restart cheap and what stops a
fleet burning a fresh invite every time the container comes back (D-080).

This is the first module in the simulator that touches a filesystem or a
network. Everything it decides — who the station is, what it will hear — was
decided by the pure modules beside it, which is why those can be checked without
either.

Reference: docs/MSP-SPEC.md §3, §4.1, §5; docs/DECISIONS.md D-023, D-075, D-080.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from meridian_client.credentials import (
    StationCredentials,
    load_credentials,
    save_credentials,
)
from meridian_client.held_assignments import AssignmentRecord
from meridian_client.observation_queue import ObservationQueue
from meridian_client.registration import register
from meridian_client.station_loop import StationLoop, TickOutcome
from meridian_client.transport import MspTransport
from meridian_sim.config import (
    RunConfig,
    profile_for_station,
    seed_for_station,
    state_dir_for_station,
)
from meridian_sim.executor import SimulatedExecutor
from meridian_sim.faults import FaultState

__all__ = [
    "RegistrationNeededError",
    "StationPaths",
    "VirtualStation",
    "paths_for",
    "register_or_resume",
]

_log = logging.getLogger(__name__)

CREDENTIALS_FILE = "credentials.json"
REGISTRATION_KEY_FILE = "registration.key"
HELD_ASSIGNMENTS_FILE = "held.json"
OUTBOX_DIRECTORY = "outbox"
"""The four things a station keeps between ticks and across reboots.

The same four a real station on a Pi keeps, in the same formats, written by the
same client code — which is what makes a restart here evidence about a restart
there rather than a rehearsal of one.
"""


class RegistrationNeededError(RuntimeError):
    """This station has never registered and no invite was offered.

    Separate from a refused invite, which the transport reports as
    ``invalid_invite``: that says the platform said no, and this says nobody
    asked it. An operator fixes them differently — the first by issuing a new
    invite, the second by giving the run the invites it already has.
    """


@dataclass(frozen=True, slots=True)
class StationPaths:
    """Where one virtual station keeps each of its four files."""

    directory: Path
    credentials: Path
    registration_key: Path
    held_assignments: Path
    outbox: Path


def paths_for(config: RunConfig, index: int) -> StationPaths:
    """Where station ``index`` keeps its state.

    Args:
        config: The run being executed.
        index: Which station, counting from one.

    Returns:
        The four paths, none of which is created here — whether a station has
        registered before is read from whether its credential file exists, and a
        function that answered by creating it could never say no.
    """
    directory = state_dir_for_station(config, index)
    return StationPaths(
        directory=directory,
        credentials=directory / CREDENTIALS_FILE,
        registration_key=directory / REGISTRATION_KEY_FILE,
        held_assignments=directory / HELD_ASSIGNMENTS_FILE,
        outbox=directory / OUTBOX_DIRECTORY,
    )


class VirtualStation:
    """One registered virtual station, ready to tick.

    Args:
        index: Which station, counting from one.
        credentials: Its identity on the network.
        loop: The reference client's loop, already assembled.
        transport: The connection the loop speaks over, owned here so that
            closing the station closes it.

    Note:
        Deliberately not a subclass or a wrapper of anything in the client. It
        holds a :class:`~meridian_client.station_loop.StationLoop` and forwards
        one call to it, so what a virtual station does on a tick is exactly what
        a real one does and there is nowhere for the two to drift apart.
    """

    def __init__(
        self,
        index: int,
        credentials: StationCredentials,
        loop: StationLoop,
        transport: MspTransport,
    ) -> None:
        """Hold a station's identity, its loop and the connection beneath it."""
        self.index = index
        self.station_id = credentials.station_id
        self.heartbeat_interval_s = credentials.heartbeat_interval_s
        self._loop = loop
        self._transport = transport

    def tick(self, now: datetime) -> TickOutcome:
        """Run one heartbeat cycle. The loop's own, unmodified."""
        return self._loop.tick(now)

    def close(self) -> None:
        """Release this station's connection pool."""
        self._transport.close()

    def __enter__(self) -> VirtualStation:
        """Enter a context that closes this station's connections on the way out."""
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Close, whether or not the body raised."""
        self.close()


def register_or_resume(
    config: RunConfig,
    index: int,
    invite_token: str | None = None,
    *,
    http_transport: httpx.BaseTransport | None = None,
    faults: FaultState | None = None,
) -> VirtualStation:
    """Bring station ``index`` up, registering it only if it has never registered.

    Args:
        config: The run being executed.
        index: Which station, counting from one.
        invite_token: A one-time invite, needed only on a station's first ever
            start. Ignored once credentials exist, which is what makes a restart
            free.
        http_transport: Where the bytes go. Defaults to a real network
            connection; a test supplies one reaching the platform in-process, so
            the registration under test is the one a station really performs.
        faults: What is currently broken, shared with whatever is writing it.
            The receiver reads it to decide whether it can begin a pass at all
            (:data:`~meridian_sim.faults.RECEIVER_DOWN`); every other fault is
            injected beneath the client, inside ``http_transport``. ``None`` is
            a station whose radio always works.

    Returns:
        A station whose identity is on disk and whose loop is ready to tick.

    Raises:
        RegistrationNeededError: The station has no credentials and no invite
            was offered.
        meridian_client.transport.ProtocolError: The platform refused the
            registration — ``invalid_invite`` covers a token that was never
            valid, one already consumed by another station, and a registration
            key that does not match a consumed one.
        httpx.HTTPError: The platform could not be reached.

    Note:
        **Registration and the running connection use different transports**,
        because the bearer token is a header fixed when a client is built and
        registration is the call that obtains it. The first is closed before the
        second is opened, so a station that fails part-way through leaves no
        socket behind.
    """
    paths = paths_for(config, index)
    credentials = load_credentials(paths.credentials)
    if credentials is None:
        credentials = _register(config, index, invite_token, http_transport)
        save_credentials(paths.credentials, credentials)
        _log.info("station %d registered as %s", index, credentials.station_id)
    else:
        _log.info("station %d resumed as %s", index, credentials.station_id)

    return _assemble(config, index, credentials, _Wiring(paths, http_transport, faults))


def _register(
    config: RunConfig,
    index: int,
    invite_token: str | None,
    http_transport: httpx.BaseTransport | None,
) -> StationCredentials:
    """Present this station to the platform for the first time.

    The registration key reaches disk before the request is sent — that ordering
    is the whole of D-023, and it is the client's, not ours: a crash between the
    platform's commit and the key being written would leave the invite consumed
    and nothing on disk able to prove the retry came from the same station.
    """
    if invite_token is None:
        raise RegistrationNeededError(
            f"station {index} has never registered and no invite was offered"
        )

    paths = paths_for(config, index)
    profile = profile_for_station(
        index, seed_for_station(config.master_seed, index), config.run_id
    )
    with MspTransport(
        config.base_url, http_transport=http_transport
    ) as unauthenticated:
        return register(
            unauthenticated,
            profile,
            invite_token=invite_token,
            registration_key_path=paths.registration_key,
        )


@dataclass(frozen=True, slots=True)
class _Wiring:
    """Where one station's files are, where its bytes go, and what is broken.

    Bundled so :func:`_assemble` stays inside CLAUDE.local.md §2's parameter
    limit, and because the three genuinely travel together: they are everything
    about a station that is not derived from its seed.
    """

    paths: StationPaths
    http_transport: httpx.BaseTransport | None
    faults: FaultState | None


def _assemble(
    config: RunConfig,
    index: int,
    credentials: StationCredentials,
    wiring: _Wiring,
) -> VirtualStation:
    """Build the loop a registered station runs.

    The fault state reaches the executor as well as the transport. Without that
    the receiver has no way to know its radio is down, and a station in that
    state reports ``no_signal`` — a measurement nothing measured — instead of
    the ``not_attempted`` MSP §4.4 requires.
    """
    transport = MspTransport(
        config.base_url,
        bearer_token=credentials.bearer_token,
        http_transport=wiring.http_transport,
    )
    loop = StationLoop(
        transport,
        credentials,
        AssignmentRecord(wiring.paths.held_assignments),
        SimulatedExecutor(seed_for_station(config.master_seed, index), wiring.faults),
        ObservationQueue(wiring.paths.outbox),
    )
    return VirtualStation(index, credentials, loop, transport)
