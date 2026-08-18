"""Small pieces every route module needs: lod/bbox validation, shared cache-control values.

Kept out of ``geometry_api.errors``/``geometry_api.pagination`` because those are framework- and
domain-agnostic; this module is route glue and is allowed to know about HTTP status codes.
"""

from __future__ import annotations

from fastapi import status

from geometry_api.errors import ApiError
from geometry_api.revisions import LOD_LEVELS

CACHE_CONTROL_IMMUTABLE = "public, max-age=31536000, immutable"
"""Revisioned artifact endpoints only (mesh, batch) — FAZ-3-PLAN.md §8/§11, madde 8."""

CACHE_CONTROL_METADATA = "public, max-age=30, must-revalidate"
"""/v1/datasets, .../territories, .../viewport — short-lived, never 'immutable'."""


def validate_lod(lod: str) -> None:
    """422 (validation_error) for a missing ``lod``, distinct 400 (unknown_lod) for a present but
    invalid one (FAZ-3-PLAN.md §9.1) — the caller declares ``lod: str = Query(...)`` (required,
    not ``Literal[...]``) so FastAPI's own validation only ever produces the first case."""
    if lod not in LOD_LEVELS:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "unknown_lod",
            f"lod {lod!r} is not one of {LOD_LEVELS}",
            details={"lod": lod, "validValues": list(LOD_LEVELS)},
        )


def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    """``x1,y1,x2,y2`` in local metres (docs/api.md) — not WGS84 degrees."""
    parts = raw.split(",")
    if len(parts) != 4:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "invalid_bbox",
            f"bbox must be 'x1,y1,x2,y2', got {raw!r}",
        )
    try:
        x1, y1, x2, y2 = (float(part) for part in parts)
    except ValueError as exc:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST, "invalid_bbox", f"bbox values must be numbers: {raw!r}"
        ) from exc
    if x1 > x2 or y1 > y2:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST, "invalid_bbox", f"bbox min must be <= max: {raw!r}"
        )
    return (x1, y1, x2, y2)


def bbox_intersects(
    entry_bbox: tuple[float, float, float, float] | list[float],
    query_bbox: tuple[float, float, float, float],
) -> bool:
    ex1, ey1, ex2, ey2 = entry_bbox
    qx1, qy1, qx2, qy2 = query_bbox
    return ex1 <= qx2 and ex2 >= qx1 and ey1 <= qy2 and ey2 >= qy1
