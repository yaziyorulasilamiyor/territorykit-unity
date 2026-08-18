"""BatchCache: order-independent keys, revision-namespaced storage, size-bounded LRU eviction."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from geometry_api.cache import BatchCache


def test_compute_key_ignores_territory_id_order() -> None:
    key_ab = BatchCache.compute_key("tr-adm1", "high", "identity", ["06", "34"])
    key_ba = BatchCache.compute_key("tr-adm1", "high", "identity", ["34", "06"])

    assert key_ab == key_ba


def test_compute_key_ignores_duplicate_ids() -> None:
    key_once = BatchCache.compute_key("tr-adm1", "high", "identity", ["06"])
    key_twice = BatchCache.compute_key("tr-adm1", "high", "identity", ["06", "06"])

    assert key_once == key_twice


def test_compute_key_differs_by_dataset_lod_and_entry_encoding() -> None:
    base = BatchCache.compute_key("tr-adm1", "high", "identity", ["06"])
    assert base != BatchCache.compute_key("other", "high", "identity", ["06"])
    assert base != BatchCache.compute_key("tr-adm1", "medium", "identity", ["06"])
    assert base != BatchCache.compute_key("tr-adm1", "high", "gzip", ["06"])


def test_get_before_any_put_is_a_miss(tmp_path: Path) -> None:
    cache = BatchCache(tmp_path, max_bytes=1_000_000)
    assert cache.get("rev-a", "some-key") is None


def test_put_then_get_round_trips(tmp_path: Path) -> None:
    cache = BatchCache(tmp_path, max_bytes=1_000_000)
    cache.put("rev-a", "key-1", b"tkmb-bytes")

    assert cache.get("rev-a", "key-1") == b"tkmb-bytes"


def test_entries_are_namespaced_by_revision_on_disk(tmp_path: Path) -> None:
    cache = BatchCache(tmp_path, max_bytes=1_000_000)
    cache.put("rev-a", "key-1", b"bytes-a")

    assert (tmp_path / "batch" / "rev-a" / "key-1.tkmb").read_bytes() == b"bytes-a"
    assert cache.get("rev-b", "key-1") is None, "same key, different revision, must not collide"


def test_pruning_a_revision_directory_removes_only_its_own_entries(tmp_path: Path) -> None:
    import shutil

    cache = BatchCache(tmp_path, max_bytes=1_000_000)
    cache.put("rev-a", "key-1", b"a")
    cache.put("rev-b", "key-1", b"b")

    shutil.rmtree(tmp_path / "batch" / "rev-a")

    assert cache.get("rev-a", "key-1") is None
    assert cache.get("rev-b", "key-1") == b"b"


def test_eviction_keeps_total_size_under_the_limit(tmp_path: Path) -> None:
    cache = BatchCache(tmp_path, max_bytes=30)
    cache.put("rev-a", "k1", b"x" * 10)
    cache.put("rev-a", "k2", b"x" * 10)
    cache.put("rev-a", "k3", b"x" * 10)
    cache.put("rev-a", "k4", b"x" * 10)  # pushes total to 40 > 30, must evict

    total = sum(p.stat().st_size for p in (tmp_path / "batch").rglob("*.tkmb"))
    assert total <= 30


def test_touching_an_entry_with_get_protects_it_from_eviction(tmp_path: Path) -> None:
    cache = BatchCache(tmp_path, max_bytes=25)
    cache.put("rev-a", "old", b"x" * 10)
    time.sleep(0.02)
    cache.put("rev-a", "new", b"x" * 10)
    time.sleep(0.02)
    cache.get("rev-a", "old")  # touch: "old" is now more recently used than "new"
    time.sleep(0.02)

    cache.put("rev-a", "newest", b"x" * 10)  # total would be 30 > 25, one entry must go

    assert cache.get("rev-a", "old") is not None, "touched entry should survive eviction"
    assert cache.get("rev-a", "newest") is not None


def test_a_replace_race_where_a_concurrent_writer_already_won_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows can raise a sharing violation when two writers race os.replace onto the same
    destination — POSIX rename has no such restriction. Since entries are content-addressed, a
    concurrent writer landing first means the destination already holds the bytes this call was
    about to write; that must not surface as a put() failure."""
    import geometry_api.cache as cache_module

    cache = BatchCache(tmp_path, max_bytes=1_000_000)
    real_replace = cache_module.os.replace

    def racing_replace(src: object, dst: object) -> None:
        # This call is standing in for "another writer's os.replace already landed first".
        real_replace(src, dst)
        raise PermissionError("simulated concurrent-replace sharing violation")

    monkeypatch.setattr(cache_module.os, "replace", racing_replace)

    cache.put("rev-a", "key-1", b"the-bytes")  # must not raise

    assert cache.get("rev-a", "key-1") == b"the-bytes"


def test_a_replace_failure_with_no_winning_writer_still_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import geometry_api.cache as cache_module

    cache = BatchCache(tmp_path, max_bytes=1_000_000)

    def always_fails(src: object, dst: object) -> None:
        raise PermissionError("simulated failure, nothing ever gets written")

    monkeypatch.setattr(cache_module.os, "replace", always_fails)

    with pytest.raises(PermissionError):
        cache.put("rev-a", "key-1", b"the-bytes")
