"""``GET``/``HEAD`` ``/v1/datasets/{dataset_id}/revisions/{revision_id}/mesh/{territory_id}``
(FAZ-3-PLAN.md §9).

The canonical immutable artifact URL: ``revision_id`` is a required path segment (resolved via
``resolve_pinned_revision`` — no "current" fallback), ``lod`` a required query param with no
default (Unity must choose it having already read ``/v1/datasets/{id}``'s per-level flags, §8).

Gzip is a precomputed file, never a runtime compression call (madde 9): ``.tkms.gz`` was written
by ``scripts/publish_dataset.py``, and this route only ever picks between the two files that
already exist. ``ETag`` comes from ``etags.json``, also precomputed — no hash is run at request
time (§1, §11.1). ``Vary: Accept-Encoding`` is set because the response body genuinely differs by
that header (§11.2).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from geometry_api.conditional import accepts_gzip, etag_matches
from geometry_api.deps import RegistryDep, as_api_error, resolve_pinned_revision
from geometry_api.registry import RegistryError, ResolvedRevision
from geometry_api.routes.common import CACHE_CONTROL_IMMUTABLE, validate_lod

router = APIRouter()


_MESH_PATH = "/v1/datasets/{dataset_id}/revisions/{revision_id}/mesh/{territory_id}"


def get_mesh(
    request: Request,
    territory_id: str,
    resolved: Annotated[ResolvedRevision, Depends(resolve_pinned_revision)],
    registry: RegistryDep,
    lod: str = Query(..., description="Required, no default: 'high' | 'medium' | 'low'"),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    accept_encoding: str = Header(default="", alias="Accept-Encoding"),
) -> Response:
    """Download one territory's TKMS mesh at a pinned, immutable revision.

    Serves the precomputed gzip variant when the client accepts it (never compresses at request
    time), answers `304` on a matching `If-None-Match`, and always carries a strong `ETag` plus
    `Cache-Control: public, max-age=31536000, immutable`. `HEAD` returns the same headers with
    no body.
    """
    validate_lod(lod)
    try:
        entry = registry.territory_entry(resolved, lod, territory_id)
    except RegistryError as exc:
        raise as_api_error(exc) from exc

    etags = registry.load_etags(resolved, lod)
    filename = entry["file"]
    gzip_name = f"{filename}.gz"
    use_gzip = accepts_gzip(accept_encoding) and gzip_name in etags
    serve_name = gzip_name if use_gzip else filename
    etag = etags[serve_name]

    headers = {
        "ETag": etag,
        "Cache-Control": CACHE_CONTROL_IMMUTABLE,
        "Vary": "Accept-Encoding",
    }
    if use_gzip:
        headers["Content-Encoding"] = "gzip"

    if etag_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)

    data = (resolved.path / lod / serve_name).read_bytes()
    headers["Content-Length"] = str(len(data))
    body = b"" if request.method == "HEAD" else data
    return Response(
        content=body,
        status_code=status.HTTP_200_OK,
        headers=headers,
        media_type="application/octet-stream",
    )


# Registered as two routes rather than one @api_route(methods=["GET", "HEAD"]) — FastAPI derives
# an operation id from (function name, path) and does not factor in the method, so a single
# combined route produced two OpenAPI operations sharing one id. Explicit, distinct ids fix that.
router.add_api_route(_MESH_PATH, get_mesh, methods=["GET"], operation_id="get_mesh")
router.add_api_route(_MESH_PATH, get_mesh, methods=["HEAD"], operation_id="head_mesh")
