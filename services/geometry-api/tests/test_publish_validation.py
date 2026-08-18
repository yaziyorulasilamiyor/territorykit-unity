"""publish_dataset.py's fail-closed gate (FAZ-3-PLAN.md §1.1): two independent checks, both
required, before a single byte moves into artifacts_dir.

Written as **mutations of a healthy build**, the same discipline test_lod_scripts.py uses for
``manifest_validation.check()`` itself: each test takes a build that publishes cleanly, breaks
exactly one thing, and asserts the publisher now refuses — and that nothing was written.
"""

from __future__ import annotations

import json
from pathlib import Path

import publish_dataset
import pytest
from publish_fixtures import write_healthy_build, write_healthy_build_with_territories


def _assert_nothing_published(artifacts_dir: Path, dataset_id: str) -> None:
    dataset_root = artifacts_dir / dataset_id
    assert not dataset_root.exists() or not (dataset_root / "revisions").exists()
    assert not (dataset_root / "latest-revision.json").exists() if dataset_root.exists() else True


def test_the_healthy_build_publishes(tmp_path: Path) -> None:
    """Without this, every mutation below could be failing for the wrong reason."""
    build_dir = tmp_path / "build"
    write_healthy_build(build_dir)

    revision_id = publish_dataset.publish(build_dir, "fixture", tmp_path / "artifacts")

    assert len(revision_id) == 64


def test_a_manifest_that_lies_about_itself_is_rejected(tmp_path: Path) -> None:
    """check() catches this: pickingUnsafe set to False while the recorded events say True.

    This is the exact shape of the guarantee phase 2's fifth review round closed
    (docs/mesh-format.md's "lossy: true while pickingUnsafe: false is unreachable"). The reviewer
    who found phase 3's first draft of this test pointed out it had the direction backwards: a
    corrupted manifest reaching the API unchanged is a failure, not a demonstration that the API
    "faithfully relays" it. The correct behaviour is refusing to publish it at all.
    """
    build_dir = tmp_path / "build"
    report = write_healthy_build(build_dir)

    low = report["levels"]["low"]
    low["loss"]["events"] = [
        {
            "stage": "simplification",
            "kind": "dropped_part",
            "count": 1,
            "area": 500.0,
            "details": ["a dropped part"],
        }
    ]
    low["loss"]["lossy"] = True
    low["lossy"] = True
    # The lie: the events say picking is unsafe, but the flag claims otherwise.
    low["pickingUnsafe"] = False
    (build_dir / "lod-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )

    with pytest.raises(publish_dataset.PublishError, match="pickingUnsafe"):
        publish_dataset.publish(build_dir, "fixture", tmp_path / "artifacts")

    _assert_nothing_published(tmp_path / "artifacts", "fixture")


def test_a_report_that_does_not_match_its_own_index_json_is_rejected(tmp_path: Path) -> None:
    """The check check() cannot do on its own: the report can be internally consistent and still
    not describe the index.json files it is about to be published alongside — a stale report left
    next to a freshly rebuilt manifest, for instance.
    """
    build_dir = tmp_path / "build"
    write_healthy_build(build_dir)

    manifest_path = build_dir / "medium" / "index.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["totals"]["vertices"] += 1
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    with pytest.raises(publish_dataset.PublishError, match="does not match the staged index"):
        publish_dataset.publish(build_dir, "fixture", tmp_path / "artifacts")

    _assert_nothing_published(tmp_path / "artifacts", "fixture")


def test_a_missing_lod_report_is_rejected(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    write_healthy_build(build_dir)
    (build_dir / "lod-report.json").unlink()

    with pytest.raises(publish_dataset.PublishError, match="lod-report.json"):
        publish_dataset.publish(build_dir, "fixture", tmp_path / "artifacts")


# ---- manifest <-> mesh cross-check (W1): check() and check_report_matches_build() never open
# a single .tkms file, so neither can catch a manifest pointing at a missing or swapped mesh. ----


def test_a_deleted_mesh_file_the_manifest_still_references_is_rejected(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    write_healthy_build(build_dir)
    (build_dir / "high" / "T1.tkms").unlink()

    with pytest.raises(publish_dataset.PublishError, match="missing mesh file"):
        publish_dataset.publish(build_dir, "fixture", tmp_path / "artifacts")

    _assert_nothing_published(tmp_path / "artifacts", "fixture")


def test_a_mesh_file_swapped_with_another_territorys_is_rejected(tmp_path: Path) -> None:
    """The scenario check_report_matches_build() cannot see: totals/flags are untouched, only
    one territory's bytes on disk are wrong — replaced with a *different*, equally valid
    territory's mesh (same directory, same naming convention, both individually legitimate
    files). Only a per-file content check catches this."""
    build_dir = tmp_path / "build"
    write_healthy_build_with_territories(build_dir, ("T1", "T2"))

    t1_path = build_dir / "high" / "T1.tkms"
    t2_path = build_dir / "high" / "T2.tkms"
    t1_path.write_bytes(t2_path.read_bytes())  # T1's file now holds T2's content

    with pytest.raises(publish_dataset.PublishError, match="does not match the manifest's sha256"):
        publish_dataset.publish(build_dir, "fixture", tmp_path / "artifacts")

    _assert_nothing_published(tmp_path / "artifacts", "fixture")


def test_an_unreferenced_extra_mesh_file_is_rejected(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    write_healthy_build(build_dir)
    (build_dir / "high" / "ghost.tkms").write_bytes(b"nobody claims this file")

    with pytest.raises(publish_dataset.PublishError, match="no territory in the manifest"):
        publish_dataset.publish(build_dir, "fixture", tmp_path / "artifacts")

    _assert_nothing_published(tmp_path / "artifacts", "fixture")


def test_a_valid_build_publishes_index_json_unchanged(tmp_path: Path) -> None:
    """The manifest a client reads through the API is exactly the manifest the build wrote —
    the API is a mirror of an already-validated file, not a second opinion (FAZ-3-PLAN.md §8)."""
    build_dir = tmp_path / "build"
    write_healthy_build(build_dir)
    artifacts_dir = tmp_path / "artifacts"

    revision_id = publish_dataset.publish(build_dir, "fixture", artifacts_dir)

    for lod in ("high", "medium", "low"):
        source = (build_dir / lod / "index.json").read_bytes()
        published = (
            artifacts_dir / "fixture" / "revisions" / revision_id / lod / "index.json"
        ).read_bytes()
        assert source == published
