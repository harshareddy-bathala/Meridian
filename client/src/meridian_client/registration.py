"""Admitting this station to the network, once, and surviving a lost response.

Builds MSP §4.1's ``register`` body from a station's own description, sends it,
and returns the credentials the platform minted. The registration key is written
to disk **before** the request leaves, which is what makes the whole operation
safe to retry: the platform recognises a repeat by that key and hands back the
same ``station_id`` with a fresh token, rather than consuming a second invite.

The wire shape lives here and the file handling lives in
:mod:`meridian_client.credentials`, so the body a station sends can be checked
against the specification without a filesystem.

Reference: docs/MSP-SPEC.md §3, §4.1, §5; docs/DECISIONS.md D-005 (``simulated``
is top-level), D-023 (the registration key), D-034 (a bound invite rotates).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from meridian_client import __version__
from meridian_client.credentials import StationCredentials, ensure_registration_key
from meridian_client.transport import MspTransport

__all__ = [
    "CLIENT_IMPLEMENTATION",
    "ReceiveChain",
    "StationProfile",
    "register",
]

CLIENT_IMPLEMENTATION = "meridian-reference"
"""What this client calls itself in MSP §4.1's ``client.impl``.

The platform records it so a network running several implementations can tell
whose bug a malformed message is. A fork that changes behaviour should change
this string; a fork that only changes deployment should not.
"""


@dataclass(frozen=True, slots=True)
class ReceiveChain:
    """One antenna and receiver chain, as MSP §4.1's capability object.

    Frequencies are integers in Hz and elevations are degrees above the local
    horizon, matching the wire and the platform's columns exactly — a client that
    converted units here would be the only place in the system that did.
    """

    band: str
    freq_min_hz: int
    freq_max_hz: int
    modes: tuple[str, ...]
    polarisation: str
    tracking: bool
    min_elevation_deg: float


@dataclass(frozen=True, slots=True)
class StationProfile:
    """Everything about this station that does not come from the platform.

    Supplied by whoever runs the station — from a config file, or constructed by
    the simulator for a virtual one — and unchanged by registration.
    """

    name: str
    operator: str

    lat_deg: float
    lon_deg: float
    alt_m: float
    """Altitude above the ellipsoid. Required, not optional: a pass at 10° is
    seconds different from a site 900 m up, and a defaulted zero would put that
    error into every prediction for the station without anybody choosing it."""

    capabilities: tuple[ReceiveChain, ...]

    simulated: bool = False
    simulator_run_id: str | None = None
    seed: int | None = None
    """Set together with ``simulated`` and never otherwise (MSP §5).

    A simulated station declares itself at registration and the platform treats
    that as authoritative from then on, so no later payload can launder simulated
    output into the measured results (CLAUDE.md rule 5, D-048).
    """

    horizon_mask: tuple[tuple[float, float], ...] = field(default_factory=tuple)
    """Declared obstructions as ``(azimuth_deg, min_elevation_deg)`` pairs.

    What the operator already knows is in the way — a building, a ridge. Kept
    distinct from the horizon profile the platform later learns from observations
    (D-031), because a declared mask is a claim and a learned one is evidence.
    """


def _capability_payload(chain: ReceiveChain) -> dict[str, object]:
    """One receive chain, in MSP §4.1's wire shape."""
    return {
        "band": chain.band,
        "freq_min_hz": chain.freq_min_hz,
        "freq_max_hz": chain.freq_max_hz,
        "modes": list(chain.modes),
        "polarisation": chain.polarisation,
        "tracking": chain.tracking,
        "min_elevation_deg": chain.min_elevation_deg,
    }


def build_register_body(
    profile: StationProfile, invite_token: str, registration_key: str
) -> dict[str, object]:
    """MSP §4.1's ``register`` request, as a JSON-serialisable object.

    Args:
        profile: The station's own description.
        invite_token: The one-time token the operator issued out of band, or the
            replacement invite bound to this station when rotating (D-034).
        registration_key: This station's durable key, already on disk.

    Returns:
        The request body. Separate from :func:`register` so the exact wire shape
        can be asserted against the specification without a platform to send it
        to.

    Note:
        ``simulator_run_id`` and ``seed`` are omitted rather than sent as
        ``null`` for a real station. MSP §5 pairs them with ``simulated``, and a
        measured station that transmitted the fields at all — even empty — would
        invite a reader to wonder which of the two the platform believed.
    """
    body: dict[str, object] = {
        "invite_token": invite_token,
        "registration_key": registration_key,
        "name": profile.name,
        "operator": profile.operator,
        "location": {
            "lat": profile.lat_deg,
            "lon": profile.lon_deg,
            "alt_m": profile.alt_m,
        },
        "simulated": profile.simulated,
        "capabilities": [_capability_payload(one) for one in profile.capabilities],
        "client": {"impl": CLIENT_IMPLEMENTATION, "version": __version__},
    }
    if profile.simulated:
        body["simulator_run_id"] = profile.simulator_run_id
        body["seed"] = profile.seed
    if profile.horizon_mask:
        body["horizon_mask"] = [
            {"az_deg": azimuth_deg, "min_el_deg": min_elevation_deg}
            for azimuth_deg, min_elevation_deg in profile.horizon_mask
        ]
    return body


def register(
    transport: MspTransport,
    profile: StationProfile,
    *,
    invite_token: str,
    registration_key_path: Path,
) -> StationCredentials:
    """Register this station, or recover the identity a lost response left behind.

    Args:
        transport: An unauthenticated transport — this is the call that obtains
            the token every later call needs.
        profile: The station's own description.
        invite_token: The operator's one-time invite.
        registration_key_path: Where this station's key lives. Read if it exists,
            generated and written if it does not.

    Returns:
        The credentials to persist. **Not written to disk here** — the caller
        chooses where an identity lives, and a function that both spoke to the
        network and wrote a credential file could not be tested without both.

    Raises:
        ProtocolError: The platform refused. ``invalid_invite`` covers a token
            that was never valid, one already consumed by a different station,
            and a key that does not match a consumed one — MSP §3 does not say
            which, so a station can only report that it was refused.
        httpx.HTTPError: The platform could not be reached.

    Note:
        **The key is on disk before the request is sent**, because
        :func:`ensure_registration_key` runs first and the order of these two
        statements is the whole of D-023. Reversed, a crash between the
        platform's commit and this station's write would leave the invite
        consumed, the station row created, and nothing on disk able to prove the
        retry came from the same station.
    """
    registration_key = ensure_registration_key(registration_key_path)
    body = build_register_body(profile, invite_token, registration_key)

    response = transport.post_json("/msp/v0/register", body)
    admitted = response.json()

    return StationCredentials(
        station_id=str(admitted["station_id"]),
        bearer_token=str(admitted["token"]),
        registration_key=registration_key,
        heartbeat_interval_s=int(admitted["heartbeat_interval_s"]),
    )
