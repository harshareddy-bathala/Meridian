"""The plain words a report uses for each stored value.

One sentence per cause, per outcome and per verdict bucket, so every report
says the same thing in the same words, and a reviewer can read every sentence a
report can contain in this one file (D-303).

**"Undetermined" is said plainly.** It is the diagnosis's answer when the
evidence supports no cause, and a report says exactly that, with the causes
that were tested; it is never rounded up into the likeliest one (D-098).

Words are part of a template version. Changing a sentence here changes every
report rendered after it, so a change is a new template version, and the
pinned hashes in ``tests/unit/test_notification_templates.py`` fail until one
is made.

Standard library only.

Reference: docs/DECISIONS.md D-098, D-273, D-303.
"""

from __future__ import annotations

import unicodedata

__all__ = [
    "CAUSES",
    "OUTCOMES",
    "OUTCOME_NAMES",
    "UNSCORED",
    "VERDICT_BUCKETS",
    "cause_name",
    "clean",
    "verdict_bucket",
]

CAUSES = {
    "satellite_silent": (
        "The satellite was not transmitting: the catalogue marks it off, or "
        "other stations listening near the pass heard nothing either."
    ),
    "station_not_listening": (
        "The station was not confirmed listening: its heartbeats did not show "
        "it tuned to this satellite, on this frequency, for this pass."
    ),
    "obstruction": (
        "The signal was lost in directions where this station's own past "
        "receptions show an obstruction."
    ),
    "interference": (
        "The noise floor was raised above this station's own usual level at "
        "the same gain, which points to interference."
    ),
    "timing_fault": (
        "The station's clock, or the times it listened or recorded, were off "
        "by more than the stated timing uncertainty allows."
    ),
    "undetermined": "The cause could not be determined from the evidence.",
}
"""One sentence per Stage 27 cause (D-273)."""

_CAUSE_NAMES = {
    "satellite_silent": "satellite silent",
    "station_not_listening": "station not listening",
    "obstruction": "obstruction",
    "interference": "interference",
    "timing_fault": "timing fault",
    "undetermined": "undetermined",
}

OUTCOMES = {
    "decoded": "The pass was received and decoded.",
    "signal_no_decode": "A signal was heard, but nothing could be decoded.",
    "no_signal": "No signal was heard.",
    "aborted": "The station stopped the reception before the pass ended.",
    "not_attempted": "The station reported that it did not attempt the pass.",
    "no_report": "The station sent no report for this pass.",
}
"""One sentence per MSP outcome, and one for a pass with no report."""

OUTCOME_NAMES = {
    "decoded": "decoded",
    "signal_no_decode": "signal but no decode",
    "no_signal": "no signal",
    "aborted": "stopped early",
    "not_attempted": "not attempted",
    "no_report": "no report",
}

VERDICT_BUCKETS = (
    (0.75, "very likely usable"),
    (0.50, "likely usable"),
    (0.25, "doubtful"),
    (0.00, "unlikely to be usable"),
)
"""Fixed edges, highest first: a probability takes the first bucket it reaches."""

UNSCORED = "not yet scored"

NAME_LIMIT = 64
"""The most characters of a station or satellite name a report prints."""


def cause_name(cause: str) -> str:
    """A cause's short name, for lists."""
    return _CAUSE_NAMES.get(cause, clean(cause))


def verdict_bucket(probability_usable: float) -> str:
    """The words for a probability that a reception is usable."""
    for edge, words in VERDICT_BUCKETS:
        if probability_usable >= edge:
            return words
    return VERDICT_BUCKETS[-1][1]


def clean(text: str, limit: int = NAME_LIMIT) -> str:
    """A name fit to print: printable characters only, one line, capped.

    A station's name is the station's own words, sent at registration, and a
    report goes to a person. Control and format characters are dropped, so no
    name can add a line, reorder text or hide characters, and runs of space
    are one space.
    """
    kept = "".join(
        " " if character.isspace() else character
        for character in text
        if character.isspace() or unicodedata.category(character)[0] != "C"
    )
    folded = " ".join(kept.split())
    if len(folded) > limit:
        folded = folded[: limit - 1].rstrip() + "…"
    return folded or "(unnamed)"
