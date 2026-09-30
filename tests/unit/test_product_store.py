"""The station's product store: named by hash, written whole, kept under a cap.

Marked as a unit test by living in ``tests/unit``.

Reference: docs/DECISIONS.md D-176.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from meridian_client.reception.product_store import (
    HeldProduct,
    ProductStore,
    digest_of,
)


def _file(directory: Path, name: str, contents: bytes) -> Path:
    path = directory / name
    path.write_bytes(contents)
    return path


def _age(store_root: Path, sha256: str, seconds_ago: int) -> None:
    """Make a kept product look older, so eviction order can be chosen."""
    path = store_root / sha256
    stamp = path.stat().st_mtime - seconds_ago
    os.utime(path, (stamp, stamp))


def test_a_kept_product_is_named_by_its_hash(tmp_path: Path) -> None:
    store = ProductStore(tmp_path / "products")
    source = _file(tmp_path, "waterfall.png", b"waterfall")

    sha256 = store.keep(source)

    assert sha256 == hashlib.sha256(b"waterfall").hexdigest()
    assert (tmp_path / "products" / sha256).read_bytes() == b"waterfall"
    assert store.holds(sha256)


def test_keeping_the_same_bytes_twice_keeps_one_copy(tmp_path: Path) -> None:
    store = ProductStore(tmp_path / "products")
    first = store.keep(_file(tmp_path, "a.png", b"same"))
    second = store.keep(_file(tmp_path, "b.png", b"same"))

    assert first == second
    assert [one.name for one in (tmp_path / "products").iterdir()] == [first]


def test_no_partial_file_is_left_behind(tmp_path: Path) -> None:
    store = ProductStore(tmp_path / "products")
    store.keep(_file(tmp_path, "a.png", b"bytes"))

    assert not [
        one for one in (tmp_path / "products").iterdir() if one.name.startswith(".")
    ]


def test_the_oldest_product_is_evicted_to_fit_the_cap(tmp_path: Path) -> None:
    store = ProductStore(tmp_path / "products", max_bytes=10)
    old = store.keep(_file(tmp_path, "old", b"x" * 6))
    assert old is not None
    _age(tmp_path / "products", old, 60)

    new = store.keep(_file(tmp_path, "new", b"y" * 6))

    assert new is not None
    assert store.holds(new)
    assert not store.holds(old)


def test_the_product_just_kept_is_never_evicted(tmp_path: Path) -> None:
    """Even when its file's time ties with the one it has to push out."""
    store = ProductStore(tmp_path / "products", max_bytes=10)
    newer = store.keep(_file(tmp_path, "newer", b"n" * 6))
    assert newer is not None
    source = _file(tmp_path, "older", b"o" * 6)

    kept = store.keep(source)
    assert kept is not None

    assert store.holds(kept)


def test_a_product_larger_than_the_store_is_not_kept(tmp_path: Path) -> None:
    store = ProductStore(tmp_path / "products", max_bytes=4)

    assert store.keep(_file(tmp_path, "big", b"12345")) is None
    assert not (tmp_path / "products").exists()


def test_a_missing_source_is_not_kept(tmp_path: Path) -> None:
    store = ProductStore(tmp_path / "products")

    assert store.keep(tmp_path / "gone.png") is None


def test_only_a_hash_is_asked_about(tmp_path: Path) -> None:
    """``holds`` never turns a string into a path it did not make."""
    store = ProductStore(tmp_path / "products")
    store.keep(_file(tmp_path, "a", b"a"))

    assert not store.holds("../a")
    assert not store.holds("")


def test_a_non_positive_cap_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        ProductStore(tmp_path, max_bytes=0)


def test_a_held_product_is_declared_as_msp_carries_it(tmp_path: Path) -> None:
    sha256, size = digest_of(_file(tmp_path, "a.png", b"png"))

    declared = HeldProduct("waterfall", sha256, size).as_declared()

    assert declared == {
        "kind": "waterfall",
        "uri": f"station:products/{sha256}",
        "sha256": sha256,
        "size_bytes": 3,
    }
