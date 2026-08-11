"""The ``register`` body this client builds, checked field by field.

``build_register_body`` is pure, so every case here is a profile written out by
hand and a dictionary compared against MSP §4.1. Whether the platform *accepts*
the body is a different question, asserted against the real model in
``tests/msp_conformance/test_client_platform_agreement.py``.

Marked as a unit test by living in ``tests/unit``: no network, no filesystem.

Reference: docs/MSP-SPEC.md §4.1, §5; docs/DECISIONS.md D-005, D-031.
"""

from __future__ import annotations

from meridian_client import __version__
from meridian_client.registration import (
    CLIENT_IMPLEMENTATION,
    ReceiveChain,
    StationProfile,
    build_register_body,
)

VHF_CHAIN = ReceiveChain(
    band="vhf",
    freq_min_hz=136_000_000,
    freq_max_hz=138_000_000,
    modes=("lrpt",),
    polarisation="rhcp",
    tracking=False,
    min_elevation_deg=10.0,
)
"""Station 001's fixed quadrifilar helix at 137 MHz — `tracking` is False
because the antenna does not move, which is the hardware PROJECT.md builds."""


def profile(**overrides: object) -> StationProfile:
    """A measured station in Bengaluru, with fields replaced as a test needs."""
    fields: dict[str, object] = {
        "name": "station-001",
        "operator": "meridian",
        "lat_deg": 12.9716,
        "lon_deg": 77.5946,
        "alt_m": 920.0,
        "capabilities": (VHF_CHAIN,),
    }
    fields.update(overrides)
    return StationProfile(**fields)  # type: ignore[arg-type]


def test_the_body_carries_every_field_msp_4_1_requires() -> None:
    """The ten top-level keys of §4.1's request, and no others."""
    body = build_register_body(profile(), "an-invite", "a-key")

    assert set(body) == {
        "invite_token",
        "registration_key",
        "name",
        "operator",
        "location",
        "simulated",
        "capabilities",
        "client",
    }


def test_location_uses_the_short_wire_names() -> None:
    """§4.1 names them `lat` and `lon`, not `lat_deg` and `lon_deg`.

    The units live in the Python names, per CLAUDE.local.md §4, and the wire uses
    the specification's spelling. Translating in one place is the point of having
    this function at all.
    """
    body = build_register_body(profile(), "an-invite", "a-key")

    assert body["location"] == {"lat": 12.9716, "lon": 77.5946, "alt_m": 920.0}


def test_altitude_travels_even_when_it_is_sea_level() -> None:
    """Zero is a measurement here, not a missing value.

    A station on the coast and a station that never reported its altitude are
    different, and only one of them should produce trustworthy pass timings.
    """
    body = build_register_body(profile(alt_m=0.0), "an-invite", "a-key")

    assert body["location"] == {"lat": 12.9716, "lon": 77.5946, "alt_m": 0.0}


def test_a_measured_station_omits_the_simulator_fields_entirely() -> None:
    """Not sent as null (MSP §5).

    `simulated: false` alongside a `seed` — even an empty one — invites a reader
    to wonder which of the two the platform believed. There is nothing to wonder
    about if the fields are absent.
    """
    body = build_register_body(profile(), "an-invite", "a-key")

    assert body["simulated"] is False
    assert "simulator_run_id" not in body
    assert "seed" not in body


def test_a_simulated_station_declares_its_run_and_seed() -> None:
    """CLAUDE.md rule 5 at the layer where the label originates.

    Registration is where a station's nature is established; the platform treats
    it as authoritative afterwards, so a simulated station that failed to say so
    here would launder simulated output into the measured results for good.
    """
    body = build_register_body(
        profile(simulated=True, simulator_run_id="run-1", seed=4471),
        "an-invite",
        "a-key",
    )

    assert body["simulated"] is True
    assert body["simulator_run_id"] == "run-1"
    assert body["seed"] == 4471


def test_capabilities_are_a_list_of_objects_with_modes_as_a_list() -> None:
    """Tuples are the client's internal shape; JSON has only arrays."""
    body = build_register_body(profile(), "an-invite", "a-key")

    assert body["capabilities"] == [
        {
            "band": "vhf",
            "freq_min_hz": 136_000_000,
            "freq_max_hz": 138_000_000,
            "modes": ["lrpt"],
            "polarisation": "rhcp",
            "tracking": False,
            "min_elevation_deg": 10.0,
        }
    ]


def test_a_declared_horizon_mask_uses_the_short_wire_names() -> None:
    """§4.1 spells them `az_deg` and `min_el_deg` (D-031)."""
    body = build_register_body(
        profile(horizon_mask=((0.0, 25.0), (180.0, 8.0))), "an-invite", "a-key"
    )

    assert body["horizon_mask"] == [
        {"az_deg": 0.0, "min_el_deg": 25.0},
        {"az_deg": 180.0, "min_el_deg": 8.0},
    ]


def test_a_station_with_no_declared_obstruction_omits_the_mask() -> None:
    """An empty mask and no mask are the same claim; only one needs sending."""
    assert "horizon_mask" not in build_register_body(profile(), "an-invite", "a-key")


def test_the_client_identifies_itself_and_its_version() -> None:
    """So a network running several implementations can tell whose bug it is."""
    body = build_register_body(profile(), "an-invite", "a-key")

    assert body["client"] == {"impl": CLIENT_IMPLEMENTATION, "version": __version__}


def test_the_invite_and_the_key_are_separate_fields() -> None:
    """One is consumed once; the other is this station's forever (D-023, D-034)."""
    body = build_register_body(profile(), "an-invite", "a-key")

    assert body["invite_token"] == "an-invite"
    assert body["registration_key"] == "a-key"
