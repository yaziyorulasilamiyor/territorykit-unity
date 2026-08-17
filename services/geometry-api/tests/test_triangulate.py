"""Triangulation: area conservation, coverage, holes, parts and winding hygiene.

Area equality alone is a weak check — overlapping triangles plus an equal-sized gap sum to the
right number. The coverage test is the strong one: every sampled interior point must land in
exactly one triangle, which rules out overlaps and gaps at the same time.
"""

from __future__ import annotations

import mapbox_earcut
import numpy as np
import pytest
from conftest import MeshCase
from helpers import count_containing_triangles, random_points_inside, triangle_signed_areas
from shapely.geometry import LineString, Polygon

from geometry_api.loader import Dataset, load_dataset
from geometry_api.projection import Origin, project_geometry
from geometry_api.triangulate import (
    DEGENERATE_AREA_EPSILON_M2,
    GeometryLoss,
    TriangulationError,
    quantize_to_storage_precision,
    signed_area,
    triangulate,
)

AREA_TOLERANCE = 0.001
"""0.1%, from the phase contract."""

COVERAGE_SAMPLE_POINTS = 1000


def _mesh_area(case: MeshCase) -> float:
    return float(np.abs(triangle_signed_areas(case.mesh.vertices, case.mesh.indices)).sum())


def _relative_area_error(case: MeshCase) -> float:
    return abs(_mesh_area(case) - case.quantized.area) / case.quantized.area


def _assert_full_coverage(case: MeshCase, seed: int) -> None:
    points = random_points_inside(case.quantized, COVERAGE_SAMPLE_POINTS, seed=seed)
    counts = count_containing_triangles(case.mesh.vertices, case.mesh.indices, points)
    uncovered = int((counts == 0).sum())
    overlapped = int((counts > 1).sum())
    assert uncovered == 0 and overlapped == 0, (
        f"{case.name}: {uncovered} points fell in no triangle (gap), "
        f"{overlapped} fell in more than one (overlap)"
    )


def test_area_is_conserved_for_every_province(sample_meshes: list[MeshCase]) -> None:
    """The contract is 0.1%; what actually comes out is machine precision.

    The exact worst value is not reproducible across numpy versions and summation orders — it
    moves around the 1e-15 range — so the assertion is a bound, and the bound is what gets
    quoted. A measured digit nobody else can reproduce does not belong in a report.
    """
    errors = {case.name: _relative_area_error(case) for case in sample_meshes}
    worst_name = max(errors, key=lambda name: errors[name])
    assert errors[worst_name] < AREA_TOLERANCE, (
        f"worst area error {errors[worst_name]:.2e} on {worst_name}"
    )
    assert errors[worst_name] < 1e-12, (
        f"expected machine-precision agreement, measured {errors[worst_name]:.2e} on {worst_name}"
    )


def test_area_is_conserved_for_the_fixtures(
    hole_mesh: MeshCase, two_hole_mesh: MeshCase, multipolygon_mesh: MeshCase
) -> None:
    for case in (hole_mesh, two_hole_mesh, multipolygon_mesh):
        assert _relative_area_error(case) < AREA_TOLERANCE, case.name


def test_every_interior_point_lies_in_exactly_one_triangle(
    sample_meshes: list[MeshCase],
    hole_mesh: MeshCase,
    two_hole_mesh: MeshCase,
    multipolygon_mesh: MeshCase,
) -> None:
    """The strong coverage check: no gaps, no overlaps, on fixtures and on all 81 provinces."""
    for seed, case in enumerate((hole_mesh, two_hole_mesh, multipolygon_mesh)):
        _assert_full_coverage(case, seed=seed)
    for seed, case in enumerate(sample_meshes):
        _assert_full_coverage(case, seed=seed)


def test_points_inside_holes_are_covered_by_no_triangle(
    hole_mesh: MeshCase, two_hole_mesh: MeshCase, multipolygon_mesh: MeshCase
) -> None:
    for seed, case in enumerate((hole_mesh, two_hole_mesh, multipolygon_mesh)):
        parts = (
            case.quantized.geoms if case.quantized.geom_type == "MultiPolygon" else [case.quantized]
        )
        holes = [Polygon(interior) for part in parts for interior in part.interiors]
        assert holes, f"{case.name} was expected to have at least one hole"
        for hole_index, hole in enumerate(holes):
            points = random_points_inside(hole, 200, seed=seed * 100 + hole_index)
            counts = count_containing_triangles(case.mesh.vertices, case.mesh.indices, points)
            assert int(counts.sum()) == 0, f"{case.name}: hole {hole_index} was triangulated over"


def test_hole_centre_is_not_covered(hole_mesh: MeshCase) -> None:
    """The narrow version of the hole check, kept because it is the one the brief names."""
    hole_centre = Polygon(hole_mesh.quantized.interiors[0]).centroid
    counts = count_containing_triangles(
        hole_mesh.mesh.vertices, hole_mesh.mesh.indices, np.array([[hole_centre.x, hole_centre.y]])
    )
    assert counts.tolist() == [0]


def test_two_holes_are_both_excluded(two_hole_mesh: MeshCase) -> None:
    """Both holes survive the cumulative ring-offset arithmetic."""
    exterior_area = Polygon(two_hole_mesh.quantized.exterior).area
    hole_areas = [Polygon(ring).area for ring in two_hole_mesh.quantized.interiors]
    assert len(hole_areas) == 2
    expected = exterior_area - sum(hole_areas)
    assert _mesh_area(two_hole_mesh) == pytest.approx(expected, rel=AREA_TOLERANCE)
    assert _mesh_area(two_hole_mesh) < exterior_area - 0.9 * sum(hole_areas)


def test_mesh_vertices_are_exactly_representable_in_float32(
    sample_meshes: list[MeshCase],
) -> None:
    """The invariant that keeps the encoder from ever meeting a collapsed triangle."""
    for case in sample_meshes:
        vertices = case.mesh.vertices
        assert np.array_equal(vertices, vertices.astype(np.float32).astype(np.float64)), case.name


def test_float32_quantization_barely_moves_the_polygon(sample_meshes: list[MeshCase]) -> None:
    """Bounds the cost of snapping before triangulating, so the area test above means something.

    Snapping is what the mesh delivers either way; this measures what it costs. Worst case
    across the 81 provinces is ~1.4e-7 relative area, four orders below the 0.1% contract.
    """
    for case in sample_meshes:
        assert case.quantized.is_valid, f"{case.name} self-intersects once snapped"
        drift = abs(case.quantized.area - case.projected.area) / case.projected.area
        assert drift < 1e-5, f"{case.name}: quantization moved the area by {drift:.2e}"


def test_no_degenerate_triangles(sample_meshes: list[MeshCase]) -> None:
    total_dropped = sum(case.mesh.loss.degenerate_triangles for case in sample_meshes)
    assert total_dropped == 0, (
        f"earcut emitted {total_dropped} zero-area triangles on the real dataset; "
        "they were dropped, but the count is supposed to stay at zero"
    )
    for case in sample_meshes:
        areas = np.abs(triangle_signed_areas(case.mesh.vertices, case.mesh.indices))
        assert areas.min() > DEGENERATE_AREA_EPSILON_M2, case.name


def test_a_hole_that_goes_collinear_is_reported_not_swallowed(fixtures_dir) -> None:
    """The zero-area ring check, on a case the older length check could not have caught.

    The hole keeps three distinct points after duplicate removal — so `len(ring) < 3` passes —
    but its vertices land on one line once snapped to float32. Without the area check it would
    reach earcut, contribute nothing, and be filled in silently.
    """
    dataset = load_dataset(fixtures_dir / "collapsing-hole.geojson")
    origin = Origin(lon=dataset.origin_lon, lat=dataset.origin_lat)
    projected = project_geometry(dataset.territories[0].geometry, origin)

    ring = np.asarray(projected.interiors[0].coords)[:-1, :2]
    snapped = quantize_to_storage_precision(ring)
    deduplicated = snapped[np.any(snapped != np.roll(snapped, -1, axis=0), axis=1)]
    assert len(deduplicated) >= 3, "the old length check would already have rejected this ring"
    assert signed_area(deduplicated) == 0.0, "and the new check is what does reject it"
    assert Polygon(projected.interiors[0]).area > 200, "a real 226 m2 hole, not a rounding artefact"

    mesh = triangulate(projected)
    assert mesh.loss.skipped_rings == 1
    assert mesh.loss.is_lossy


def test_a_part_whose_triangles_all_degenerate_is_reported_not_swallowed(fixtures_dir) -> None:
    """The loss path that used to leak: the part was recorded before its triangles were dropped.

    part_count claimed three parts, every loss counter read zero, and the third part was gone.
    """
    dataset = load_dataset(fixtures_dir / "vanishing-part.geojson")
    origin = Origin(lon=dataset.origin_lon, lat=dataset.origin_lat)
    projected = project_geometry(dataset.territories[0].geometry, origin)
    assert len(projected.geoms) == 3, "three valid parts go in"

    mesh = triangulate(projected)
    assert mesh.part_count == 2, "part_count must describe the mesh, not the input"
    assert mesh.loss.skipped_parts == 1
    assert mesh.loss.degenerate_triangles > 0
    assert mesh.loss.is_lossy


def test_recorded_parts_always_carry_triangles(
    sample_meshes: list[MeshCase], multipolygon_mesh: MeshCase
) -> None:
    """The invariant behind the accounting, checked from outside triangulate as well."""
    for case in [multipolygon_mesh, *sample_meshes]:
        triangles = case.mesh.indices.reshape(-1, 3)
        for start, end in case.mesh.part_ranges:
            inside = ((triangles >= start) & (triangles < end)).all(axis=1).sum()
            assert inside > 0, f"{case.name}: a recorded part carries no triangles"


def test_loss_structure_reports_every_counter_as_a_typed_event(fixtures_dir) -> None:
    """Each counter becomes an event of a kind the schema knows, or the build stops.

    The three counters used to be serialised as three ``skipped*``/``degenerate*`` keys and
    recognised downstream by their names. They are now emitted as kinds, which means a fourth
    counter added here without a matching kind in ``geometry_api.loss`` raises instead of being
    quietly ignored by whoever reads the manifest.
    """
    assert GeometryLoss().is_lossy is False
    assert GeometryLoss().as_events() == (), "no zero-valued events; an empty ledger is empty"
    assert GeometryLoss(degenerate_triangles=1).is_lossy is True

    every = GeometryLoss(skipped_parts=2, skipped_rings=3, degenerate_triangles=4)
    assert {item.kind: item.count for item in every.as_events()} == {
        "skipped_part": 2,
        "skipped_ring": 3,
        "degenerate_triangle": 4,
    }
    assert all(item.stage == "triangulation" for item in every.as_events())
    assert every.ledger.stages_recorded == ("triangulation",), (
        "this structure can only speak for triangulation and has to say so"
    )


def test_no_triangle_spans_two_parts(
    multipolygon_mesh: MeshCase, sample_meshes: list[MeshCase]
) -> None:
    multipart = [multipolygon_mesh] + [c for c in sample_meshes if c.mesh.part_count > 1]
    assert len(multipart) == 21, "3-part fixture plus the 20 provinces with islands"

    for case in multipart:
        triangles = case.mesh.indices.reshape(-1, 3)
        starts = np.array([start for start, _ in case.mesh.part_ranges])
        # Which part each corner belongs to; all three corners must agree.
        part_of_corner = np.searchsorted(starts, triangles, side="right") - 1
        assert np.all(part_of_corner[:, 0] == part_of_corner[:, 1]), case.name
        assert np.all(part_of_corner[:, 0] == part_of_corner[:, 2]), case.name


def test_part_count_is_preserved(
    multipolygon_mesh: MeshCase, sample_meshes: list[MeshCase]
) -> None:
    assert multipolygon_mesh.mesh.part_count == 3
    for case in sample_meshes:
        assert case.mesh.part_count == case.territory.part_count, case.name
        assert case.mesh.loss.skipped_parts == 0
        assert case.mesh.loss.skipped_rings == 0

    mugla = next(case for case in sample_meshes if case.territory.name == "Muğla")
    assert mugla.mesh.part_count == 256
    assert mugla.mesh.vertex_count == 60_478


def test_indices_address_only_their_own_part(multipolygon_mesh: MeshCase) -> None:
    ranges = multipolygon_mesh.mesh.part_ranges
    assert ranges[0][0] == 0
    for (_, end), (next_start, _) in zip(ranges, ranges[1:], strict=False):
        assert end == next_start, "part ranges must tile the vertex buffer without gaps"
    assert ranges[-1][1] == multipolygon_mesh.mesh.vertex_count


def test_ring_winding_is_normalized_before_earcut() -> None:
    """A dataset that ignores RFC 7946 must triangulate to the same surface.

    Ring winding is hygiene, not the mechanism: earcut tells a hole from an exterior by ring
    order. This test pins that reading — if the two results ever diverge, the hole handling
    has started depending on input winding.
    """
    exterior = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    hole = [(0.4, 0.4), (0.4, 0.6), (0.6, 0.6), (0.6, 0.4)]

    rfc_compliant = triangulate(Polygon(exterior, [hole]))
    reversed_rings = triangulate(Polygon(exterior[::-1], [hole[::-1]]))

    expected_area = 1.0 - 0.04
    for mesh in (rfc_compliant, reversed_rings):
        area = float(np.abs(triangle_signed_areas(mesh.vertices, mesh.indices)).sum())
        # 0.4 and 0.6 are not exact in float32, and the rings are snapped before triangulating.
        assert area == pytest.approx(expected_area, rel=1e-6)
        counts = count_containing_triangles(mesh.vertices, mesh.indices, np.array([[0.5, 0.5]]))
        assert counts.tolist() == [0], "the hole must stay a hole regardless of input winding"

    assert rfc_compliant.vertex_count == reversed_rings.vertex_count


def test_coverage_helper_distinguishes_gaps_from_overlaps() -> None:
    """Guards the guard: a coverage check that always returns 1 would prove nothing."""
    square = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    lower_right = np.array([0, 1, 2], dtype=np.uint32)
    inside = np.array([[0.8, 0.2]])
    outside = np.array([[0.2, 0.8]])

    assert count_containing_triangles(square, lower_right, inside).tolist() == [1]
    assert count_containing_triangles(square, lower_right, outside).tolist() == [0]
    doubled = np.concatenate([lower_right, lower_right])
    assert count_containing_triangles(square, doubled, inside).tolist() == [2]


def test_area_alone_would_miss_a_merged_hole_offset_bug() -> None:
    """Why the phase carries a coverage test and not just an area test.

    Feeding earcut ``[4, 12]`` instead of ``[4, 8, 12]`` merges two holes into one ring span.
    The binding accepts it, and the resulting mesh has *exactly* the correct total area — the
    overlaps and the gaps cancel out. Only per-point coverage sees the damage.
    """
    exterior = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]
    first_hole = [[1.0, 1.0], [1.0, 3.0], [3.0, 3.0], [3.0, 1.0]]
    second_hole = [[6.0, 6.0], [6.0, 8.0], [8.0, 8.0], [8.0, 6.0]]
    vertices = np.array(exterior + first_hole + second_hole, dtype=np.float64)
    polygon = Polygon(exterior, [first_hole, second_hole])

    merged_offsets = mapbox_earcut.triangulate_float64(vertices, np.array([4, 12], np.uint32))
    merged_area = float(np.abs(triangle_signed_areas(vertices, merged_offsets)).sum())
    assert merged_area == pytest.approx(polygon.area), "the area test is blind to this bug"

    points = random_points_inside(polygon, 500, seed=11)
    counts = count_containing_triangles(vertices, merged_offsets, points)
    assert int((counts == 0).sum()) > 0 and int((counts > 1).sum()) > 0, (
        "the coverage test is what catches it"
    )

    correct_offsets = mapbox_earcut.triangulate_float64(vertices, np.array([4, 8, 12], np.uint32))
    correct_counts = count_containing_triangles(vertices, correct_offsets, points)
    assert np.all(correct_counts == 1)


def test_signed_area_sign_convention() -> None:
    counter_clockwise = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    assert signed_area(counter_clockwise) == pytest.approx(1.0)
    assert signed_area(counter_clockwise[::-1]) == pytest.approx(-1.0)


def test_duplicate_ring_vertices_are_dropped() -> None:
    repeated = Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
    assert triangulate(repeated).vertex_count == 4


def test_vertices_closer_than_float32_resolution_are_merged() -> None:
    """At Turkey-scale coordinates a float32 step is a few centimetres.

    Two points a nanometre apart are the same point once stored, so they must merge before
    earcut rather than become a zero-area sliver the encoder has to refuse.
    """
    x = 400_000.0
    near_duplicate = Polygon(
        [(x, 0.0), (x + 1e-9, 0.0), (x + 1000.0, 0.0), (x + 1000.0, 1000.0), (x, 1000.0)]
    )
    mesh = triangulate(near_duplicate)
    assert mesh.vertex_count == 4
    assert mesh.loss.degenerate_triangles == 0


def test_rejects_unsupported_geometry() -> None:
    with pytest.raises(TriangulationError, match="unsupported geometry type"):
        triangulate(LineString([(0, 0), (1, 1)]))


def test_rejects_zero_area_polygon() -> None:
    collinear = Polygon([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (0.0, 0.0)])
    with pytest.raises(TriangulationError):
        triangulate(collinear)


def test_projected_fixture_dataset_keeps_its_structure(multipolygon_dataset: Dataset) -> None:
    origin = Origin(lon=multipolygon_dataset.origin_lon, lat=multipolygon_dataset.origin_lat)
    projected = project_geometry(multipolygon_dataset.territories[0].geometry, origin)
    mesh = triangulate(projected)
    assert mesh.part_count == 3
    assert mesh.bounds[0] < 0 < mesh.bounds[2]
