"""``meridian.api.public.models.satellites`` — the catalogue's response bodies.

Pure functions over a store row, so these run without a database. The one thing
the models compute is element-set age, and most of what is asserted here is that
it stays honest at the edges: absent, ahead of now, and long past.

Reference: docs/DECISIONS.md D-021, D-066.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from meridian.api.public.models.satellites import PublicSatellite, PublicTransmitter
from meridian.store.satellite_catalogue import (
    CataloguedSatellite,
    CataloguedTransmitter,
)

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


def satellite_row(**overrides: object) -> CataloguedSatellite:
    """A catalogued satellite, with fields replaced as a test needs."""
    fields: dict[str, object] = {
        "satellite_id": "norad:25544",
        "name": "ISS (ZARYA)",
        "orbital_regime": "leo",
        "priority": 1.0,
        "is_active": True,
        "latest_element_set_epoch": NOW - timedelta(hours=6),
    }
    fields.update(overrides)
    return CataloguedSatellite(**fields)  # type: ignore[arg-type]


def transmitter_row(**overrides: object) -> CataloguedTransmitter:
    """A catalogued transmitter, with fields replaced as a test needs."""
    fields: dict[str, object] = {
        "satellite_id": "norad:25544",
        "centre_freq_hz": 137_100_000,
        "mode": "lrpt",
        "polarisation": "rhcp",
        "bandwidth_hz": 120_000,
        "is_active": True,
        "source": "manual",
    }
    fields.update(overrides)
    return CataloguedTransmitter(**fields)  # type: ignore[arg-type]


def test_the_age_is_measured_from_the_epoch() -> None:
    """Six hours old is 21600 seconds, in the unit orbit.types already uses."""
    published = PublicSatellite.from_row(satellite_row(), now=NOW)

    assert published.element_set_age_s == 6 * 60 * 60


def test_no_element_set_gives_no_age() -> None:
    """A tracked object we cannot propagate is a gap, not an age of zero.

    Zero would sort as the freshest satellite in the catalogue, putting the one
    we know least about at the top of a list ordered by confidence.
    """
    published = PublicSatellite.from_row(
        satellite_row(latest_element_set_epoch=None), now=NOW
    )

    assert published.latest_element_set_epoch is None
    assert published.element_set_age_s is None


def test_an_epoch_ahead_of_now_gives_a_negative_age() -> None:
    """Not clamped, because a forward epoch is normal rather than an error.

    Element sets are routinely published describing a moment slightly in the
    future. Clamping to zero would report the freshest set the archive holds with
    the same number as one that arrived this second.
    """
    published = PublicSatellite.from_row(
        satellite_row(latest_element_set_epoch=NOW + timedelta(hours=3)), now=NOW
    )

    assert published.element_set_age_s == -3 * 60 * 60


def test_the_epoch_is_published_beside_the_age() -> None:
    """The age is what a reader wants; the epoch is what makes it checkable."""
    epoch = NOW - timedelta(days=9)

    published = PublicSatellite.from_row(
        satellite_row(latest_element_set_epoch=epoch), now=NOW
    )

    assert published.latest_element_set_epoch == epoch
    assert published.element_set_age_s == 9 * 24 * 60 * 60


def test_a_silent_satellite_says_so_rather_than_disappearing() -> None:
    """The flag is the whole reason the public read is not the scheduler's."""
    published = PublicSatellite.from_row(satellite_row(is_active=False), now=NOW)

    assert published.is_active is False


def test_the_operator_weighting_is_published() -> None:
    """Half the answer to why one pass was chosen over another."""
    published = PublicSatellite.from_row(satellite_row(priority=2.5), now=NOW)

    assert published.priority == 2.5


def test_a_transmitter_is_published_as_recorded() -> None:
    """Nothing about a downlink is derived, so nothing should change shape."""
    published = PublicTransmitter.from_row(transmitter_row())

    assert published.centre_freq_hz == 137_100_000
    assert published.bandwidth_hz == 120_000
    assert published.polarisation == "rhcp"
    assert published.source == "manual"


def test_an_unknown_polarisation_stays_null() -> None:
    """`null` is a real record. `linear` would be a fabricated measurement."""
    published = PublicTransmitter.from_row(
        transmitter_row(polarisation=None, bandwidth_hz=None)
    )

    assert published.polarisation is None
    assert published.bandwidth_hz is None


def test_a_switched_off_downlink_is_published_labelled() -> None:
    """A live spacecraft can have one transmitter off, and a reader needs both."""
    published = PublicTransmitter.from_row(transmitter_row(is_active=False))

    assert published.is_active is False


def test_a_simulator_entry_declares_where_it_came_from() -> None:
    """An invented downlink must not read as a fact about a real spacecraft."""
    published = PublicTransmitter.from_row(transmitter_row(source="simulator"))

    assert published.source == "simulator"


def test_no_satellite_response_claims_to_be_simulated_data() -> None:
    """The catalogue holds real objects, and neither table has the column.

    Asserted rather than assumed: adding a `simulated` field here later would be
    inventing a provenance claim the store cannot support, and this is the test
    that would fail when someone did.
    """
    assert "simulated" not in PublicSatellite.model_fields
    assert "simulated" not in PublicTransmitter.model_fields
