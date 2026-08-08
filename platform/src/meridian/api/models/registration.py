"""Pydantic models for MSP §4.1's ``register`` message.

Parses and validates the wire request, and translates it into
``meridian.registry.RegistrationRequest`` — the reshaping from wire field
names (``location.lat``, ``client.impl``) into the domain shape's flat
fields (``lat_deg``, ``client_implementation``) lives here, on the boundary,
and nowhere past it. Wire names are fixed by MSP §4.1 and are carried as
Pydantic aliases, so the Python identifiers can obey CLAUDE.local.md §4's
rule that a name states its unit and spells out its words.

This module holds no business logic and touches no database — matching
``meridian.api.msp``'s own rule, extended to the models a route validates
against.

Reference: docs/MSP-SPEC.md §4.1, §5.
"""

from __future__ import annotations

import json
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from meridian.registry import RegistrationRequest
from meridian.store.stations import Capability

__all__ = [
    "CapabilityPayload",
    "ClientInfo",
    "HorizonMaskEntry",
    "Location",
    "RegisterRequestBody",
    "RegisterResponseBody",
]


class Location(BaseModel):
    """MSP §4.1's ``location`` object.

    Altitude is required, not defaulted — it materially affects pass
    geometry, and a silent default biases every prediction for a rooftop
    site.
    """

    # The Python names carry their unit (CLAUDE.local.md §4); the aliases are
    # the wire names MSP §4.1 fixes and cannot change. Validation accepts the
    # alias only — `populate_by_name` is deliberately not set, so a body sending
    # `lat_deg` is rejected rather than quietly admitted alongside `lat`.
    #
    # The upper bound on longitude is 180, not the 360 this field shipped with.
    # 360 is azimuth's range: under it 200 and -160 were two spellings of one
    # meridian, and a station could store either. ISO 6709, migration 0007 and
    # DATA-MODEL.md's conventions all say -180..180. See D-052.
    lat_deg: float = Field(alias="lat", ge=-90, le=90)
    lon_deg: float = Field(alias="lon", ge=-180, le=180)
    alt_m: float


class HorizonMaskEntry(BaseModel):
    """One entry of an optional, azimuth-resolved declared obstruction (D-031)."""

    azimuth_deg: float = Field(alias="az_deg")
    min_elevation_deg: float = Field(alias="min_el_deg")


class CapabilityPayload(BaseModel):
    """One MSP §4.1 capability.

    Ranges mirror ``station_capabilities``'s ``CHECK`` constraints, so a
    client mistake here becomes ``400 malformed`` rather than an unhandled
    ``psycopg.errors.CheckViolation`` reaching the generic 500 handler.
    """

    band: Literal["vhf", "uhf", "l", "s", "other"]
    freq_min_hz: int = Field(gt=0)
    freq_max_hz: int = Field(gt=0)
    modes: list[str]
    polarisation: Literal["rhcp", "lhcp", "linear_v", "linear_h", "linear", "none"]
    tracking: bool
    min_elevation_deg: float = Field(ge=-90, le=90)
    horizon_mask: list[HorizonMaskEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _frequency_range_is_ordered(self) -> Self:
        if self.freq_min_hz > self.freq_max_hz:
            raise ValueError("freq_min_hz must not exceed freq_max_hz")
        return self

    def to_capability(self) -> Capability:
        """The store layer's insertable shape.

        ``horizon_mask`` is pre-serialised to the JSON text
        ``station_capabilities.horizon_mask_json`` expects — shaping it is
        this boundary's job, not the store layer's.
        """
        # The dict keys are the *stored* shape D-031 fixes for
        # `station_capabilities.horizon_mask_json`, and stay as they are. Only
        # the Python attribute names spell themselves out.
        mask = [
            {"az_deg": entry.azimuth_deg, "min_el_deg": entry.min_elevation_deg}
            for entry in self.horizon_mask
        ]
        return Capability(
            band=self.band,
            freq_min_hz=self.freq_min_hz,
            freq_max_hz=self.freq_max_hz,
            modes=tuple(self.modes),
            polarisation=self.polarisation,
            tracking=self.tracking,
            min_elevation_deg=self.min_elevation_deg,
            horizon_mask_json=json.dumps(mask),
        )


class ClientInfo(BaseModel):
    """MSP §4.1's ``client`` object."""

    implementation: str = Field(alias="impl")
    version: str


class RegisterRequestBody(BaseModel):
    """MSP §4.1's full ``register`` payload."""

    invite_token: str
    registration_key: str
    name: str
    operator: str
    location: Location
    simulated: bool
    simulator_run_id: str | None = None
    seed: int | None = None
    capabilities: list[CapabilityPayload] = Field(min_length=1)
    client: ClientInfo

    @model_validator(mode="after")
    def _simulated_fields_are_consistent(self) -> Self:
        """Enforce MSP §5's simulated-field pairing.

        ``simulator_run_id`` and ``seed`` are required together with
        ``simulated: true`` and absent together with ``false`` — the same
        rule ``stations``' ``station_simulated_fields`` ``CHECK`` enforces
        at the DB layer, caught here first with a real message instead of
        a constraint name.
        """
        both_present = self.simulator_run_id is not None and self.seed is not None
        either_present = self.simulator_run_id is not None or self.seed is not None
        if self.simulated and not both_present:
            raise ValueError(
                "simulator_run_id and seed are required when simulated is true"
            )
        if not self.simulated and either_present:
            raise ValueError(
                "simulator_run_id and seed must be absent when simulated is false"
            )
        return self

    def to_registration_request(self) -> RegistrationRequest:
        """The domain shape ``PsycopgRegistry.register`` consumes."""
        return RegistrationRequest(
            invite_token=self.invite_token,
            registration_key=self.registration_key,
            name=self.name,
            operator=self.operator,
            lat_deg=self.location.lat_deg,
            lon_deg=self.location.lon_deg,
            alt_m=self.location.alt_m,
            simulated=self.simulated,
            simulator_run_id=self.simulator_run_id,
            seed=self.seed,
            capabilities=[
                capability.to_capability() for capability in self.capabilities
            ],
            client_implementation=self.client.implementation,
            client_version=self.client.version,
        )


class RegisterResponseBody(BaseModel):
    """MSP §4.1's ``register`` response — exactly these three fields."""

    station_id: str
    token: str
    heartbeat_interval_s: int
