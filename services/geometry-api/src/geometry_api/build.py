"""Batch mesh build: ``python -m geometry_api.build --input <dataset> --output <dir>``.

Runs the whole Phase 1 pipeline over every territory in a dataset — load, project, triangulate,
encode — and writes one ``.tkms`` file each plus an ``index.json`` manifest.

The output is deterministic: the same input produces byte-identical files, with territories
sorted by id and no timestamps anywhere. Phase 3's content-addressed cache depends on that, so
it is asserted by a test rather than left as an intention.

Nothing is written until every territory has been built, so a failure cannot leave a directory
that looks like a complete build.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .encoding import UINT16_MAX_VERTEX_COUNT, encode_tkms
from .loader import Dataset, DatasetError, InvalidGeometryPolicy, Territory, load_dataset
from .loss import (
    STAGE_SIMPLIFICATION,
    STAGE_TRIANGULATION,
    STAGE_UPSTREAM,
    LossLedger,
    LossSchemaError,
    ledger_from_manifest,
)
from .projection import Origin, ProjectionError, project_geometry
from .simplify import (
    DEFAULT_MAX_DROPPED_PART_AREA,
    DEFAULT_MAX_TOTAL_DROPPED_AREA,
    LOD_HIGH,
    LOD_LEVELS,
    PART_SEVERE_SHRINK_RATIO,
    SimplifyError,
    SimplifyResult,
    simplify_dataset,
)
from .triangulate import GeometryLoss, TriangulationError, triangulate

AVAILABLE_LODS = LOD_LEVELS

PROJECTION_NAME = "webmercator-local-meters"
MANIFEST_FILENAME = "index.json"
MESH_SUFFIX = ".tkms"

UINT16_WARNING_RATIO = 0.8
"""Warn once a mesh uses this much of the uint16 index space, so the ceiling is never a surprise."""

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")

_WINDOWS_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{digit}" for digit in range(1, 10)}
    | {f"LPT{digit}" for digit in range(1, 10)}
)
"""Windows refuses these names whatever the extension, so a territory called CON needs a nudge."""


class BuildError(RuntimeError):
    """Raised when at least one territory could not be built."""


@dataclass(frozen=True)
class MeshEntry:
    """One built mesh, before anything touches the disk."""

    territory: Territory
    filename: str
    payload: bytes
    vertex_count: int
    triangle_count: int
    part_count: int
    bounds_local: tuple[float, float, float, float]
    uses_uint32_indices: bool
    loss: GeometryLoss = GeometryLoss()

    @property
    def uint16_usage(self) -> float:
        return self.vertex_count / UINT16_MAX_VERTEX_COUNT

    @property
    def is_lossy(self) -> bool:
        """True when anything the source geometry had did not reach the mesh."""
        return self.loss.is_lossy

    def as_manifest_entry(self) -> dict[str, Any]:
        return {
            "id": self.territory.id,
            "name": self.territory.name,
            "file": self.filename,
            "vertexCount": self.vertex_count,
            "triangleCount": self.triangle_count,
            "partCount": self.part_count,
            "indexFormat": "uint32" if self.uses_uint32_indices else "uint16",
            "byteLength": len(self.payload),
            "bboxLocal": list(self.bounds_local),
            "lossy": self.is_lossy,
            # Typed events rather than three named counters, so a reader of one territory sees
            # the same vocabulary as a reader of the level.
            "lossEvents": [item.as_dict() for item in self.loss.as_events()],
            "repaired": self.territory.repaired,
            "parentId": self.territory.parent_id,
            # Phase 3's territories filter (docs/api.md, ?administrativeLevel=). Named after the
            # API field rather than the loader's Python attribute (Territory.level) because this
            # is the wire contract, not an internal name.
            "administrativeLevel": self.territory.level,
            "neighborIds": list(self.territory.neighbor_ids),
        }


def build_meshes(
    dataset: Dataset, lod: str = LOD_HIGH, allow_lossy: bool = False
) -> list[MeshEntry]:
    """Build every territory in memory. Raises BuildError listing all failures, not just one.

    At ``high`` the mesh is supposed to be the source geometry, so a part or hole lost to float32
    quantization is a build failure unless ``allow_lossy`` says otherwise. Phase 2's lower levels
    drop detail on purpose; this gate is only about the level that claims not to.
    """
    if lod not in AVAILABLE_LODS:
        raise BuildError(f"unknown lod {lod!r}; available: {', '.join(AVAILABLE_LODS)}")

    origin = Origin(lon=dataset.origin_lon, lat=dataset.origin_lat)
    entries: list[MeshEntry] = []
    failures: list[str] = []
    used_filenames: dict[str, str] = {}

    for territory in sorted(dataset.territories, key=lambda t: t.id):
        try:
            projected = project_geometry(territory.geometry, origin)
            mesh = triangulate(projected)
            payload = encode_tkms(mesh.vertices, mesh.indices)
        except (ProjectionError, TriangulationError, ValueError) as exc:
            failures.append(f"{territory.id} ({territory.name}): {exc}")
            continue

        filename = _unique_filename(territory.id, used_filenames)
        entries.append(
            MeshEntry(
                territory=territory,
                filename=filename,
                payload=payload,
                vertex_count=mesh.vertex_count,
                triangle_count=mesh.triangle_count,
                part_count=mesh.part_count,
                bounds_local=mesh.bounds,
                uses_uint32_indices=mesh.vertex_count > UINT16_MAX_VERTEX_COUNT,
                loss=mesh.loss,
            )
        )

    if failures:
        raise BuildError(
            f"{len(failures)} of {len(dataset)} territories failed:\n  " + "\n  ".join(failures)
        )

    lossy = [entry for entry in entries if entry.is_lossy]
    if lossy and lod == LOD_HIGH and not allow_lossy:
        detail = "\n  ".join(
            f"{entry.territory.id} ({entry.territory.name}): {entry.loss.describe()} lost"
            for entry in lossy
        )
        raise BuildError(
            f"{len(lossy)} territories lost geometry to float32 quantization at lod '{LOD_HIGH}', "
            f"which is supposed to preserve the source. Re-run with --allow-lossy to accept it "
            f"(the counts are recorded per territory in the manifest either way):\n  " + detail
        )
    return entries


def collect_loss(
    entries: Sequence[MeshEntry],
    simplification: SimplifyResult | None,
    upstream: Mapping[str, Any] | None,
) -> LossLedger:
    """Every stage's typed records in one ledger, with one flag derived from all of them.

    The flag comes from the **events** each stage recorded, read through the closed schema in
    ``loss.py``. It does not read any stage's own ``lossy`` boolean, including the one an
    upstream JSON file supplies: three review rounds in a row found this function trusting a
    boolean that disagreed with the numbers beside it, and a fourth found the naming convention
    that was supposed to make the numbers self-describing.

    The cases that used to slip through and now cannot:

    * ``simplification`` recorded a 20.000 m² dropped part while its ``skippedParts`` counter
      read 0. The counter is gone; a dropped part *is* a ``dropped_part`` event.
    * ``upstream`` arrived as ``{"droppedParts": 7, "lossy": false}`` and was taken at its word.
      The boolean is never read, on the way in or the way out.
    * ``removedRings`` set the flag while ``collapsedParts`` did not, because one matched a
      prefix list and the other did not. There is no prefix list; a kind outside the schema
      raises ``LossSchemaError`` rather than reading as "nothing happened".

    Adding a fifth loss source means adding a kind to ``loss.EVENT_KINDS`` and emitting it. The
    flag follows without this function changing, and so does CI's independent recomputation,
    because both read the same schema.
    """
    triangulation = GeometryLoss(
        skipped_parts=sum(entry.loss.skipped_parts for entry in entries),
        skipped_rings=sum(entry.loss.skipped_rings for entry in entries),
        degenerate_triangles=sum(entry.loss.degenerate_triangles for entry in entries),
    )

    events = list(triangulation.as_events())
    stages = [STAGE_TRIANGULATION]
    if simplification is not None:
        events.extend(simplification.ledger.events)
        stages.append(STAGE_SIMPLIFICATION)
    if upstream is not None:
        events.extend(_upstream_events(upstream))
        stages.append(STAGE_UPSTREAM)
    return LossLedger.of(events, stages_recorded=stages)


def _upstream_events(upstream: Mapping[str, Any]) -> tuple[Any, ...]:
    """Read the ledger out of what ``scripts/build_lod.py`` wrote, refusing anything else.

    Fail-closed on purpose. A file this build cannot parse is not a file that lost nothing, and
    the previous version's answer to an unrecognised shape was to find no matching keys in it and
    move on — which is how seven dropped islets were once reported as no loss at all.
    """
    block = upstream.get("loss", upstream)
    events = ledger_from_manifest(block if isinstance(block, Mapping) else None).events
    off_stage = sorted({item.stage for item in events} - {STAGE_UPSTREAM})
    if off_stage:
        raise LossSchemaError(
            f"--upstream-loss carries events for stage(s) {', '.join(off_stage)}; a file "
            f"describing what happened before this build can only speak for '{STAGE_UPSTREAM}'"
        )
    return events


def build_manifest(
    dataset: Dataset,
    entries: Sequence[MeshEntry],
    lod: str,
    simplification: SimplifyResult | None = None,
    upstream_loss: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    origin = Origin(lon=dataset.origin_lon, lat=dataset.origin_lat)
    min_x = min(entry.bounds_local[0] for entry in entries)
    min_y = min(entry.bounds_local[1] for entry in entries)
    max_x = max(entry.bounds_local[2] for entry in entries)
    max_y = max(entry.bounds_local[3] for entry in entries)
    ledger = collect_loss(entries, simplification, upstream_loss)

    return {
        "datasetId": dataset.id,
        "datasetName": dataset.name,
        "sourceFormat": dataset.source_format,
        "lod": lod,
        "meshFormat": "TKMS",
        "meshFormatVersion": 1,
        "origin": {
            "lon": origin.lon,
            "lat": origin.lat,
            "projection": PROJECTION_NAME,
            "scale": origin.scale,
        },
        "boundsWgs84": list(dataset.bounds),
        "boundsLocal": [min_x, min_y, max_x, max_y],
        "territoryCount": len(entries),
        "totals": {
            "vertices": sum(entry.vertex_count for entry in entries),
            "triangles": sum(entry.triangle_count for entry in entries),
            "bytes": sum(len(entry.payload) for entry in entries),
            "repairedTerritories": sum(1 for entry in entries if entry.territory.repaired),
        },
        # One flag over every stage, derived from the events under "loss" and from nothing else.
        # No stage can be lossy while this reads false, and nothing can be lossy without an
        # event naming what went missing.
        "lossy": ledger.is_lossy,
        "loss": ledger.as_manifest_dict(),
        # What phases 4 and 5 read before they trust a level for picking. Derived from the same
        # ledger as "lossy" above — every stage, not just simplification — because the mesh a
        # click hits is the end of the whole chain.
        **_client_flags(ledger),
        # Kept at the top level as well: phase 1's manifest readers and the CI report checker
        # already address it here, and moving it would break them for no gain.
        "simplification": simplification.as_manifest_dict() if simplification else None,
        "sourceMetadata": dict(dataset.metadata),
        "territories": [entry.as_manifest_entry() for entry in entries],
    }


def _client_flags(ledger: LossLedger) -> dict[str, Any]:
    """The two booleans a renderer needs, stated rather than left to be inferred.

    Phase 2 measured the topology changes and wrote them into the manifest, but nothing on the
    Unity side would have had a field to read: ``topologyChanges`` is a block of counts, and
    asking a client to decide "is 30 merges a problem?" is asking it to re-derive a policy.
    These two say it outright, for phases 4 and 5 to gate on.

    ``pickingUnsafe`` is the sharper one, and it is about the **final** mesh. The fifth review
    round found it derived from ``SimplifyResult`` alone, which made it answer a question nobody
    asks: "did the simplifier change the topology", not "can this mesh answer a click". The gap
    was demonstrable — a ``high`` level whose triangulation recorded ``skipped_part: 1`` was
    written as ``lossy: true`` with ``pickingUnsafe: false``, and CI passed it. Holes merging or
    splitting, and anything the geoBoundaries normalization removed before the build, were
    invisible to it for the same reason.

    So it is derived from the ledger, which spans normalization, simplification and
    triangulation, through the per-kind declarations in ``loss.py``. ``False`` now means what a
    reader would assume it means: nothing between the source polygons and this mesh removed
    geometry or changed the part/enclave structure. ``lossy: true`` with ``pickingUnsafe: false``
    is unreachable rather than merely unobserved — see ``PICKING_UNSAFE_KINDS``.
    """
    return {
        "topologyChanged": ledger.topology_changed,
        "pickingUnsafe": ledger.picking_unsafe,
    }


def write_build(
    output_dir: Path,
    entries: Sequence[MeshEntry],
    manifest: dict[str, Any],
    clean: bool = False,
) -> None:
    """Write the meshes and the manifest.

    ``clean`` removes meshes left by an earlier run so the directory describes exactly one
    build. It only ever deletes files this tool writes — never the directory itself, and never
    anything it did not put there.

    Refuses to write into a path with a ``revisions`` component (Phase 3,
    ``docs/phases/FAZ-3-PLAN.md`` §3.5b): that name is reserved for
    ``scripts/publish_dataset.py``'s atomic staging-then-rename output. A build command writing
    there directly would create an unvalidated, non-atomic directory that looks like a published
    revision but never went through the fail-closed checks in ``manifest_validation`` or the
    staging/verify/rename sequence — the exact thing Phase 3's immutability guarantee assumes
    cannot happen.
    """
    if "revisions" in output_dir.resolve().parts:
        raise BuildError(
            f"refusing to write into {output_dir} — it contains a 'revisions' path component, "
            f"which is reserved for scripts/publish_dataset.py's atomic publish. Build into a "
            f"plain directory (e.g. the output of scripts/build_lod.py) and publish it instead."
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    if clean:
        for stale in output_dir.glob(f"*{MESH_SUFFIX}"):
            stale.unlink()
        (output_dir / MANIFEST_FILENAME).unlink(missing_ok=True)
    for entry in entries:
        (output_dir / entry.filename).write_bytes(entry.payload)
    text = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    (output_dir / MANIFEST_FILENAME).write_text(text, encoding="utf-8")


def _unique_filename(territory_id: str, used: dict[str, str]) -> str:
    """Turn a territory id into a filename no filesystem will quietly merge with another.

    Ids may contain characters that are not valid in a path (TerritoryKit uses colons), and
    collisions are tracked case-insensitively: on Windows and macOS "A" and "a" are the same
    file, so a case-sensitive check would let one territory overwrite another while the manifest
    still listed both.
    """
    stem = _UNSAFE_FILENAME_CHARS.sub("_", territory_id).rstrip(". ") or "territory"
    if stem.upper() in _WINDOWS_RESERVED_STEMS:
        stem = f"{stem}_"

    candidate = stem
    suffix = 1
    while candidate.casefold() in used and used[candidate.casefold()] != territory_id:
        suffix += 1
        candidate = f"{stem}-{suffix}"
    used[candidate.casefold()] = territory_id
    return candidate + MESH_SUFFIX


def _check_simplification(result: SimplifyResult, allow_lossy: bool) -> None:
    """At ``high`` simplification must not drop anything; lower levels drop detail on purpose.

    Same gate as the triangulation one, applied one stage earlier. ``high`` is the level that
    claims to still be the source geometry, so a part or hole lost here is a build failure
    rather than a line in the manifest nobody reads.

    Asks ``result.is_lossy``, which reads the ledger's loss events. Merges and splits are
    recorded as changes rather than losses and deliberately do not fail this gate — see
    ``docs/PROJE-TALIMATI.md`` §FAZ 2 — but a dropped part or a vanished enclave does, whatever
    the part count happens to add up to.
    """
    if result.lod != LOD_HIGH or not result.is_lossy or allow_lossy:
        return
    dropped = ""
    if result.dropped_parts:
        dropped = f" (including {result.dropped_area:.1f} m² of dropped parts)"
    raise BuildError(
        f"simplification at lod '{LOD_HIGH}' (tolerance {result.tolerance}) lost "
        f"{result.ledger.describe()}{dropped}, but that level is supposed to preserve the "
        f"source. Re-run with --allow-lossy to accept it, or lower the tolerance."
    )


def _print_simplification(result: SimplifyResult, stream: Any) -> None:
    print(
        f"lod '{result.lod}' (tolerance {result.tolerance}): "
        f"{result.source_vertex_count} → {result.vertex_count} vertices "
        f"({result.vertex_ratio:.1%} of source), parts "
        f"{result.source_part_count} → {result.part_count}, holes "
        f"{result.source_hole_count} → {result.hole_count}",
        file=stream,
    )
    if result.topology_changed:
        print(
            f"  topology changed: {result.merges} part merge(s), {result.splits} split(s), "
            f"{result.created_parts} created — no area lost, but the component structure differs "
            f"from the source, and the merges add {result.merge_added_area:.1f} m² of land the "
            f"source called water (upper bound)",
            file=stream,
        )
    print(
        f"  area: source {result.source_area / 1e6:.1f} km² → output "
        f"{result.output_area / 1e6:.1f} km² ({result.removed_area / 1e6:.3f} km² of the source "
        f"is no longer covered, {result.added_area / 1e6:.3f} km² is new); retained "
        f"{result.retained_area_ratio:.4%}, worst part kept "
        f"{result.min_retained_area_ratio:.1%} ({result.severe_shrink_count} part(s) under "
        f"{PART_SEVERE_SHRINK_RATIO:.0%})",
        file=stream,
    )
    if result.is_lossy:
        print(
            f"  simplification lost {result.ledger.describe()} "
            f"(recorded in {MANIFEST_FILENAME} under 'loss')",
            file=stream,
        )


def _print_report(entries: Sequence[MeshEntry], manifest: dict[str, Any], stream: Any) -> None:
    print(
        f"{'territory':<28} {'vertices':>9} {'triangles':>10} {'parts':>6} {'index':>7} "
        f"{'bytes':>9}",
        file=stream,
    )
    for entry in entries:
        print(
            f"{entry.territory.name[:28]:<28} {entry.vertex_count:>9} {entry.triangle_count:>10} "
            f"{entry.part_count:>6} {'uint32' if entry.uses_uint32_indices else 'uint16':>7} "
            f"{len(entry.payload):>9}",
            file=stream,
        )
    totals = manifest["totals"]
    print(
        f"\n{len(entries)} territories, {totals['vertices']} vertices, "
        f"{totals['triangles']} triangles, {totals['bytes']} bytes",
        file=stream,
    )


def _print_index_warnings(entries: Sequence[MeshEntry], stream: Any) -> None:
    for entry in entries:
        if entry.uses_uint32_indices or entry.uint16_usage < UINT16_WARNING_RATIO:
            continue
        print(
            f"warning: {entry.territory.name} uses {entry.uint16_usage:.1%} of the uint16 index "
            f"space ({entry.vertex_count} of {UINT16_MAX_VERTEX_COUNT} vertices); "
            f"{UINT16_MAX_VERTEX_COUNT - entry.vertex_count} vertices of headroom",
            file=stream,
        )


def _print_loss_warnings(entries: Sequence[MeshEntry], stream: Any) -> None:
    for entry in entries:
        if entry.is_lossy:
            print(
                f"warning: {entry.territory.name} lost {entry.loss.describe()} — too small to "
                f"survive the float32 storage grid (recorded in {MANIFEST_FILENAME})",
                file=stream,
            )
        if entry.territory.repaired:
            print(
                f"warning: {entry.territory.name} had invalid source geometry and was rebuilt "
                f"with make_valid; its shape is not exactly the source shape",
                file=stream,
            )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m geometry_api.build",
        description="Triangulate every territory in a dataset and write TKMS meshes.",
    )
    parser.add_argument("--input", required=True, type=Path, help="dataset .geojson or .json")
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="output directory; existing meshes are overwritten (see --clean)",
    )
    parser.add_argument("--lod", default=LOD_HIGH, choices=AVAILABLE_LODS)
    parser.add_argument("--quiet", action="store_true", help="suppress the per-territory table")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="delete meshes and the manifest left by an earlier run before writing",
    )
    parser.add_argument(
        "--allow-lossy",
        action="store_true",
        help="accept parts or holes lost to float32 quantization at the high level",
    )
    parser.add_argument(
        "--repair-invalid",
        action="store_true",
        help="rebuild self-intersecting geometry with make_valid instead of rejecting it",
    )
    parser.add_argument(
        "--max-lost-area",
        type=float,
        default=DEFAULT_MAX_DROPPED_PART_AREA,
        help=f"fail if simplification drops a single part larger than this many square metres "
        f"(default: {DEFAULT_MAX_DROPPED_PART_AREA:.0f}); 'inf' records losses without failing",
    )
    parser.add_argument(
        "--max-total-lost-area",
        type=float,
        default=DEFAULT_MAX_TOTAL_DROPPED_AREA,
        help=f"fail if simplification drops more than this many square metres in total across "
        f"every part (default: {DEFAULT_MAX_TOTAL_DROPPED_AREA:.0f}); the per-part limit alone "
        f"lets any number of sub-threshold parts through",
    )
    parser.add_argument(
        "--upstream-loss",
        type=Path,
        help="JSON describing geometry lost before this build (scripts/build_lod.py writes it "
        "for the geoBoundaries normalization step), folded into the manifest's lossy flag",
    )
    args = parser.parse_args(argv)

    upstream_loss: dict[str, Any] | None = None
    if args.upstream_loss:
        try:
            upstream_loss = json.loads(args.upstream_loss.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"error: cannot read --upstream-loss {args.upstream_loss}: {exc}", file=sys.stderr
            )
            return 1

    on_invalid: InvalidGeometryPolicy = "repair" if args.repair_invalid else "reject"
    try:
        dataset = load_dataset(args.input, on_invalid=on_invalid)
        simplification = simplify_dataset(
            dataset,
            args.lod,
            max_dropped_part_area=args.max_lost_area,
            max_total_dropped_area=args.max_total_lost_area,
        )
        _check_simplification(simplification, allow_lossy=args.allow_lossy)
        entries = build_meshes(simplification.dataset, lod=args.lod, allow_lossy=args.allow_lossy)
        # Inside the try: a --upstream-loss file this build cannot parse is a failure, not a
        # traceback and not a silent "then nothing was lost upstream".
        manifest = build_manifest(dataset, entries, args.lod, simplification, upstream_loss)
    except (DatasetError, BuildError, SimplifyError, LossSchemaError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    write_build(args.output, entries, manifest, clean=args.clean)

    if not args.quiet:
        _print_report(entries, manifest, sys.stdout)
        _print_simplification(simplification, sys.stdout)
    _print_index_warnings(entries, sys.stderr)
    _print_loss_warnings(entries, sys.stdout)
    print(f"wrote {len(entries)} meshes and {MANIFEST_FILENAME} to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
