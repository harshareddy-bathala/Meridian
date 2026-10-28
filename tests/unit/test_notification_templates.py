"""Owner-report templates: the same facts give the same text, in plain words.

Marked as a unit test by living in ``tests/unit``. Templates are pure
functions, so everything about the text is checked here without a database:
- the same facts and version give an identical hash, and each released version
  is pinned to the hash it gave when it was released (D-303);
- an undetermined cause is said to be undetermined, with what was tested;
- a simulated station's report says so in its subject and first line;
- a decoded pass with no verdict says it is not yet scored;
- a station's own name cannot add a line or hide characters.

Reference: docs/DECISIONS.md D-098, D-302, D-303.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from meridian.notifications.facts import (
    CauseTested,
    ChainWarningFact,
    DiagnosisFact,
    PassFacts,
    VerdictFact,
    WeeklyFacts,
    facts_json,
    inputs_sha256,
)
from meridian.notifications.templates import (
    CURRENT,
    SIMULATED_LINE,
    TEMPLATES,
    content_sha256,
    render,
)
from meridian.notifications.wording import CAUSES, clean, verdict_bucket

START = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
MONDAY = datetime(2026, 10, 12, tzinfo=UTC)

DECODED = PassFacts(
    station_id="st_001",
    station_name="Station 001",
    simulated=False,
    satellite_id="norad:57166",
    satellite_name="METEOR-M2 3",
    window_start=START,
    window_end=START + timedelta(minutes=11),
    max_elevation_deg=61.4,
    outcome="decoded",
    peak_snr_db=11.4,
    frames_decoded=412,
    verdict=VerdictFact(
        probability_usable=0.82, partial_below=0.4, method="verdict-1:abcdefabcdef"
    ),
    diagnosis=None,
)

TESTED = (
    CauseTested("station_not_listening", fired=False),
    CauseTested("satellite_silent", fired=False),
    CauseTested("obstruction", fired=False),
    CauseTested("interference", fired=False),
    CauseTested("timing_fault", fired=False),
)

LOST = replace(
    DECODED,
    outcome="signal_no_decode",
    frames_decoded=None,
    peak_snr_db=4.2,
    verdict=None,
    diagnosis=DiagnosisFact(cause="undetermined", tested=TESTED, method="diagnosis-1"),
)

WEEK = WeeklyFacts(
    station_id="st_001",
    station_name="Station 001",
    simulated=False,
    period_start=MONDAY,
    period_end=MONDAY + timedelta(days=7),
    hours_heard=160,
    hours=168,
    passes=42,
    outcomes=(
        ("decoded", 30),
        ("signal_no_decode", 4),
        ("no_signal", 3),
        ("no_report", 5),
    ),
    verdicts=(("very likely usable", 20), ("likely usable", 10)),
    causes=(("obstruction", 3), ("undetermined", 2)),
    warnings=(
        ChainWarningFact(
            band="vhf",
            freq_min_hz=137_000_000,
            freq_max_hz=138_000_000,
            raised_at=datetime(2026, 10, 15, 6, 0, tzinfo=UTC),
            shortfall_db=4.2,
        ),
    ),
)

PINNED = {
    ("pass-1", "decoded"): (
        "d2cffa1de3abdb25e57bbe65fe2d4e2f31da4fcac08fac4b8dde2d729bb7cfd1"
    ),
    ("pass-1", "lost"): (
        "86f4cce1981eb08766413943345e48b0611fede4b7aecb11918827317d750b25"
    ),
    ("weekly-1", "week"): (
        "308d5882540d8f0b71ba095007af34151189b0f83fda700edc12142e601bd212"
    ),
}
"""Each released version's hash on the facts above. A failure here means a
released template changed: add a new version instead (D-303)."""

FIXTURES = {"decoded": DECODED, "lost": LOST, "week": WEEK}


@pytest.mark.parametrize(("version", "fixture"), sorted(PINNED))
def test_a_released_template_still_renders_what_it_did(
    version: str, fixture: str
) -> None:
    rendered = render(version, FIXTURES[fixture])

    assert content_sha256(rendered).hex() == PINNED[(version, fixture)]


def test_every_released_version_is_pinned() -> None:
    assert {version for version, _ in PINNED} == set(TEMPLATES)
    assert set(CURRENT.values()) <= set(TEMPLATES)


@pytest.mark.parametrize("facts", [DECODED, LOST, WEEK], ids=["pass", "lost", "week"])
def test_the_same_facts_give_the_same_text_and_hashes(facts: object) -> None:
    kind = "pass" if isinstance(facts, PassFacts) else "weekly"
    version = CURRENT[kind]

    first = render(version, facts)  # type: ignore[arg-type]
    again = render(version, replace(facts))  # type: ignore[type-var]

    assert first == again
    assert content_sha256(first) == content_sha256(again)
    assert inputs_sha256(facts) == inputs_sha256(replace(facts))  # type: ignore[arg-type, type-var]


def test_the_same_instant_in_another_zone_is_the_same_fact() -> None:
    elsewhere = replace(
        DECODED, window_start=START.astimezone(timezone(timedelta(hours=5, minutes=30)))
    )

    assert inputs_sha256(elsewhere) == inputs_sha256(DECODED)
    assert render("pass-1", elsewhere) == render("pass-1", DECODED)


def test_a_changed_fact_changes_both_hashes() -> None:
    changed = replace(DECODED, frames_decoded=411)

    assert inputs_sha256(changed) != inputs_sha256(DECODED)
    assert content_sha256(render("pass-1", changed)) != content_sha256(
        render("pass-1", DECODED)
    )


def test_an_instant_without_a_zone_is_refused() -> None:
    with pytest.raises(ValueError, match="zone"):
        facts_json(replace(DECODED, window_start=START.replace(tzinfo=None)))


def test_an_undetermined_cause_is_said_to_be_undetermined() -> None:
    body = render("pass-1", LOST).body

    assert f"Cause of the loss: {CAUSES['undetermined']}" in body
    assert (
        "Also tested, and not shown by the evidence: station not listening,"
        " satellite silent, obstruction, interference, timing fault." in body
    )
    assert "undetermined" in render("pass-1", LOST).subject


def test_a_named_cause_is_given_with_what_else_was_shown() -> None:
    tested = (
        CauseTested("obstruction", fired=True),
        CauseTested("interference", fired=True),
        CauseTested("timing_fault", fired=False),
    )
    obstructed = replace(
        LOST, diagnosis=DiagnosisFact("obstruction", tested, "diagnosis-1")
    )

    body = render("pass-1", obstructed).body

    assert f"Cause of the loss: {CAUSES['obstruction']}" in body
    assert "Also shown, less strongly: interference." in body
    assert "Also tested, and not shown by the evidence: timing fault." in body


def test_a_pass_with_no_report_says_so() -> None:
    silent = replace(LOST, outcome=None, peak_snr_db=None)

    assert "The station sent no report for this pass." in render("pass-1", silent).body


@pytest.mark.parametrize("facts", [DECODED, WEEK], ids=["pass", "week"])
def test_a_simulated_report_says_so_first(facts: object) -> None:
    kind = "pass" if isinstance(facts, PassFacts) else "weekly"
    rendered = render(CURRENT[kind], replace(facts, simulated=True))  # type: ignore[type-var]

    assert rendered.body.splitlines()[0] == SIMULATED_LINE
    assert rendered.subject.startswith("[SIMULATED] ")


@pytest.mark.parametrize("facts", [DECODED, WEEK], ids=["pass", "week"])
def test_a_measured_report_never_says_simulated(facts: object) -> None:
    kind = "pass" if isinstance(facts, PassFacts) else "weekly"
    rendered = render(CURRENT[kind], facts)  # type: ignore[arg-type]

    assert "SIMULATED" not in rendered.subject + rendered.body


def test_a_decoded_pass_with_no_verdict_is_not_yet_scored() -> None:
    body = render("pass-1", replace(DECODED, verdict=None)).body

    assert "Verdict: not yet scored." in body


def test_a_verdict_below_the_partial_threshold_says_partial() -> None:
    low = replace(
        DECODED,
        verdict=VerdictFact(0.31, 0.4, "verdict-1:abcdefabcdef"),
    )

    body = render("pass-1", low).body

    assert "0.31 probability the reception is usable, doubtful." in body
    assert "below the partial threshold of 0.40" in body


@pytest.mark.parametrize(
    ("probability", "words"),
    [
        (1.0, "very likely usable"),
        (0.75, "very likely usable"),
        (0.7499, "likely usable"),
        (0.5, "likely usable"),
        (0.25, "doubtful"),
        (0.0, "unlikely to be usable"),
    ],
)
def test_a_verdict_bucket_has_fixed_edges(probability: float, words: str) -> None:
    assert verdict_bucket(probability) == words


def test_a_week_with_no_passes_and_no_warning_says_so() -> None:
    quiet = replace(WEEK, passes=0, outcomes=(), verdicts=(), causes=(), warnings=())

    body = render("weekly-1", quiet).body

    assert "Passes: none were held to the end of their window." in body
    assert "Receive chains: no warning open at the end of the week." in body


def test_a_week_names_its_open_warning() -> None:
    body = render("weekly-1", WEEK).body

    assert "the vhf chain (137.000-138.000 MHz)" in body
    assert "since 2026-10-15 06:00 UTC" in body
    assert "4.2 dB short" in body


@pytest.mark.parametrize(
    ("name", "printed"),
    [
        ("Station 001\nSubject: you won", "Station 001 Subject: you won"),
        ("Roof\u202e1 00 noitatS", "Roof1 00 noitatS"),
        ("tab\there\x00", "tab here"),
        ("\u200b\u200b", "(unnamed)"),
        ("x" * 100, "x" * 63 + "…"),
    ],
    ids=["new-line", "bidi-override", "control", "invisible", "long"],
)
def test_a_station_name_is_cleaned_before_it_is_printed(
    name: str, printed: str
) -> None:
    rendered = render("pass-1", replace(DECODED, station_name=name))

    assert clean(name) == printed
    assert f"Station: {printed} (st_001)" in rendered.body
    assert "\n" not in rendered.subject


def test_a_template_renders_only_its_own_kind() -> None:
    with pytest.raises(TypeError):
        render("weekly-1", DECODED)
    with pytest.raises(KeyError):
        render("pass-0", DECODED)
