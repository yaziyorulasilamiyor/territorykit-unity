"""``POST /v1/datasets/{dataset_id}/revisions/{revision_id}/mesh/batch`` (FAZ-3-PLAN.md §10).

Assembly is pure I/O — reading already-precomputed ``.tkms``/``.tkms.gz`` files and concatenating
them via ``geometry_api.tkmb`` — cached by :class:`~geometry_api.cache.BatchCache`, itself checked
only *after* the revision has been resolved (§10.3/X15): a pruned revision answers 410 before the
cache is ever consulted, so a stale cache entry from before a prune can never be served.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from geometry_api.config import settings
from geometry_api.deps import BatchCacheDep, MetricsDep, RegistryDep, resolve_pinned_revision
from geometry_api.errors import ApiError
from geometry_api.registry import DatasetRegistry, ResolvedRevision
from geometry_api.routes.common import CACHE_CONTROL_IMMUTABLE, validate_lod
from geometry_api.tkmb import encode_tkmb

router = APIRouter()


class BatchRequest(BaseModel):
    territoryIds: list[str] = Field(min_length=1)
    lod: str
    entryEncoding: Literal["identity", "gzip"] = "identity"


def _assemble(
    registry: DatasetRegistry,
    resolved: ResolvedRevision,
    lod: str,
    ids: list[str],
    entry_encoding: str,
) -> bytes:
    manifest = registry.load_manifest(resolved, lod)
    by_id = {entry["id"]: entry for entry in manifest.get("territories", [])}

    entries: dict[str, bytes] = {}
    missing: list[str] = []
    for territory_id in ids:
        entry = by_id.get(territory_id)
        if entry is None:
            missing.append(territory_id)
            continue
        filename = entry["file"]
        serve_name = f"{filename}.gz" if entry_encoding == "gzip" else filename
        entries[territory_id] = (resolved.path / lod / serve_name).read_bytes()

    return encode_tkmb(entries, missing, entry_encoding)


@router.post("/v1/datasets/{dataset_id}/revisions/{revision_id}/mesh/batch")
def post_mesh_batch(
    body: BatchRequest,
    resolved: Annotated[ResolvedRevision, Depends(resolve_pinned_revision)],
    registry: RegistryDep,
    cache: BatchCacheDep,
    metrics: MetricsDep,
) -> Response:
    """Bundle several territories' meshes into one TKMB container at a pinned revision.

    Requested ids that don't exist are listed inside the container itself (`missing`), never as
    a `404` — the batch URL is always valid, its contents may just be partial. Deduplicates
    requested ids and always returns them TOC-sorted by id, regardless of request order, so the
    same set of ids produces byte-identical output (and hits the same cache entry) however it
    was listed.
    """
    validate_lod(body.lod)
    ids = sorted(set(body.territoryIds))
    if len(ids) > settings.batch_max_territories:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "batch_too_large",
            f"{len(ids)} territoryIds requested, over the {settings.batch_max_territories} limit",
            details={"requested": len(ids), "max": settings.batch_max_territories},
        )

    key = cache.compute_key(resolved.dataset_id, body.lod, body.entryEncoding, ids)
    data = cache.get(resolved.revision_id, key)
    if data is not None:
        metrics.record_batch_cache(hit=True)
    else:
        metrics.record_batch_cache(hit=False)
        data = _assemble(registry, resolved, body.lod, ids, body.entryEncoding)
        cache.put(resolved.revision_id, key, data)

    return Response(
        content=data,
        status_code=status.HTTP_200_OK,
        headers={
            "Content-Length": str(len(data)),
            "Cache-Control": CACHE_CONTROL_IMMUTABLE,
        },
        media_type="application/octet-stream",
    )
