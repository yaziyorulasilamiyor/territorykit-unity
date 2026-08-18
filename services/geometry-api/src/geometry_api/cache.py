"""Content-addressed cache for assembled TKMB batch responses (FAZ-3-PLAN.md §10.3).

Assembling a batch is pure I/O — concatenating already-precomputed ``.tkms``/``.tkms.gz`` files
(§9, §12) — never geometry work, so caching it is about not repeating the concatenation, not
about avoiding a computation the "no geometry at request time" rule already forbids doing twice.

**Keyed and stored per revision, not globally (§10.3, Z10).** ``cache_dir/batch/{revisionId}/
{key}.tkmb`` — a real directory named after the revision, not a filename that merely *contains*
it. That is what lets ``scripts/publish_dataset.py`` clean up a pruned revision's cache entries
with a single ``rmtree`` instead of a filename pattern search, and what makes it cheap for a
caller to check *revision* validity before ever touching the cache (§10.3, X15): the route
resolves the revision through :mod:`geometry_api.registry` first, and only passes a
``revisionId`` in here that is already known to still exist.

**Writes are atomic; there is no lock.** Two concurrent misses for the same key may both compute
and both write — harmless, because the computation is deterministic (same inputs, same bytes) and
each write is temp-file-then-``os.replace``, so a reader never observes a partially written file.

**Eviction is size-bounded and LRU-ish, not time-bounded.** ``get()`` touches the file's mtime on
a hit; when a ``put()`` would push the batch cache over ``max_bytes``, the least-recently-*read*
files are deleted first until it is back under the limit. No background thread — eviction only
ever runs inline, right before a write.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4


class BatchCache:
    def __init__(self, cache_dir: Path, max_bytes: int) -> None:
        self._root = Path(cache_dir) / "batch"
        self._max_bytes = max_bytes

    @staticmethod
    def compute_key(
        dataset_id: str, lod: str, entry_encoding: str, territory_ids: Iterable[str]
    ) -> str:
        """Order-independent: ``[A, B]`` and ``[B, A]`` produce the same key (§10.2/Z9's TOC-order
        fix depends on this — the same key must always mean the same bytes)."""
        canonical = f"{dataset_id}/{lod}/{entry_encoding}/" + ",".join(sorted(set(territory_ids)))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _path(self, revision_id: str, key: str) -> Path:
        return self._root / revision_id / f"{key}.tkmb"

    def get(self, revision_id: str, key: str) -> bytes | None:
        path = self._path(revision_id, key)
        try:
            data = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            return None
        try:
            os.utime(path, None)
        except OSError:  # pragma: no cover - best-effort LRU bookkeeping only
            pass
        return data

    def put(self, revision_id: str, key: str, data: bytes) -> None:
        path = self._path(revision_id, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Named from a fresh uuid rather than "{key}.tmp-{uuid}" — revision_id and key are each a
        # 64-char sha256 hex digest, and stacking both into one path component (on top of a deep
        # pytest tmp_path) is enough to clear Windows' 260-character MAX_PATH.
        tmp = path.parent / f".tmp-{uuid4().hex}"
        tmp.write_bytes(data)
        try:
            os.replace(tmp, path)
        except OSError:
            # Windows can raise a sharing violation when two writers race to replace the same
            # destination at once — POSIX rename has no such restriction, but MoveFileEx does.
            # Cache entries are content-addressed, so a concurrent writer landing first means the
            # destination already holds the same bytes this call was about to write; discard our
            # own temp file rather than treating that as a real failure. If the destination truly
            # isn't there, this was a different problem and the error is real.
            tmp.unlink(missing_ok=True)
            if not path.exists():
                raise
        self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        if not self._root.is_dir():
            return
        files = [p for p in self._root.rglob("*.tkmb") if p.is_file()]
        sizes = {p: p.stat().st_size for p in files}
        total = sum(sizes.values())
        if total <= self._max_bytes:
            return
        for path in sorted(files, key=lambda p: p.stat().st_mtime):
            if total <= self._max_bytes:
                break
            try:
                path.unlink()
                total -= sizes[path]
            except OSError:  # pragma: no cover - a concurrent evictor already removed it
                continue
