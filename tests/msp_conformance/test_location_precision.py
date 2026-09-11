"""MSP §4.1's ``location_precision_decimals``, checked against the specification.

The field is new in MSP 0.2 and is the protocol's first additive change since the
0.1 freeze, so these tests are as much about §7's minor rule as about the field:
a 0.1 station omits it entirely and must still register.

Everything here is validation of the wire model, so there is no database and no
network — the platform's *behaviour* once the value is stored is asserted in
``tests/integration``. What this file pins is the contract a stranger writing a
station client reads §4.1 to find.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.

Reference: docs/MSP-SPEC.md §4.1, §7; docs/DECISIONS.md D-082.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from meridian.api.models.registration import RegisterRequestBody

VALID_BODY: dict[str, Any] = {
    "invite_token": "an-invite",
    "registration_key": "a-key",
    "name": "station-001",
    "operator": "meridian",
    "location": {"lat": 12.9716, "lon": 77.5946, "alt_m": 920.0},
    "simulated": False,
    "capabilities": [
        {
            "band": "vhf",
            "freq_min_hz": 136_000_000,
            "freq_max_hz": 138_000_000,
            "modes": ["lrpt"],
            "polarisation": "rhcp",
            "tracking": False,
            "min_elevation_deg": 10.0,
        }
    ],
    "client": {"impl": "meridian-reference", "version": "0.1.0"},
}
"""A §4.1 body with the field absent — which is exactly what a 0.1 station sends."""


def body_with(**overrides: Any) -> dict[str, Any]:
    """``VALID_BODY`` with top-level keys replaced or added."""
    return {**VALID_BODY, **overrides}


def test_a_station_that_never_heard_of_the_field_still_registers() -> None:
    """§7's minor rule, exercised for the first time.

    This is the case the whole additive-versioning promise exists for: a client
    built against 0.1 sends a 0.1 body, and the platform must take it. If this
    test ever fails, MSP 0.2 was a major change wearing a minor number.
    """
    parsed = RegisterRequestBody.model_validate(VALID_BODY)

    assert parsed.location_precision_decimals == 2


@pytest.mark.parametrize("decimals", [1, 2, 3, 4, 5, 6])
def test_every_value_the_specification_permits_is_accepted(decimals: int) -> None:
    """§4.1 fixes the range as 1 to 6 inclusive, so all six are tested."""
    parsed = RegisterRequestBody.model_validate(
        body_with(location_precision_decimals=decimals)
    )

    assert parsed.location_precision_decimals == decimals


@pytest.mark.parametrize("decimals", [0, -1, 7, 12])
def test_a_value_outside_the_range_is_refused(decimals: int) -> None:
    """Both ends are closed.

    Zero is refused rather than clamped because a whole degree is roughly 111 km
    and no longer places a station usefully; 7 is refused because it is finer
    than any coordinate an operator types, so accepting it would only invite the
    belief that some finer, unrounded setting exists.
    """
    with pytest.raises(ValidationError):
        RegisterRequestBody.model_validate(
            body_with(location_precision_decimals=decimals)
        )


@pytest.mark.parametrize("value", ["2", "approximate", "exact", 2.5, None, True])
def test_anything_that_is_not_an_integer_is_refused(value: Any) -> None:
    """§4.1 says a non-integer — a string included — is ``malformed``.

    ``"approximate"`` and ``"exact"`` are named here on purpose: they are the
    design D-082 rejected, and an implementation written against an early draft
    would send one of them. Refusing it produces a ``malformed`` naming the
    field, rather than a silent fallback to the default that would leave the
    operator believing they had asked for something.

    ``"2"`` is refused for the same reason the others are, which is why this
    field is strict where its neighbours are not: a client that guesses at the
    type should be corrected at registration, not coerced into working.
    """
    with pytest.raises(ValidationError):
        RegisterRequestBody.model_validate(body_with(location_precision_decimals=value))


def test_declaring_a_precision_leaves_the_coordinates_untouched() -> None:
    """The field governs publication only (§4.1), and this is where that starts.

    The single highest-risk error in this feature is a coarsened coordinate
    reaching pass geometry, where it would look like a scheduling bug months
    later. The domain request handed to the registry must carry every digit the
    station sent, whatever precision it asked to be published at.
    """
    parsed = RegisterRequestBody.model_validate(
        body_with(location_precision_decimals=1)
    )

    request = parsed.to_registration_request()

    assert request.lat_deg == 12.9716
    assert request.lon_deg == 77.5946
    assert request.location_precision_decimals == 1
