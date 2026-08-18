"""Publishes a ``scripts/build_lod.py`` output as an immutable, revision-identified artifact.

    python scripts/publish_dataset.py --build-dir <build_lod output> --dataset-id <id>
                                       [--artifacts-dir data/artifacts] [--keep N]

This is the only writer of ``artifacts_dir`` (``docs/phases/FAZ-3-PLAN.md`` §2) — the directory
tree the running API serves from. The API process never computes geometry and never validates a
manifest at request time; both of those happen exactly once, here, before a revision is allowed
to exist under ``revisions/`` at all.

**The gate, and what it runs against (§1.1).** Everything is copied into a staging directory
*first*; every check below runs against that staged snapshot, never against ``build_dir``
directly. ``build_dir`` is not this process's to hold still — nothing stops another process from
rebuilding into it mid-publish — so reading it twice (once to validate, once to copy) would let
the two reads see different data. Copy once, then only ever look at the copy:

1. ``_verify_copy()`` — did every byte make it into staging unchanged?
2. ``manifest_validation.check()`` — is the staged report internally consistent (the same check
   ``scripts/check_lod_report.py`` runs in CI)?
3. ``manifest_validation.check_report_matches_build()`` — does the staged report actually
   describe the staged ``index.json`` files, or could one be stale relative to the other?
4. ``_validate_manifest_matches_meshes()`` — for every territory every staged ``index.json``
   claims: does its ``.tkms`` file exist, is it the declared length, does it hash to the declared
   ``sha256``? This is the check the first three cannot do: a manifest can be perfectly
   self-consistent and still point at a deleted file, or at a *different* territory's mesh that
   happens to already exist on disk (a copy/rename mistake upstream, not a hypothetical — this is
   exactly the class of bug the checks above cannot see because they never open a `.tkms` file).

Any of the four failing stops the run before ``artifacts_dir`` is touched.

**The publish sequence (§3.2).** Copy to a staging directory on the same filesystem, validate the
staged snapshot as above, hash the *staging snapshot* (not ``build_dir`` — see
``geometry_api.revisions``) to get the revision id, then ``os.rename`` staging into
``revisions/{revisionId}/`` — atomic on a POSIX or NTFS filesystem when source and destination
share a volume. Only after that succeeds does ``latest-revision.json`` get repointed, and only
with an atomic temp-file-then-replace write (§3.3, §3.4 use the same helper for the tombstone
file). A run that finds ``revisions/{revisionId}/`` already present (a previous run's rename
succeeded but crashed before repointing, or this exact build was already published) re-verifies
it and completes the pointer update rather than silently doing nothing — see
``test_retry_after_pointer_write_crash_completes_pointer`` in
``tests/test_publish_atomicity.py``.

**Retention (§3.3, §3.4).** Dataset-wide, not per level — one revision already spans
high/medium/low. Pruning skips (defers to the next run) any revision with an unexpired lease file
under ``cache_dir/leases/{revisionId}/``, so an in-flight request cannot have its files deleted
out from under it. A pruned revision is recorded in ``pruned-revisions.json`` *before* its
directory is removed, so the API can answer ``410 Gone`` rather than ``404`` for a URL that used
to work.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from geometry_api import manifest_validation
from geometry_api.config import settings
from geometry_api.revisions import LOD_LEVELS, compute_revision_id

META_FILENAME = "_meta.json"
POINTER_FILENAME = "latest-revision.json"
TOMBSTONE_FILENAME = "pruned-revisions.json"


class PublishError(RuntimeError):
    """Raised when a build cannot be published. Nothing under artifacts_dir has been changed."""


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _etag_for(data: bytes) -> str:
    return '"' + hashlib.sha256(data).hexdigest() + '"'


def _write_json_atomic(path: Path, data: Any) -> None:
    """Write ``path`` so a reader never sees a partial file: temp file, then ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".tmp-{uuid4().hex}"
    tmp.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def _load_report(root: Path) -> dict[str, Any]:
    report_path = root / "lod-report.json"
    if not report_path.exists():
        raise PublishError(
            f"{report_path} does not exist — run scripts/build_lod.py first, "
            f"then publish its output directory"
        )
    try:
        report: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PublishError(f"{report_path} is not valid JSON: {exc}") from exc
    return report


def _validate_report(report: dict[str, Any], staging: Path) -> None:
    """The fail-closed gate (§1.1), run against the staged snapshot. Raises with every failure
    named, changes nothing."""
    failures = manifest_validation.check(report)
    if failures:
        raise PublishError(
            "lod-report.json failed validation — refusing to publish:\n  " + "\n  ".join(failures)
        )
    failures = manifest_validation.check_report_matches_build(report, staging)
    if failures:
        raise PublishError(
            "lod-report.json does not match the staged index.json files — refusing to "
            "publish:\n  " + "\n  ".join(failures)
        )


def _copy_build_to_staging(build_dir: Path, staging: Path) -> None:
    """Copy the report, every mesh and every manifest into staging *before* anything reads them,
    generating the gzip variant and etags alongside.

    Everything this process validates or hashes from here on reads only from ``staging`` — never
    ``build_dir`` again — so a concurrent rebuild of ``build_dir`` cannot make the checks and the
    published bytes disagree about what they saw (§1.1).

    Only ``lod-report.json``, ``.tkms`` and ``index.json`` come from ``build_dir``;
    ``*.tkms.gz`` and ``etags.json`` are produced here, once, so the API never compresses at
    request time (Phase 3 rule: no runtime work that could have been precomputed).
    """
    report_path = build_dir / "lod-report.json"
    if not report_path.exists():
        raise PublishError(
            f"{report_path} does not exist — run scripts/build_lod.py first, "
            f"then publish its output directory"
        )
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "lod-report.json").write_bytes(report_path.read_bytes())

    for lod in LOD_LEVELS:
        source_dir = build_dir / lod
        if not source_dir.is_dir():
            raise PublishError(f"{source_dir} does not exist — incomplete build_dir")
        staged_dir = staging / lod
        staged_dir.mkdir(parents=True)
        etags: dict[str, str] = {}
        for path in sorted(source_dir.iterdir()):
            if not path.is_file():
                continue
            data = path.read_bytes()
            (staged_dir / path.name).write_bytes(data)
            etags[path.name] = _etag_for(data)
            if path.suffix == ".tkms":
                gz_data = gzip.compress(data, compresslevel=9, mtime=0)
                gz_name = path.name + ".gz"
                (staged_dir / gz_name).write_bytes(gz_data)
                etags[gz_name] = _etag_for(gz_data)
        (staged_dir / "etags.json").write_text(
            json.dumps(etags, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def _verify_copy(build_dir: Path, staging: Path) -> None:
    """Prove the staged ``lod-report.json``/``.tkms``/``index.json`` files are byte-for-byte the
    source's — catches copy corruption, not what the source *meant*, which is what
    ``_validate_report``/``_validate_manifest_matches_meshes`` check next, on the staged copy.

    Does not touch the generated ``*.tkms.gz``/``etags.json`` — those have no source-side
    counterpart to compare against; they are checked for real by the round-trip decode tests on
    ``geometry_api.encoding`` and by the gzip content-coding tests on the mesh route.
    """
    report_source = (build_dir / "lod-report.json").read_bytes()
    report_staged = (staging / "lod-report.json").read_bytes()
    if report_source != report_staged:
        raise PublishError("lod-report.json: staged copy does not match the source byte-for-byte")

    for lod in LOD_LEVELS:
        source_dir = build_dir / lod
        staged_dir = staging / lod
        source_files = {p.name for p in source_dir.iterdir() if p.is_file()}
        staged_relevant = {
            p.name for p in staged_dir.iterdir() if p.is_file() and p.name in source_files
        }
        if source_files != staged_relevant:
            missing = sorted(source_files - staged_relevant)
            extra = sorted(staged_relevant - source_files)
            raise PublishError(
                f"{lod}: staged file set does not match build_dir (missing {missing}, "
                f"unexpected {extra})"
            )
        for name in source_files:
            source_bytes = (source_dir / name).read_bytes()
            staged_bytes = (staged_dir / name).read_bytes()
            if source_bytes != staged_bytes:
                raise PublishError(
                    f"{lod}/{name}: staged copy does not match the source byte-for-byte"
                )


def _validate_manifest_matches_meshes(staging: Path) -> None:
    """Prove every territory a staged manifest claims actually has the mesh it claims.

    ``check()`` and ``check_report_matches_build()`` only compare level-wide summary numbers
    (totals, flags) — neither opens a single ``.tkms`` file. A manifest can be internally
    consistent, and can agree with the report, while ``territories[i].file`` names a file that
    was deleted, or that was silently replaced with a *different* territory's mesh (same
    directory, same filename convention, wrong bytes). Only opening the file and checking its
    length and content hash against what the manifest itself declares catches that.
    """
    for lod in LOD_LEVELS:
        level_dir = staging / lod
        manifest = json.loads((level_dir / "index.json").read_text(encoding="utf-8"))
        territories = manifest.get("territories", [])

        claimed: dict[str, dict[str, Any]] = {}
        for entry in territories:
            filename = entry.get("file")
            if not filename:
                raise PublishError(f"{lod}: territory {entry.get('id')!r} has no 'file' field")
            if filename in claimed:
                raise PublishError(
                    f"{lod}: territories {claimed[filename].get('id')!r} and "
                    f"{entry.get('id')!r} both claim mesh file {filename!r}"
                )
            claimed[filename] = entry

        on_disk = {path.name for path in level_dir.glob("*.tkms")}
        missing = sorted(set(claimed) - on_disk)
        if missing:
            raise PublishError(f"{lod}: manifest references missing mesh file(s): {missing}")
        extra = sorted(on_disk - set(claimed))
        if extra:
            raise PublishError(
                f"{lod}: mesh file(s) on disk that no territory in the manifest references: {extra}"
            )

        for filename, entry in claimed.items():
            territory_id = entry.get("id")
            data = (level_dir / filename).read_bytes()

            expected_length = entry.get("byteLength")
            if not isinstance(expected_length, int):
                raise PublishError(
                    f"{lod}/{filename}: manifest has no integer 'byteLength' for territory "
                    f"{territory_id!r}"
                )
            if len(data) != expected_length:
                raise PublishError(
                    f"{lod}/{filename}: file is {len(data)} bytes, manifest declares "
                    f"byteLength={expected_length} for territory {territory_id!r}"
                )

            expected_hash = entry.get("sha256")
            if not isinstance(expected_hash, str):
                raise PublishError(
                    f"{lod}/{filename}: manifest has no 'sha256' for territory {territory_id!r}"
                )
            actual_hash = hashlib.sha256(data).hexdigest()
            if actual_hash != expected_hash:
                raise PublishError(
                    f"{lod}/{filename}: content does not match the manifest's sha256 for "
                    f"territory {territory_id!r} (got {actual_hash}, expected {expected_hash}) "
                    f"— the file may have been replaced with a different territory's mesh"
                )


def _has_active_lease(cache_dir: Path, revision_id: str, stale_after_seconds: float) -> bool:
    """True if a request may currently be reading this revision (§3.4)."""
    lease_dir = cache_dir / "leases" / revision_id
    if not lease_dir.is_dir():
        return False
    now = time.time()
    return any(
        (now - lease_file.stat().st_mtime) < stale_after_seconds
        for lease_file in lease_dir.iterdir()
        if lease_file.is_file()
    )


def _tombstone(dataset_root: Path, revision_id: str, pruned_at: str) -> None:
    path = dataset_root / TOMBSTONE_FILENAME
    existing: list[dict[str, str]] = []
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    existing.append({"revisionId": revision_id, "prunedAt": pruned_at})
    _write_json_atomic(path, existing)


@dataclass(frozen=True)
class _RevisionRecord:
    revision_id: str
    published_at: str


def _list_published_revisions(dataset_root: Path) -> list[_RevisionRecord]:
    revisions_dir = dataset_root / "revisions"
    if not revisions_dir.is_dir():
        return []
    records = []
    for path in revisions_dir.iterdir():
        meta_path = path / META_FILENAME
        if not path.is_dir() or not meta_path.exists():
            continue  # not (yet) a fully-published revision — never prune a half-written one
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            records.append(
                _RevisionRecord(revision_id=path.name, published_at=str(meta["publishedAt"]))
            )
        except (json.JSONDecodeError, KeyError):
            continue
    return records


def _prune_old_revisions(
    dataset_root: Path, keep: int, current_revision_id: str, cache_dir: Path
) -> None:
    records = _list_published_revisions(dataset_root)
    # ISO 8601 with a fixed format sorts lexicographically in time order.
    records.sort(key=lambda r: r.published_at, reverse=True)
    keep_ids = {current_revision_id} | {r.revision_id for r in records[:keep]}

    for record in records:
        if record.revision_id in keep_ids:
            continue
        if _has_active_lease(cache_dir, record.revision_id, settings.lease_stale_after_seconds):
            continue  # deferred: an in-flight request may still be reading this revision
        _tombstone(dataset_root, record.revision_id, _utcnow_iso())
        shutil.rmtree(dataset_root / "revisions" / record.revision_id, ignore_errors=True)
        shutil.rmtree(cache_dir / "batch" / record.revision_id, ignore_errors=True)
        shutil.rmtree(cache_dir / "leases" / record.revision_id, ignore_errors=True)


def publish(
    build_dir: Path,
    dataset_id: str,
    artifacts_dir: Path,
    *,
    keep: int | None = None,
    published_at: str | None = None,
    cache_dir: Path | None = None,
) -> str:
    """Validate, publish and prune. Returns the published revision id. See module docstring."""
    resolved_keep = settings.revision_retain_count if keep is None else keep
    if resolved_keep < 1:
        raise PublishError(
            f"--keep must be >= 1 (the just-published revision always counts as kept), "
            f"got {resolved_keep}"
        )
    resolved_cache_dir = Path(settings.cache_dir) if cache_dir is None else cache_dir

    dataset_root = artifacts_dir / dataset_id
    staging = dataset_root / ".staging" / uuid4().hex
    try:
        # Copy first, validate only the copy (§1.1) — build_dir is never read again after this
        # call returns, so a concurrent rebuild of it cannot make what was checked and what gets
        # published disagree.
        _copy_build_to_staging(build_dir, staging)
        _verify_copy(build_dir, staging)

        report = _load_report(staging)
        _validate_report(report, staging)
        _validate_manifest_matches_meshes(staging)

        revision_id = compute_revision_id(staging)
        final = dataset_root / "revisions" / revision_id

        if final.exists():
            if compute_revision_id(final) != revision_id:
                raise PublishError(
                    f"revisions/{revision_id} already exists but its content does not hash to "
                    f"its own name — corrupted after a previous publish; refusing to overwrite "
                    f"or point to it. Investigate before retrying."
                )
            shutil.rmtree(staging)
        else:
            (staging / META_FILENAME).write_text(
                json.dumps(
                    {
                        "revisionId": revision_id,
                        "publishedAt": published_at or _utcnow_iso(),
                        "sourceBuildDir": str(build_dir),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            final.parent.mkdir(parents=True, exist_ok=True)
            os.rename(staging, final)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    # Read back rather than trust the local variable: on the idempotent/retry path `final`
    # pre-dates this run, and its publishedAt — not "now" — is what the pointer must carry.
    meta = json.loads((final / META_FILENAME).read_text(encoding="utf-8"))
    _write_json_atomic(
        dataset_root / POINTER_FILENAME,
        {"revisionId": revision_id, "publishedAt": meta["publishedAt"]},
    )
    _prune_old_revisions(dataset_root, resolved_keep, revision_id, resolved_cache_dir)
    return revision_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/publish_dataset.py",
        description="Validate and publish a scripts/build_lod.py output as an immutable revision.",
    )
    parser.add_argument("--build-dir", required=True, type=Path, help="scripts/build_lod.py output")
    parser.add_argument("--dataset-id", required=True, help="dataset id under --artifacts-dir")
    parser.add_argument("--artifacts-dir", type=Path, default=Path(settings.artifacts_dir))
    parser.add_argument("--cache-dir", type=Path, default=Path(settings.cache_dir))
    parser.add_argument(
        "--keep",
        type=int,
        default=None,
        help=f"revisions to retain, dataset-wide (default: GEOMETRY_API_REVISION_RETAIN_COUNT / "
        f"{settings.revision_retain_count})",
    )
    parser.add_argument(
        "--published-at",
        default=None,
        help="ISO 8601 timestamp to record (default: now); only used the first time a given "
        "revision id is published",
    )
    args = parser.parse_args(argv)

    try:
        revision_id = publish(
            args.build_dir,
            args.dataset_id,
            args.artifacts_dir,
            keep=args.keep,
            published_at=args.published_at,
            cache_dir=args.cache_dir,
        )
    except PublishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"published {args.dataset_id} revision {revision_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
