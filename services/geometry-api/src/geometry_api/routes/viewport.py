"""``GET /v1/datasets/{dataset_id}/viewport`` (FAZ-3-PLAN.md §5) — id list only, same
cursor/limit/scanTruncated contract as ``territories`` (they share ``geometry_api.pagination``)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status

from geometry_api.config import settings
from geometry_api.deps import RegistryDep, resolve_revision
from geometry_api.errors import ApiError
from geometry_api.pagination import CursorError, CursorFilterMismatchError, paginate
from geometry_api.registry import ResolvedRevision
from geometry_api.routes.common import (
    CACHE_CONTROL_METADATA,
    bbox_intersects,
    parse_bbox,
    validate_lod,
)

router = APIRouter()


@router.get("/v1/datasets/{dataset_id}/viewport")
def get_viewport(
    response: Response,
    resolved: Annotated[ResolvedRevision, Depends(resolve_revision)],
    registry: RegistryDep,
    bbox: str = Query(...),
    lod: str = Query(...),
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None),
) -> dict[str, Any]:
    validate_lod(lod)
    bbox_tuple = parse_bbox(bbox)
    resolved_limit = limit if limit is not None else settings.territories_page_size_default
    if resolved_limit > settings.territories_page_size_max:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "limit_too_large",
            f"limit {resolved_limit} exceeds the maximum of {settings.territories_page_size_max}",
        )

    manifest = registry.load_manifest(resolved, lod)
    entries = manifest.get("territories", [])
    filters: dict[str, Any] = {
        "bbox": list(bbox_tuple),
        "parentId": None,
        "administrativeLevel": None,
    }

    def predicate(entry: dict[str, Any]) -> bool:
        return bbox_intersects(entry["bboxLocal"], bbox_tuple)

    try:
        page = paginate(
            entries,
            predicate=predicate,
            cursor=cursor,
            limit=resolved_limit,
            scan_cap=settings.territories_scan_cap,
            revision_id=resolved.revision_id,
            lod=lod,
            filters=filters,
        )
    except CursorFilterMismatchError as exc:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "cursor_filter_mismatch", str(exc)) from exc
    except CursorError as exc:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "invalid_cursor", str(exc)) from exc

    response.headers["Cache-Control"] = CACHE_CONTROL_METADATA
    return {
        "revisionId": resolved.revision_id,
        "lod": lod,
        "territoryIds": [entry["id"] for entry in page.items],
        "nextCursor": page.next_cursor,
        "scanTruncated": page.scan_truncated,
    }
