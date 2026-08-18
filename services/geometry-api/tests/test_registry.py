"""DatasetRegistry: the single resolution point every route depends on (FAZ-3-PLAN.md §3.4-§3.5).

Built against real published revisions (via scripts/publish_dataset.py + publish_fixtures.py)
rather than hand-built directory trees, so the registry is tested against exactly what the
publisher actually produces.
"""

from __future__ import annotations

import json
from pathlib import Path

import publish_dataset
import pytest
from publish_fixtures import bump_territory_content, write_healthy_build

from geometry_api.registry import (
    DatasetNotFoundError,
    DatasetRegistry,
    RevisionCorruptedError,
    RevisionGoneError,
    RevisionNotFoundError,
    TerritoryNotFoundError,
)


@pytest.fixture
def published(tmp_path: Path) -> tuple[DatasetRegistry, Path, str]:
    """One dataset, one published revision. Returns (registry, artifacts_dir, revision_id)."""
    build_dir = tmp_path / "build"
    write_healthy_build(build_dir)
    artifacts_dir = tmp_path / "artifacts"
    cache_dir = tmp_path / "cache"
    revision_id = publish_dataset.publish(
        build_dir,
        "fixture",
        artifacts_dir,
        cache_dir=cache_dir,
        published_at="2026-01-01T00:00:00Z",
    )
    registry = DatasetRegistry(artifacts_dir, cache_dir)
    return registry, artifacts_dir, revision_id


def test_resolving_with_no_revision_returns_the_current_one(
    published: tuple[DatasetRegistry, Path, str],
) -> None:
    registry, _artifacts_dir, revision_id = published

    resolved = registry.resolve("fixture")

    assert resolved.revision_id == revision_id
    assert resolved.is_current is True
    assert resolved.published_at == "2026-01-01T00:00:00Z"


def test_resolving_the_current_id_explicitly_is_also_current(
    published: tuple[DatasetRegistry, Path, str],
) -> None:
    registry, _artifacts_dir, revision_id = published

    resolved = registry.resolve("fixture", requested_revision=revision_id)

    assert resolved.is_current is True


def test_an_unknown_dataset_is_not_found(published: tuple[DatasetRegistry, Path, str]) -> None:
    registry, _artifacts_dir, _revision_id = published

    with pytest.raises(DatasetNotFoundError):
        registry.resolve("does-not-exist")


def test_an_unknown_revision_is_not_found(published: tuple[DatasetRegistry, Path, str]) -> None:
    registry, _artifacts_dir, _revision_id = published

    with pytest.raises(RevisionNotFoundError):
        registry.resolve("fixture", requested_revision="0" * 64)


def test_a_pruned_revision_is_gone_not_not_found(tmp_path: Path) -> None:
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
    registry = DatasetRegistry(artifacts_dir, cache_dir)

    with pytest.raises(RevisionGoneError) as excinfo:
        registry.resolve("fixture", requested_revision=old_id)
    assert excinfo.value.pruned_at


def test_an_old_but_still_retained_revision_is_not_current(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    write_healthy_build(build_dir)
    artifacts_dir = tmp_path / "artifacts"
    cache_dir = tmp_path / "cache"

    first_id = publish_dataset.publish(
        build_dir,
        "fixture",
        artifacts_dir,
        keep=3,
        cache_dir=cache_dir,
        published_at="2026-01-01T00:00:00Z",
    )
    bump_territory_content(build_dir)
    publish_dataset.publish(
        build_dir,
        "fixture",
        artifacts_dir,
        keep=3,
        cache_dir=cache_dir,
        published_at="2026-02-01T00:00:00Z",
    )
    registry = DatasetRegistry(artifacts_dir, cache_dir)

    resolved = registry.resolve("fixture", requested_revision=first_id)

    assert resolved.revision_id == first_id
    assert resolved.is_current is False


def test_a_new_publish_is_picked_up_without_recreating_the_registry(
    published: tuple[DatasetRegistry, Path, str],
) -> None:
    """The pointer cache is invalidated by mtime, not held forever (§3.4)."""
    registry, artifacts_dir, first_id = published
    assert registry.resolve("fixture").revision_id == first_id

    build_dir = artifacts_dir.parent / "build"
    bump_territory_content(build_dir)
    second_id = publish_dataset.publish(
        build_dir,
        "fixture",
        artifacts_dir,
        cache_dir=artifacts_dir.parent / "cache",
        published_at="2026-02-01T00:00:00Z",
    )

    assert registry.resolve("fixture").revision_id == second_id


def test_content_altered_after_publish_is_detected_as_corrupted(
    published: tuple[DatasetRegistry, Path, str],
) -> None:
    registry, artifacts_dir, revision_id = published
    tampered = artifacts_dir / "fixture" / "revisions" / revision_id / "high" / "index.json"
    tampered.write_bytes(tampered.read_bytes() + b"\ntampered\n")

    with pytest.raises(RevisionCorruptedError):
        registry.resolve("fixture")


def test_integrity_is_verified_once_then_cached(
    published: tuple[DatasetRegistry, Path, str],
) -> None:
    """A revision that passed verification is trusted for the rest of the process's life —
    tampering *after* the first successful resolve is not re-detected, by design (§3.5a: the
    check runs once per process, not on every request, to stay off the request-time hot path)."""
    registry, artifacts_dir, revision_id = published
    registry.resolve("fixture")  # first resolution verifies and caches "ok"

    tampered = artifacts_dir / "fixture" / "revisions" / revision_id / "high" / "index.json"
    tampered.write_bytes(tampered.read_bytes() + b"\ntampered after the first resolve\n")

    resolved = registry.resolve("fixture")  # no error: verification result was cached
    assert resolved.revision_id == revision_id


def test_lease_creates_and_removes_a_file_under_cache_dir(
    published: tuple[DatasetRegistry, Path, str],
) -> None:
    registry, _artifacts_dir, revision_id = published

    assert registry.active_lease_count(revision_id) == 0
    with registry.lease(revision_id):
        assert registry.active_lease_count(revision_id) == 1
    assert registry.active_lease_count(revision_id) == 0


def test_known_dataset_ids_reflects_the_filesystem(
    published: tuple[DatasetRegistry, Path, str],
) -> None:
    registry, artifacts_dir, _revision_id = published
    assert registry.known_dataset_ids() == ["fixture"]

    (artifacts_dir / "second").mkdir()
    assert registry.known_dataset_ids() == ["fixture", "second"]


def test_territory_entry_finds_the_fixture_territory(
    published: tuple[DatasetRegistry, Path, str],
) -> None:
    registry, _artifacts_dir, _revision_id = published
    resolved = registry.resolve("fixture")

    entry = registry.territory_entry(resolved, "high", "T1")

    assert entry["id"] == "T1"


def test_territory_entry_raises_for_an_unknown_id(
    published: tuple[DatasetRegistry, Path, str],
) -> None:
    registry, _artifacts_dir, _revision_id = published
    resolved = registry.resolve("fixture")

    with pytest.raises(TerritoryNotFoundError):
        registry.territory_entry(resolved, "high", "does-not-exist")


def test_load_etags_matches_the_file_on_disk(
    published: tuple[DatasetRegistry, Path, str],
) -> None:
    registry, artifacts_dir, revision_id = published
    resolved = registry.resolve("fixture")

    etags = registry.load_etags(resolved, "high")

    on_disk = json.loads(
        (artifacts_dir / "fixture" / "revisions" / revision_id / "high" / "etags.json").read_text()
    )
    assert etags == on_disk
    assert etags["T1.tkms"].startswith('"') and etags["T1.tkms"].endswith('"')
    assert "T1.tkms.gz" in etags


def test_load_manifest_matches_the_file_on_disk(
    published: tuple[DatasetRegistry, Path, str],
) -> None:
    registry, artifacts_dir, revision_id = published
    resolved = registry.resolve("fixture")

    manifest = registry.load_manifest(resolved, "high")

    on_disk = json.loads(
        (artifacts_dir / "fixture" / "revisions" / revision_id / "high" / "index.json").read_text()
    )
    assert manifest == on_disk
