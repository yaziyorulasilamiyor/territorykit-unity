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
from .loader import Dataset, DatasetError, Territory, load_dataset
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

    @property
    def uint16_usage(self) -> float:
        return self.vertex_count / UINT16_MAX_VERTEX_COUNT

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
            "parentId": self.territory.parent_id,
            "neighborIds": list(self.territory.neighbor_ids),
        }


def build_meshes(dataset: Dataset, lod: str = LOD_HIGH) -> list[MeshEntry]:
    """Build every territory in memory. Raises BuildError listing all failures, not just one."""
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
            )
        )

    if failures:
        raise BuildError(
            f"{len(failures)} of {len(dataset)} territories failed:\n  " + "\n  ".join(failures)
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
        },
        "sourceMetadata": dict(dataset.metadata),
        "territories": [entry.as_manifest_entry() for entry in entries],
    }


def write_build(output_dir: Path, entries: Sequence[MeshEntry], manifest: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        (output_dir / entry.filename).write_bytes(entry.payload)
    text = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    (output_dir / MANIFEST_FILENAME).write_text(text, encoding="utf-8")


def _unique_filename(territory_id: str, used: dict[str, str]) -> str:
    """Territory ids may contain characters no filesystem accepts (TerritoryKit uses colons)."""
    stem = _UNSAFE_FILENAME_CHARS.sub("_", territory_id) or "territory"
    candidate = stem
    suffix = 1
    while candidate in used and used[candidate] != territory_id:
        suffix += 1
        candidate = f"{stem}-{suffix}"
    used[candidate] = territory_id
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
        help="output directory; existing files are overwritten but never deleted",
    )
    parser.add_argument("--lod", default=LOD_HIGH, choices=AVAILABLE_LODS)
    parser.add_argument("--quiet", action="store_true", help="suppress the per-territory table")
    args = parser.parse_args(argv)

    try:
        dataset = load_dataset(args.input)
        entries = build_meshes(dataset, lod=args.lod)
    except (DatasetError, BuildError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    manifest = build_manifest(dataset, entries, args.lod)
    write_build(args.output, entries, manifest)

    if not args.quiet:
        _print_report(entries, manifest, sys.stdout)
    _print_index_warnings(entries, sys.stderr)
    print(f"wrote {len(entries)} meshes and {MANIFEST_FILENAME} to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
