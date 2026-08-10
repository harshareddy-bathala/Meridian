"""The propagator never reaches the network, asserted rather than intended.

Meridian's independence test says no external service is a runtime dependency of
scheduling or reception: *if every external service went offline permanently
tomorrow, Meridian would still schedule, receive, decode, monitor and report
using our own station alone.* Propagation is the first place that could quietly
stop being true, because Skyfield's loader downloads data files when it is asked
to and the request would succeed on every developer machine and in CI.

It would then fail on a Pi in a college building on the one night the network
was down, during a pass that does not repeat — which is the failure this project
is least able to explain and least able to afford.

So the socket is taken away rather than trusted. Any attempt to open one raises,
and the test fails with the call site rather than with a timeout an hour later.

Reference: CLAUDE.md, "The independence test";
``meridian.orbit.skyfield_service``.
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta

import pytest

from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.orbit.types import ElementSet, GroundSite

ISS_LINE1 = "1 25544U 98067A   14020.93268519  .00009878  00000-0  18200-3 0  5082"
ISS_LINE2 = "2 25544  51.6498 109.4756 0003572  55.9686 274.8005 15.49815350868473"

SITE = GroundSite(lat_deg=12.9716, lon_deg=77.5946, alt_m=920.0)
INSTANT = datetime(2014, 1, 23, 11, 18, 7, tzinfo=UTC)


class NetworkUsedError(RuntimeError):
    """Raised in place of opening a socket, so the failure names what happened."""


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace socket construction and connection with something that raises.

    Autouse: every test in this file is about what happens without a network,
    and there is no case here that wants one.

    Both calls are patched. Blocking only ``socket.socket`` would miss a library
    that holds a socket from before the patch; blocking only
    ``create_connection`` would miss one that builds its own. Neither is likely,
    and the cost of covering both is two lines.
    """

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise NetworkUsedError("propagation attempted to use the network")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_constructing_the_service_touches_no_network() -> None:
    """The timescale is built from Skyfield's bundled leap-second table.

    This is the call that would download one. `load.timescale(builtin=True)` is
    written out explicitly in the service for this reason, and this test is what
    stops that becoming decorative if the library's default ever changes.
    """
    assert SkyfieldOrbitService() is not None


def test_propagating_a_pass_touches_no_network() -> None:
    """And neither does the work itself.

    Construction succeeding proves nothing on its own — a loader that fetched
    lazily, on the first propagation rather than at startup, would pass the test
    above and fail in the field.
    """
    service = SkyfieldOrbitService()
    element_set = ElementSet(
        satellite_id="norad:25544",
        epoch=datetime(2014, 1, 20, 22, 23, 4, tzinfo=UTC),
        line1=ISS_LINE1,
        line2=ISS_LINE2,
        source="manual",
    )

    angles = service.look_angles(
        element_set, SITE, INSTANT, INSTANT + timedelta(minutes=5), step_s=30.0
    )

    assert len(angles) == 10


def test_the_guard_itself_works() -> None:
    """A negative control.

    Without this, the two tests above would keep passing if the fixture stopped
    patching anything — and a test that cannot fail is worse than no test,
    because it reads as evidence.
    """
    with pytest.raises(NetworkUsedError):
        socket.create_connection(("example.invalid", 80))

    # Both patches, not just one. The fixture argues for covering `socket.socket`
    # as well, and a control that exercised only `create_connection` would let
    # that half be dropped without anything noticing.
    with pytest.raises(NetworkUsedError):
        socket.socket()
