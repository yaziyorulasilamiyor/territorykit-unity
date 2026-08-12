"""Phase 2's precondition, checked in Phase 1 where it is still cheap to fix.

Phase 2 removes cracks between neighbouring regions by simplifying each shared boundary once
and rebuilding both polygons from it. That only works if a shared boundary is actually shared —
if the same source vertex comes out of the pipeline as two different numbers for two provinces,
the arc graph splits and Phase 2 is broken before it starts.

Two things have to hold, and they are different claims:

1. the dataset really does give neighbours bit-identical vertices, and
2. our projection and float32 quantization are path-independent — the same input coordinate
   yields the same output regardless of which province's array it travelled in.

The second is the one this phase introduced a risk to, by adding quantization.
"""

from __future__ import annotations

import numpy as np
import pytest
import shapely
from shapely import STRtree

from geometry_api.loader import Dataset, Territory
from geometry_api.projection import Origin, project_coords, project_geometry
from geometry_api.triangulate import quantize_to_storage_precision

CoordinateMap = dict[tuple[float, float], tuple[float, float]]

EXPECTED_ADJACENT_PAIRS = 200
"""Province pairs whose geometries intersect at all."""

EXPECTED_BOUNDARY_PAIRS = 197
"""Of those, the ones sharing an actual boundary *line*. The other 3 meet at a single point.

Phase 2's arc graph is built from shared boundaries, so a corner touch is not one of its inputs
— the distinction is recorded here so the two numbers are never conflated in a report again.
"""

EXPECTED_SHARED_VERTICES = 58_179


def _coordinate_map(territory: Territory, origin: Origin) -> CoordinateMap:
    """Source lon/lat -> the quantized local metres that province's own mesh would store.

    shapely.transform preserves coordinate order, so zipping the two arrays pairs each source
    vertex with the value the pipeline produced for it.
    """
    source = shapely.get_coordinates(territory.geometry)
    quantized = shapely.get_coordinates(
        shapely.transform(
            project_geometry(territory.geometry, origin), quantize_to_storage_precision
        )
    )
    assert len(source) == len(quantized)
    return {
        (float(a[0]), float(a[1])): (float(b[0]), float(b[1]))
        for a, b in zip(source, quantized, strict=True)
    }


@pytest.fixture(scope="session")
def adjacency(sample_dataset: Dataset) -> tuple[list[Territory], list[tuple[int, int]]]:
    territories = list(sample_dataset)
    geometries = [territory.geometry for territory in territories]
    tree = STRtree(geometries)

    pairs: set[tuple[int, int]] = set()
    for i, geometry in enumerate(geometries):
        for candidate in tree.query(geometry):
            j = int(candidate)
            if j > i and geometry.intersects(geometries[j]):
                pairs.add((i, j))
    return territories, sorted(pairs)


@pytest.fixture(scope="session")
def coordinate_maps(sample_dataset: Dataset) -> list[CoordinateMap]:
    origin = Origin(lon=sample_dataset.origin_lon, lat=sample_dataset.origin_lat)
    return [_coordinate_map(territory, origin) for territory in sample_dataset]


def test_neighbouring_provinces_share_exact_source_vertices(
    adjacency: tuple[list[Territory], list[tuple[int, int]]],
) -> None:
    """Claim 1: the dataset gives neighbours bit-identical vertices, not merely close ones."""
    territories, pairs = adjacency
    assert len(pairs) == EXPECTED_ADJACENT_PAIRS

    with_shared_line = [
        (i, j)
        for i, j in pairs
        if territories[i].geometry.intersection(territories[j].geometry).length > 0.0
    ]
    assert len(with_shared_line) == EXPECTED_BOUNDARY_PAIRS, (
        "intersecting is not the same as sharing a boundary; the remainder touch at one point"
    )

    vertex_sets = [
        {tuple(coordinate) for coordinate in shapely.get_coordinates(territory.geometry)}
        for territory in territories
    ]
    without_shared = [
        (territories[i].name, territories[j].name)
        for i, j in pairs
        if not vertex_sets[i] & vertex_sets[j]
    ]
    assert not without_shared, (
        f"{len(without_shared)} adjacent pairs share no exact vertex, so Phase 2 cannot build an "
        f"arc graph from equality alone: {without_shared[:5]}"
    )


def test_shared_boundary_vertices_quantize_to_identical_values(
    adjacency: tuple[list[Territory], list[tuple[int, int]]],
    coordinate_maps: list[CoordinateMap],
) -> None:
    """Claim 2: quantization is path-independent, so a shared boundary stays shared.

    If this fails, Phase 2's crack test is unwinnable: the two sides of a boundary would be
    different geometry before simplification even runs.
    """
    territories, pairs = adjacency
    checked = 0
    mismatches: list[str] = []

    for i, j in pairs:
        for point in coordinate_maps[i].keys() & coordinate_maps[j].keys():
            checked += 1
            left, right = coordinate_maps[i][point], coordinate_maps[j][point]
            if left != right:
                mismatches.append(
                    f"{territories[i].name}/{territories[j].name} at {point}: {left} != {right}"
                )

    assert checked == EXPECTED_SHARED_VERTICES
    assert not mismatches, f"{len(mismatches)} shared vertices diverged: {mismatches[:5]}"


def test_quantization_does_not_depend_on_array_context() -> None:
    """The mechanism behind claim 2, isolated from the dataset.

    Elementwise float32 rounding cannot depend on neighbouring values; this pins that, so a
    future switch to a batched or approximate implementation cannot break Phase 2 silently.
    """
    rng = np.random.default_rng(7)
    point = np.array([[-431_099.123_456_789, 157_282.987_654_321]])
    crowd = np.concatenate([rng.uniform(-9e5, 9e5, size=(5000, 2)), point])

    alone = quantize_to_storage_precision(point)
    in_context = quantize_to_storage_precision(crowd)[-1:]
    assert np.array_equal(alone, in_context)
    assert alone.tobytes() == in_context.tobytes()


def test_projection_does_not_depend_on_array_context(sample_dataset: Dataset) -> None:
    """The same for the projection step, which runs per territory over differently sized arrays."""
    origin = Origin(lon=sample_dataset.origin_lon, lat=sample_dataset.origin_lat)
    point = np.array([[28.9784, 41.0082]])
    rng = np.random.default_rng(11)
    crowd = np.concatenate([rng.uniform(26.0, 44.0, size=(5000, 2)) * [1.0, 0.9], point])

    alone = project_coords(point, origin)
    in_context = project_coords(crowd, origin)[-1:]
    assert alone.tobytes() == in_context.tobytes()
