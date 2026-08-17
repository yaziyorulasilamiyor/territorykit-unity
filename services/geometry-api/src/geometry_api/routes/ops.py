"""``/health``, ``/ready``, ``/metrics`` — deliberately outside ``/v1`` (FAZ-3-PLAN.md §13.0).

Operational probes, not part of the versioned resource API: ``/health`` already shipped in phase
0 at this exact path, and readiness/metrics follow the same convention for consistency rather
than living under ``/v1`` alongside the datasets/territories/mesh surface.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from geometry_api import __version__
from geometry_api.config import settings
from geometry_api.deps import MetricsDep, RegistryDep
from geometry_api.registry import RegistryError
from geometry_api.revisions import LOD_LEVELS

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness only: no I/O, no dependency on the registry. Unchanged since phase 0."""
    return {"status": "ok", "version": __version__}


@router.get("/ready")
def ready(registry: RegistryDep) -> JSONResponse:
    """Every *configured* dataset must be structurally sound, not just 'at least one' (§13.1,
    X13) — checked against the registry, which already ran the §3.5a integrity hash on first
    resolution, so this does no per-call heavy work of its own."""
    required = settings.required_dataset_ids
    dataset_ids = required if required is not None else registry.known_dataset_ids()

    failures: dict[str, str] = {}
    for dataset_id in dataset_ids:
        try:
            resolved = registry.resolve(dataset_id)
        except RegistryError as exc:
            failures[dataset_id] = str(exc)
            continue
        missing_levels = [
            lod for lod in LOD_LEVELS if not (resolved.path / lod / "index.json").exists()
        ]
        if missing_levels:
            failures[dataset_id] = f"missing index.json for level(s): {missing_levels}"

    if failures:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "failedDatasets": sorted(failures),
                "reason": failures,
            },
        )
    return JSONResponse(content={"status": "ready", "datasets": len(dataset_ids)})


@router.get("/metrics")
def metrics_endpoint(metrics: MetricsDep) -> dict[str, Any]:
    return metrics.snapshot()
