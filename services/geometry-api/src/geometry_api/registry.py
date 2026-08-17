"""Resolves a dataset + optional ``?revision=`` into the exact published revision to serve.

The single place every route goes through before touching ``artifacts_dir`` (FAZ-3-PLAN.md §3.4).
Framework-free on purpose: this module knows nothing about FastAPI or HTTP status codes, only
about the on-disk contract ``scripts/publish_dataset.py`` writes. ``geometry_api.deps`` wraps
:meth:`DatasetRegistry.resolve` and :meth:`DatasetRegistry.lease` into the FastAPI dependency
every route actually calls, translating the exceptions raised here into the one JSON error shape
(``geometry_api.errors``).

**What is cached and why.** Re-reading ``latest-revision.json`` or re-hashing a whole revision on
every request would be exactly the per-request heavy work Phase 3 forbids (§1). So:

* The current-revision pointer is cached per dataset, invalidated by comparing the file's
  ``mtime`` — one ``stat()`` per request, not a re-read, and never stale by more than one publish.
* A revision's integrity (§3.5a: does it still hash to its own name?) is checked **once** per
  process, the first time that revision is resolved — including the moment it *becomes* current,
  since that is naturally the first time a request resolves it after the pointer moves — and the
  boolean is cached forever after, because a published revision's bytes never change.
* The tombstone list (``pruned-revisions.json``) is cached the same way as the pointer.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from geometry_api.revisions import compute_revision_id

POINTER_FILENAME = "latest-revision.json"
TOMBSTONE_FILENAME = "pruned-revisions.json"
META_FILENAME = "_meta.json"


class RegistryError(Exception):
    """Base for every resolution failure. ``geometry_api.deps`` maps each subclass to a status."""


class DatasetNotFoundError(RegistryError):
    def __init__(self, dataset_id: str) -> None:
        super().__init__(f"no published dataset {dataset_id!r}")
        self.dataset_id = dataset_id


class RevisionNotFoundError(RegistryError):
    def __init__(self, dataset_id: str, revision_id: str) -> None:
        super().__init__(f"revision {revision_id!r} does not exist for dataset {dataset_id!r}")
        self.dataset_id = dataset_id
        self.revision_id = revision_id


class RevisionGoneError(RegistryError):
    """The revision existed and was later pruned. §3.3: this is 410, not 404."""

    def __init__(self, dataset_id: str, revision_id: str, pruned_at: str) -> None:
        super().__init__(
            f"revision {revision_id!r} of dataset {dataset_id!r} was pruned at {pruned_at}"
        )
        self.dataset_id = dataset_id
        self.revision_id = revision_id
        self.pruned_at = pruned_at


class RevisionCorruptedError(RegistryError):
    """§3.5a: a published revision no longer hashes to its own directory name."""

    def __init__(self, dataset_id: str, revision_id: str) -> None:
        super().__init__(
            f"revision {revision_id!r} of dataset {dataset_id!r} does not hash to its own name "
            f"— corrupted after publish"
        )
        self.dataset_id = dataset_id
        self.revision_id = revision_id


class TerritoryNotFoundError(RegistryError):
    def __init__(self, dataset_id: str, revision_id: str, territory_id: str, lod: str) -> None:
        super().__init__(
            f"territory {territory_id!r} does not exist at lod {lod!r} of "
            f"{dataset_id}@{revision_id}"
        )
        self.dataset_id = dataset_id
        self.revision_id = revision_id
        self.territory_id = territory_id
        self.lod = lod


@dataclass(frozen=True)
class ResolvedRevision:
    """What every route needs after resolving a dataset + optional ``?revision=``."""

    dataset_id: str
    revision_id: str
    published_at: str
    is_current: bool
    path: Path
    """``artifacts_dir/{dataset_id}/revisions/{revision_id}`` — contains high/medium/low."""


class DatasetRegistry:
    def __init__(self, artifacts_dir: Path, cache_dir: Path) -> None:
        self._artifacts_dir = artifacts_dir
        self._cache_dir = cache_dir
        self._pointer_cache: dict[str, tuple[float, str, str]] = {}
        self._tombstone_cache: dict[str, tuple[float, dict[str, str]]] = {}
        self._verified: set[tuple[str, str]] = set()
        self._manifest_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._etags_cache: dict[tuple[str, str, str], dict[str, str]] = {}

    # ---- dataset discovery ---------------------------------------------------------------

    def known_dataset_ids(self) -> list[str]:
        """Every dataset directory under artifacts_dir right now — rescanned each call.

        A plain directory listing, not geometry work, so re-doing it per call keeps a freshly
        published dataset visible without a restart.
        """
        if not self._artifacts_dir.is_dir():
            return []
        return sorted(p.name for p in self._artifacts_dir.iterdir() if p.is_dir())

    # ---- resolution -----------------------------------------------------------------------

    def resolve(self, dataset_id: str, requested_revision: str | None = None) -> ResolvedRevision:
        """The single resolution point (§3.4). Called once per request, result used throughout."""
        dataset_root = self._artifacts_dir / dataset_id
        if not dataset_root.is_dir():
            raise DatasetNotFoundError(dataset_id)

        if requested_revision is None:
            revision_id, published_at = self._current_pointer(dataset_id)
            is_current = True
        else:
            revision_id = requested_revision
            current_id, _ = self._safe_current_pointer(dataset_id)
            is_current = revision_id == current_id

        revision_path = dataset_root / "revisions" / revision_id
        if not revision_path.is_dir():
            pruned_at = self._pruned_at(dataset_id, revision_id)
            if pruned_at is not None:
                raise RevisionGoneError(dataset_id, revision_id, pruned_at)
            raise RevisionNotFoundError(dataset_id, revision_id)

        self._verify_integrity(dataset_id, revision_id, revision_path)

        if requested_revision is not None:
            published_at = self._meta_published_at(revision_path)

        return ResolvedRevision(dataset_id, revision_id, published_at, is_current, revision_path)

    def _safe_current_pointer(self, dataset_id: str) -> tuple[str | None, str | None]:
        try:
            return self._current_pointer(dataset_id)
        except DatasetNotFoundError:
            return None, None

    def _current_pointer(self, dataset_id: str) -> tuple[str, str]:
        pointer_path = self._artifacts_dir / dataset_id / POINTER_FILENAME
        if not pointer_path.exists():
            raise DatasetNotFoundError(dataset_id)
        mtime = pointer_path.stat().st_mtime
        cached = self._pointer_cache.get(dataset_id)
        if cached is not None and cached[0] == mtime:
            return cached[1], cached[2]
        try:
            data = json.loads(pointer_path.read_text(encoding="utf-8"))
            revision_id, published_at = str(data["revisionId"]), str(data["publishedAt"])
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise DatasetNotFoundError(dataset_id) from exc
        self._pointer_cache[dataset_id] = (mtime, revision_id, published_at)
        return revision_id, published_at

    def _pruned_at(self, dataset_id: str, revision_id: str) -> str | None:
        path = self._artifacts_dir / dataset_id / TOMBSTONE_FILENAME
        if not path.exists():
            return None
        mtime = path.stat().st_mtime
        cached = self._tombstone_cache.get(dataset_id)
        if cached is None or cached[0] != mtime:
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
                mapping = {str(e["revisionId"]): str(e["prunedAt"]) for e in entries}
            except (json.JSONDecodeError, KeyError, TypeError):
                mapping = {}
            cached = (mtime, mapping)
            self._tombstone_cache[dataset_id] = cached
        return cached[1].get(revision_id)

    def _verify_integrity(self, dataset_id: str, revision_id: str, revision_path: Path) -> None:
        key = (dataset_id, revision_id)
        if key in self._verified:
            return
        if compute_revision_id(revision_path) != revision_id:
            raise RevisionCorruptedError(dataset_id, revision_id)
        self._verified.add(key)

    def _meta_published_at(self, revision_path: Path) -> str:
        meta = json.loads((revision_path / META_FILENAME).read_text(encoding="utf-8"))
        return str(meta["publishedAt"])

    # ---- manifests --------------------------------------------------------------------------

    def load_manifest(self, resolved: ResolvedRevision, lod: str) -> dict[str, Any]:
        """The published ``index.json`` for one level. Cached — a revision's content never
        changes once it has passed :meth:`_verify_integrity`."""
        key = (resolved.dataset_id, resolved.revision_id, lod)
        cached = self._manifest_cache.get(key)
        if cached is not None:
            return cached
        manifest: dict[str, Any] = json.loads(
            (resolved.path / lod / "index.json").read_text(encoding="utf-8")
        )
        self._manifest_cache[key] = manifest
        return manifest

    def load_etags(self, resolved: ResolvedRevision, lod: str) -> dict[str, str]:
        """filename -> quoted strong ETag, from the file ``publish_dataset.py`` precomputed
        (§11.1: ETags are never hashed at request time). Cached like :meth:`load_manifest`."""
        key = (resolved.dataset_id, resolved.revision_id, lod)
        cached = self._etags_cache.get(key)
        if cached is not None:
            return cached
        etags: dict[str, str] = json.loads(
            (resolved.path / lod / "etags.json").read_text(encoding="utf-8")
        )
        self._etags_cache[key] = etags
        return etags

    def territory_entry(
        self, resolved: ResolvedRevision, lod: str, territory_id: str
    ) -> dict[str, Any]:
        manifest = self.load_manifest(resolved, lod)
        for entry in manifest.get("territories", []):
            if entry.get("id") == territory_id:
                return entry
        raise TerritoryNotFoundError(resolved.dataset_id, resolved.revision_id, territory_id, lod)

    # ---- leases (§3.4) ----------------------------------------------------------------------

    @contextmanager
    def lease(self, revision_id: str) -> Iterator[None]:
        """Marks ``revision_id`` as actively being read for the lifetime of the ``with`` block.

        Lives under ``cache_dir``, never ``artifacts_dir`` — the API process only ever writes
        here, keeping the published tree read-only from its point of view (§2). A separate
        process, ``scripts/publish_dataset.py``, checks this directory before pruning.
        """
        lease_dir = self._cache_dir / "leases" / revision_id
        lease_dir.mkdir(parents=True, exist_ok=True)
        lease_path = lease_dir / f"{uuid4().hex}.lease"
        lease_path.touch()
        try:
            yield
        finally:
            lease_path.unlink(missing_ok=True)

    def active_lease_count(self, revision_id: str) -> int:
        """For tests and /metrics — not used in pruning decisions (that logic lives in
        scripts/publish_dataset.py, a separate process)."""
        lease_dir = self._cache_dir / "leases" / revision_id
        if not lease_dir.is_dir():
            return 0
        return sum(1 for p in lease_dir.iterdir() if p.is_file())
