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

MAX_TOTAL_DROPPED_AREA = 50_000.0
"""Mirrors geometry_api.simplify.DEFAULT_MAX_TOTAL_DROPPED_AREA, duplicated for the same reason.

Without a cumulative limit the per-part one is a formality: any number of parts just under
10.000 m² pass one at a time. TUR ADM1's worst level drops 685 m² in total.
"""

MAX_NORMALIZATION_DROPS = 20
"""Islets plus interior rings the geoBoundaries normalization may drop before CI objects.

TUR ADM1 drops 7. The ceiling is not the observed number, so a routine data refresh does not
turn CI red, but a change that starts discarding whole regions does.
"""

LOSS_KEY_PREFIXES = ("dropped", "skipped", "lost", "removed", "degenerate")
"""Mirrors geometry_api.loss.LOSS_KEY_PREFIXES — the naming convention that marks a loss record.

Recomputing the ``lossy`` flag here from the same records the build derived it from is the whole
point of this half of the checker. If it imported the build's own derivation it could only
confirm that the build agrees with itself.
"""


def _records(block: dict[str, Any]) -> dict[str, Any]:
    """The entries of a loss block that record something lost, booleans excluded."""
    return {
        key: value
        for key, value in block.items()
        if key.startswith(LOSS_KEY_PREFIXES) and not isinstance(value, bool)
    }


def _shows_loss(block: dict[str, Any]) -> bool:
    for value in _records(block).values():
        if isinstance(value, (int, float)):
            if value != 0:
                return True
        elif value:
            return True
    return False


def _check_loss_block(label: str, block: dict[str, Any]) -> list[str]:
    """Three ways a loss block can be internally dishonest, checked on every block.

    Each of these passed CI before, verified by mutating a real report:

    * ``droppedParts: 7`` beside ``droppedPartDetails: []`` — a count with nothing behind it.
    * ``droppedParts: -7`` — a negative count, which arithmetic on the total silently absorbs.
    * a ``lossy`` boolean that contradicts the records next to it in either direction.
    """
    failures: list[str] = []
    records = _records(block)

    for key, value in records.items():
        if isinstance(value, (int, float)) and value < 0:
            failures.append(f"{label}: '{key}' is {value}; a loss record cannot be negative")

    for key, value in records.items():
        if not key.endswith("Details") or not isinstance(value, list):
            continue
        count_key = key.removesuffix("Details") + "s"
        count = block.get(count_key)
        if isinstance(count, int) and count != len(value):
            failures.append(
                f"{label}: '{count_key}' is {count} but '{key}' lists {len(value)} entry(ies); "
                f"a count with no detail behind it cannot be reviewed"
            )

    declared = block.get("lossy")
    if isinstance(declared, bool) and declared != _shows_loss(block):
        failures.append(
            f"{label}: reports lossy={declared} but its records say "
            f"{_shows_loss(block)}; the records are what happened"
        )

    return failures


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
        if simplification.get("droppedPartArea", 0.0) > MAX_TOTAL_DROPPED_AREA:
            failures.append(
                f"{lod} dropped {simplification['droppedPartArea']:.0f} m² in total, over the "
                f"{MAX_TOTAL_DROPPED_AREA:.0f} m² cumulative limit"
            )

    failures.extend(_check_level_loss(levels))
    failures.extend(_check_normalization(report.get("normalization"), levels))
    return failures


def _check_level_loss(levels: dict[str, Any]) -> list[str]:
    """Every level must carry its full loss block, and every block must be self-consistent.

    ``build_lod.py`` used to copy only the aggregate ``lossy`` flag out of each manifest, which
    left this script with nothing to verify it against: a level could report ``lossy: true``
    while its ``loss.upstream`` block was absent entirely, and CI called that a pass.
    """
    failures: list[str] = []
    for lod in LOD_LEVELS:
        loss = levels[lod].get("loss")
        if not isinstance(loss, dict):
            failures.append(
                f"{lod} carries no 'loss' block, so its lossy flag cannot be checked against "
                f"anything; the build did not record where the loss came from"
            )
            continue

        stages = ("triangulation", "simplification", "upstream")
        missing = [name for name in stages if not isinstance(loss.get(name), dict)]
        if missing:
            failures.append(
                f"{lod}: loss block is missing stage(s) {', '.join(missing)}; every stage that "
                f"can lose geometry has to say so even when it lost nothing"
            )
        for name in stages:
            block = loss.get(name)
            if isinstance(block, dict):
                failures.extend(_check_loss_block(f"{lod}.loss.{name}", block))

        derived = any(
            _shows_loss(loss[name]) for name in stages if isinstance(loss.get(name), dict)
        )
        for key, where in ((loss.get("lossy"), f"{lod}.loss"), (levels[lod].get("lossy"), lod)):
            if isinstance(key, bool) and key != derived:
                failures.append(
                    f"{where} reports lossy={key} but its three stages record {derived}"
                )
    return failures


def _check_normalization(normalization: dict[str, Any] | None, levels: dict[str, Any]) -> list[str]:
    """The normalization step drops real geometry, so its numbers get checked too.

    Nothing here looked at this block before, which meant the seven islets the chain removes from
    the source could have become seventy without the CI run noticing.
    """
    if normalization is None:
        return ["report has no 'normalization' block; the chain did not record what it dropped"]

    failures = _check_loss_block("normalization", normalization)
    dropped = normalization.get("droppedParts", 0) + normalization.get("droppedHoles", 0)

    if dropped > MAX_NORMALIZATION_DROPS:
        failures.append(
            f"normalization dropped {dropped} geometries, over the expected ceiling of "
            f"{MAX_NORMALIZATION_DROPS}; the source data or the area floor changed"
        )

    # A lossy normalization must reach every level's manifest, or a consumer reading one level
    # would be told nothing was lost. Checked against the records rather than the boolean, so a
    # normalization block that under-reports itself cannot excuse the levels too.
    if _shows_loss(normalization):
        silent = [lod for lod in LOD_LEVELS if not levels[lod].get("lossy")]
        if silent:
            failures.append(
                f"normalization was lossy but these levels report lossy=false: {', '.join(silent)}"
            )

    # The per-level upstream block is supposed to *be* the normalization block. If they drift,
    # one of the two is describing a different run and neither can be trusted.
    for lod in LOD_LEVELS:
        upstream = (levels[lod].get("loss") or {}).get("upstream")
        if not isinstance(upstream, dict):
            continue
        differing = [
            key for key, value in _records(normalization).items() if upstream.get(key) != value
        ]
        if differing:
            failures.append(
                f"{lod}.loss.upstream disagrees with the normalization block on "
                f"{', '.join(sorted(differing))}"
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
        simplification = levels[lod]["simplification"]
        changes = simplification.get("topologyChanges", {})
        print(
            f"{lod:7s} {levels[lod]['vertices']:>8} vertices "
            f"({levels[lod]['vertices'] / high:6.1%} of high), "
            f"{levels[lod]['triangles']:>8} triangles, "
            f"{simplification.get('droppedPartArea', 0.0):7.1f} m² dropped, "
            f"{changes.get('merges', 0):>3} merges / {changes.get('splits', 0):>3} splits"
        )
    print("lod ladder OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
