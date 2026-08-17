"""Metrics: exact schema and types (not just field presence), empty-window behaviour (§13.3)."""

from __future__ import annotations

from geometry_api.metrics import Metrics


def test_a_fresh_snapshot_has_no_requests_and_no_latency_rows() -> None:
    snapshot = Metrics().snapshot()

    assert snapshot["schemaVersion"] == 1
    assert isinstance(snapshot["processId"], int)
    assert isinstance(snapshot["uptimeSeconds"], float)
    assert snapshot["requests"] == []
    assert snapshot["cache"] == {"batchHits": 0, "batchMisses": 0}
    assert snapshot["latencyMs"] == []


def test_a_route_that_was_never_called_has_no_latency_row() -> None:
    metrics = Metrics()
    metrics.record_request("GET /v1/datasets", 200, 3.0)

    snapshot = metrics.snapshot()

    routes_with_latency = {row["route"] for row in snapshot["latencyMs"]}
    assert routes_with_latency == {"GET /v1/datasets"}


def test_request_counts_are_grouped_by_route_and_status() -> None:
    metrics = Metrics()
    metrics.record_request("GET /v1/datasets", 200, 1.0)
    metrics.record_request("GET /v1/datasets", 200, 2.0)
    metrics.record_request("GET /v1/datasets", 404, 0.5)

    snapshot = metrics.snapshot()

    counts = {(row["route"], row["status"]): row["count"] for row in snapshot["requests"]}
    assert counts == {("GET /v1/datasets", 200): 2, ("GET /v1/datasets", 404): 1}


def test_latency_row_types_and_shape_are_exact() -> None:
    metrics = Metrics()
    for value in (1.0, 2.0, 3.0, 4.0, 5.0):
        metrics.record_request("GET /v1/datasets", 200, value)

    row = metrics.snapshot()["latencyMs"][0]

    assert row.keys() == {"route", "sampleCount", "p50", "p95", "p99"}
    assert row["sampleCount"] == 5
    assert isinstance(row["p50"], float)
    assert isinstance(row["p95"], float)
    assert isinstance(row["p99"], float)
    assert row["p50"] in (2.0, 3.0)  # nearest-rank, exact value depends on rounding convention
    assert row["p99"] == 5.0


def test_the_latency_window_is_bounded_not_unbounded() -> None:
    metrics = Metrics(max_samples_per_route=10)
    for value in range(1000):
        metrics.record_request("GET /v1/x", 200, float(value))

    row = metrics.snapshot()["latencyMs"][0]

    assert row["sampleCount"] == 10
    assert row["p99"] == 999.0, "the window keeps the most recent samples"


def test_batch_cache_hits_and_misses_are_counted_separately() -> None:
    metrics = Metrics()
    metrics.record_batch_cache(hit=True)
    metrics.record_batch_cache(hit=True)
    metrics.record_batch_cache(hit=False)

    assert metrics.snapshot()["cache"] == {"batchHits": 2, "batchMisses": 1}
