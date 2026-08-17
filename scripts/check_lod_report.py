"""Checks a ``lod-report.json`` against the phase 2 contract.

    python scripts/check_lod_report.py <lod-report.json>

The unit tests already prove the geometry claims — no cracks, shared vertices, coverage. This
checks the thing they cannot: that the chain as a whole, run from a raw geoBoundaries file
through the TerritoryKit CLI, still produced a sane ladder. In CI a green build_lod.py run only
means it exited 0; this makes it mean something.

**Phase 3.** The checker itself (``check()`` and everything it calls) now lives in
``geometry_api.manifest_validation``, so ``scripts/publish_dataset.py`` can run the exact same
check before it will publish a build — not a second copy that can drift from this one. This file
is the CLI: parse arguments, run the shared check, print the ladder summary.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from geometry_api.loss import SCHEMA_VERSION, STAGES
from geometry_api.manifest_validation import (
    LOD_LEVELS,
    LOW_VERTEX_BUDGET,
    MAX_DROPPED_PART_AREA,
    MAX_NORMALIZATION_DROPS,
    MAX_TOTAL_DROPPED_AREA,
    check,
)

__all__ = [
    "LOD_LEVELS",
    "LOW_VERTEX_BUDGET",
    "MAX_DROPPED_PART_AREA",
    "MAX_NORMALIZATION_DROPS",
    "MAX_TOTAL_DROPPED_AREA",
    "SCHEMA_VERSION",
    "STAGES",
    "check",
    "main",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/check_lod_report.py")
    parser.add_argument("report", type=Path, help="path to lod-report.json")
    args = parser.parse_args(argv)

    if not args.report.exists():
        print(f"error: {args.report} does not exist", file=sys.stderr)
        return 1

    report: dict[str, Any] = json.loads(args.report.read_text(encoding="utf-8"))
    failures = check(report)

    if failures:
        print(f"{len(failures)} expectation(s) failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    levels = report["levels"]
    high = levels["high"]["vertices"]
    for lod in LOD_LEVELS:
        level = levels[lod]
        simplification = level["simplification"]
        changes = simplification.get("topologyChanges", {})
        budget = simplification.get("areaBudget", {})
        print(
            f"{lod:7s} {level['vertices']:>8} vertices "
            f"({level['vertices'] / high:6.1%} of high), "
            f"{level['triangles']:>8} triangles, "
            f"{simplification.get('droppedPartArea', 0.0):7.1f} m² dropped, "
            f"{changes.get('merges', 0):>3} merges / {changes.get('splits', 0):>3} splits / "
            f"{changes.get('created', 0):>3} created"
        )
        print(
            f"        area retained {budget.get('retainedAreaRatio', 1.0):8.4%}, worst part "
            f"{budget.get('minPartRetainedAreaRatio', 1.0):6.1%}, "
            f"{budget.get('removedArea', 0.0) / 1e6:8.3f} km² of the source uncovered, "
            f"{budget.get('addedArea', 0.0) / 1e6:8.3f} km² new, "
            f"merges added {changes.get('mergeAddedArea', 0.0) / 1e6:.3f} km²"
        )
        print(
            f"        lossy={str(level.get('lossy')):5s} "
            f"topologyChanged={str(level.get('topologyChanged')):5s} "
            f"pickingUnsafe={str(level.get('pickingUnsafe')):5s}"
        )
    print("lod ladder OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
