"""publish_dataset.py's staging → verify → rename → pointer sequence (FAZ-3-PLAN.md §3.2-§3.4):
a failure at any point must leave artifacts_dir exactly as it was, and a retry after a crash
between the rename and the pointer write must complete the pointer rather than doing nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import publish_dataset
import pytest
from publish_fixtures import write_healthy_build


def test_interrupted_during_copy_leaves_no_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates a crash right after the copy step. Staging must be discarded and nothing under
    revisions/ or the pointer may exist."""
    build_dir = tmp_path / "build"
    write_healthy_build(build_dir)
    artifacts_dir = tmp_path / "artifacts"

    real_copy = publish_dataset._copy_build_to_staging

    def copy_then_crash(build_dir_arg: Path, staging: Path) -> None:
        real_copy(build_dir_arg, staging)
        raise OSError("simulated failure after the copy step")

    monkeypatch.setattr(publish_dataset, "_copy_build_to_staging", copy_then_crash)

    with pytest.raises(OSError, match="simulated failure"):
        publish_dataset.publish(build_dir, "fixture", artifacts_dir)

    dataset_root = artifacts_dir / "fixture"
    assert not (dataset_root / "revisions").exists()
    assert not (dataset_root / "latest-revision.json").exists()
    staging_root = dataset_root / ".staging"
    assert not staging_root.exists() or not any(staging_root.iterdir())


def test_checksum_mutation_during_verify_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates _verify_copy catching a staged file that no longer matches its source."""
    build_dir = tmp_path / "build"
    write_healthy_build(build_dir)
    artifacts_dir = tmp_path / "artifacts"

    def failing_verify(build_dir_arg: Path, staging: Path) -> None:
        raise publish_dataset.PublishError("simulated checksum mutation during verify")

    monkeypatch.setattr(publish_dataset, "_verify_copy", failing_verify)

    with pytest.raises(publish_dataset.PublishError, match="simulated checksum mutation"):
        publish_dataset.publish(build_dir, "fixture", artifacts_dir)

    dataset_root = artifacts_dir / "fixture"
    assert not (dataset_root / "revisions").exists()
    assert not (dataset_root / "latest-revision.json").exists()


def test_retry_after_pointer_write_crash_completes_pointer(tmp_path: Path) -> None:
    """A first run whose rename succeeded but whose pointer write never happened (simulated by
    deleting the pointer after a real, successful publish) must be completed by a retry — not
    treated as already done and skipped, and not treated as a fresh publish that rewrites
    history."""
    build_dir = tmp_path / "build"
    write_healthy_build(build_dir)
    artifacts_dir = tmp_path / "artifacts"

    first_id = publish_dataset.publish(
        build_dir, "fixture", artifacts_dir, published_at="2026-01-01T00:00:00Z"
    )
    pointer_path = artifacts_dir / "fixture" / "latest-revision.json"
    pointer_path.unlink()  # simulate: rename succeeded, the crash was between rename and pointer

    retried_id = publish_dataset.publish(
        build_dir, "fixture", artifacts_dir, published_at="2099-01-01T00:00:00Z"
    )

    assert retried_id == first_id
    revisions_dir = artifacts_dir / "fixture" / "revisions"
    assert [p.name for p in revisions_dir.iterdir()] == [first_id], "no second revision created"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    assert pointer["revisionId"] == first_id
    assert pointer["publishedAt"] == "2026-01-01T00:00:00Z", (
        "the retry must not overwrite the original publish time with 'now'"
    )


def test_a_corrupted_existing_revision_is_refused_rather_than_overwritten(tmp_path: Path) -> None:
    """If revisions/{id} exists but no longer hashes to its own name, publishing again must not
    silently overwrite or trust it — that would defeat the immutability guarantee."""
    build_dir = tmp_path / "build"
    write_healthy_build(build_dir)
    artifacts_dir = tmp_path / "artifacts"

    revision_id = publish_dataset.publish(build_dir, "fixture", artifacts_dir)
    tampered = artifacts_dir / "fixture" / "revisions" / revision_id / "high" / "index.json"
    tampered.write_bytes(tampered.read_bytes() + b"\ntampered after publish\n")

    with pytest.raises(publish_dataset.PublishError, match="corrupted after a previous publish"):
        publish_dataset.publish(build_dir, "fixture", artifacts_dir)


def test_active_lease_defers_pruning(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    write_healthy_build(build_dir)
    artifacts_dir = tmp_path / "artifacts"
    cache_dir = tmp_path / "cache"

    first_id = publish_dataset.publish(
        build_dir,
        "fixture",
        artifacts_dir,
        keep=1,
        cache_dir=cache_dir,
        published_at="2026-01-01T00:00:00Z",
    )
    lease_dir = cache_dir / "leases" / first_id
    lease_dir.mkdir(parents=True)
    (lease_dir / "in-flight-request.lease").touch()

    (build_dir / "high" / "T1.tkms").write_bytes(b"tkms-fixture-high-v2")
    second_id = publish_dataset.publish(
        build_dir,
        "fixture",
        artifacts_dir,
        keep=1,
        cache_dir=cache_dir,
        published_at="2026-02-01T00:00:00Z",
    )

    revisions_dir = artifacts_dir / "fixture" / "revisions"
    assert (revisions_dir / first_id).exists(), "an active lease must defer pruning"
    assert (revisions_dir / second_id).exists()
    tombstone_path = artifacts_dir / "fixture" / "pruned-revisions.json"
    pruned_ids = (
        {t["revisionId"] for t in json.loads(tombstone_path.read_text())}
        if tombstone_path.exists()
        else set()
    )
    assert first_id not in pruned_ids

    (lease_dir / "in-flight-request.lease").unlink()
    (build_dir / "high" / "T1.tkms").write_bytes(b"tkms-fixture-high-v3")
    publish_dataset.publish(
        build_dir,
        "fixture",
        artifacts_dir,
        keep=1,
        cache_dir=cache_dir,
        published_at="2026-03-01T00:00:00Z",
    )

    assert not (revisions_dir / first_id).exists(), "the lease is gone, pruning now proceeds"
    assert not (cache_dir / "batch" / first_id).exists()
    assert not (cache_dir / "leases" / first_id).exists()
    pruned_ids = {t["revisionId"] for t in json.loads(tombstone_path.read_text())}
    assert first_id in pruned_ids


def test_keep_must_be_at_least_one(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    write_healthy_build(build_dir)

    with pytest.raises(publish_dataset.PublishError, match="--keep must be >= 1"):
        publish_dataset.publish(build_dir, "fixture", tmp_path / "artifacts", keep=0)


def test_pruned_revision_is_gone_but_pointer_stays_valid(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    write_healthy_build(build_dir)
    artifacts_dir = tmp_path / "artifacts"

    first_id = publish_dataset.publish(
        build_dir, "fixture", artifacts_dir, keep=1, published_at="2026-01-01T00:00:00Z"
    )
    (build_dir / "high" / "T1.tkms").write_bytes(b"tkms-fixture-high-v2")
    second_id = publish_dataset.publish(
        build_dir, "fixture", artifacts_dir, keep=1, published_at="2026-02-01T00:00:00Z"
    )

    revisions_dir = artifacts_dir / "fixture" / "revisions"
    assert not (revisions_dir / first_id).exists()
    assert (revisions_dir / second_id).exists()
    pointer = json.loads((artifacts_dir / "fixture" / "latest-revision.json").read_text())
    assert pointer["revisionId"] == second_id
