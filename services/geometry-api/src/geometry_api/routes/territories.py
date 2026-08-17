"""``GET /v1/datasets/{dataset_id}/territories`` (FAZ-3-PLAN.md §4)."""

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


def _public_fields(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entry["id"],
        "name": entry["name"],
        "parentId": entry.get("parentId"),
        "administrativeLevel": entry.get("administrativeLevel"),
        "bboxLocal": entry["bboxLocal"],
        "neighborIds": entry.get("neighborIds", []),
    }


@router.get("/v1/datasets/{dataset_id}/territories")
def list_territories(
    response: Response,
    resolved: Annotated[ResolvedRevision, Depends(resolve_revision)],
    registry: RegistryDep,
    lod: str = Query(...),
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None),
    bbox: str | None = Query(default=None),
    parent_id: str | None = Query(default=None, alias="parentId"),
    administrative_level: int | None = Query(default=None, alias="administrativeLevel"),
) -> dict[str, Any]:
    """Cursor-paginated territory list for one level, filterable by `bbox` (local metres),
    `parentId` and `administrativeLevel`. The cursor is bound to the revision, lod and exact
    filter set it was issued under; reusing it under different filters is rejected rather than
    silently reapplied."""
    validate_lod(lod)
    resolved_limit = limit if limit is not None else settings.territories_page_size_default
    if resolved_limit > settings.territories_page_size_max:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "limit_too_large",
            f"limit {resolved_limit} exceeds the maximum of {settings.territories_page_size_max}",
        )
    bbox_tuple = parse_bbox(bbox) if bbox is not None else None

    manifest = registry.load_manifest(resolved, lod)
    entries = manifest.get("territories", [])
    filters: dict[str, Any] = {
        "bbox": list(bbox_tuple) if bbox_tuple is not None else None,
        "parentId": parent_id,
        "administrativeLevel": administrative_level,
    }

    def predicate(entry: dict[str, Any]) -> bool:
        if bbox_tuple is not None and not bbox_intersects(entry["bboxLocal"], bbox_tuple):
            return False
        if parent_id is not None and entry.get("parentId") != parent_id:
            return False
        if (
            administrative_level is not None
            and entry.get("administrativeLevel") != administrative_level
        ):
            return False
        return True

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
        "items": [_public_fields(entry) for entry in page.items],
        "nextCursor": page.next_cursor,
        "scanTruncated": page.scan_truncated,
    }
