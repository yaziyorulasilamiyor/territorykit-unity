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

**Known limits of these checks.** They pass on this dataset and the result is real, but they are
not a proof of crack-freeness in general:

1. *The gap metric only sees enclosed gaps.* It counts interior rings of the union, so a crack
   that reaches the outer boundary of the country is not enclosed by anything and leaves no
   hole to find. Cracks along the coast or the land border can therefore go unseen. Interior
   province-to-province cracks — which is where independent simplification actually breaks
   things, and where TerritoryKit's output failed — do get caught.
2. *The shared-vertex check tests existence, not completeness.* It asserts each neighbouring
   pair shares **at least one** stored vertex. Two provinces agreeing on one end of a long
   border and disagreeing along all of it would still pass. The gap and overlap measurements are
   what actually constrain the whole boundary; this one explains the mechanism behind them.
3. *Overlap is only measured between pairs whose bounding boxes meet*, which is exhaustive for
   real overlaps but means the count is of candidate pairs, not of all pairs.

Closing 1 and 2 needs a per-boundary vertex-sequence comparison rather than a set intersection.
That is deliberately not attempted here; the limits are recorded so the guarantee is not read as
stronger than it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
import shapely
from shapely.geometry.base import BaseGeometry

from geometry_api.build import collect_loss
from geometry_api.encoding import decode_tkms, encode_tkms
from geometry_api.loader import Dataset, Territory
from geometry_api.projection import Origin, project_geometry
from geometry_api.simplify import (
    LOD_HIGH,
    LOD_LEVELS,
    LOD_LOW,
    LOD_MEDIUM,
    DroppedPart,
    SimplifyError,
    SimplifyResult,
    TopologyChange,
    _analyse_parts,
    _check_dropped_area,
    simplify_dataset,
)
from geometry_api.triangulate import GeometryLoss, triangulate

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

    An invalid result fails the test instead of being repaired. It used to call ``make_valid``
    here, which changed nothing today — all three levels produce valid surfaces — but a decoded
    mesh whose triangles do not tile the region is precisely the defect the gap and overlap
    measurements below exist to catch, and repairing it first would let them measure the repair.
    """
    decoded = decode_tkms(payload)
    corners = np.asarray(decoded.vertices, dtype=np.float64)[
        np.asarray(decoded.indices).reshape(-1, 3)
    ]
    triangles = shapely.polygons(np.concatenate([corners, corners[:, :1]], axis=1))
    merged = shapely.coverage_union_all(triangles)
    assert merged.is_valid, (
        f"the decoded mesh does not form a valid surface "
        f"({shapely.is_valid_reason(merged)}); repairing it here would hide exactly the kind of "
        f"broken triangulation the crack measurements are looking for"
    )
    return merged


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
    """The phase's central claim, measured on decoded meshes rather than on polygons.

    Only *enclosed* gaps are visible here — see limit 1 in the module docstring.
    """
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

    Limit, stated because the name overpromises: this asserts each pair shares **at least one**
    stored vertex, not that they agree along the whole boundary. See limit 2 in the module
    docstring — the area measurements are what constrain the rest of the border.
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


def test_dropped_parts_are_recorded_with_their_area(sample_dataset: Dataset) -> None:
    """A count cannot tell 20 slivers from one real island, so the areas are kept.

    Also pins the distinction the part *count* hides: at low the count falls by 23, but only one
    part actually disappears — the other 22 are the net effect of parts merging into their
    neighbours as the gap between them closes, which loses no area at all. ``skippedParts`` is
    the number of parts that vanished, not the change in the count; the merges are booked under
    ``topologyChanges`` and are proved to add up in
    ``test_topology_accounting_and_loss_add_up_to_the_part_count_change``.

    (23 and not the 20 the full chain reports: this runs on the raw GeoJSON, which still has the
    seven islets that build_lod.py's normalization removes before the importer sees them.)
    """
    result = simplify_dataset(sample_dataset, LOD_LOW)

    assert result.source_part_count - result.part_count == 23, "part count falls by 23"
    assert result.loss.skipped_parts == 1, "but only one part actually vanishes"
    assert len(result.dropped_parts) == 1

    vanished = result.largest_dropped_part
    assert vanished is not None
    assert vanished.territory_name == "Artvin"
    assert vanished.area == pytest.approx(684.6, abs=0.5)
    assert result.dropped_area == pytest.approx(vanished.area)


def test_dropping_a_part_bigger_than_the_limit_fails_the_build(sample_dataset: Dataset) -> None:
    """The gate is on area, so an island that matters cannot leave through the count."""
    simplify_dataset(sample_dataset, LOD_LOW, max_dropped_part_area=10_000.0)

    with pytest.raises(SimplifyError, match="too big to lose"):
        simplify_dataset(sample_dataset, LOD_LOW, max_dropped_part_area=100.0)


# --------------------------------------------------------------------------------------------
# The detector itself. ``_analyse_parts`` is exercised directly because these are cases the real
# dataset does not produce — the point of a counterexample is that it is constructed, and
# steering topojson into emitting one is neither possible nor the thing under test.
# --------------------------------------------------------------------------------------------

_ORIGIN = Origin(lon=35.0, lat=39.0)


def _box(min_lon: float, min_lat: float, size: float) -> shapely.Polygon:
    return shapely.box(min_lon, min_lat, min_lon + size, min_lat + size)


def _territory(geometry: BaseGeometry) -> Territory:
    return Territory(id="tr:test", name="Test", geometry=geometry)


def test_a_vanished_part_is_found_even_when_the_part_count_is_unchanged() -> None:
    """Counterexample 1: source A+B, output A+C. Two parts on each side, B gone.

    The detector used to return early whenever the counts matched, on the assumption that an
    equal number of parts meant the same parts. It does not: simplification can drop one part
    and split another in the same region, and the arithmetic hides both.
    """
    a = _box(30.0, 39.0, 0.10)
    b = _box(31.0, 39.0, 0.05)
    c = _box(32.0, 39.0, 0.05)

    source = _territory(shapely.MultiPolygon([a, b]))
    output = shapely.MultiPolygon([a, c])

    dropped, _ = _analyse_parts(source, output, _ORIGIN)

    assert len(dropped) == 1, "B has no counterpart in the output and must be reported"
    assert dropped[0].area == pytest.approx(project_geometry(b, _ORIGIN).area)


def test_a_part_touching_the_output_at_a_single_point_has_not_survived() -> None:
    """Counterexample 2: a part that only *touches* the result shares no area with it.

    ``intersects()`` is true for a single shared vertex, so a part of well over a square
    kilometre counted as alive and the 10.000 m² gate never fired.
    """
    big = _box(30.0, 39.0, 0.012)
    # Shares exactly one corner with `big`, nothing else.
    neighbour = _box(30.012, 39.012, 0.05)

    source = _territory(big)
    lost_area = project_geometry(big, _ORIGIN).area
    assert lost_area > 1_000_000, "the fixture has to be far above the 10.000 m² gate to matter"
    assert big.intersects(neighbour), "the whole point is that intersects() says yes"
    assert big.intersection(neighbour).area == 0.0

    dropped, _ = _analyse_parts(source, neighbour, _ORIGIN)

    assert len(dropped) == 1
    assert dropped[0].area == pytest.approx(lost_area)

    with pytest.raises(SimplifyError, match="too big to lose"):
        _check_dropped_area(dropped, LOD_LOW, 0.0025, 10_000.0, float("inf"))


def test_a_part_that_only_moved_its_boundary_still_counts_as_alive() -> None:
    """The other side of the threshold: simplification is allowed to move a boundary.

    Without this the two tests above could be satisfied by calling every changed part dropped,
    which would make the numbers useless in the opposite direction.
    """
    source = _territory(_box(30.0, 39.0, 0.10))
    shrunk = shapely.box(30.0, 39.0, 30.06, 39.06)  # 36% of the source area

    dropped, change = _analyse_parts(source, shrunk, _ORIGIN)

    assert dropped == []
    assert change is None


def test_merges_and_splits_are_counted_as_topology_change_not_as_loss() -> None:
    """Two islands becoming one part lose no ground, so they are booked separately."""
    left = _box(30.0, 39.0, 0.05)
    right = _box(30.06, 39.0, 0.05)
    merged = shapely.box(30.0, 39.0, 30.11, 39.05)

    two_islands = _territory(shapely.MultiPolygon([left, right]))
    dropped, change = _analyse_parts(two_islands, merged, _ORIGIN)

    assert dropped == [], "nothing was lost; the two parts are both inside the merged one"
    assert change is not None
    assert (change.merges, change.splits) == (1, 0)

    # And the reverse direction, including the three-way case the report's arithmetic depends on.
    thirds = shapely.MultiPolygon(
        [
            shapely.box(30.0, 39.0, 30.03, 39.05),
            shapely.box(30.04, 39.0, 30.07, 39.05),
            shapely.box(30.08, 39.0, 30.11, 39.05),
        ]
    )
    _, split = _analyse_parts(_territory(merged), thirds, _ORIGIN)
    assert split is not None
    assert (split.merges, split.splits) == (0, 2), "one part becoming three contributes 2, not 1"


def test_the_cumulative_gate_catches_what_the_per_part_gate_waves_through() -> None:
    """Six parts of 9.999 m² clear the single-part limit and take 60.000 m² with them."""
    slivers = [
        DroppedPart(territory_id=f"tr:{index}", territory_name=f"P{index}", area=9_999.0)
        for index in range(6)
    ]

    _check_dropped_area(slivers, LOD_LOW, 0.0025, 10_000.0, float("inf"))

    with pytest.raises(SimplifyError, match="cumulative limit"):
        _check_dropped_area(slivers, LOD_LOW, 0.0025, 10_000.0, 50_000.0)


def test_the_cumulative_gate_is_wired_into_the_real_build(sample_dataset: Dataset) -> None:
    result = simplify_dataset(sample_dataset, LOD_LOW)
    assert 0.0 < result.dropped_area < 50_000.0, "the default has to have headroom on real data"

    with pytest.raises(SimplifyError, match="cumulative limit"):
        simplify_dataset(
            sample_dataset,
            LOD_LOW,
            max_dropped_part_area=float("inf"),
            max_total_dropped_area=100.0,
        )


def test_topology_accounting_and_loss_add_up_to_the_part_count_change(
    sample_dataset: Dataset,
) -> None:
    """The identity that makes the two books trustworthy, checked on the real dataset.

    Every part the source had is either still there, merged with another, split into several, or
    gone. So (parts in − parts out) has to equal (merges − splits + parts dropped) exactly. A
    number invented on either side breaks this.
    """
    for lod in LOD_LEVELS:
        result = simplify_dataset(sample_dataset, lod)
        expected = result.source_part_count - result.part_count
        accounted = result.merges - result.splits + len(result.dropped_parts)
        assert accounted == expected, (
            f"lod '{lod}': part count moved by {expected} but the books account for "
            f"{accounted} ({result.merges} merges, {result.splits} splits, "
            f"{len(result.dropped_parts)} dropped)"
        )

    low = simplify_dataset(sample_dataset, LOD_LOW)
    assert low.merges > 0 and low.splits > 0, "low is the level with topology churn to record"
    manifest = low.as_manifest_dict()
    assert manifest["topologyChanges"]["netPartChange"] == low.merges - low.splits
    assert manifest["topologyChanges"]["regions"], "the report has to name which regions changed"


def test_a_merge_alone_does_not_make_a_level_lossy() -> None:
    """Merges are a topology change, not a loss, so they must not reach the lossy flag."""
    merged_only = _result_with(
        dropped=(),
        changes=(TopologyChange(territory_id="tr:1", territory_name="One", merges=19, splits=0),),
    )

    assert merged_only.is_lossy is False
    assert collect_loss([], merged_only, upstream=None)["lossy"] is False
    assert merged_only.as_manifest_dict()["topologyChanges"]["merges"] == 19


def _result_with(
    dropped: tuple, changes: tuple, loss: GeometryLoss | None = None
) -> SimplifyResult:
    """A SimplifyResult carrying exactly the records a test wants to reason about."""
    return SimplifyResult(
        dataset=Dataset(
            id="d",
            name="D",
            territories=(_territory(_box(30.0, 39.0, 0.1)),),
            source_format="geojson",
        ),
        lod=LOD_LOW,
        tolerance=0.0025,
        loss=loss or GeometryLoss(),
        source_vertex_count=10,
        vertex_count=10,
        source_part_count=2,
        part_count=2,
        source_hole_count=0,
        hole_count=0,
        dropped_parts=dropped,
        topology_changes=changes,
    )


def test_a_dropped_part_makes_the_build_lossy_even_when_every_counter_reads_zero() -> None:
    """Counterexample 3: dropped_parts is populated, GeometryLoss is all zeroes.

    This is the phase 1 bug's third appearance. ``collect_loss`` asked
    ``simplification.loss.is_lossy`` — three integers — and answered "nothing was lost" while a
    20.000 m² part sat in the records right next to it.
    """
    result = _result_with(
        dropped=(DroppedPart(territory_id="tr:1", territory_name="One", area=20_000.0),),
        changes=(),
        loss=GeometryLoss(),
    )

    assert result.loss.is_lossy is False, "the counters really are all zero; that is the trap"
    assert result.is_lossy is True
    assert collect_loss([], result, upstream=None)["lossy"] is True


def test_upstream_counts_outrank_the_upstream_boolean_in_both_directions() -> None:
    """Counterexample 4: an upstream block whose flag contradicts its own numbers.

    ``{"droppedParts": 7, "lossy": false}`` used to be accepted verbatim, so seven islets
    removed before this build ever started were reported as no loss at all. The boolean is now
    never read; what the producer counted is what counts.
    """
    lying_low = {"droppedParts": 7, "droppedPartDetails": ["a"] * 7, "lossy": False}
    assert collect_loss([], None, upstream=lying_low)["lossy"] is True

    lying_high = {"droppedParts": 0, "droppedPartDetails": [], "droppedHoles": 0, "lossy": True}
    assert collect_loss([], None, upstream=lying_high)["lossy"] is False, (
        "a boolean with no records behind it is not evidence either; "
        "scripts/check_lod_report.py is what fails the producer for lying"
    )

    honest = {"droppedParts": 7, "droppedPartDetails": ["a"] * 7, "lossy": True}
    assert collect_loss([], None, upstream=honest)["lossy"] is True


def test_the_lossy_flag_covers_every_stage_not_just_triangulation(
    sample_dataset: Dataset, tmp_path: Path
) -> None:
    """Regression: 'low' reported lossy=false while simplification had dropped 20 parts.

    Same shape as the phase 1 bug — a loss path with no counter — so the flag is checked against
    each source that can lose geometry, including one upstream of this process entirely.
    """
    entries: list = []
    simplification = simplify_dataset(sample_dataset, LOD_LOW)
    assert simplification.loss.is_lossy, "low drops parts; the fixture for this test is wrong"

    loss = collect_loss(entries, simplification, upstream=None)
    assert loss["lossy"] is True, "simplification loss alone must set the flag"

    clean = simplify_dataset(sample_dataset, LOD_HIGH)
    assert not clean.loss.is_lossy
    assert collect_loss(entries, clean, upstream=None)["lossy"] is False

    upstream = {"droppedParts": 7, "droppedHoles": 0, "lossy": True}
    assert collect_loss(entries, clean, upstream=upstream)["lossy"] is True, (
        "geometry dropped before this build still has to reach the manifest"
    )


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
