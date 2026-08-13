"""Phase 2: the three detail levels, and the proof that simplifying them left no cracks.

The crack check is the reason this phase exists, so it is measured on the geometry Unity will
actually receive — every territory is simplified, projected, triangulated, encoded to TKMS and
**decoded back**, and the surface is rebuilt from the decoded float32 triangles. Checking the
simplified polygons instead would skip the two steps most likely to introduce a gap.

Two numbers carry the claim:

* **gap** — interior rings in the union of all 81 provinces at once. A crack between two
  neighbours is an enclosed sliver, which shows up as a hole. The union is taken over
  everything rather than pair by pair, because a crack that opens out where three provinces
  meet is not enclosed by either pair alone.
* **overlap** — the area two provinces both claim, summed over intersecting pairs. Measured
  directly and not as (sum of areas − union area): those are two numbers near 7.8e11 m², and
  subtracting them leaves cancellation noise larger than the overlaps worth catching.

Both are asserted to be *exactly* zero, not zero within a tolerance. That is not optimism: phase
1 proved neighbours are handed bit-identical vertices and that quantization is elementwise, and
simplification runs on shared arcs, so the two sides of a boundary are the same numbers. Anything
above zero means that chain broke somewhere, and a tolerance would hide it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest
import shapely
from shapely.geometry.base import BaseGeometry

from geometry_api.encoding import decode_tkms, encode_tkms
from geometry_api.loader import Dataset
from geometry_api.projection import Origin, project_geometry
from geometry_api.simplify import LOD_HIGH, LOD_LEVELS, LOD_LOW, LOD_MEDIUM, simplify_dataset
from geometry_api.triangulate import triangulate

from helpers import count_containing_triangles, random_points_inside  # isort: skip

LOW_VERTEX_BUDGET = 0.25
"""The contract: 'low' may keep at most a quarter of 'high' vertices."""

EXPECTED_TERRITORY_COUNT = 81


@dataclass(frozen=True)
class LevelRun:
    """One detail level carried all the way to decoded meshes."""

    lod: str
    surfaces: dict[str, BaseGeometry]
    """Territory id -> the surface its decoded float32 triangles actually cover."""
    vertex_count: int
    part_count: int
    hole_count: int
    payloads: dict[str, bytes]


def _surface_from_decoded(payload: bytes) -> BaseGeometry:
    """Rebuild the meshed surface from the bytes a client would receive.

    The triangles tile the region without overlapping, so this is a valid coverage and
    ``coverage_union_all`` can merge them far faster than a general union.
    """
    decoded = decode_tkms(payload)
    corners = np.asarray(decoded.vertices, dtype=np.float64)[
        np.asarray(decoded.indices).reshape(-1, 3)
    ]
    triangles = shapely.polygons(np.concatenate([corners, corners[:, :1]], axis=1))
    merged = shapely.coverage_union_all(triangles)
    return merged if merged.is_valid else shapely.make_valid(merged)


def _run_level(dataset: Dataset, lod: str) -> LevelRun:
    simplified = simplify_dataset(dataset, lod)
    origin = Origin(lon=dataset.origin_lon, lat=dataset.origin_lat)

    surfaces: dict[str, BaseGeometry] = {}
    payloads: dict[str, bytes] = {}
    for territory in simplified.dataset:
        mesh = triangulate(project_geometry(territory.geometry, origin))
        payload = encode_tkms(mesh.vertices, mesh.indices)
        payloads[territory.id] = payload
        surfaces[territory.id] = _surface_from_decoded(payload)

    return LevelRun(
        lod=lod,
        surfaces=surfaces,
        vertex_count=simplified.vertex_count,
        part_count=simplified.part_count,
        hole_count=simplified.hole_count,
        payloads=payloads,
    )


@pytest.fixture(scope="session")
def levels(sample_dataset: Dataset) -> dict[str, LevelRun]:
    """All three levels, built once. The whole fixture is the expensive part of this module."""
    return {lod: _run_level(sample_dataset, lod) for lod in LOD_LEVELS}


def _intersecting_pairs(
    surfaces: dict[str, BaseGeometry],
) -> list[tuple[str, str, BaseGeometry]]:
    """Every pair of surfaces whose bounding boxes meet, with their actual intersection."""
    ids = sorted(surfaces)
    geometries = [surfaces[key] for key in ids]
    tree = shapely.STRtree(geometries)

    pairs: list[tuple[str, str, BaseGeometry]] = []
    for i, geometry in enumerate(geometries):
        for candidate in tree.query(geometry):
            j = int(candidate)
            if j <= i:
                continue
            pairs.append((ids[i], ids[j], geometry.intersection(geometries[j])))
    return pairs


def _interior_ring_area(geometry: BaseGeometry) -> float:
    parts = geometry.geoms if geometry.geom_type == "MultiPolygon" else [geometry]
    return sum(shapely.Polygon(ring).area for part in parts for ring in part.interiors)


@pytest.mark.parametrize("lod", LOD_LEVELS)
def test_no_cracks_between_neighbours_after_triangulation_and_float32(
    levels: dict[str, LevelRun], lod: str
) -> None:
    """The phase's central claim, measured on decoded meshes rather than on polygons."""
    run = levels[lod]
    union = shapely.union_all(list(run.surfaces.values()))

    gap = _interior_ring_area(union)
    assert gap == 0.0, (
        f"lod '{lod}': {gap:.3f} m² of enclosed gap between provinces after triangulation and "
        f"float32. Shared boundaries are supposed to be bit-identical, so this must be exactly 0"
    )

    # Measured pair by pair rather than as (sum of areas - union area). Those are two numbers
    # around 7.8e11 m², and subtracting them leaves float64 cancellation noise of a few
    # thousandths of a m² — enough to drown a real overlap of the size worth catching.
    worst: tuple[float, str] = (0.0, "")
    total_overlap = 0.0
    for left_id, right_id, intersection in _intersecting_pairs(run.surfaces):
        area = intersection.area
        total_overlap += area
        if area > worst[0]:
            worst = (area, f"{left_id} / {right_id}")

    assert total_overlap == 0.0, (
        f"lod '{lod}': provinces overlap by {total_overlap:.6f} m² in total after triangulation "
        f"and float32; worst pair {worst[1]} at {worst[0]:.6f} m²"
    )


@pytest.mark.parametrize("lod", LOD_LEVELS)
def test_neighbours_still_share_bit_identical_vertices(
    levels: dict[str, LevelRun], sample_dataset: Dataset, lod: str
) -> None:
    """Phase 1's shared-vertex equality, repeated on each simplified level.

    The crack test above proves the areas line up; this proves *why*, and would catch a change
    that happened to close the gaps by some other route.
    """
    simplified = simplify_dataset(sample_dataset, lod)
    origin = Origin(lon=sample_dataset.origin_lon, lat=sample_dataset.origin_lat)

    quantized: dict[str, set[tuple[float, float]]] = {}
    for territory in simplified.dataset:
        decoded = decode_tkms(
            encode_tkms(*_mesh_arrays(territory.geometry, origin)),
        )
        quantized[territory.id] = {
            (float(x), float(y)) for x, y in np.asarray(decoded.vertices, dtype=np.float64)
        }

    territories = list(simplified.dataset)
    tree = shapely.STRtree([t.geometry for t in territories])
    shared_pairs = 0
    for i, left in enumerate(territories):
        for candidate in tree.query(left.geometry):
            j = int(candidate)
            if j <= i:
                continue
            right = territories[j]
            if left.geometry.intersection(right.geometry).length <= 0.0:
                continue
            shared_pairs += 1
            assert quantized[left.id] & quantized[right.id], (
                f"lod '{lod}': {left.name} and {right.name} share a boundary but no stored "
                f"vertex, so the two sides are different geometry"
            )

    assert shared_pairs > 0, "no neighbouring pairs found; the check proved nothing"


def _mesh_arrays(geometry: BaseGeometry, origin: Origin) -> tuple[np.ndarray, np.ndarray]:
    mesh = triangulate(project_geometry(geometry, origin))
    return mesh.vertices, mesh.indices


@pytest.mark.parametrize("lod", LOD_LEVELS)
def test_every_interior_point_lands_in_exactly_one_triangle(
    levels: dict[str, LevelRun], sample_dataset: Dataset, lod: str
) -> None:
    """Phase 1's coverage sweep, repeated per level.

    Sampling inside the *decoded* surface rather than the source polygon is deliberate: at low
    the two differ by design, and the claim is that the mesh covers what it says it covers.
    """
    run = levels[lod]
    sampled = 0
    for territory_id, surface in sorted(run.surfaces.items()):
        if surface.area <= 0.0:
            continue
        points = random_points_inside(surface, 50, seed=abs(hash(territory_id)) % 10_000)
        decoded = decode_tkms(run.payloads[territory_id])
        counts = count_containing_triangles(decoded.vertices, decoded.indices, points)
        assert set(np.unique(counts)) <= {1}, (
            f"lod '{lod}', territory {territory_id}: "
            f"{int((counts != 1).sum())} of {len(points)} points were not in exactly one triangle"
        )
        sampled += len(points)

    assert sampled >= EXPECTED_TERRITORY_COUNT * 50


def test_low_stays_within_a_quarter_of_high(levels: dict[str, LevelRun]) -> None:
    high = levels[LOD_HIGH].vertex_count
    medium = levels[LOD_MEDIUM].vertex_count
    low = levels[LOD_LOW].vertex_count

    assert medium < high, "medium must be coarser than high"
    assert low < medium, "low must be coarser than medium"
    assert low / high <= LOW_VERTEX_BUDGET, (
        f"low keeps {low / high:.1%} of high's {high} vertices, over the "
        f"{LOW_VERTEX_BUDGET:.0%} ceiling"
    )


def test_topology_is_preserved_exactly_at_high_and_only_loses_detail_below(
    levels: dict[str, LevelRun], sample_dataset: Dataset
) -> None:
    """Region count never moves; parts may only be dropped, never invented; holes never appear.

    Part counts *do* fall at medium and low — small islands stop being representable, which is
    what a lower detail level means. What must not happen is a part or hole appearing out of
    nowhere, which is the signature of a simplification artifact rather than lost detail.
    """
    source = simplify_dataset(sample_dataset, LOD_HIGH)

    assert source.part_count == source.source_part_count, "high must keep every part"
    assert source.hole_count == source.source_hole_count == 0
    assert not source.loss.is_lossy, "high is the level that claims to preserve the source"

    previous_parts = source.source_part_count
    for lod in LOD_LEVELS:
        run = levels[lod]
        assert len(run.surfaces) == EXPECTED_TERRITORY_COUNT, (
            f"lod '{lod}' changed the region count; regions must never appear or disappear"
        )
        assert run.part_count <= previous_parts, (
            f"lod '{lod}' has more parts ({run.part_count}) than the level above "
            f"({previous_parts}); simplification may drop detail, never invent it"
        )
        assert run.hole_count == 0, (
            f"lod '{lod}' invented {run.hole_count} hole(s); the source dataset has none"
        )
        previous_parts = run.part_count


@pytest.mark.parametrize("lod", LOD_LEVELS)
def test_the_same_input_produces_the_same_bytes(sample_dataset: Dataset, lod: str) -> None:
    """Phase 3's content-addressed cache assumes a level rebuilds byte for byte."""
    origin = Origin(lon=sample_dataset.origin_lon, lat=sample_dataset.origin_lat)

    def build() -> dict[str, bytes]:
        simplified = simplify_dataset(sample_dataset, lod)
        return {
            territory.id: encode_tkms(*_mesh_arrays(territory.geometry, origin))
            for territory in simplified.dataset
        }

    first, second = build(), build()
    assert first.keys() == second.keys()
    differing = [key for key in first if first[key] != second[key]]
    assert not differing, (
        f"lod '{lod}': {len(differing)} meshes differed between runs: {differing[:5]}"
    )
