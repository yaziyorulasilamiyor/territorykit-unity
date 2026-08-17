"""Measures p50/p95/p99 latency against a *running* geometry-api process.

    python scripts/bench_api.py --base-url http://localhost:8000 --requests 500

**This is a measurement, not a gate (FAZ-3-PLAN.md §16.2/Z20).** There is no pass/fail threshold
here and none should be added: latency depends on the machine it runs on, so a fixed number would
either false-alarm constantly or be so loose it means nothing. This script is not run in CI; it
produces a number for `FAZ-3-RAPOR.md` to quote, and a JSON file for next time's comparison.

Needs a dataset already published under the server's configured ``artifacts_dir`` — publish one
first with ``scripts/publish_dataset.py`` if the server has none.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Timing:
    label: str
    samples_ms: list[float]

    def percentile(self, fraction: float) -> float:
        ordered = sorted(self.samples_ms)
        index = min(len(ordered) - 1, int(fraction * len(ordered)))
        return ordered[index]

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "requests": len(self.samples_ms),
            "p50Ms": round(self.percentile(0.50), 3),
            "p95Ms": round(self.percentile(0.95), 3),
            "p99Ms": round(self.percentile(0.99), 3),
        }


def _time_request(method: str, url: str, body: bytes | None = None) -> float:
    request = urllib.request.Request(  # noqa: S310 - fixed localhost URL, not user input
        url, data=body, method=method, headers={"Content-Type": "application/json"} if body else {}
    )
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        response.read()
    return (time.perf_counter() - start) * 1000


def _bench(
    label: str, method: str, url: str, body: bytes | None, requests: int, warmup: int
) -> Timing:
    for _ in range(warmup):
        _time_request(method, url, body)
    samples = [_time_request(method, url, body) for _ in range(requests)]
    return Timing(label=label, samples_ms=samples)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/bench_api.py")
    parser.add_argument("--base-url", required=True)
    parser.add_argument(
        "--dataset-id", default=None, help="default: the first dataset /v1/datasets lists"
    )
    parser.add_argument("--territory-id", default=None, help="default: the first territory listed")
    parser.add_argument("--lod", default="high")
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--output", default=None, help="write the JSON result here too")
    args = parser.parse_args(argv)

    try:
        with urllib.request.urlopen(f"{args.base_url}/v1/datasets", timeout=10) as response:  # noqa: S310
            datasets = json.loads(response.read())["datasets"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: could not list datasets at {args.base_url}: {exc}", file=sys.stderr)
        return 1
    if not datasets:
        print(f"error: {args.base_url}/v1/datasets is empty — publish one first", file=sys.stderr)
        return 1

    dataset_id = args.dataset_id or datasets[0]["id"]
    revision_id = datasets[0]["currentRevisionId"] if args.dataset_id is None else None
    if revision_id is None:
        with urllib.request.urlopen(  # noqa: S310
            f"{args.base_url}/v1/datasets/{dataset_id}", timeout=10
        ) as response:
            revision_id = json.loads(response.read())["revisionId"]

    territory_id = args.territory_id
    if territory_id is None:
        with urllib.request.urlopen(  # noqa: S310
            f"{args.base_url}/v1/datasets/{dataset_id}/territories?lod={args.lod}&limit=1",
            timeout=10,
        ) as response:
            items = json.loads(response.read())["items"]
        if not items:
            print(
                f"error: dataset {dataset_id!r} has no territories at lod {args.lod!r}",
                file=sys.stderr,
            )
            return 1
        territory_id = items[0]["id"]

    mesh_url = (
        f"{args.base_url}/v1/datasets/{dataset_id}/revisions/{revision_id}/mesh/"
        f"{territory_id}?lod={args.lod}"
    )
    batch_url = f"{args.base_url}/v1/datasets/{dataset_id}/revisions/{revision_id}/mesh/batch"
    batch_body = json.dumps({"territoryIds": [territory_id], "lod": args.lod}).encode("utf-8")
    territories_url = (
        f"{args.base_url}/v1/datasets/{dataset_id}/territories?lod={args.lod}&limit=50"
    )

    timings = [
        _bench("mesh_get_cache_hit", "GET", mesh_url, None, args.requests, args.warmup),
        _bench("batch_post_cache_hit", "POST", batch_url, batch_body, args.requests, args.warmup),
        _bench("territories_page", "GET", territories_url, None, args.requests, args.warmup),
    ]

    result = {
        "note": "machine-dependent — for trend comparison across phases, not an absolute number",
        "baseUrl": args.base_url,
        "datasetId": dataset_id,
        "revisionId": revision_id,
        "territoryId": territory_id,
        "lod": args.lod,
        "timings": [timing.as_dict() for timing in timings],
    }

    text = json.dumps(result, indent=2)
    print(text)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        print(f"wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
