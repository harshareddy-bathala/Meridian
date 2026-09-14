"""The reception layer has nowhere a transmission could come from (D-126).

Hard rule 4: the station never transmits. A denylist of transmit-capable
programs is not claimed as a safeguard — a list cannot be complete — so these
check the shape instead: every public method on the protocols and adapters is
one this test names, and nothing in the package opens a device path. The decoder
adapter runs programs that read a recording; which program is configuration.

A new method on a receiver or a rotator therefore fails here until someone adds
it to the allowlist below, which is the moment to ask what it does.

Marked as a unit test by living in ``tests/unit``.

Reference: docs/DECISIONS.md D-126; CLAUDE.md hard rule 4.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from meridian_client.reception import (
    null_rotator,
    protocols,
    subprocess_decoder,
    synthetic_receivers,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RECEPTION_SOURCES = sorted(
    (REPO_ROOT / "client" / "src" / "meridian_client" / "reception").rglob("*.py")
)

RECEIVER_SURFACE = {"hears_the_sky", "start", "alive", "stop"}
ROTATOR_SURFACE = {"prepare", "release"}
DECODER_SURFACE = {"supports", "start"}
DECODE_RUN_SURFACE = {"poll", "cancel"}


def public_members(cls: type) -> set[str]:
    return {name for name, _ in inspect.getmembers(cls) if not name.startswith("_")}


@pytest.mark.parametrize(
    ("cls", "surface"),
    [
        (protocols.Receiver, RECEIVER_SURFACE),
        (synthetic_receivers.SimulatedReceiver, RECEIVER_SURFACE),
        (synthetic_receivers.FileReplayReceiver, RECEIVER_SURFACE),
        (protocols.RotatorController, ROTATOR_SURFACE),
        (null_rotator.NullRotator, ROTATOR_SURFACE),
        (protocols.Decoder, DECODER_SURFACE),
        (subprocess_decoder.SubprocessDecoder, DECODER_SURFACE),
        (protocols.DecodeRun, DECODE_RUN_SURFACE),
        # `failed` builds a run that never started; it launches nothing.
        (subprocess_decoder.SubprocessDecodeRun, DECODE_RUN_SURFACE | {"failed"}),
    ],
    ids=lambda value: getattr(value, "__name__", "surface"),
)
def test_receivers_decoders_and_rotators_expose_only_receive_shaped_calls(
    cls: type, surface: set[str]
) -> None:
    assert public_members(cls) == surface


@pytest.mark.parametrize("path", RECEPTION_SOURCES, ids=lambda path: path.name)
def test_nothing_in_the_reception_layer_names_a_device_path(path: Path) -> None:
    """No adapter here opens a device; a physical one reads through a program."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]

    assert [one for one in literals if "/dev/" in one] == []


def test_the_scan_covers_the_package() -> None:
    assert {path.name for path in RECEPTION_SOURCES} >= {
        "protocols.py",
        "synthetic_receivers.py",
        "null_rotator.py",
        "capture_folder.py",
        "subprocess_decoder.py",
    }
