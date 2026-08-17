"""FastAPI dependencies wrapping :mod:`geometry_api.registry` (FAZ-3-PLAN.md §3.4, §9.2).

Two shapes of the same underlying resolve-then-lease sequence, because the plan freezes two
different URL contracts for two different kinds of endpoint:

* Metadata endpoints (`/v1/datasets/{dataset_id}`, `.../territories`, `.../viewport`) resolve an
  *optional* ``?revision=`` — omitted means "the current one" — via :func:`resolve_revision`.
* Artifact endpoints (mesh, batch) always name an exact, already-published revision as a path
  segment — there is no "current" fallback, because the whole point of the revisioned URL is that
  it never changes meaning — via :func:`resolve_pinned_revision`.

Both acquire a lease (registry.lease) for the lifetime of the request and release it in a
``finally``, so a concurrent prune cannot delete files a request is still reading (§3.4). Every
:class:`~geometry_api.registry.RegistryError` is translated to the one JSON error shape here —
routes never see the registry's own exception types.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Query, Request, status

from geometry_api.cache import BatchCache
from geometry_api.errors import ApiError
from geometry_api.metrics import Metrics
from geometry_api.registry import (
    DatasetNotFoundError,
    DatasetRegistry,
    RegistryError,
    ResolvedRevision,
    RevisionCorruptedError,
    RevisionGoneError,
    RevisionNotFoundError,
    TerritoryNotFoundError,
)


def get_registry(request: Request) -> DatasetRegistry:
    registry: DatasetRegistry = request.app.state.registry
    return registry


def get_batch_cache(request: Request) -> BatchCache:
    cache: BatchCache = request.app.state.batch_cache
    return cache


def get_metrics(request: Request) -> Metrics:
    metrics: Metrics = request.app.state.metrics
    return metrics


def as_api_error(exc: RegistryError) -> ApiError:
    """The one place a registry failure becomes an HTTP-shaped one."""
    if isinstance(exc, DatasetNotFoundError):
        return ApiError(status.HTTP_404_NOT_FOUND, "dataset_not_found", str(exc))
    if isinstance(exc, RevisionGoneError):
        return ApiError(
            status.HTTP_410_GONE,
            "revision_gone",
            str(exc),
            details={"revisionId": exc.revision_id, "prunedAt": exc.pruned_at},
        )
    if isinstance(exc, RevisionNotFoundError):
        return ApiError(
            status.HTTP_404_NOT_FOUND,
            "revision_not_found",
            str(exc),
            details={"revisionId": exc.revision_id},
        )
    if isinstance(exc, RevisionCorruptedError):
        return ApiError(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "revision_corrupted",
            str(exc),
            details={"revisionId": exc.revision_id},
        )
    if isinstance(exc, TerritoryNotFoundError):
        return ApiError(
            status.HTTP_404_NOT_FOUND,
            "territory_not_found",
            str(exc),
            details={"territoryId": exc.territory_id, "lod": exc.lod},
        )
    return ApiError(  # pragma: no cover - every RegistryError subclass is handled above
        status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error", str(exc)
    )


RegistryDep = Annotated[DatasetRegistry, Depends(get_registry)]
BatchCacheDep = Annotated[BatchCache, Depends(get_batch_cache)]
MetricsDep = Annotated[Metrics, Depends(get_metrics)]


def _resolve_and_lease(
    registry: DatasetRegistry, dataset_id: str, requested_revision: str | None
) -> Iterator[ResolvedRevision]:
    try:
        resolved = registry.resolve(dataset_id, requested_revision)
    except RegistryError as exc:
        raise as_api_error(exc) from exc
    with registry.lease(resolved.revision_id):
        yield resolved


def resolve_revision(
    dataset_id: str,
    registry: RegistryDep,
    revision: Annotated[
        str | None,
        Query(description="Pin to a specific published revision; omit for the current one"),
    ] = None,
) -> Iterator[ResolvedRevision]:
    """For metadata endpoints: ``?revision=`` optional, defaults to current."""
    yield from _resolve_and_lease(registry, dataset_id, revision)


def resolve_pinned_revision(
    dataset_id: str, revision_id: str, registry: RegistryDep
) -> Iterator[ResolvedRevision]:
    """For artifact endpoints (mesh, batch): the revision is a required path segment."""
    yield from _resolve_and_lease(registry, dataset_id, revision_id)
