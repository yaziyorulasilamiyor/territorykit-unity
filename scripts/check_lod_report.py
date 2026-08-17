"""Checks a ``lod-report.json`` against the phase 2 contract.

    python scripts/check_lod_report.py <lod-report.json>

The unit tests already prove the geometry claims — no cracks, shared vertices, coverage. This
checks the thing they cannot: that the chain as a whole, run from a raw geoBoundaries file
through the TerritoryKit CLI, still produced a sane ladder. In CI a green build_lod.py run only
means it exited 0; this makes it mean something.

**What is shared with the build and what is not.** The *schema* — which event kinds exist, which
of them are losses, which side of the area budget each one moves — is imported from
``geometry_api.loss``. It used to be copied here as a list of key-name prefixes, with a comment
arguing that the copy was the point. It was not: two lists of the same rule can drift, and a
checker that has drifted in the same direction as the build agrees with it for the wrong reason.

What stays independent is every *derivation*. This script recomputes the ``lossy`` flag from the
events, recomputes the area budget from the recorded areas, and recomputes both counting
identities from the recorded counts, then compares all of them with what the build wrote. Sharing
a vocabulary is not the same as sharing an answer.

Exits non-zero with the failing expectations listed, so CI names what broke.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from geometry_api.loss import (
    CATEGORY_LOSS,
    SCHEMA_VERSION,
    SIDE_ADDED,
    SIDE_REMOVED,
    STAGE_SIMPLIFICATION,
    STAGES,
    LossEvent,
    LossSchemaError,
    events_from_manifest,
    kind_of,
)

LOD_LEVELS = ("high", "medium", "low")
LOW_VERTEX_BUDGET = 0.25
"""'low' may keep at most a quarter of 'high' vertices."""

MAX_DROPPED_PART_AREA = 10_000.0
"""Mirrors geometry_api.simplify.DEFAULT_MAX_DROPPED_PART_AREA.

Deliberately duplicated rather than imported: this is a *threshold*, not a schema. The script
checks a report produced by some other run, possibly an older build, so it must state the
contract it is checking against instead of reading whatever the current code happens to allow.
The schema above is imported for the opposite reason — a vocabulary that differs between producer
and checker makes both of them wrong.
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

BUDGET_RELATIVE_TOLERANCE = 1e-9
"""Slack in the area identity, as a fraction of the source area. Float64 room, not policy."""


def _events(block: Any, label: str) -> tuple[list[LossEvent], list[str]]:
    """Parse a serialised ledger, turning every schema violation into a named CI failure.

    Fail-closed. An event whose kind is not in the schema is a failure here, not a record to skip
    — that asymmetry is the whole reason the naming convention this replaced could be walked
    around four times in a row.
    """
    try:
        return list(events_from_manifest(block)), []
    except LossSchemaError as exc:
        return [], [f"{label}: {exc}"]


def _side_total(events: list[LossEvent], side: str) -> float:
    return sum(item.area for item in events if kind_of(item.kind).side == side)


def _count(events: list[LossEvent], kind: str) -> int:
    return sum(item.count for item in events if item.kind == kind)


def _check_stages(label: str, block: dict[str, Any], events: list[LossEvent]) -> list[str]:
    """Every stage that can change geometry has to say so, even when it changed nothing."""
    failures: list[str] = []
    recorded = block.get("stagesRecorded")
    if not isinstance(recorded, list):
        return [
            f"{label}: no 'stagesRecorded' list; a stage that lost nothing cannot be told "
            f"from a stage nobody asked"
        ]
    missing = [stage for stage in STAGES if stage not in recorded]
    if missing:
        failures.append(
            f"{label}: no records at all from stage(s) {', '.join(missing)}; every stage that can "
            f"change geometry has to be asked even when the answer is nothing"
        )
    off_schema = sorted({item.stage for item in events} - set(recorded))
    if off_schema:
        failures.append(
            f"{label}: carries events for stage(s) {', '.join(off_schema)} that it does not list "
            f"as recorded"
        )
    return failures


def _check_declared_flag(label: str, declared: Any, derived: bool) -> list[str]:
    if isinstance(declared, bool) and declared != derived:
        return [
            f"{label}: reports lossy={declared} but its events say {derived}; the events are "
            f"what happened"
        ]
    return []


def _check_area_budget(
    lod: str, simplification: dict[str, Any], events: list[LossEvent]
) -> list[str]:
    """The identity, recomputed here from the report's own numbers.

    ``source area + everything recorded as added − everything recorded as removed`` has to be the
    output area. The build asserts this per territory while it still has the geometry; this
    asserts it per level from what got written down, so a build that computed the identity
    correctly and then serialised something else does not pass.
    """
    budget = simplification.get("areaBudget")
    if not isinstance(budget, dict):
        return [
            f"{lod}: simplification has no 'areaBudget' block, so there is nothing to check the "
            f"recorded events against; the build did not measure what it changed"
        ]

    failures: list[str] = []
    source = float(budget.get("sourceArea", 0.0))
    output = float(budget.get("outputArea", 0.0))
    added = _side_total(events, SIDE_ADDED)
    removed = _side_total(events, SIDE_REMOVED)
    slack = max(1.0, BUDGET_RELATIVE_TOLERANCE * source)

    balance = source + added - removed - output
    if abs(balance) > slack:
        failures.append(
            f"{lod}: the area budget does not balance. Source {source:.1f} m² + {added:.1f} m² "
            f"added − {removed:.1f} m² removed = {source + added - removed:.1f} m², but the "
            f"output covers {output:.1f} m² ({balance:+.1f} m², over {slack:.1f} m² of slack). "
            f"Geometry changed without an event describing it."
        )

    for key, recomputed in (("removedArea", removed), ("addedArea", added)):
        stated = budget.get(key)
        if isinstance(stated, (int, float)) and abs(float(stated) - recomputed) > slack:
            failures.append(
                f"{lod}: areaBudget.{key} is {float(stated):.1f} m² but the events add up to "
                f"{recomputed:.1f} m²"
            )

    retained = budget.get("retainedAreaRatio")
    if isinstance(retained, (int, float)) and source > 0.0:
        expected = (source - removed) / source
        if abs(float(retained) - expected) > 1e-9:
            failures.append(
                f"{lod}: areaBudget.retainedAreaRatio is {float(retained):.9f} but the areas give "
                f"{expected:.9f}"
            )
    return failures


def _check_counting_identities(
    lod: str, simplification: dict[str, Any], events: list[LossEvent]
) -> list[str]:
    """Parts in = parts out + what the records say happened, and the same for holes.

    The hole identity is the one that closes the case a hole *count* alone cannot see. When a
    source enclave disappears completely there is no output ring left to inspect, so every
    version of this checker that only compared ``holeCount`` against ``sourceHoleCount`` for
    *invented* holes let a vanished one through with ``lossy: false`` and an empty failure list.
    The counts are read from the events, so a build that removed the enclave and recorded nothing
    fails here.
    """
    failures: list[str] = []
    changes = simplification.get("topologyChanges")
    changes = changes if isinstance(changes, dict) else {}

    dropped_parts = _count(events, "dropped_part")
    merges = _count(events, "part_merge")
    splits = _count(events, "part_split")
    created = _count(events, "part_created")
    dropped_holes = _count(events, "dropped_hole")
    hole_merges = _count(events, "hole_merge")
    hole_splits = _count(events, "hole_split")

    part_change = simplification["sourcePartCount"] - simplification["partCount"]
    accounted = dropped_parts + merges - splits - created
    if part_change != accounted:
        failures.append(
            f"{lod}: the part count moved by {part_change} "
            f"({simplification['sourcePartCount']} -> {simplification['partCount']}) but the "
            f"events account for {accounted} ({dropped_parts} dropped, {merges} merges, "
            f"{splits} splits, {created} created)"
        )

    hole_change = simplification["sourceHoleCount"] - simplification["holeCount"]
    hole_accounted = dropped_holes + hole_merges - hole_splits
    if hole_change != hole_accounted:
        failures.append(
            f"{lod}: the hole count moved by {hole_change} "
            f"({simplification['sourceHoleCount']} -> {simplification['holeCount']}) but the "
            f"events account for {hole_accounted} ({dropped_holes} dropped, {hole_merges} merges, "
            f"{hole_splits} splits). A source enclave that vanishes leaves no output ring behind, "
            f"so this count is the only thing that sees it"
        )

    # The same numbers exist twice in a manifest: as events, and as the topologyChanges block a
    # client reads. If the two disagree, one of them is describing a different run.
    for key, from_events in (
        ("merges", merges),
        ("splits", splits),
        ("created", created),
        ("droppedHoles", dropped_holes),
    ):
        stated = changes.get(key)
        if isinstance(stated, int) and stated != from_events:
            failures.append(
                f"{lod}: topologyChanges.{key} is {stated} but the events record {from_events}"
            )
    return failures


def _check_client_flags(lod: str, level: dict[str, Any], events: list[LossEvent]) -> list[str]:
    """``topologyChanged`` and ``pickingUnsafe`` must follow from the events, not from opinion.

    Phase 4/5 read these to decide whether a level can answer a click. A level that merged two
    islands and reports ``pickingUnsafe: false`` would hand Unity a land bridge and no warning.

    ``events`` is **every** stage's, not simplification's. The fifth review round found this
    function reading the simplification slice: a ``high`` level whose triangulation dropped a part
    was checked against an empty event list and passed with ``pickingUnsafe: false`` beside
    ``lossy: true``. The same blind spot covered ``hole_merge``/``hole_split`` and everything the
    geoBoundaries normalization removed upstream.

    The list of unsafe kinds is not written out here: it is ``kind_of(...).picking_unsafe`` from
    the shared schema, for the reason at the top of this file. What stays independent is the
    derivation — this recomputes the flags from the report's own events and compares.
    """
    failures: list[str] = []
    # Any recorded event of an unsafe kind counts, without also asking that its count be nonzero:
    # a ``dropped_part`` written with ``count: 0`` is a malformed record, and reading it as
    # "nothing happened" is the fail-open move this whole schema exists to prevent. Events with
    # nothing in them at all never reach a ledger — ``LossLedger.of`` drops those.
    changed = any(kind_of(item.kind).changes_topology for item in events)
    unsafe = any(kind_of(item.kind).picking_unsafe for item in events)

    for key, derived in (("topologyChanged", changed), ("pickingUnsafe", unsafe)):
        stated = level.get(key)
        if stated is None:
            failures.append(
                f"{lod}: no '{key}' flag; phases 4 and 5 have nothing to gate picking on"
            )
        elif not isinstance(stated, bool):
            failures.append(
                f"{lod}: {key} is {stated!r}, not a boolean; a client reading this with a "
                f"truthiness test would get an answer nobody wrote"
            )
        elif stated != derived:
            culprits = sorted(
                {
                    item.kind
                    for item in events
                    if (
                        kind_of(item.kind).changes_topology
                        if key == "topologyChanged"
                        else kind_of(item.kind).picking_unsafe
                    )
                }
            )
            failures.append(
                f"{lod}: {key} is {stated} but the events say {derived}"
                + (f" ({', '.join(culprits)})" if culprits else "")
            )
    return failures


def _check_lossy_implies_unsafe(lod: str, level: dict[str, Any]) -> list[str]:
    """A level cannot report that geometry went missing and that clicks are still reliable.

    Stated as its own check, against the two serialised booleans, rather than left to follow from
    the recomputation above. That check compares each flag with the events; this one compares the
    flags with *each other*, so it holds even for a report whose events and flags were both
    written by something that agreed with itself and was wrong. It is the shape of the fifth
    round's blocker: ``lossy: true`` and ``pickingUnsafe: false`` in the same manifest, which
    Phase 4/5 would resolve by believing the flag they were told to gate on.
    """
    lossy, unsafe = level.get("lossy"), level.get("pickingUnsafe")
    if lossy is True and unsafe is False:
        return [
            f"{lod}: lossy is true but pickingUnsafe is false. Something the source had is not in "
            f"this level's mesh, so a click there cannot be trusted; pickingUnsafe: false claims "
            f"the geometry is topologically the source"
        ]
    return []


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
    if high["holeCount"] != high["sourceHoleCount"]:
        failures.append(
            f"high changed the hole count ({high['sourceHoleCount']} -> {high['holeCount']}); it "
            f"is the level that must preserve the source"
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
    """Every level's ledger: parseable, complete, self-consistent, and matching its geometry."""
    failures: list[str] = []
    for lod in LOD_LEVELS:
        loss = levels[lod].get("loss")
        if not isinstance(loss, dict):
            failures.append(
                f"{lod} carries no 'loss' block, so its lossy flag cannot be checked against "
                f"anything; the build did not record where the loss came from"
            )
            continue
        if loss.get("schemaVersion") != SCHEMA_VERSION:
            failures.append(
                f"{lod}: loss block declares schemaVersion {loss.get('schemaVersion')!r}, this "
                f"checker understands {SCHEMA_VERSION}; refusing to guess what the records mean"
            )
            continue

        events, parse_failures = _events(loss, f"{lod}.loss")
        failures.extend(parse_failures)
        if parse_failures:
            continue

        failures.extend(_check_stages(f"{lod}.loss", loss, events))

        derived = any(kind_of(item.kind).category == CATEGORY_LOSS for item in events)
        failures.extend(_check_declared_flag(f"{lod}.loss", loss.get("lossy"), derived))
        failures.extend(_check_declared_flag(lod, levels[lod].get("lossy"), derived))

        simplification = levels[lod]["simplification"]
        simplify_events = [item for item in events if item.stage == STAGE_SIMPLIFICATION]
        failures.extend(_check_area_budget(lod, simplification, simplify_events))
        failures.extend(_check_counting_identities(lod, simplification, simplify_events))
        # Every stage's events, unlike the two checks above: the budget and the counting
        # identities are simplification's books, but a click lands on the final mesh.
        failures.extend(_check_client_flags(lod, levels[lod], events))
        failures.extend(_check_lossy_implies_unsafe(lod, levels[lod]))

        if lod == "high":
            lost = [item for item in simplify_events if item.is_loss]
            if lost:
                kinds = ", ".join(sorted({item.kind for item in lost}))
                failures.append(
                    f"high recorded loss in simplification ({kinds}); it is the level that must "
                    f"preserve the source"
                )
    return failures


def _check_normalization(normalization: dict[str, Any] | None, levels: dict[str, Any]) -> list[str]:
    """The normalization step drops real geometry, so its numbers get checked too.

    Nothing here looked at this block before, which meant the seven islets the chain removes from
    the source could have become seventy without the CI run noticing.
    """
    if normalization is None:
        return ["report has no 'normalization' block; the chain did not record what it dropped"]

    failures: list[str] = []
    events, parse_failures = _events(normalization.get("loss"), "normalization.loss")
    failures.extend(parse_failures)
    if parse_failures:
        return failures

    derived = any(kind_of(item.kind).category == CATEGORY_LOSS for item in events)
    declared = (normalization.get("loss") or {}).get("lossy")
    failures.extend(_check_declared_flag("normalization", declared, derived))

    dropped = sum(item.count for item in events)
    if dropped > MAX_NORMALIZATION_DROPS:
        failures.append(
            f"normalization dropped {dropped} geometries, over the expected ceiling of "
            f"{MAX_NORMALIZATION_DROPS}; the source data or the area floor changed"
        )

    # A lossy normalization must reach every level's manifest, or a consumer reading one level
    # would be told nothing was lost. Checked against the events rather than the boolean, so a
    # normalization block that under-reports itself cannot excuse the levels too.
    if derived:
        silent = [lod for lod in LOD_LEVELS if not levels[lod].get("lossy")]
        if silent:
            failures.append(
                f"normalization was lossy but these levels report lossy=false: {', '.join(silent)}"
            )

    # The per-level upstream events are supposed to *be* the normalization's events. If they
    # drift, one of the two is describing a different run and neither can be trusted.
    reference = {(item.kind, item.count, item.details) for item in events}
    for lod in LOD_LEVELS:
        level_events, level_failures = _events(levels[lod].get("loss"), f"{lod}.loss")
        if level_failures:
            continue
        upstream = {
            (item.kind, item.count, item.details)
            for item in level_events
            if item.stage == "upstream"
        }
        if upstream != reference:
            failures.append(
                f"{lod}.loss upstream events disagree with the normalization block: the level has "
                f"{sorted(kind for kind, _, _ in upstream)} against "
                f"{sorted(kind for kind, _, _ in reference)}"
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
