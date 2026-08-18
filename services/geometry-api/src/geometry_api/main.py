"""FastAPI app: mounts the versioned resource API (``/v1/...``) and the unversioned operational
probes (``/health``, ``/ready``, ``/metrics`` — §13.0) on top of one shared, per-process
:class:`~geometry_api.registry.DatasetRegistry` / batch cache / metrics instance, built once at
startup from :mod:`geometry_api.config`.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from geometry_api import __version__
from geometry_api.cache import BatchCache
from geometry_api.config import settings
from geometry_api.errors import install_error_handlers
from geometry_api.metrics import Metrics
from geometry_api.registry import DatasetRegistry
from geometry_api.routes import batch, datasets, mesh, ops, territories, viewport


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.registry = DatasetRegistry(Path(settings.artifacts_dir), Path(settings.cache_dir))
    app.state.batch_cache = BatchCache(Path(settings.cache_dir), settings.batch_cache_max_bytes)
    app.state.metrics = Metrics()
    yield


app = FastAPI(title="TerritoryKit Geometry API", version=__version__, lifespan=lifespan)

install_error_handlers(app)

if settings.cors_allow_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_methods=["GET", "HEAD", "POST"],
        allow_headers=["If-None-Match", "Content-Type"],
    )


@app.middleware("http")
async def _record_metrics(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = (time.monotonic() - start) * 1000
    route = request.scope.get("route")
    path_template = getattr(route, "path", request.url.path)
    metrics: Metrics | None = getattr(request.app.state, "metrics", None)
    if metrics is not None:
        route_label = f"{request.method} {path_template}"
        metrics.record_request(route_label, response.status_code, duration_ms)
    return response


app.include_router(ops.router)
app.include_router(datasets.router)
app.include_router(territories.router)
app.include_router(viewport.router)
app.include_router(mesh.router)
app.include_router(batch.router)
