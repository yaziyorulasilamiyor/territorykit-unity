"""geometry_api.deps: registry resolution wired into a minimal FastAPI app, error mapping, leases
released after the response (FAZ-3-PLAN.md §3.4, §9.2)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import publish_dataset
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from publish_fixtures import bump_territory_content, write_healthy_build

from geometry_api.deps import resolve_pinned_revision, resolve_revision
from geometry_api.errors import install_error_handlers
from geometry_api.registry import DatasetRegistry, ResolvedRevision


def _make_app(artifacts_dir: Path, cache_dir: Path) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.state.registry = DatasetRegistry(artifacts_dir, cache_dir)

    @app.get("/optional/{dataset_id}")
    def optional_route(
        resolved: Annotated[ResolvedRevision, Depends(resolve_revision)],
    ) -> dict[str, object]:
        return {"revisionId": resolved.revision_id, "isCurrent": resolved.is_current}

    @app.get("/pinned/{dataset_id}/{revision_id}")
    def pinned_route(
        resolved: Annotated[ResolvedRevision, Depends(resolve_pinned_revision)],
    ) -> dict[str, object]:
        return {"revisionId": resolved.revision_id, "path": str(resolved.path)}

    return app


def _publish(tmp_path: Path) -> tuple[Path, Path, str]:
    build_dir = tmp_path / "build"
    write_healthy_build(build_dir)
    artifacts_dir = tmp_path / "artifacts"
    cache_dir = tmp_path / "cache"
    revision_id = publish_dataset.publish(build_dir, "fixture", artifacts_dir, cache_dir=cache_dir)
    return artifacts_dir, cache_dir, revision_id


def test_optional_revision_defaults_to_current(tmp_path: Path) -> None:
    artifacts_dir, cache_dir, revision_id = _publish(tmp_path)
    client = TestClient(_make_app(artifacts_dir, cache_dir))

    response = client.get("/optional/fixture")

    assert response.status_code == 200
    assert response.json() == {"revisionId": revision_id, "isCurrent": True}


def test_optional_revision_accepts_a_pin(tmp_path: Path) -> None:
    artifacts_dir, cache_dir, revision_id = _publish(tmp_path)
    client = TestClient(_make_app(artifacts_dir, cache_dir))

    response = client.get(f"/optional/fixture?revision={revision_id}")

    assert response.status_code == 200
    assert response.json()["revisionId"] == revision_id


def test_unknown_dataset_is_404_with_the_shared_error_shape(tmp_path: Path) -> None:
    artifacts_dir, cache_dir, _revision_id = _publish(tmp_path)
    client = TestClient(_make_app(artifacts_dir, cache_dir))

    response = client.get("/optional/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "dataset_not_found"
    assert response.headers["cache-control"] == "no-store"


def test_pinned_route_requires_the_revision_in_the_path(tmp_path: Path) -> None:
    artifacts_dir, cache_dir, revision_id = _publish(tmp_path)
    client = TestClient(_make_app(artifacts_dir, cache_dir))

    response = client.get(f"/pinned/fixture/{revision_id}")

    assert response.status_code == 200
    assert response.json()["revisionId"] == revision_id


def test_pinned_route_with_an_unknown_revision_is_404(tmp_path: Path) -> None:
    artifacts_dir, cache_dir, _revision_id = _publish(tmp_path)
    client = TestClient(_make_app(artifacts_dir, cache_dir))

    response = client.get(f"/pinned/fixture/{'0' * 64}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "revision_not_found"


def test_a_pruned_revision_is_410(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    write_healthy_build(build_dir)
    artifacts_dir = tmp_path / "artifacts"
    cache_dir = tmp_path / "cache"
    old_id = publish_dataset.publish(
        build_dir,
        "fixture",
        artifacts_dir,
        keep=1,
        cache_dir=cache_dir,
        published_at="2026-01-01T00:00:00Z",
    )
    bump_territory_content(build_dir)
    publish_dataset.publish(
        build_dir,
        "fixture",
        artifacts_dir,
        keep=1,
        cache_dir=cache_dir,
        published_at="2026-02-01T00:00:00Z",
    )
    client = TestClient(_make_app(artifacts_dir, cache_dir))

    response = client.get(f"/pinned/fixture/{old_id}")

    assert response.status_code == 410
    body = response.json()
    assert body["error"]["code"] == "revision_gone"
    assert body["error"]["details"]["prunedAt"]


def test_the_lease_is_released_after_the_response(tmp_path: Path) -> None:
    artifacts_dir, cache_dir, revision_id = _publish(tmp_path)
    app = _make_app(artifacts_dir, cache_dir)
    client = TestClient(app)
    registry: DatasetRegistry = app.state.registry

    response = client.get("/optional/fixture")

    assert response.status_code == 200
    assert registry.active_lease_count(revision_id) == 0
