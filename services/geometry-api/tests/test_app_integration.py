"""End-to-end: publish a fixture, then exercise every route through the real, fully-wired app.

Settings.artifacts_dir/cache_dir are monkeypatched to a tmp_path before the app's lifespan runs
(``with TestClient(app) as client``), so this hits geometry_api.main.app exactly as uvicorn would
serve it, pointed at an isolated, throwaway published dataset.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import publish_dataset
import pytest
from fastapi.testclient import TestClient
from publish_fixtures import write_healthy_build

from geometry_api import tkmb
from geometry_api.config import settings


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, str]:
    build_dir = tmp_path / "build"
    write_healthy_build(build_dir)
    artifacts_dir = tmp_path / "artifacts"
    cache_dir = tmp_path / "cache"
    revision_id = publish_dataset.publish(build_dir, "fixture", artifacts_dir, cache_dir=cache_dir)

    monkeypatch.setattr(settings, "artifacts_dir", str(artifacts_dir))
    monkeypatch.setattr(settings, "cache_dir", str(cache_dir))

    from geometry_api.main import app

    with TestClient(app) as test_client:
        yield test_client, revision_id


def test_health_is_unversioned_and_needs_no_registry(client: tuple[TestClient, str]) -> None:
    test_client, _revision_id = client
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_is_ready_when_the_only_dataset_is_healthy(client: tuple[TestClient, str]) -> None:
    test_client, _revision_id = client
    response = test_client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "datasets": 1}


def test_list_datasets(client: tuple[TestClient, str]) -> None:
    test_client, revision_id = client
    response = test_client.get("/v1/datasets")
    assert response.status_code == 200
    body = response.json()
    assert body["datasets"] == [
        {"id": "fixture", "name": "fixture", "currentRevisionId": revision_id, "territoryCount": 1}
    ]


def test_get_dataset_carries_all_client_flags_per_level(
    client: tuple[TestClient, str],
) -> None:
    test_client, revision_id = client
    response = test_client.get("/v1/datasets/fixture")
    assert response.status_code == 200
    body = response.json()
    assert body["revisionId"] == revision_id
    assert body["isCurrentRevision"] is True
    assert body["boundsLocal"] == [-1000.0, -1000.0, 1000.0, 1000.0]
    levels = {level["lod"]: level for level in body["levels"]}
    assert set(levels) == {"high", "medium", "low"}
    for level in levels.values():
        assert level["lossy"] is False
        assert level["topologyChanged"] is False
        assert level["pickingUnsafe"] is False
        assert level["simplification"] == {"topologyChanged": False}


def test_get_dataset_unknown_id_is_404(client: tuple[TestClient, str]) -> None:
    test_client, _revision_id = client
    response = test_client.get("/v1/datasets/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "dataset_not_found"


def test_territories_lists_the_fixture_territory(client: tuple[TestClient, str]) -> None:
    test_client, _revision_id = client
    response = test_client.get("/v1/datasets/fixture/territories?lod=high")
    assert response.status_code == 200
    body = response.json()
    assert body["nextCursor"] is None
    assert [item["id"] for item in body["items"]] == ["T1"]
    assert body["items"][0]["administrativeLevel"] == 1


def test_territories_missing_lod_is_422(client: tuple[TestClient, str]) -> None:
    test_client, _revision_id = client
    response = test_client.get("/v1/datasets/fixture/territories")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_territories_unknown_lod_value_is_400(client: tuple[TestClient, str]) -> None:
    test_client, _revision_id = client
    response = test_client.get("/v1/datasets/fixture/territories?lod=ultra")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unknown_lod"


def test_viewport_finds_the_territory_inside_the_bbox(client: tuple[TestClient, str]) -> None:
    test_client, _revision_id = client
    response = test_client.get("/v1/datasets/fixture/viewport?lod=high&bbox=-10,-10,10,10")
    assert response.status_code == 200
    assert response.json()["territoryIds"] == ["T1"]


def test_viewport_outside_the_bbox_finds_nothing(client: tuple[TestClient, str]) -> None:
    test_client, _revision_id = client
    response = test_client.get("/v1/datasets/fixture/viewport?lod=high&bbox=100,100,200,200")
    assert response.status_code == 200
    assert response.json()["territoryIds"] == []


def test_mesh_identity_download_matches_the_published_bytes(client: tuple[TestClient, str]) -> None:
    """httpx sends its own 'Accept-Encoding: gzip, ...' by default, so 'identity' is requested
    explicitly here — otherwise this would exercise the gzip path by accident."""
    test_client, revision_id = client
    response = test_client.get(
        f"/v1/datasets/fixture/revisions/{revision_id}/mesh/T1?lod=high",
        headers={"Accept-Encoding": "identity"},
    )
    assert response.status_code == 200
    assert response.content == b"tkms-fixture-high"
    assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert response.headers["Vary"] == "Accept-Encoding"
    assert "Content-Encoding" not in response.headers
    assert response.headers["ETag"].startswith('"') and response.headers["ETag"].endswith('"')


def test_mesh_conditional_get_returns_304(client: tuple[TestClient, str]) -> None:
    test_client, revision_id = client
    url = f"/v1/datasets/fixture/revisions/{revision_id}/mesh/T1?lod=high"
    first = test_client.get(url)
    etag = first.headers["ETag"]

    second = test_client.get(url, headers={"If-None-Match": etag})

    assert second.status_code == 304
    assert second.content == b""
    assert second.headers["ETag"] == etag
    assert second.headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_mesh_gzip_variant_has_a_different_etag_and_decodes_to_the_same_bytes(
    client: tuple[TestClient, str],
) -> None:
    test_client, revision_id = client
    url = f"/v1/datasets/fixture/revisions/{revision_id}/mesh/T1?lod=high"

    identity = test_client.get(url, headers={"Accept-Encoding": "identity"})
    gzipped = test_client.get(url, headers={"Accept-Encoding": "gzip"})

    assert gzipped.headers["Content-Encoding"] == "gzip"
    assert gzipped.headers["ETag"] != identity.headers["ETag"]
    # httpx decodes Content-Encoding: gzip transparently, so .content is already plain bytes —
    # the wire bytes themselves are checked separately, against the published .tkms.gz file, in
    # test_mesh_gzip_body_is_the_precomputed_file_not_runtime_compressed.
    assert gzipped.content == b"tkms-fixture-high" == identity.content


def test_mesh_gzip_body_is_the_precomputed_file_not_runtime_compressed(
    client: tuple[TestClient, str], tmp_path: Path
) -> None:
    """Reads the .tkms.gz file straight off disk — sidesteps httpx's transparent decoding to
    prove the *wire* bytes really are gzip and really do decompress to the source content."""
    _test_client, revision_id = client
    raw = (
        tmp_path / "artifacts" / "fixture" / "revisions" / revision_id / "high" / "T1.tkms.gz"
    ).read_bytes()
    assert gzip.decompress(raw) == b"tkms-fixture-high"


def test_mesh_head_matches_get_headers_with_no_body(client: tuple[TestClient, str]) -> None:
    test_client, revision_id = client
    url = f"/v1/datasets/fixture/revisions/{revision_id}/mesh/T1?lod=high"

    head = test_client.head(url)
    get = test_client.get(url)

    assert head.status_code == get.status_code == 200
    assert head.content == b""
    assert head.headers["ETag"] == get.headers["ETag"]
    assert head.headers["Content-Length"] == get.headers["Content-Length"]


def test_mesh_unknown_territory_is_404(client: tuple[TestClient, str]) -> None:
    test_client, revision_id = client
    response = test_client.get(
        f"/v1/datasets/fixture/revisions/{revision_id}/mesh/does-not-exist?lod=high"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "territory_not_found"


def test_mesh_unknown_revision_is_404(client: tuple[TestClient, str]) -> None:
    test_client, _revision_id = client
    response = test_client.get(f"/v1/datasets/fixture/revisions/{'0' * 64}/mesh/T1?lod=high")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "revision_not_found"


def test_batch_returns_found_entries_and_records_missing_ids(
    client: tuple[TestClient, str],
) -> None:
    test_client, revision_id = client
    response = test_client.post(
        f"/v1/datasets/fixture/revisions/{revision_id}/mesh/batch",
        json={"territoryIds": ["T1", "does-not-exist"], "lod": "high"},
    )
    assert response.status_code == 200
    container = tkmb.decode_tkmb(response.content)
    assert container.entries == {"T1": b"tkms-fixture-high"}
    assert container.missing_ids == ["does-not-exist"]


def test_batch_is_a_cache_hit_on_the_second_identical_request(
    client: tuple[TestClient, str],
) -> None:
    test_client, revision_id = client
    body = {"territoryIds": ["T1"], "lod": "high"}
    url = f"/v1/datasets/fixture/revisions/{revision_id}/mesh/batch"

    first = test_client.post(url, json=body)
    second = test_client.post(url, json=body)

    assert first.content == second.content
    metrics = test_client.get("/metrics").json()
    assert metrics["cache"]["batchHits"] >= 1


def test_metrics_reflects_traffic_after_requests_were_made(client: tuple[TestClient, str]) -> None:
    test_client, _revision_id = client
    test_client.get("/v1/datasets")

    snapshot = test_client.get("/metrics").json()

    assert snapshot["schemaVersion"] == 1
    routes = {row["route"] for row in snapshot["requests"]}
    assert "GET /v1/datasets" in routes
