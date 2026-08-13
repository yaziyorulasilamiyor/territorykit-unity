"""Checks a ``lod-report.json`` against the phase 2 contract.

    python scripts/check_lod_report.py <lod-report.json>

The unit tests already prove the geometry claims — no cracks, shared vertices, coverage. This
checks the thing they cannot: that the chain as a whole, run from a raw geoBoundaries file
through the TerritoryKit CLI, still produced a sane ladder. In CI a green build_lod.py run only
means it exited 0; this makes it mean something.

Exits non-zero with the failing expectations listed, so CI names what broke.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

LOD_LEVELS = ("high", "medium", "low")
LOW_VERTEX_BUDGET = 0.25
"""'low' may keep at most a quarter of 'high' vertices."""


def check(report: dict[str, Any]) -> list[str]:
    """Return a list of failures; empty means the report satisfies the contract."""
    failures: list[str] = []
    levels = report.get("levels", {})

    missing = [lod for lod in LOD_LEVELS if lod not in levels]
    if missing:
        return [f"report is missing level(s): {', '.join(missing)}"]

    counts = {lod: levels[lod]["territoryCount"] for lod in LOD_LEVELS}
    if len(set(counts.values())) != 1:
        failures.append(f"territory count differs between levels: {counts}")

    vertices = {lod: levels[lod]["vertices"] for lod in LOD_LEVELS}
    if not vertices["high"] > vertices["medium"] > vertices["low"]:
        failures.append(f"levels are not strictly coarsening: {vertices}")

    ratio = vertices["low"] / vertices["high"] if vertices["high"] else 1.0
    if ratio > LOW_VERTEX_BUDGET:
        failures.append(
            f"low keeps {ratio:.1%} of high's vertices, over the {LOW_VERTEX_BUDGET:.0%} ceiling"
        )

    high = levels["high"]["simplification"]
    if high["partCount"] != high["sourcePartCount"]:
        failures.append(
            f"high dropped parts ({high['sourcePartCount']} -> {high['partCount']}); it is the "
            f"level that must preserve the source"
        )
    if high["skippedParts"] or high["skippedRings"]:
        failures.append(
            f"high reported loss: {high['skippedParts']} parts, {high['skippedRings']} rings"
        )

    for lod in LOD_LEVELS:
        simplification = levels[lod]["simplification"]
        if simplification["holeCount"] > simplification["sourceHoleCount"]:
            failures.append(
                f"{lod} invented {simplification['holeCount'] - simplification['sourceHoleCount']} "
                f"hole(s); simplification may drop detail, never add it"
            )
        if levels[lod]["triangles"] <= 0:
            failures.append(f"{lod} produced no triangles")

    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/check_lod_report.py")
    parser.add_argument("report", type=Path, help="path to lod-report.json")
    args = parser.parse_args(argv)

    if not args.report.exists():
        print(f"error: {args.report} does not exist", file=sys.stderr)
        return 1

    report = json.loads(args.report.read_text(encoding="utf-8"))
    failures = check(report)

    if failures:
        print(f"{len(failures)} expectation(s) failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    levels = report["levels"]
    high = levels["high"]["vertices"]
    for lod in LOD_LEVELS:
        print(
            f"{lod:7s} {levels[lod]['vertices']:>8} vertices "
            f"({levels[lod]['vertices'] / high:6.1%} of high), "
            f"{levels[lod]['triangles']:>8} triangles"
        )
    print("lod ladder OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
