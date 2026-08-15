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

MAX_DROPPED_PART_AREA = 10_000.0
"""Mirrors geometry_api.simplify.DEFAULT_MAX_DROPPED_PART_AREA.

Deliberately duplicated rather than imported: this script checks a report produced by some other
run, possibly an older build, so it must state the contract it is checking against instead of
reading whatever the current code happens to allow.
"""

MAX_NORMALIZATION_DROPS = 20
"""Islets plus interior rings the geoBoundaries normalization may drop before CI objects.

TUR ADM1 drops 7. The ceiling is not the observed number, so a routine data refresh does not
turn CI red, but a change that starts discarding whole regions does.
"""


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
        if simplification.get("largestDroppedPartArea", 0.0) > MAX_DROPPED_PART_AREA:
            failures.append(
                f"{lod} dropped a part of "
                f"{simplification['largestDroppedPartArea']:.0f} m², over the "
                f"{MAX_DROPPED_PART_AREA:.0f} m² limit"
            )

    failures.extend(_check_normalization(report.get("normalization"), levels))
    return failures


def _check_normalization(normalization: dict[str, Any] | None, levels: dict[str, Any]) -> list[str]:
    """The normalization step drops real geometry, so its numbers get checked too.

    Nothing here looked at this block before, which meant the seven islets the chain removes from
    the source could have become seventy without the CI run noticing.
    """
    if normalization is None:
        return ["report has no 'normalization' block; the chain did not record what it dropped"]

    failures: list[str] = []
    dropped = normalization.get("droppedParts", 0) + normalization.get("droppedHoles", 0)

    if dropped > MAX_NORMALIZATION_DROPS:
        failures.append(
            f"normalization dropped {dropped} geometries, over the expected ceiling of "
            f"{MAX_NORMALIZATION_DROPS}; the source data or the area floor changed"
        )
    if dropped and not normalization.get("lossy"):
        failures.append("normalization dropped geometry but did not report itself as lossy")

    # A lossy normalization must reach every level's manifest, or a consumer reading one level
    # would be told nothing was lost.
    if normalization.get("lossy"):
        silent = [lod for lod in LOD_LEVELS if not levels[lod].get("lossy")]
        if silent:
            failures.append(
                f"normalization was lossy but these levels report lossy=false: {', '.join(silent)}"
            )

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
