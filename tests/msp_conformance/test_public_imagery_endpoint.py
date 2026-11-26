"""``/api/v1/imagery`` and ``/imagery/{hash}/{path}`` against the real application.

Pins D-376: ``not_yet_computed`` until an index is named and areas are
published; only a published area's scenes listed, paged by keyset and filtered
by area, class and time; one scene's manifest as it was published; each file a
manifest lists served with its exact bytes, immutable, as an attachment or
inline; nothing else under ``/imagery/``; and a damaged index, manifest or file
refused, never served.

The index and manifests here are written by hand; ``tests/unit/
test_imagery_endpoint_models.py`` checks the publisher writes this shape.

No database: every answer is read from files. Marked ``msp_conformance`` by
the directory hook.

Reference: docs/DECISIONS.md D-085, D-086, D-091, D-326, D-332, D-375, D-376.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app

TILE = "tiles/12/2918/1898.webp"
FILES = {
    "scene.tif": b"II*\x00 a GeoTIFF",
    "scene.png": b"\x89PNG a picture",
    "scene.pgw": b"0.0001\n0\n0\n-0.0001\n77.58005\n12.97995\n",
    "display.png": b"\x89PNG the display picture",
    TILE: b"RIFF a tile",
}
CAPTION = "High-resolution satellite image of Bengaluru urban."
SUMMARY = {
    "schema": "areas-summary-1",
    "report_sha256": "a" * 64,
    "method": "regions-1",
    "since": "2026-10-05T00:00:00+00:00",
    "as_of": "2026-11-02T00:00:00+00:00",
    "sources": [],
    "areas": [
        {
            "area_id": 1,
            "label": "Bengaluru urban",
            "area_km2": 4.8,
            "bbox": [77.58, 12.96, 77.60, 12.98],
        }
    ],
    "series": [],
    "changes": [],
}


def manifest(
    *, area_id: int, source_class: str, acquired_at: str, files: dict[str, bytes]
) -> dict[str, Any]:
    return {
        "schema": "imagery-manifest-1",
        "method": "imagery-1",
        "area_id": area_id,
        "area_label": "Bengaluru urban",
        "source_id": "sentinel2_l2a",
        "source_class": source_class,
        "platform": "Sentinel-2B",
        "instrument": "MSI",
        "acquired_at": acquired_at,
        "bounds": [77.58, 12.96, 77.60, 12.98],
        "crs": "EPSG:4326",
        "pixel_size_deg": [0.0001, 0.0001],
        "resolution_m": 10.0,
        "cloud_fraction": 0.04,
        "cloud_method": "as counted from the product's scene classification layer",
        "rendering": "the visual asset as published",
        "zoom_min": 9,
        "zoom_max": 12,
        "simulated": False,
        "credit": "Contains modified Copernicus Sentinel data 2026",
        "licence": "Copernicus free, full and open",
        "caption": CAPTION,
        "caption_sha256": hashlib.sha256(CAPTION.encode()).hexdigest(),
        "caption_template": "imagery-caption-1",
        "annotations": [
            {
                "kind": "area",
                "geometry": {"type": "box", "bbox": [77.58, 12.96, 77.60, 12.98]},
                "label": "Bengaluru urban",
                "text": "Bengaluru urban: the published area, drawn as its box",
                "citations": [
                    {
                        "source_id": "meridian_areas",
                        "record_id": "areas-summary-1 of report aaaaaaaaaaaa, area 1",
                        "url": "/api/v1/areas",
                        "licence": "Meridian's published areas summary",
                    }
                ],
            },
            {
                "kind": "fire",
                "geometry": {"type": "point", "lat_deg": 12.97, "lon_deg": 77.59},
                "label": "Fire",
                "text": "Active fire detected 16 Nov 2026 05:30 UTC",
                "citations": [
                    {
                        "source_id": "nasa_firms",
                        "record_id": "0123456789ab row 1",
                        "url": "https://firms.modaps.eosdis.nasa.gov/",
                        "licence": "NASA open data",
                    }
                ],
            },
        ],
        "inputs": [
            {
                "kind": "template",
                "ref": "imagery-caption-1",
                "sha256": "f" * 64,
            }
        ],
        "files": [
            {
                "path": path,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
            for path, data in sorted(files.items())
        ],
    }


class Published:
    """A published imagery directory the test writes, scene by scene."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.entries: list[dict[str, Any]] = []
        root.mkdir(parents=True)

    def scene(
        self,
        *,
        area_id: int = 1,
        source_class: str = "high_resolution",
        acquired_at: str = "2026-11-16T05:21:00Z",
        files: dict[str, bytes] = FILES,
    ) -> str:
        body = manifest(
            area_id=area_id,
            source_class=source_class,
            acquired_at=acquired_at,
            files=files,
        )
        data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        name = hashlib.sha256(data).hexdigest()[:12]
        directory = self.root / name
        for path, content in files.items():
            (directory / path).parent.mkdir(parents=True, exist_ok=True)
            (directory / path).write_bytes(content)
        (directory / "manifest.json").write_bytes(data)
        self.entries.append(
            {
                "hash": name,
                "area_id": area_id,
                "source_class": source_class,
                "source_id": "sentinel2_l2a",
                "acquired_at": acquired_at,
                "published_at": "2026-11-18T06:00:00Z",
                "size_bytes": sum(len(one) for one in files.values()) + len(data),
                "simulated": False,
            }
        )
        self.write()
        return name

    def write(self, **extra: object) -> None:
        index = {"schema": "imagery-index-1", "scenes": self.entries, **extra}
        (self.root / "index.json").write_text(json.dumps(index), encoding="utf-8")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", "postgresql://meridian:meridian@127.0.0.1:1/x")
    monkeypatch.setenv("RATE_LIMITS", "off")
    for name in ("IMAGERY_INDEX", "AREAS_PUBLISHED", "AREAS_SUMMARY"):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(), raise_server_exceptions=False) as started:
        yield started


@pytest.fixture
def published(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Published:
    """Areas published, and ``IMAGERY_INDEX`` naming an index of no scene yet."""
    summary = tmp_path / "areas.json"
    summary.write_text(json.dumps(SUMMARY), encoding="utf-8")
    monkeypatch.setenv("AREAS_PUBLISHED", "true")
    monkeypatch.setenv("AREAS_SUMMARY", str(summary))
    imagery = Published(tmp_path / "imagery")
    imagery.write()
    name_index(client, str(imagery.root / "index.json"))
    return imagery


def name_index(client: TestClient, path: str) -> None:
    """What ``IMAGERY_INDEX`` would have set when the lifespan loaded settings."""
    state = client.app.state  # type: ignore[attr-defined]
    state.settings = replace(state.settings, imagery_index=path)


# --- closed ------------------------------------------------------------------------


def test_no_index_named_is_not_yet_computed(client: TestClient) -> None:
    body = client.get("/api/v1/imagery").json()

    assert set(body) == {"status", "reason", "available_from_stage"}
    assert (body["status"], body["available_from_stage"]) == ("not_yet_computed", 38)
    assert client.get("/api/v1/imagery/0123456789ab").json()["status"] == (
        "not_yet_computed"
    )
    missing = client.get(f"/imagery/0123456789ab/{TILE}")
    assert (missing.status_code, missing.json()["error"]) == (404, "not_found")


def test_an_index_without_published_areas_stays_closed(
    client: TestClient, tmp_path: Path
) -> None:
    """Pictures of an area are published only while the area is (D-326)."""
    imagery = Published(tmp_path / "imagery")
    name = imagery.scene()
    name_index(client, str(imagery.root / "index.json"))

    assert client.get("/api/v1/imagery").json()["status"] == "not_yet_computed"
    assert client.get(f"/imagery/{name}/{TILE}").status_code == 404


# --- the listing and one scene --------------------------------------------------------


def test_a_published_scene_is_listed_with_what_its_card_needs(
    client: TestClient, published: Published
) -> None:
    name = published.scene()

    body = client.get("/api/v1/imagery").json()

    assert body["status"] == "computed" and body["next_cursor"] is None
    (item,) = body["items"]
    assert item == {
        "hash": name,
        "area_id": 1,
        "area_label": "Bengaluru urban",
        "source_class": "high_resolution",
        "source_id": "sentinel2_l2a",
        "platform": "Sentinel-2B",
        "instrument": "MSI",
        "acquired_at": "2026-11-16T05:21:00Z",
        "published_at": "2026-11-18T06:00:00Z",
        "cloud_fraction": 0.04,
        "caption": CAPTION,
        "credit": "Contains modified Copernicus Sentinel data 2026",
        "display": f"/imagery/{name}/display.png",
        "size_bytes": published.entries[0]["size_bytes"],
        "simulated": False,
    }


def test_one_scene_is_its_manifest_as_published(
    client: TestClient, published: Published
) -> None:
    name = published.scene()
    stored = json.loads((published.root / name / "manifest.json").read_bytes())

    body = client.get(f"/api/v1/imagery/{name}").json()

    assert body["status"] == "computed" and body["simulated"] is False
    assert (body["hash"], body["base"]) == (name, f"/imagery/{name}/")
    assert body["manifest"] == stored
    assert all(one["citations"] for one in body["manifest"]["annotations"])


def test_an_unpublished_areas_scene_is_not_listed_or_served(
    client: TestClient, published: Published
) -> None:
    shown = published.scene()
    hidden = published.scene(area_id=2)

    listed = [one["hash"] for one in client.get("/api/v1/imagery").json()["items"]]

    assert listed == [shown]
    assert client.get(f"/api/v1/imagery/{hidden}").status_code == 404
    assert client.get(f"/imagery/{hidden}/{TILE}").status_code == 404


def test_the_listing_is_filtered_by_area_class_and_time(
    client: TestClient, published: Published
) -> None:
    early = published.scene(acquired_at="2026-11-01T05:00:00Z")
    daily = published.scene(
        source_class="daily_wide", acquired_at="2026-11-17T00:00:00Z"
    )

    def listed(query: str) -> list[str]:
        return [
            one["hash"]
            for one in client.get(f"/api/v1/imagery?{query}").json()["items"]
        ]

    assert listed("") == [daily, early], "newest acquisition first"
    assert listed("source_class=daily_wide") == [daily]
    assert listed("since=2026-11-10T00:00:00Z") == [daily]
    assert listed("until=2026-11-10T00:00:00Z") == [early]
    assert listed("area=2") == []


@pytest.mark.parametrize(
    "query",
    [
        "since=2026-11-10T00:00:00",
        "since=2026-11-10T00:00:00Z&until=2026-11-01T00:00:00Z",
        "source_class=received",
        "cursor=not-a-cursor",
    ],
)
def test_a_query_that_cannot_be_answered_is_refused(
    client: TestClient, published: Published, query: str
) -> None:
    published.scene()

    refused = client.get(f"/api/v1/imagery?{query}")

    assert (refused.status_code, refused.json()["error"]) == (400, "invalid_query")


def test_the_listing_pages_by_keyset(client: TestClient, published: Published) -> None:
    newest = published.scene(acquired_at="2026-11-16T05:21:00Z")
    oldest = published.scene(acquired_at="2026-11-02T05:21:00Z")

    first = client.get("/api/v1/imagery?limit=1").json()
    second = client.get(f"/api/v1/imagery?limit=1&cursor={first['next_cursor']}").json()

    assert [one["hash"] for one in first["items"]] == [newest]
    assert [one["hash"] for one in second["items"]] == [oldest]
    assert second["next_cursor"] is None


# --- the files ---------------------------------------------------------------------


def test_a_tile_and_the_display_picture_are_inline_and_immutable(
    client: TestClient, published: Published
) -> None:
    name = published.scene()

    for path, media_type in ((TILE, "image/webp"), ("display.png", "image/png")):
        response = client.get(f"/imagery/{name}/{path}")
        assert response.status_code == 200
        assert response.content == FILES[path]
        assert response.headers["content-type"] == media_type
        assert response.headers["cache-control"] == (
            "public, max-age=31536000, immutable"
        )
        assert "content-disposition" not in response.headers


@pytest.mark.parametrize("path", ["scene.tif", "scene.png", "scene.pgw"])
def test_the_downloads_are_attachments_with_their_exact_bytes(
    client: TestClient, published: Published, path: str
) -> None:
    name = published.scene()

    response = client.get(f"/imagery/{name}/{path}")

    assert response.status_code == 200
    assert response.content == FILES[path]
    assert response.headers["content-disposition"] == (
        f'attachment; filename="{name}-{path}"'
    )


def test_the_manifest_is_served_as_it_was_published(
    client: TestClient, published: Published
) -> None:
    name = published.scene()

    response = client.get(f"/imagery/{name}/manifest.json")

    assert response.content == (published.root / name / "manifest.json").read_bytes()
    assert response.headers["content-disposition"].startswith("attachment;")


@pytest.mark.parametrize(
    "path",
    ["index.json", "..%2Findex.json", "tiles/12/2918/9999.webp", "notes.txt", "x/y"],
)
def test_nothing_the_manifest_does_not_list_is_served(
    client: TestClient, published: Published, path: str
) -> None:
    name = published.scene()
    (published.root / name / "notes.txt").write_text("not listed", encoding="utf-8")

    refused = client.get(f"/imagery/{name}/{path}")

    assert refused.status_code == 404
    assert refused.json()["error"] == "not_found", "in the public API's words"


def test_the_index_itself_is_not_a_file_of_any_scene(
    client: TestClient, published: Published
) -> None:
    """``/imagery/<name>/../index.json`` is normalised to this before it is sent."""
    published.scene()

    refused = client.get("/imagery/index.json")

    assert refused.status_code in {400, 404}
    assert set(refused.json()) == {"error", "message"}


def test_a_file_changed_after_publishing_is_refused_not_served(
    client: TestClient, published: Published
) -> None:
    name = published.scene()
    (published.root / name / TILE).write_bytes(b"RIFF another tile")

    refused = client.get(f"/imagery/{name}/{TILE}")

    assert (refused.status_code, refused.json()["error"]) == (500, "server_error")
    assert "does not match its manifest" in refused.json()["message"]


def test_the_files_carry_the_platforms_own_security_policy(
    client: TestClient, published: Published
) -> None:
    """Same-origin images: ``img-src 'self'`` already allows them (D-208)."""
    name = published.scene()

    served = client.get(f"/imagery/{name}/{TILE}")
    page = client.get("/api/v1/imagery")

    policy = served.headers["content-security-policy"]
    assert policy == page.headers["content-security-policy"]
    assert "img-src 'self'" in policy


# --- refused whole ------------------------------------------------------------------


def test_an_index_with_a_key_it_does_not_know_is_refused_naming_the_setting(
    client: TestClient, published: Published
) -> None:
    published.scene()
    published.write(note="hand edited")

    refused = client.get("/api/v1/imagery")

    assert (refused.status_code, refused.json()["error"]) == (500, "server_error")
    assert "IMAGERY_INDEX" in refused.json()["message"]


def test_a_manifest_that_does_not_hash_to_its_name_is_refused(
    client: TestClient, published: Published
) -> None:
    name = published.scene()
    path = published.root / name / "manifest.json"
    path.write_bytes(path.read_bytes().replace(b"Sentinel-2B", b"Sentinel-2A"))

    assert client.get("/api/v1/imagery").status_code == 500
    assert client.get(f"/imagery/{name}/{TILE}").status_code == 500


def test_a_manifest_of_another_scene_than_its_entry_is_refused(
    client: TestClient, published: Published
) -> None:
    published.scene()
    published.entries[0]["acquired_at"] = "2026-11-15T05:21:00Z"
    published.write()

    assert client.get("/api/v1/imagery").status_code == 500


def test_a_simulated_scene_says_so_in_the_listing_and_the_detail(
    client: TestClient, published: Published
) -> None:
    """The demonstration's synthetic scene, labelled at the API too (rule 5)."""
    name = published.scene()
    path = published.root / name / "manifest.json"
    body = json.loads(path.read_bytes())
    body["simulated"] = True
    data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    simulated = hashlib.sha256(data).hexdigest()[:12]
    (published.root / name).rename(published.root / simulated)
    (published.root / simulated / "manifest.json").write_bytes(data)
    published.entries[0].update(hash=simulated, simulated=True)
    published.write()

    (item,) = client.get("/api/v1/imagery").json()["items"]
    detail = client.get(f"/api/v1/imagery/{simulated}").json()

    assert item["simulated"] is True
    assert detail["simulated"] is True and detail["manifest"]["simulated"] is True


def test_an_entry_that_disagrees_with_its_manifest_about_simulation_is_refused(
    client: TestClient, published: Published
) -> None:
    published.scene()
    published.entries[0]["simulated"] = True
    published.write()

    assert client.get("/api/v1/imagery").status_code == 500
