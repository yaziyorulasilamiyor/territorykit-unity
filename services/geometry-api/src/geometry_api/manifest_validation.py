"""Fail-closed validation of a completed LOD build, run once at publish time.

Two independent checks, both required before ``scripts/publish_dataset.py`` will move a build
into ``revisions/`` (see ``docs/phases/FAZ-3-PLAN.md`` §1.1):

* :func:`check` — is the ``lod-report.json`` *internally* consistent? This is
  ``scripts/check_lod_report.py``'s original checker, moved here so the CI script and the
  publisher share one implementation instead of two that can drift. It recomputes the ``lossy``/
  ``topologyChanged``/``pickingUnsafe`` flags, the area budget and the part/hole counting
  identities from the report's own recorded events, and compares them with what the report
  claims. A report that lies about itself is caught here.
* :func:`check_report_matches_build` — does the report actually describe *this* ``build_dir``?
  ``check()`` alone cannot tell a report that is honest about itself from one that is honest
  about a different run: a stale ``lod-report.json`` left beside freshly rebuilt meshes, or an
  ``index.json`` hand-edited after the fact, both pass ``check()`` on their own numbers. This
  cross-checks the report's per-level totals and flags against the ``index.json`` files that are
  about to be copied into a revision.

Passing both is what lets the API skip re-deriving these flags at request time (Phase 3's
"zero geometry at request time" rule): by the time a revision exists under ``revisions/``, both
checks have already run against it once, and the API is trusted to relay what it finds unchanged.

**What stays shared, what does not.** The event schema (``geometry_api.loss``) is imported, never
copied — Phase 2 spent four review rounds closing exactly that kind of drift. What is *not*
shared: this module never re-derives geometry (no shapely, no earcut, no topojson import) — it
only re-derives arithmetic identities from numbers the build already wrote down. See Phase 3's
"known limitation, inherited" note: a report can misclassify a real loss as a change (e.g. a
`dropped_part` recorded as `part_merge` + `boundary_retreat`) and still satisfy every identity
here, because the identities prove internal consistency, not that the classification itself was
correct. Phase 2's own report documents this gap; Phase 3 does not close it.
"""

from __future__ import annotations

import json
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

Deliberately duplicated rather than imported: this is a *threshold*, not a schema. The check runs
against a report produced by some other run, possibly an older build, so it must state the
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
"""Islets plus interior rings the geoBoundaries normalization may drop before this objects.

TUR ADM1 drops 7. The ceiling is not the observed number, so a routine data refresh does not
block publishing, but a change that starts discarding whole regions does.
"""

BUDGET_RELATIVE_TOLERANCE = 1e-9
"""Slack in the area identity, as a fraction of the source area. Float64 room, not policy."""


def _events(block: Any, label: str) -> tuple[list[LossEvent], list[str]]:
    """Parse a serialised ledger, turning every schema violation into a named failure.

    Fail-closed. An event whose kind is not in the schema is a failure here, not a record to skip.
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
    source enclave disappears completely there is no output ring left to inspect, so a checker
    that only compares ``holeCount`` against ``sourceHoleCount`` for *invented* holes lets a
    vanished one through with ``lossy: false`` and an empty failure list. The counts are read from
    the events, so a build that removed the enclave and recorded nothing fails here.
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

    ``events`` is **every** stage's, not simplification's — see the module docstring in
    ``geometry_api.loss`` for the case that forced this.
    """
    failures: list[str] = []
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
    """A level cannot report that geometry went missing and that clicks are still reliable."""
    lossy, unsafe = level.get("lossy"), level.get("pickingUnsafe")
    if lossy is True and unsafe is False:
        return [
            f"{lod}: lossy is true but pickingUnsafe is false. Something the source had is not in "
            f"this level's mesh, so a click there cannot be trusted; pickingUnsafe: false claims "
            f"the geometry is topologically the source"
        ]
    return []


def check(report: dict[str, Any]) -> list[str]:
    """Return a list of failures against a whole ``lod-report.json``; empty means it is sound.

    Runs the cross-level checks a single ``index.json`` cannot: strict coarsening between levels,
    territory-count agreement, and that the upstream normalization's loss reached every level.
    """
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
    """The normalization step drops real geometry, so its numbers get checked too."""
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


_MANIFEST_COMPARISON_FIELDS: tuple[str, ...] = (
    "territoryCount",
    "vertices",
    "triangles",
    "bytes",
    "lossy",
    "topologyChanged",
    "pickingUnsafe",
    "simplification",
    "loss",
)


def check_report_matches_build(report: dict[str, Any], build_dir: Path) -> list[str]:
    """Prove ``report`` (``lod-report.json``) actually describes the ``index.json`` files at
    ``build_dir/{lod}/index.json`` that are about to be copied into a revision.

    :func:`check` proves the report is honest about *itself*. It says nothing about whether the
    report was produced by this particular build: a stale ``lod-report.json`` left beside a
    freshly rebuilt ``index.json``, or a manifest hand-edited after the report was written, both
    pass ``check()`` on their own numbers. This is the second, independent gate — every field a
    client of ``/v1/datasets/{id}`` reads (§6, §8 of the phase 3 plan) is compared, report value
    against manifest value, and any mismatch is named rather than averaged away.
    """
    failures: list[str] = []
    levels = report.get("levels", {})

    for lod in LOD_LEVELS:
        level = levels.get(lod)
        index_path = build_dir / lod / "index.json"
        if level is None:
            failures.append(f"{lod}: report has no level to compare against {index_path}")
            continue
        if not index_path.exists():
            failures.append(f"{lod}: {index_path} does not exist")
            continue
        try:
            manifest = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append(f"{lod}: {index_path} is not valid JSON: {exc}")
            continue

        totals = manifest.get("totals") or {}
        from_manifest: dict[str, Any] = {
            "territoryCount": manifest.get("territoryCount"),
            "vertices": totals.get("vertices"),
            "triangles": totals.get("triangles"),
            "bytes": totals.get("bytes"),
            "lossy": manifest.get("lossy"),
            "topologyChanged": manifest.get("topologyChanged"),
            "pickingUnsafe": manifest.get("pickingUnsafe"),
            "simplification": manifest.get("simplification"),
            "loss": manifest.get("loss"),
        }
        for field_name in _MANIFEST_COMPARISON_FIELDS:
            report_value = level.get(field_name)
            manifest_value = from_manifest[field_name]
            if report_value != manifest_value:
                failures.append(
                    f"{lod}.{field_name}: lod-report.json says {report_value!r} but "
                    f"{index_path} says {manifest_value!r} — the report does not describe the "
                    f"files about to be published"
                )
    return failures
