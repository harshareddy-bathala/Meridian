"""The network's weekly report: template ``network-weekly-1`` on fixed facts (D-367).

A released version is pinned by its hash on fixed facts, as the owner reports'
are (D-303), so a changed sentence fails here until it is a new version.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from meridian.notifications.network import (
    CURRENT_NETWORK,
    NETWORK_TEMPLATES,
    SIMULATED_HEADING,
    NetworkWeeklyFacts,
    PopulationWeek,
    network_inputs_sha256,
    render_network,
)
from meridian.notifications.templates import content_sha256
from meridian.notifications.wording import CLASS_NAMES
from meridian.reliability.classification import PASS_CLASSES

MONDAY = datetime(2026, 11, 2, tzinfo=UTC)

MEASURED = PopulationWeek(
    simulated=False,
    stations=1,
    stations_heard=1,
    passes=20,
    classes=(
        ("successful_reception", 17),
        ("confirmed_miss", 1),
        ("station_not_confirmed_listening", 1),
        ("satellite_silent", 1),
    ),
    captured=17,
    eligible=19,
    capture_low=0.6863,
    capture_high=0.9706,
    lost=2,
    allowed=1.9,
    causes=(("obstruction", 1), ("undetermined", 1)),
    warnings_open=1,
)

EMPTY = PopulationWeek(
    simulated=True,
    stations=0,
    stations_heard=0,
    passes=0,
    classes=(),
    captured=0,
    eligible=0,
    capture_low=None,
    capture_high=None,
    lost=0,
    allowed=0.0,
    causes=(),
    warnings_open=0,
)

WEEK = NetworkWeeklyFacts(
    period_start=MONDAY,
    period_end=MONDAY + timedelta(days=7),
    classification_method="classification-1",
    classification_sha256="ab" * 32,
    capture_target=0.9,
    measured=MEASURED,
    simulated=EMPTY,
)

SIMULATED = replace(
    WEEK,
    simulated=replace(MEASURED, simulated=True, stations=12, stations_heard=11),
)

PINNED = {
    ("network-weekly-1", "measured"): (
        "10dde2780d12a77999790d1a5754d123762726271ccdd3c8b5799f72cfeab129"
    ),
}
"""Each released version's content hash on fixed facts."""


def _body(facts: NetworkWeeklyFacts) -> str:
    return render_network(CURRENT_NETWORK, facts).body


def test_every_released_version_is_pinned() -> None:
    assert {version for version, _ in PINNED} == set(NETWORK_TEMPLATES)


@pytest.mark.parametrize(("version", "fixture"), sorted(PINNED))
def test_a_released_template_still_renders_what_it_did(
    version: str, fixture: str
) -> None:
    facts = {"measured": WEEK}[fixture]
    rendered = render_network(version, facts)
    assert content_sha256(rendered).hex() == PINNED[(version, fixture)]


def test_the_measured_week_reads_in_plain_words() -> None:
    body = _body(WEEK)
    assert "Stations: 1 registered, 1 heard from during the week." in body
    assert (
        "Passes settled: 20; decoded 17, missed while listening 1, not confirmed"
        " listening 1, satellite silent 1." in body
    )
    assert (
        "Capture rate: 17 of 19 passes a station could have captured, 89%, with a"
        " 95% interval of 69% to 97%." in body
    )
    assert (
        "Loss budget: 2 lost, against 1.9 the week could lose at a 90% target:"
        " over the budget." in body
    )
    assert "Losses by cause: obstruction 1, undetermined 1." in body
    assert "Receive chains: 1 warning open at the end of the week." in body


def test_no_simulated_station_means_no_simulated_section() -> None:
    assert SIMULATED_HEADING not in _body(WEEK)
    assert "simulated" not in _body(WEEK).lower()


def test_simulated_stations_have_their_own_section_saying_so() -> None:
    body = _body(SIMULATED)
    measured, simulated = body.split(SIMULATED_HEADING)
    assert "MEASURED STATIONS" in measured
    assert "Stations: 12 registered, 11 heard from during the week." in simulated
    assert "Stations: 12" not in measured


def test_no_measured_station_is_said_in_words() -> None:
    facts = replace(SIMULATED, measured=replace(EMPTY, simulated=False))
    assert "No measured station is registered." in _body(facts)


def test_a_week_with_no_settled_pass_says_so() -> None:
    quiet = replace(MEASURED, passes=0, classes=(), captured=0, eligible=0, lost=0)
    body = _body(replace(WEEK, measured=quiet))
    assert "Passes: none closed in the week and was settled." in body
    assert "Capture rate" not in body


def test_a_loss_not_yet_diagnosed_is_not_given_a_cause() -> None:
    body = _body(replace(WEEK, measured=replace(MEASURED, causes=())))
    assert "Losses by cause: none diagnosed yet." in body


def test_a_week_within_its_budget_says_so() -> None:
    body = _body(replace(WEEK, measured=replace(MEASURED, lost=1)))
    assert ": within the budget." in body


def test_a_pass_not_confirmed_listening_is_never_called_a_miss() -> None:
    """Rule 7: only a confirmed miss is a miss."""
    assert "miss" not in CLASS_NAMES["station_not_confirmed_listening"]
    assert [key for key, words in CLASS_NAMES.items() if "miss" in words] == [
        "confirmed_miss"
    ]


def test_every_pass_class_has_a_name() -> None:
    assert set(CLASS_NAMES) == set(PASS_CLASSES)


def test_the_same_instant_in_another_zone_is_the_same_fact() -> None:
    elsewhere = replace(
        WEEK,
        period_start=MONDAY.astimezone(timezone(timedelta(hours=5, minutes=30))),
    )
    assert network_inputs_sha256(elsewhere) == network_inputs_sha256(WEEK)


def test_a_changed_fact_changes_both_hashes() -> None:
    changed = replace(WEEK, measured=replace(MEASURED, warnings_open=0))
    assert network_inputs_sha256(changed) != network_inputs_sha256(WEEK)
    assert content_sha256(render_network(CURRENT_NETWORK, changed)) != (
        content_sha256(render_network(CURRENT_NETWORK, WEEK))
    )


def test_an_unknown_version_is_refused() -> None:
    with pytest.raises(KeyError):
        render_network("network-weekly-0", WEEK)
