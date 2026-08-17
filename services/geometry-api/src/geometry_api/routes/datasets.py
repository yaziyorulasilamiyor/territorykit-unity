"""``GET /v1/datasets``, ``GET /v1/datasets/{dataset_id}`` (FAZ-3-PLAN.md §6).

Metadata only — no binary artifacts here, so these read straight through
:class:`~geometry_api.registry.DatasetRegistry` without a lease (only ``routes/mesh.py`` and
``routes/batch.py`` touch a revision's actual files).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response

from geometry_api.deps import RegistryDep, resolve_revision
from geometry_api.registry import RegistryError, ResolvedRevision
from geometry_api.revisions import LOD_LEVELS
from geometry_api.routes.common import CACHE_CONTROL_METADATA

router = APIRouter()


@router.get("/v1/datasets")
def list_datasets(response: Response, registry: RegistryDep) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for dataset_id in registry.known_dataset_ids():
        try:
            resolved = registry.resolve(dataset_id)
            manifest = registry.load_manifest(resolved, "high")
        except RegistryError:
            # Not yet published, or the current pointer is broken — not a dataset a client can
            # use yet, so it is left off the list rather than surfaced as a partial entry.
            continue
        items.append(
            {
                "id": dataset_id,
                "name": manifest.get("datasetName", dataset_id),
                "currentRevisionId": resolved.revision_id,
                "territoryCount": manifest.get("territoryCount"),
            }
        )
    response.headers["Cache-Control"] = CACHE_CONTROL_METADATA
    return {"datasets": items}


@router.get("/v1/datasets/{dataset_id}")
def get_dataset(
    response: Response,
    resolved: Annotated[ResolvedRevision, Depends(resolve_revision)],
    registry: RegistryDep,
) -> dict[str, Any]:
    levels: list[dict[str, Any]] = []
    high_manifest: dict[str, Any] | None = None
    for lod in LOD_LEVELS:
        manifest = registry.load_manifest(resolved, lod)
        if lod == "high":
            high_manifest = manifest
        simplification = manifest.get("simplification") or {}
        levels.append(
            {
                "lod": lod,
                "territoryCount": manifest["territoryCount"],
                "vertexCount": manifest["totals"]["vertices"],
                "triangleCount": manifest["totals"]["triangles"],
                "byteLength": manifest["totals"]["bytes"],
                # All three client flags, unchanged from the manifest — publish_dataset.py already
                # proved them consistent (§1.1); the API relays, never recomputes (§8; Z12 added
                # 'lossy' here alongside topologyChanged/pickingUnsafe).
                "lossy": manifest["lossy"],
                "topologyChanged": manifest["topologyChanged"],
                "pickingUnsafe": manifest["pickingUnsafe"],
                "simplification": {"topologyChanged": simplification.get("topologyChanged")},
            }
        )

    assert high_manifest is not None  # LOD_LEVELS always includes "high"
    response.headers["Cache-Control"] = CACHE_CONTROL_METADATA
    return {
        "id": resolved.dataset_id,
        "name": high_manifest.get("datasetName", resolved.dataset_id),
        "sourceFormat": high_manifest.get("sourceFormat"),
        "revisionId": resolved.revision_id,
        "isCurrentRevision": resolved.is_current,
        "publishedAt": resolved.published_at,
        "origin": high_manifest.get("origin"),
        "boundsWgs84": high_manifest.get("boundsWgs84"),
        # high's boundsLocal stands for the dataset (Z11) — least simplified, closest to source;
        # per-level bounds are still available via territories[].bboxLocal at each lod.
        "boundsLocal": high_manifest.get("boundsLocal"),
        "territoryCount": high_manifest.get("territoryCount"),
        "levels": levels,
    }
