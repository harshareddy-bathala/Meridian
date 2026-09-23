"""``meridian.datasets.canonical`` — the bytes every snapshot is hashed over.

One test per rule in D-070 as D-144 generalises it, plus a pinned digest: the
gate is that the same inputs always give the same hash, so a change to these
bytes is a change to every snapshot hash, and should fail here by name first.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from meridian.datasets.canonical import canonical_bytes, canonical_line

INDIAN_STANDARD_TIME = timezone(timedelta(hours=5, minutes=30))
AOS = datetime(2026, 8, 14, 9, 41, 18, 123456, tzinfo=UTC)

ROW = {
    "pass_id": 812,
    "station_id": "st_7fa3c1",
    "satellite_id": "norad:57166",
    "aos": AOS,
    "max_elevation_deg": 47.25,
    "modes": ["lrpt", "apt"],
    "simulated": False,
    "content_sha256": bytes.fromhex("ab" * 32),
    "note": None,
}

PINNED_SHA256 = "cdb952f51a9afe8b965db1a4b791c57d05461f3f15cbb1941c1bd3cb4630f59e"
"""The digest of :data:`ROW`'s line, written down when the rendering was built."""


# --- the same facts, the same bytes ----------------------------------------


def test_key_order_does_not_change_the_bytes() -> None:
    backwards = dict(reversed(list(ROW.items())))

    assert canonical_bytes(backwards) == canonical_bytes(ROW)


def test_nested_keys_are_sorted_too() -> None:
    assert canonical_bytes({"b": {"z": 1, "a": 2}, "a": 0}) == (
        b'{"a":0,"b":{"a":2,"z":1}}'
    )


def test_one_instant_in_two_zones_renders_once() -> None:
    """A station reporting +05:30 and one reporting Z agree."""
    local = AOS.astimezone(INDIAN_STANDARD_TIME)

    assert canonical_bytes({"t": local}) == canonical_bytes({"t": AOS})


def test_timestamps_are_utc_milliseconds_with_a_z() -> None:
    """Microseconds are truncated: the resolution every stored digest uses."""
    assert canonical_bytes(AOS) == b'"2026-08-14T09:41:18.123Z"'


def test_there_is_no_whitespace_and_text_is_not_escaped() -> None:
    assert canonical_bytes({"name": "Meteor-M № 2-3", "x": [1, 2]}) == (
        '{"name":"Meteor-M № 2-3","x":[1,2]}'.encode()
    )


def test_bytes_are_lowercase_hex() -> None:
    assert canonical_bytes(b"\x00\xab") == b'"00ab"'


def test_a_tuple_and_a_list_are_the_same_array() -> None:
    assert canonical_bytes((1, 2)) == canonical_bytes([1, 2])


# --- different facts, different bytes --------------------------------------


def test_array_order_is_kept() -> None:
    """A series in a different order is a different measurement."""
    assert canonical_bytes([1, 2]) != canonical_bytes([2, 1])


def test_null_and_absent_stay_different() -> None:
    assert canonical_bytes({"a": None}) != canonical_bytes({})


def test_true_is_not_one() -> None:
    assert canonical_bytes(True) == b"true"
    assert canonical_bytes(1) == b"1"


# --- what is refused --------------------------------------------------------


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_float_json_cannot_write_is_refused(value: float) -> None:
    with pytest.raises(ValueError, match="cannot be hashed"):
        canonical_bytes({"snr": value})


def test_a_naive_timestamp_is_refused() -> None:
    """It would be hashed as UTC whatever zone it was actually read in."""
    with pytest.raises(ValueError, match="timezone-aware"):
        canonical_bytes({"t": datetime(2026, 8, 14, 9, 41, 18)})  # noqa: DTZ001


def test_a_key_that_is_not_text_is_refused() -> None:
    """``json.dumps`` would make ``{1: x}`` collide with ``{"1": x}``."""
    with pytest.raises(TypeError, match="keys must be strings"):
        canonical_bytes({1: "a"})


@pytest.mark.parametrize(
    "value", [Decimal("1.5"), date(2026, 8, 14), {1, 2}, timedelta(seconds=1)]
)
def test_a_type_with_no_single_rendering_is_refused(value: object) -> None:
    with pytest.raises(TypeError, match="no canonical rendering"):
        canonical_bytes({"v": value})


# --- lines -----------------------------------------------------------------


def test_a_line_is_the_row_and_one_newline() -> None:
    line = canonical_line(ROW)

    assert line == canonical_bytes(ROW) + b"\n"
    assert line.count(b"\n") == 1
    assert json.loads(line)["aos"] == "2026-08-14T09:41:18.123Z"


def test_a_line_must_be_a_row() -> None:
    with pytest.raises(TypeError, match="mapping"):
        canonical_line([1, 2])  # type: ignore[arg-type]


def test_the_rendering_is_pinned() -> None:
    """Changing it changes every snapshot hash; do it deliberately or not at all."""
    assert canonical_line(ROW) == (
        b'{"aos":"2026-08-14T09:41:18.123Z",'
        b'"content_sha256":"' + b"ab" * 32 + b'",'
        b'"max_elevation_deg":47.25,"modes":["lrpt","apt"],"note":null,'
        b'"pass_id":812,"satellite_id":"norad:57166","simulated":false,'
        b'"station_id":"st_7fa3c1"}\n'
    )
    assert hashlib.sha256(canonical_line(ROW)).hexdigest() == PINNED_SHA256
