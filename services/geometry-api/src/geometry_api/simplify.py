"""Topology-preserving simplification: one dataset in, one detail level out.

**Why this is not TerritoryKit's simplifier.** The phase brief called for
``territory geometry simplify --strategy topology-safe`` and for this module to stay a
placeholder. That was tried first and measured, not assumed. The strategy simplifies every
ring of every zone independently with Douglas-Peucker (see
``vendor/territorykit/packages/generators/src/geometry-simplification.ts``); nothing ties a
boundary shared by two provinces to a single simplified result. Its own report says so — the
``topologyAudit`` field counted 13369 of 57978 shared segments broken at ``high`` and 48204 at
``low`` — and the CLI still exits 0. Measured geometrically on the 81-province dataset, that
left 163 of 197 neighbour pairs with gaps totalling 63 km², up to 2 km² between a single pair.
``docs/territorykit-simplification-finding.md`` has the reproduction.

**What this does instead.** ``topojson`` decomposes the dataset into arcs, where a boundary
two regions share is *one* arc, simplifies each arc once, and rebuilds both regions from the
shared result. Neighbours therefore cannot disagree about a boundary: they are reading the same
vertices. This is a library, not a hand-rolled algorithm — the distinction the brief cared
about was not writing a second simplification policy, and this does not.

**Artifact holes.** Aggressive tolerance can pull two shores of a narrow inlet through each
other, and repairing that self-intersection leaves an interior ring the source never had — a
literal hole in the region. These are removed, which is safe by construction: shared boundaries
live on *exterior* rings, so dropping an interior ring cannot move one. Every removal is
counted into ``GeometryLoss`` rather than being quietly swallowed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

import shapely
import topojson
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry

from .loader import Dataset, Territory
from .triangulate import GeometryLoss

LOD_HIGH = "high"
LOD_MEDIUM = "medium"
LOD_LOW = "low"

LOD_LEVELS: tuple[str, ...] = (LOD_HIGH, LOD_MEDIUM, LOD_LOW)

LOD_TOLERANCES: dict[str, float] = {
    LOD_HIGH: 0.00005,
    LOD_MEDIUM: 0.0005,
    LOD_LOW: 0.0025,
}
"""Simplification tolerance in WGS84 degrees, per level.

The values match the ones TerritoryKit uses for the same level names, so the two pipelines can
be compared at equal settings rather than at settings chosen to flatter this one.

At Turkey's latitude 0.00005° is roughly 4 m and 0.0025° roughly 210 m. Measured on the
81-province dataset: high keeps every part and hole (705 parts, 0 holes) while dropping 34% of
vertices; low reaches 13% of high's vertex count, against a contract ceiling of 25%.
"""


class SimplifyError(RuntimeError):
    """Raised when a level cannot be produced, or the result violates a topology invariant."""


@dataclass(frozen=True)
class SimplifyResult:
    """A simplified dataset plus what changed getting there."""

    dataset: Dataset
    lod: str
    tolerance: float
    loss: GeometryLoss
    source_vertex_count: int
    vertex_count: int
    source_part_count: int
    part_count: int
    source_hole_count: int
    hole_count: int

    @property
    def vertex_ratio(self) -> float:
        if self.source_vertex_count == 0:
            return 0.0
        return self.vertex_count / self.source_vertex_count

    def as_manifest_dict(self) -> dict[str, Any]:
        return {
            "lod": self.lod,
            "tolerance": self.tolerance,
            "sourceVertexCount": self.source_vertex_count,
            "vertexCount": self.vertex_count,
            "sourcePartCount": self.source_part_count,
            "partCount": self.part_count,
            "sourceHoleCount": self.source_hole_count,
            "holeCount": self.hole_count,
            **self.loss.as_manifest_dict(),
        }


def simplify_dataset(dataset: Dataset, lod: str) -> SimplifyResult:
    """Simplify every territory to ``lod``, keeping shared boundaries identical.

    ``high`` is not a pass-through: it is a real simplification at a fine tolerance. What makes
    it the level that "preserves the source" is that it is measured to keep every part and hole,
    which the build CLI's lossy gate then enforces.
    """
    if lod not in LOD_TOLERANCES:
        raise SimplifyError(f"unknown lod {lod!r}; available: {', '.join(LOD_LEVELS)}")

    tolerance = LOD_TOLERANCES[lod]
    # Sorted so the arc decomposition sees the same input order every run; topojson's output
    # ordering follows its input, and Phase 3's cache keys off byte-identical builds.
    territories = sorted(dataset.territories, key=lambda t: t.id)
    source_geometries = {territory.id: territory.geometry for territory in territories}

    simplified = _simplify_geometries(source_geometries, tolerance)

    skipped_parts = 0
    skipped_rings = 0
    rebuilt: list[Territory] = []
    for territory in territories:
        geometry = simplified[territory.id]
        geometry, holes_removed = _drop_artifact_holes(geometry, territory.geometry)
        if geometry.is_empty or geometry.area <= 0.0:
            raise SimplifyError(
                f"territory '{territory.id}' ({territory.name}) simplified away entirely at lod "
                f"'{lod}' (tolerance {tolerance}); the level is too coarse for this dataset"
            )
        # Derived from the outcome — parts in minus parts out — for the same reason
        # GeometryLoss counts them that way: a new way of losing a part gets counted without
        # anyone remembering to add a flag for it.
        skipped_parts += max(0, _part_count(territory.geometry) - _part_count(geometry))
        skipped_rings += holes_removed
        rebuilt.append(replace(territory, geometry=geometry))

    loss = GeometryLoss(skipped_parts=skipped_parts, skipped_rings=skipped_rings)

    result_dataset = replace(dataset, territories=tuple(rebuilt))
    if len(result_dataset) != len(dataset):
        raise SimplifyError(
            f"lod '{lod}' changed the territory count from {len(dataset)} to "
            f"{len(result_dataset)}; regions must never appear or disappear"
        )

    return SimplifyResult(
        dataset=result_dataset,
        lod=lod,
        tolerance=tolerance,
        loss=loss,
        source_vertex_count=_vertex_count(source_geometries.values()),
        vertex_count=_vertex_count(t.geometry for t in rebuilt),
        source_part_count=sum(_part_count(g) for g in source_geometries.values()),
        part_count=sum(_part_count(t.geometry) for t in rebuilt),
        source_hole_count=sum(_hole_count(g) for g in source_geometries.values()),
        hole_count=sum(_hole_count(t.geometry) for t in rebuilt),
    )


def _simplify_geometries(
    geometries: dict[str, BaseGeometry], tolerance: float
) -> dict[str, BaseGeometry]:
    """Run the arc decomposition and give each region its geometry back.

    ``prequantize=False`` matters: quantization would snap coordinates to topojson's own grid
    before simplifying, adding a second lossy step on top of the float32 rounding the encoder
    already applies, and moving vertices Phase 1 proved neighbours agree on.
    """
    topology = topojson.Topology(geometries, prequantize=False)
    collection = topology.toposimplify(tolerance).to_geojson()

    simplified: dict[str, BaseGeometry] = {}
    for feature in _features(collection):
        territory_id = feature.get("id")
        if not isinstance(territory_id, str):
            raise SimplifyError(f"simplified feature has no usable id: {territory_id!r}")
        geometry = shape(feature["geometry"])
        if not geometry.is_valid:
            # Simplification can cross a ring over itself; make_valid resolves it, and the
            # interior ring it may leave behind is handled by _drop_artifact_holes.
            geometry = shapely.make_valid(geometry)
        simplified[territory_id] = _surfaces_only(geometry, territory_id)

    missing = set(geometries) - set(simplified)
    if missing:
        raise SimplifyError(f"simplification dropped {len(missing)} territories: {sorted(missing)}")
    return simplified


def _features(collection: Any) -> list[dict[str, Any]]:
    document = json.loads(collection) if isinstance(collection, str) else collection
    features = document.get("features")
    if not isinstance(features, list):
        raise SimplifyError("simplified output is not a GeoJSON FeatureCollection")
    return features


def _surfaces_only(geometry: BaseGeometry, territory_id: str) -> BaseGeometry:
    """Keep the polygonal parts; make_valid can return stray lines that cannot become a mesh."""
    if geometry.geom_type in ("Polygon", "MultiPolygon"):
        return geometry
    if geometry.geom_type == "GeometryCollection":
        surfaces = [
            part for part in geometry.geoms if part.geom_type in ("Polygon", "MultiPolygon")
        ]
        if surfaces:
            return shapely.union_all(surfaces)
    raise SimplifyError(
        f"territory '{territory_id}' simplified into {geometry.geom_type}, which is not a surface"
    )


def _drop_artifact_holes(geometry: BaseGeometry, source: BaseGeometry) -> tuple[BaseGeometry, int]:
    """Remove interior rings the source did not have, and say how many.

    A hole that survives from the source is a real enclave and is kept. A hole with no source
    hole inside it was manufactured by simplification pulling a boundary through itself, and a
    region with a hole in it that the data never had is simply wrong.

    Only interior rings are touched. Shared boundaries are exterior rings, so nothing here can
    move a vertex a neighbour also uses — that is what makes this safe to do after the arcs
    have been simplified.
    """
    source_holes = [Polygon(ring) for part in _parts(source) for ring in part.interiors]

    removed = 0
    kept_parts = []
    for part in _parts(geometry):
        interiors = []
        for ring in part.interiors:
            hole = Polygon(ring)
            if any(hole.intersects(source_hole) for source_hole in source_holes):
                interiors.append(ring)
            else:
                removed += 1
        kept_parts.append(Polygon(part.exterior, interiors))

    if not removed:
        return geometry, 0
    rebuilt = kept_parts[0] if len(kept_parts) == 1 else MultiPolygon(kept_parts)
    return rebuilt, removed


def _parts(geometry: BaseGeometry) -> list[Any]:
    if geometry.geom_type == "MultiPolygon":
        return list(geometry.geoms)
    return [geometry]


def _part_count(geometry: BaseGeometry) -> int:
    return len(_parts(geometry))


def _hole_count(geometry: BaseGeometry) -> int:
    return sum(len(part.interiors) for part in _parts(geometry))


def _vertex_count(geometries: Any) -> int:
    total = 0
    for geometry in geometries:
        for part in _parts(geometry):
            total += len(part.exterior.coords)
            total += sum(len(ring.coords) for ring in part.interiors)
    return total
