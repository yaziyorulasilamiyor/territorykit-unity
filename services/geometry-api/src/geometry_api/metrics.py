"""In-process request/cache counters, exposed at ``GET /metrics`` (FAZ-3-PLAN.md §13.3).

No new dependency (no ``prometheus-client``) — ``pyproject.toml``'s dependencies are pinned
exactly on purpose (see its own comment), so adding one is a deliberate upgrade decision this
phase defers. Hand-rolled counters, one schema, frozen below.

**Per-process, not durable.** These reset on restart and are **not** aggregated across
``uvicorn --workers N>1``: each worker holds its own counters, and a request to ``/metrics``
only ever sees the process that happened to handle it. A multi-worker deployment needing a
combined view needs an external scraper hitting every worker — out of scope here, and stated
rather than silently wrong.

**Schema (schemaVersion 1)**::

    {
      "schemaVersion": 1,
      "processId": <int>,
      "uptimeSeconds": <float>,
      "requests": [{"route": "<METHOD> <path template>", "status": <int>, "count": <int>}, ...],
      "cache": {"batchHits": <int>, "batchMisses": <int>},
      "latencyMs": [{"route": "...", "sampleCount": <int>, "p50": <float>, "p95": <float>,
                     "p99": <float>}, ...]
    }

A route that has never been called has **no entry** in ``latencyMs`` — not a zero-sample row with
``null`` percentiles. A zero-sample row inviting a truthiness check ("is there a row?") to answer
"has this route ever been hit?" is a worse shape than simply not appearing.
"""

from __future__ import annotations

import os
import time
from collections import deque
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_MAX_SAMPLES_PER_ROUTE = 1000
"""A fixed-size rolling window per route, so memory does not grow with request volume."""


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile. Not interpolated — simple, and exact numbers are not the point of
    a rolling in-memory window."""
    index = min(len(sorted_values) - 1, int(fraction * len(sorted_values)))
    return sorted_values[index]


class Metrics:
    def __init__(self, max_samples_per_route: int = DEFAULT_MAX_SAMPLES_PER_ROUTE) -> None:
        self._start = time.monotonic()
        self._max_samples = max_samples_per_route
        self._request_counts: dict[tuple[str, int], int] = {}
        self._latencies: dict[str, deque[float]] = {}
        self._batch_hits = 0
        self._batch_misses = 0

    def record_request(self, route: str, status_code: int, duration_ms: float) -> None:
        key = (route, status_code)
        self._request_counts[key] = self._request_counts.get(key, 0) + 1
        bucket = self._latencies.setdefault(route, deque(maxlen=self._max_samples))
        bucket.append(duration_ms)

    def record_batch_cache(self, *, hit: bool) -> None:
        if hit:
            self._batch_hits += 1
        else:
            self._batch_misses += 1

    def snapshot(self) -> dict[str, Any]:
        requests = [
            {"route": route, "status": status_code, "count": count}
            for (route, status_code), count in sorted(self._request_counts.items())
        ]
        latency = []
        for route, samples in sorted(self._latencies.items()):
            if not samples:
                continue
            ordered = sorted(samples)
            latency.append(
                {
                    "route": route,
                    "sampleCount": len(ordered),
                    "p50": _percentile(ordered, 0.50),
                    "p95": _percentile(ordered, 0.95),
                    "p99": _percentile(ordered, 0.99),
                }
            )
        return {
            "schemaVersion": SCHEMA_VERSION,
            "processId": os.getpid(),
            "uptimeSeconds": time.monotonic() - self._start,
            "requests": requests,
            "cache": {"batchHits": self._batch_hits, "batchMisses": self._batch_misses},
            "latencyMs": latency,
        }
