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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .encoding import UINT16_MAX_VERTEX_COUNT, encode_tkms
from .loader import Dataset, DatasetError, InvalidGeometryPolicy, Territory, load_dataset
from .projection import Origin, ProjectionError, project_geometry
from .triangulate import TriangulationError, triangulate

LOD_HIGH = "high"
AVAILABLE_LODS = (LOD_HIGH,)
"""Phase 2 adds ``medium`` and ``low``; the flag exists now so the output layout does not move."""

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
    skipped_parts: int = 0
    skipped_rings: int = 0

    @property
    def uint16_usage(self) -> float:
        return self.vertex_count / UINT16_MAX_VERTEX_COUNT

    @property
    def is_lossy(self) -> bool:
        """True when quantization dropped a part or a hole that the source geometry had."""
        return bool(self.skipped_parts or self.skipped_rings)

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
            "skippedParts": self.skipped_parts,
            "skippedRings": self.skipped_rings,
            "repaired": self.territory.repaired,
            "parentId": self.territory.parent_id,
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
                skipped_parts=mesh.skipped_parts,
                skipped_rings=mesh.skipped_rings,
            )
        )

    if failures:
        raise BuildError(
            f"{len(failures)} of {len(dataset)} territories failed:\n  " + "\n  ".join(failures)
        )

    lossy = [entry for entry in entries if entry.is_lossy]
    if lossy and lod == LOD_HIGH and not allow_lossy:
        detail = "\n  ".join(
            f"{entry.territory.id} ({entry.territory.name}): "
            f"{entry.skipped_parts} part(s), {entry.skipped_rings} hole(s) lost"
            for entry in lossy
        )
        raise BuildError(
            f"{len(lossy)} territories lost geometry to float32 quantization at lod '{LOD_HIGH}', "
            f"which is supposed to preserve the source. Re-run with --allow-lossy to accept it "
            f"(the counts are recorded per territory in the manifest either way):\n  " + detail
        )
    return entries


def build_manifest(dataset: Dataset, entries: Sequence[MeshEntry], lod: str) -> dict[str, Any]:
    origin = Origin(lon=dataset.origin_lon, lat=dataset.origin_lat)
    min_x = min(entry.bounds_local[0] for entry in entries)
    min_y = min(entry.bounds_local[1] for entry in entries)
    max_x = max(entry.bounds_local[2] for entry in entries)
    max_y = max(entry.bounds_local[3] for entry in entries)

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
            "skippedParts": sum(entry.skipped_parts for entry in entries),
            "skippedRings": sum(entry.skipped_rings for entry in entries),
            "repairedTerritories": sum(1 for entry in entries if entry.territory.repaired),
        },
        "lossy": any(entry.is_lossy for entry in entries),
        "sourceMetadata": dict(dataset.metadata),
        "territories": [entry.as_manifest_entry() for entry in entries],
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
    """
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
                f"warning: {entry.territory.name} lost {entry.skipped_parts} part(s) and "
                f"{entry.skipped_rings} hole(s) to float32 quantization — they are too small to "
                f"survive the storage grid (recorded in {MANIFEST_FILENAME})",
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
    args = parser.parse_args(argv)

    on_invalid: InvalidGeometryPolicy = "repair" if args.repair_invalid else "reject"
    try:
        dataset = load_dataset(args.input, on_invalid=on_invalid)
        entries = build_meshes(dataset, lod=args.lod, allow_lossy=args.allow_lossy)
    except (DatasetError, BuildError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    manifest = build_manifest(dataset, entries, args.lod)
    write_build(args.output, entries, manifest, clean=args.clean)

    if not args.quiet:
        _print_report(entries, manifest, sys.stdout)
    _print_index_warnings(entries, sys.stderr)
    _print_loss_warnings(entries, sys.stdout)
    print(f"wrote {len(entries)} meshes and {MANIFEST_FILENAME} to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
