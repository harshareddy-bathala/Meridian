"""The fault specification Stage 27 was built against, pinned byte for byte.

D-105 asks for the simulator's fault effects to be reviewed by someone other
than the diagnosis's author before the diagnosis is written. Stage 27 began
without that review (D-270), so the specification it was built against is held
here: the effect sections of ``docs/SCALE-AND-FAULTS.md`` and the simulator
modules that carry them out. Changing either fails this test until the pin is
updated, and **an update names D-270 in its commit**, so every change to the
answer key after the diagnoser exists is a visible diff rather than a quiet one.

The Review lines sit above the pinned text on purpose: a reviewer signing them
does not move the pin, and signs exactly what is pinned.

No marker: a filesystem and nothing else.

Reference: docs/DECISIONS.md D-105, D-253, D-270, D-277.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs" / "SCALE-AND-FAULTS.md"
SIMULATOR = ROOT / "simulator" / "src" / "meridian_sim"

SECTIONS = {
    "### What each fault does": (
        "704b5ab7d37aa243b4839e83d761279ed34b56a55feecf4754d0eccc292e2a4b"
    ),
    "### What the fault does": (
        "91eee1d652b4e3920dd55dbd7f21799652e3369a3e094100ebe6039eba75ad15"
    ),
}
"""Each pinned section, by the heading it starts at, to the next ``##``."""

MODULES = {
    "evidence.py": "a2d4f7d272496630f2239cba12d78f40b465ad5977c627f136b8dfdfb93b935f",
    "sky_faults.py": "851b1487ff42ca4a141ae846fc2a0a6ab0977d3917fb0a9180a13da16c22d11f",
    "sky_effects.py": (
        "ff5fee163ceb57a6313274d009db68560ea0b02ba3b5910e0343d393042504dc"
    ),
}
"""The simulator modules that carry the specification out."""


def section(text: str, heading: str) -> str:
    """The text from ``heading`` to the next top-level section, trimmed.

    Stops at the next line starting ``## `` and drops the ``---`` rule before
    it, so the text pinned is the section's own and not its neighbour's.
    """
    lines = text.splitlines()
    try:
        start = lines.index(heading)
    except ValueError:
        raise AssertionError(f"{heading!r} is not a heading of the spec") from None
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    body = "\n".join(lines[start:end]).rstrip()
    return body.removesuffix("---").rstrip() + "\n"


def digest(text: str) -> str:
    """SHA-256 of ``text`` as UTF-8, in hex."""
    return hashlib.sha256(text.encode()).hexdigest()


UPDATE = (
    "the fault specification Stage 27 was built against changed. If that was "
    "meant, update the pin and name D-270 in the commit, because the review "
    "D-105 owes reads exactly the pinned text"
)


@pytest.mark.parametrize("heading", sorted(SECTIONS))
def test_the_specified_effects_are_the_ones_pinned(heading: str) -> None:
    assert digest(section(SPEC.read_text(), heading)) == SECTIONS[heading], UPDATE


@pytest.mark.parametrize("name", sorted(MODULES))
def test_the_code_carrying_them_out_is_the_code_pinned(name: str) -> None:
    found = hashlib.sha256((SIMULATOR / name).read_bytes()).hexdigest()
    assert found == MODULES[name], UPDATE


def test_signing_the_review_does_not_move_the_pin() -> None:
    text = SPEC.read_text()
    signed = text.replace(
        "**Review:** pending.", "**Review:** by a reviewer, 2026-10-02.", 1
    )
    assert signed != text
    for heading in SECTIONS:
        assert section(signed, heading) == section(text, heading)


def test_a_one_word_change_to_an_effect_moves_the_pin() -> None:
    """The positive control: the check sees a change it should see."""
    text = SPEC.read_text()
    changed = text.replace("the floor does not move", "the floor rises", 1)
    assert changed != text
    assert (
        digest(section(changed, "### What each fault does"))
        != (SECTIONS["### What each fault does"])
    )


def test_a_missing_heading_is_refused_by_name() -> None:
    with pytest.raises(AssertionError, match="What nobody does"):
        section(SPEC.read_text(), "### What nobody does")
