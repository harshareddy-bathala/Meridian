"""``/api/v1/datasets`` against the real application.

Pins the published evidence packages (D-369): ``not_yet_computed`` with no
index named, the index as it states the packages, each listed file served as an
attachment with its exact bytes, nothing the index does not list, a file
changed after publication refused rather than served, and a damaged index
refused naming the setting.

The index here is written by hand; ``tests/unit/test_evidence_publish.py``
checks that ``meridian dataset publish`` writes exactly this shape.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-086, D-093, D-332, D-369.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

psycopg = pytest.importorskip("psycopg")

from meridian.api.app import create_app  # noqa: E402
from meridian.api.dependencies import get_connection  # noqa: E402

pytestmark = pytest.mark.integration

CONTENT = "ab" * 32
NAME = CONTENT[:12]
FILES = {
    "manifest.json": b'{"kind":"evidence_dataset"}\n',
    "receptions.jsonl": b'{"assignment_id":"as_1"}\n',
    "datasheet.md": b"# Meridian evidence dataset\n",
}


def index(files: dict[str, bytes] = FILES) -> dict[str, Any]:
    return {
        "schema": "evidence-index-1",
        "packages": [
            {
                "name": NAME,
                "content_sha256": CONTENT,
                "snapshot_sha256": "cd" * 32,
                "config_sha256": "ef" * 32,
                "transformation": "evidence-1",
                "since": "2026-09-01T00:00:00Z",
                "as_of": "2026-09-23T06:00:00Z",
                "created_at": "2026-09-23T07:00:00Z",
                "licence": "CC-BY-4.0",
                "citation": f"Meridian evidence dataset, content sha256 {CONTENT}, x",
                "populations": [
                    {
                        "population": "measured",
                        "file": "receptions.jsonl",
                        "receptions": 1,
                        "simulated": False,
                    }
                ],
                "files": [
                    {
                        "name": name,
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "size_bytes": len(data),
                    }
                    for name, data in files.items()
                ],
            }
        ],
    }


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


def started(rollback: Any) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client(rollback: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("EVIDENCE_INDEX", raising=False)
    with started(rollback) as one:
        yield one


@pytest.fixture
def published(
    rollback: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[TestClient, Path]]:
    """An app whose ``EVIDENCE_INDEX`` names an index of one package."""
    evidence = tmp_path / "evidence"
    (evidence / NAME).mkdir(parents=True)
    for name, data in FILES.items():
        (evidence / NAME / name).write_bytes(data)
    (evidence / "index.json").write_text(json.dumps(index()))
    monkeypatch.setenv("EVIDENCE_INDEX", str(evidence / "index.json"))
    with started(rollback) as one:
        yield one, evidence


def test_no_index_named_is_not_yet_computed(client: TestClient) -> None:
    body = client.get("/api/v1/datasets").json()

    assert body["status"] == "not_yet_computed"
    assert body["available_from_stage"] == 37
    assert client.get(f"/api/v1/datasets/{NAME}/manifest.json").status_code == 404


def test_the_index_is_served_as_it_states_the_packages(
    published: tuple[TestClient, Path],
) -> None:
    client, _ = published

    body = client.get("/api/v1/datasets").json()

    assert body["status"] == "computed"
    assert body["schema"] == "evidence-index-1"
    assert body["packages"] == index()["packages"]


def test_a_listed_file_is_an_attachment_with_its_exact_bytes(
    published: tuple[TestClient, Path],
) -> None:
    client, _ = published

    response = client.get(f"/api/v1/datasets/{NAME}/receptions.jsonl")

    assert response.status_code == 200
    assert response.content == FILES["receptions.jsonl"]
    assert response.headers["content-type"] == "application/x-ndjson"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="{NAME}-receptions.jsonl"'
    )


@pytest.mark.parametrize(
    "path",
    [
        f"/api/v1/datasets/{NAME}/licence.txt",
        f"/api/v1/datasets/{'0' * 12}/manifest.json",
        f"/api/v1/datasets/{NAME}/..%2Findex.json",
        f"/api/v1/datasets/{NAME}/index.json",
    ],
    ids=["unlisted-file", "unlisted-package", "climbing", "the-index"],
)
def test_nothing_the_index_does_not_list_is_served(
    published: tuple[TestClient, Path], path: str
) -> None:
    client, _ = published

    response = client.get(path)

    assert response.status_code in {400, 404}
    assert response.json()["error"] in {"not_found", "invalid_query"}


def test_a_file_changed_after_publication_is_refused_not_served(
    published: tuple[TestClient, Path],
) -> None:
    client, evidence = published
    target = evidence / NAME / "receptions.jsonl"
    target.write_bytes(b'{"assignment_id":"as_2"}\n')

    response = client.get(f"/api/v1/datasets/{NAME}/receptions.jsonl")

    assert response.status_code == 500
    assert response.json()["error"] == "server_error"
    assert b"as_2" not in response.content


def test_a_damaged_index_is_refused_naming_the_setting(
    published: tuple[TestClient, Path],
) -> None:
    client, evidence = published
    damaged = index()
    damaged["packages"][0]["name"] = "0" * 12
    (evidence / "index.json").write_text(json.dumps(damaged))

    for path in ("/api/v1/datasets", f"/api/v1/datasets/{NAME}/manifest.json"):
        response = client.get(path)
        assert response.status_code == 500
        assert response.json()["error"] == "server_error"
        assert "EVIDENCE_INDEX" in response.json()["message"]
