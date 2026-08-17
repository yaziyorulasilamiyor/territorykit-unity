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

import numpy as np
import pytest
import shapely
from shapely.geometry.base import BaseGeometry

from geometry_api.build import build_manifest, collect_loss
from geometry_api.encoding import decode_tkms, encode_tkms
from geometry_api.loader import Dataset, Territory
from geometry_api.loss import (
    STAGE_SIMPLIFICATION,
    STAGE_UPSTREAM,
    LossLedger,
    LossSchemaError,
    event,
)
from geometry_api.projection import Origin, project_geometry
from geometry_api.simplify import (
    LOD_HIGH,
    LOD_LEVELS,
    LOD_LOW,
    LOD_MEDIUM,
    PART_CORRESPONDENCE_AREA_RATIO,
    PART_SEVERE_SHRINK_RATIO,
    DroppedPart,
    SimplifyError,
    SimplifyResult,
    TerritoryAccount,
    TopologyChange,
    _check_dropped_area,
    account_for,
    check_budget,
    simplify_dataset,
)
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
    assert not source.is_lossy, "high is the level that claims to preserve the source"

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
    neighbours as the gap between them closes, which loses no area at all. ``dropped_part`` is
    the number of parts that vanished, not the change in the count; the merges are booked as
    ``part_merge`` changes and are proved to add up in
    ``test_topology_accounting_and_loss_add_up_to_the_part_count_change``.

    (23 and not the 20 the full chain reports: this runs on the raw GeoJSON, which still has the
    seven islets that build_lod.py's normalization removes before the importer sees them.)
    """
    result = simplify_dataset(sample_dataset, LOD_LOW)

    assert result.source_part_count - result.part_count == 23, "part count falls by 23"
    assert result.ledger.count("dropped_part") == 1, "but only one part actually vanishes"
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
# The accounting itself. ``account_for`` is exercised directly because these are cases the real
# dataset does not produce — the point of a counterexample is that it is constructed, and
# steering topojson into emitting one is neither possible nor the thing under test.
#
# Every probe below asserts the two counting identities as well as the case it was built for.
# Round three added the identity but only checked it on the real fixture, where it happened to
# hold; round four measured it on these probes and found it false on two of them, because there
# was no accounting for output parts nothing in the source corresponds to. So the identities are
# asserted here too, on the cases that break them.
# --------------------------------------------------------------------------------------------

_ORIGIN = Origin(lon=35.0, lat=39.0)


def _box(min_lon: float, min_lat: float, size: float) -> shapely.Polygon:
    return shapely.box(min_lon, min_lat, min_lon + size, min_lat + size)


def _territory(geometry: BaseGeometry) -> Territory:
    return Territory(id="tr:test", name="Test", geometry=geometry)


def _assert_books_balance(account: TerritoryAccount) -> None:
    """The identities, on one territory, in the form the build enforces them.

    ``check_budget`` is the production code; calling it here means a probe that violates an
    identity fails with the message the build would print, not with a hand-rolled assertion that
    could drift from it.
    """
    check_budget([account], LOD_LOW)

    parts = account.source_part_count - account.part_count
    accounted = len(account.dropped_parts) + account.merges - account.splits - account.created
    assert parts == accounted, (
        f"part count moved by {parts} but the records account for {accounted}: "
        f"{len(account.dropped_parts)} dropped, {account.merges} merges, {account.splits} "
        f"splits, {account.created} created"
    )

    holes = account.source_hole_count - account.hole_count
    assert holes == account.dropped_hole_count + account.hole_merges - account.hole_splits


def test_a_vanished_part_is_found_even_when_the_part_count_is_unchanged() -> None:
    """Counterexample 1: source A+B, output A+C. Two parts on each side, B gone.

    The accounting used to return early whenever the counts matched, on the assumption that an
    equal number of parts meant the same parts. It does not.

    ``C`` in this fixture is not a piece of ``A`` that split off — it is unrelated geometry
    somewhere else entirely, which is what makes the counts match while the contents do not. An
    earlier version of this test only checked that ``B`` was reported and said nothing about
    ``C``, so the build had no reason to notice that an output part had appeared from nowhere,
    and the topology identity was quietly false here: parts in − parts out was 0 while
    merges − splits + dropped was 1. ``C`` is now asserted as a created part, which is the term
    that closes it.
    """
    a = _box(30.0, 39.0, 0.10)
    b = _box(31.0, 39.0, 0.05)
    c = _box(32.0, 39.0, 0.05)

    source = _territory(shapely.MultiPolygon([a, b]))
    output = shapely.MultiPolygon([a, c])

    account = account_for(source, output, _ORIGIN)

    dropped = account.dropped_parts
    assert len(dropped) == 1, "B has no counterpart in the output and must be reported"
    assert dropped[0].area == pytest.approx(project_geometry(b, _ORIGIN).area)

    assert account.created == 1, "C corresponds to nothing in the source and must be recorded"
    assert account.merges == account.splits == 0
    assert account.ledger.count("part_created") == 1
    assert account.ledger.area("part_created") == pytest.approx(project_geometry(c, _ORIGIN).area)
    assert account.ledger.is_lossy is True, "B is gone; a created part does not make up for it"
    _assert_books_balance(account)


def test_a_part_touching_the_output_at_a_single_point_has_not_survived() -> None:
    """Counterexample 2: a part that only *touches* the result shares no area with it.

    ``intersects()`` is true for a single shared vertex, so a part of well over a square
    kilometre counted as alive and the 10.000 m² gate never fired.

    The identity was false here too, for the same reason as above: the neighbour the source part
    touches is an output part with no source behind it.
    """
    big = _box(30.0, 39.0, 0.012)
    # Shares exactly one corner with `big`, nothing else.
    neighbour = _box(30.012, 39.012, 0.05)

    source = _territory(big)
    lost_area = project_geometry(big, _ORIGIN).area
    assert lost_area > 1_000_000, "the fixture has to be far above the 10.000 m² gate to matter"
    assert big.intersects(neighbour), "the whole point is that intersects() says yes"
    assert big.intersection(neighbour).area == 0.0

    account = account_for(source, neighbour, _ORIGIN)

    assert len(account.dropped_parts) == 1
    assert account.dropped_parts[0].area == pytest.approx(lost_area)
    assert account.created == 1, "the neighbour is an output part with no source part behind it"
    _assert_books_balance(account)

    with pytest.raises(SimplifyError, match="too big to lose"):
        _check_dropped_area(list(account.dropped_parts), LOD_LOW, 0.0025, 10_000.0, float("inf"))


def test_a_part_that_only_moved_its_boundary_still_counts_as_alive() -> None:
    """The other side of the threshold: simplification is allowed to move a boundary.

    Without this the two tests above could be satisfied by calling every changed part dropped,
    which would make the numbers useless in the opposite direction. What the part gave up is
    still recorded — as a boundary retreat, on the removed side of the area budget — because
    "the part survived" and "nothing happened to it" are different statements.
    """
    source = _territory(_box(30.0, 39.0, 0.10))
    shrunk = shapely.box(30.0, 39.0, 30.06, 39.06)  # 36% of the source area

    account = account_for(source, shrunk, _ORIGIN)

    assert account.dropped_parts == ()
    assert account.topology_change is None
    assert account.ledger.is_lossy is False
    assert account.min_retained_area_ratio == pytest.approx(0.36, abs=0.01)
    assert account.ledger.area("boundary_retreat") == pytest.approx(account.measured_removed_area)
    _assert_books_balance(account)


def test_a_part_just_over_the_survival_ratio_is_alive_and_still_recorded() -> None:
    """The 10% cliff, from both sides. Nine per cent is dropped, eleven per cent is not.

    An earlier review measured the cliff and found the problem was not where the threshold sat
    but what happened either side of it: a part retaining 11% of its area was "alive, with no
    loss record", so an 89% loss passed both area gates in silence. The threshold still decides
    only whether a part is *functionally gone*. What it no longer decides is whether anything is
    written down — the 89% is on the removed side of the budget either way, the retained ratio is
    in the manifest, and a part this far down is named in a ``severe_shrink`` record.
    """
    source_box = _box(30.0, 39.0, 0.10)
    source = _territory(source_box)
    source_area = project_geometry(source_box, _ORIGIN).area

    # sqrt(0.11) of the side length keeps 11% of the area.
    survivor = shapely.box(30.0, 39.0, 30.0 + 0.10 * 0.11**0.5, 39.0 + 0.10 * 0.11**0.5)
    alive = account_for(source, survivor, _ORIGIN)

    assert alive.min_retained_area_ratio == pytest.approx(0.11, abs=0.005)
    assert alive.min_retained_area_ratio > PART_CORRESPONDENCE_AREA_RATIO
    assert alive.dropped_parts == (), "eleven per cent is above the functionally-gone threshold"
    assert alive.ledger.is_lossy is False
    assert alive.severe_shrink_count == 1, (
        "a part down to 11% has to be named, or an 89% loss is invisible"
    )
    assert alive.ledger.count("severe_shrink") == 1
    assert alive.min_retained_area_ratio < PART_SEVERE_SHRINK_RATIO
    assert alive.ledger.area("boundary_retreat") == pytest.approx(0.89 * source_area, rel=0.05), (
        "the 89% it stopped covering is on the removed side of the budget, not nowhere"
    )
    _assert_books_balance(alive)

    # And nine per cent, just below, is a dropped part.
    remnant = shapely.box(30.0, 39.0, 30.0 + 0.10 * 0.09**0.5, 39.0 + 0.10 * 0.09**0.5)
    gone = account_for(source, remnant, _ORIGIN)

    assert len(gone.dropped_parts) == 1
    assert gone.ledger.is_lossy is True
    _assert_books_balance(gone)


def test_merges_and_splits_are_counted_as_topology_change_not_as_loss() -> None:
    """Two islands becoming one part lose no ground, so they are booked separately."""
    left = _box(30.0, 39.0, 0.05)
    right = _box(30.06, 39.0, 0.05)
    merged = shapely.box(30.0, 39.0, 30.11, 39.05)

    two_islands = _territory(shapely.MultiPolygon([left, right]))
    account = account_for(two_islands, merged, _ORIGIN)

    assert account.dropped_parts == (), "nothing was lost; both parts are inside the merged one"
    assert account.topology_change is not None
    assert (account.merges, account.splits, account.created) == (1, 0, 0)
    assert account.ledger.is_lossy is False, "a merge is a change, not a loss"
    _assert_books_balance(account)

    # The strait between the two islands is now land. That is the false bridge a click can land
    # on, so its size is measured rather than left as "the part count moved".
    strait = project_geometry(shapely.box(30.05, 39.0, 30.06, 39.05), _ORIGIN).area
    assert account.merge_added_area == pytest.approx(strait, rel=0.01)
    assert account.ledger.area("part_merge") == pytest.approx(account.merge_added_area)

    # And the reverse direction, including the three-way case the report's arithmetic depends on.
    thirds = shapely.MultiPolygon(
        [
            shapely.box(30.0, 39.0, 30.03, 39.05),
            shapely.box(30.04, 39.0, 30.07, 39.05),
            shapely.box(30.08, 39.0, 30.11, 39.05),
        ]
    )
    split = account_for(_territory(merged), thirds, _ORIGIN)
    assert split.topology_change is not None
    assert (split.merges, split.splits, split.created) == (0, 2, 0), (
        "one part becoming three contributes 2, not 1"
    )
    _assert_books_balance(split)


# --------------------------------------------------------------------------------------------
# B1 — the source hole that disappears. The counterexample the fourth review round found, and
# the two independent things that now catch it.
# --------------------------------------------------------------------------------------------


def _polygon_with_hole() -> tuple[shapely.Polygon, shapely.Polygon]:
    """A one-degree square with a real enclave in the middle, and the enclave on its own."""
    hole = shapely.box(30.4, 39.4, 30.6, 39.6)
    outer = shapely.Polygon(
        shapely.box(30.0, 39.0, 31.0, 40.0).exterior.coords, [hole.exterior.coords]
    )
    return outer, hole


def test_a_source_hole_that_disappears_entirely_is_a_loss() -> None:
    """B1. Source: one exterior ring plus one real enclave. Output: the same ring, no enclave.

    The function that removed simplification artifacts only ever walked the interior rings still
    present in the **output**. When a source enclave vanished completely there was no output ring
    left to inspect, so the loop had nothing to iterate over, ``skippedRings`` stayed 0,
    ``holeCount`` fell to 0 with no record beside it, and the level reported ``lossy: false``
    with CI returning an empty failure list. The correspondence is now read in both directions.
    """
    outer, hole = _polygon_with_hole()
    source = _territory(outer)
    output = shapely.box(30.0, 39.0, 31.0, 40.0)  # same exterior, enclave filled in

    account = account_for(source, output, _ORIGIN)

    assert account.source_hole_count == 1
    assert account.hole_count == 0
    assert account.dropped_hole_count == 1
    assert account.ledger.count("dropped_hole") == 1
    assert account.ledger.is_lossy is True, "a lost enclave is a loss, whatever the part count did"

    # Losing an enclave makes the region *bigger*: the ground it did not cover, it now does. The
    # schema puts this kind on the added side for exactly that reason, and the budget balances
    # only because it does.
    assert account.ledger.area("dropped_hole") == pytest.approx(
        project_geometry(hole, _ORIGIN).area, rel=0.01
    )
    assert account.measured_added_area == pytest.approx(account.ledger.added_area, abs=1.0)
    _assert_books_balance(account)


def test_a_real_enclave_that_survives_is_not_a_loss() -> None:
    """The other direction, or the test above could be satisfied by calling every hole lost."""
    outer, hole = _polygon_with_hole()
    source = _territory(outer)

    account = account_for(source, outer, _ORIGIN)

    assert (account.source_hole_count, account.hole_count) == (1, 1)
    assert account.dropped_hole_count == 0
    assert account.ledger.is_lossy is False
    _assert_books_balance(account)


def test_an_invented_hole_is_removed_and_recorded_as_a_change_not_a_loss() -> None:
    """A hole the source never had is an artifact; putting the ground back is a repair."""
    source = _territory(shapely.box(30.0, 39.0, 31.0, 40.0))
    invented = shapely.Polygon(
        shapely.box(30.0, 39.0, 31.0, 40.0).exterior.coords,
        [shapely.box(30.4, 39.4, 30.6, 39.6).exterior.coords],
    )

    account = account_for(source, invented, _ORIGIN)

    assert account.artifact_holes_removed == 1
    assert account.hole_count == 0, "the invented ring is gone from the geometry, not just noted"
    assert account.ledger.count("artifact_hole_removed") == 1
    assert account.ledger.is_lossy is False
    _assert_books_balance(account)


def test_the_hole_identity_fails_when_a_vanished_enclave_is_not_recorded() -> None:
    """B1 again, this time proving the budget catches it without knowing what a hole is.

    The point of the identity is that it does not need a list of loss paths. Here the geometry
    says a hole is gone — one in, none out — and the event list says nothing happened. No naming
    convention is consulted and no hole-specific check runs; the counts simply do not add up, so
    the build stops. Any future way of losing a component fails the same way.
    """
    _, hole = _polygon_with_hole()
    fill = project_geometry(hole, _ORIGIN).area
    silent = TerritoryAccount(
        territory_id="tr:test",
        territory_name="Test",
        # The area is accounted for — as ordinary boundary movement, which is exactly how a
        # filled enclave would look to code that never compared the hole counts.
        events=(event(STAGE_SIMPLIFICATION, "boundary_advance", count=1, area=fill),),
        source_area=1_000_000.0,
        output_area=1_000_000.0 + fill,
        measured_removed_area=0.0,
        measured_added_area=fill,
        source_part_count=1,
        part_count=1,
        source_hole_count=1,
        hole_count=0,
        dropped_parts=(),
        topology_change=None,
        dropped_hole_count=0,
        hole_merges=0,
        hole_splits=0,
        artifact_holes_removed=0,
        min_retained_area_ratio=1.0,
        severe_shrink_count=0,
        merge_added_area=0.0,
    )

    with pytest.raises(SimplifyError, match="hole count moved by 1"):
        check_budget([silent], LOD_LOW)


def test_the_area_identity_fails_when_geometry_changes_with_nothing_recorded() -> None:
    """The other half of the budget: ground that moved and no event saying so.

    This is the shape of every bug the four review rounds found — a stage that changed the
    geometry after the accounting had run, or a path nobody had thought to count. It does not
    matter which: the measured difference and the recorded events disagree, and the build stops.
    """
    silent = TerritoryAccount(
        territory_id="tr:test",
        territory_name="Test",
        events=(),
        source_area=1_000_000.0,
        output_area=900_000.0,
        measured_removed_area=100_000.0,
        measured_added_area=0.0,
        source_part_count=1,
        part_count=1,
        source_hole_count=0,
        hole_count=0,
        dropped_parts=(),
        topology_change=None,
        dropped_hole_count=0,
        hole_merges=0,
        hole_splits=0,
        artifact_holes_removed=0,
        min_retained_area_ratio=0.9,
        severe_shrink_count=0,
        merge_added_area=0.0,
    )

    with pytest.raises(SimplifyError, match="area budget does not balance"):
        check_budget([silent], LOD_LOW)


def test_the_area_identity_fails_when_an_event_is_on_the_wrong_side() -> None:
    """A record that exists but points the wrong way is not better than no record.

    100.000 m² left the region and the event says it arrived, so the budget is out by twice the
    figure. Catching this is why every kind declares its side in the schema rather than leaving
    the sign to whoever writes the arithmetic.
    """
    wrong_way = TerritoryAccount(
        territory_id="tr:test",
        territory_name="Test",
        events=(event(STAGE_SIMPLIFICATION, "boundary_advance", count=1, area=100_000.0),),
        source_area=1_000_000.0,
        output_area=900_000.0,
        measured_removed_area=100_000.0,
        measured_added_area=0.0,
        source_part_count=1,
        part_count=1,
        source_hole_count=0,
        hole_count=0,
        dropped_parts=(),
        topology_change=None,
        dropped_hole_count=0,
        hole_merges=0,
        hole_splits=0,
        artifact_holes_removed=0,
        min_retained_area_ratio=0.9,
        severe_shrink_count=0,
        merge_added_area=0.0,
    )

    with pytest.raises(SimplifyError, match="area budget does not balance"):
        check_budget([wrong_way], LOD_LOW)


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

    Every part the source had is either still there, merged with another, split into several,
    gone, or was never in the source at all. So (parts in − parts out) has to equal
    (dropped + merges − splits − created) exactly. A number invented on any of the four terms
    breaks it.

    The ``created`` term is the one the previous round was missing. This test passed without it
    because the real dataset happens to produce no unmatched output parts; the synthetic probes
    above are where it was false, which is why they now assert the same identity.
    """
    for lod in LOD_LEVELS:
        result = simplify_dataset(sample_dataset, lod)
        expected = result.source_part_count - result.part_count
        accounted = len(result.dropped_parts) + result.merges - result.splits - result.created_parts
        assert accounted == expected, (
            f"lod '{lod}': part count moved by {expected} but the books account for "
            f"{accounted} ({result.merges} merges, {result.splits} splits, "
            f"{result.created_parts} created, {len(result.dropped_parts)} dropped)"
        )
        assert result.source_hole_count - result.hole_count == result.dropped_hole_count

    low = simplify_dataset(sample_dataset, LOD_LOW)
    assert low.merges > 0 and low.splits > 0, "low is the level with topology churn to record"
    manifest = low.as_manifest_dict()
    changes = manifest["topologyChanges"]
    assert changes["netPartChange"] == low.merges - low.splits - low.created_parts
    assert changes["regions"], "the report has to name which regions changed"
    assert changes["mergeAddedArea"] > 0.0, (
        "30 merges close 30 straits; the land that appears there is what a click can hit"
    )


def test_the_area_budget_reaches_the_manifest_with_the_numbers_behind_it(
    sample_dataset: Dataset,
) -> None:
    """The distinction an area gate alone cannot carry, in the manifest whatever happened.

    An earlier review found that an 89% area loss could pass both area gates while every record
    read zero, because the gates only ever looked at parts that vanished outright. "Nothing was
    lost" and "the shape is the same" are different claims; these are the numbers that separate
    them, and they are written down whether or not anything was lost.
    """
    result = simplify_dataset(sample_dataset, LOD_LOW)
    budget = result.as_manifest_dict()["areaBudget"]

    assert budget["sourceArea"] > 0.0
    assert budget["removedArea"] > 0.0, "at low, boundaries move; the ground they gave up is real"
    assert budget["addedArea"] > 0.0
    assert 0.0 < budget["retainedAreaRatio"] < 1.0
    assert budget["retainedAreaRatio"] == pytest.approx(
        (budget["sourceArea"] - budget["removedArea"]) / budget["sourceArea"]
    )
    # The worst-off surviving part, which is the number the 10% threshold cannot tell you.
    assert budget["minPartRetainedAreaRatio"] == pytest.approx(0.156, abs=0.01)
    assert budget["severeShrinkParts"] > 0
    attributed = sum(
        result.ledger.area(kind) for kind in ("boundary_retreat", "part_split", "dropped_part")
    )
    assert attributed == pytest.approx(budget["removedArea"], rel=1e-6), (
        "every square metre the output stopped covering has to sit under a named record"
    )


def test_a_merge_alone_does_not_make_a_level_lossy() -> None:
    """Merges are a topology change, not a loss, so they must not reach the lossy flag."""
    merged_only = _result_with(
        ledger=LossLedger.of([event(STAGE_SIMPLIFICATION, "part_merge", count=19, area=1_234.0)]),
        changes=(TopologyChange(territory_id="tr:1", territory_name="One", merges=19, splits=0),),
    )

    assert merged_only.is_lossy is False
    assert collect_loss([], merged_only, upstream=None).is_lossy is False
    assert merged_only.as_manifest_dict()["topologyChanges"]["merges"] == 19
    assert merged_only.topology_changed is True, (
        "not lossy and not unchanged either; a client still has to be told"
    )


def _result_with(
    ledger: LossLedger | None = None,
    changes: tuple = (),
    dropped: tuple = (),
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
        ledger=ledger or LossLedger.of([]),
        source_vertex_count=10,
        vertex_count=10,
        source_part_count=2,
        part_count=2,
        source_hole_count=0,
        hole_count=0,
        dropped_parts=dropped,
        topology_changes=changes,
    )


def test_a_dropped_part_makes_the_build_lossy_even_when_no_counter_moved() -> None:
    """Counterexample 3, in the shape the typed ledger allows it to take.

    The original bug was ``collect_loss`` asking a three-integer counter struct instead of the
    records. There is no counter struct any more: a dropped part *is* a ``dropped_part`` event,
    and there is nowhere else for the flag to be read from.
    """
    result = _result_with(
        ledger=LossLedger.of(
            [
                event(
                    STAGE_SIMPLIFICATION,
                    "dropped_part",
                    count=1,
                    area=20_000.0,
                    details=["One (tr:1): 20000.0 m² lost"],
                )
            ]
        ),
        dropped=(DroppedPart(territory_id="tr:1", territory_name="One", area=20_000.0),),
    )

    assert result.is_lossy is True
    assert collect_loss([], result, upstream=None).is_lossy is True


def test_a_change_only_ledger_is_not_lossy_but_is_not_silent_either() -> None:
    """The distinction the ``lossy`` flag alone cannot carry, on the aggregate ledger."""
    changes_only = collect_loss(
        [],
        _result_with(
            ledger=LossLedger.of(
                [event(STAGE_SIMPLIFICATION, "boundary_retreat", count=81, area=4_859_841.0)]
            )
        ),
        upstream=None,
    )

    assert changes_only.is_lossy is False
    assert changes_only.removed_area == pytest.approx(4_859_841.0)
    assert changes_only.events, "the level moved 4.86 km² of boundary; that is not nothing"


def test_an_upstream_boolean_is_never_read_and_a_bad_upstream_file_fails() -> None:
    """Counterexample 4: an upstream block whose flag contradicts its own records.

    The old ``{"droppedParts": 7, "lossy": false}`` shape used to be accepted verbatim, so seven
    islets removed before this build ever started were reported as no loss at all. That whole
    shape is now unreadable — it is schema version 1 — and the reader raises rather than finding
    no matching keys in it and moving on, which is the failure mode that let it through.
    """
    lying = {
        "loss": {
            "schemaVersion": 2,
            "stagesRecorded": [STAGE_UPSTREAM],
            "lossy": False,
            "events": [
                {
                    "stage": STAGE_UPSTREAM,
                    "kind": "dropped_islet",
                    "count": 7,
                    "details": [f"islet {index}" for index in range(7)],
                }
            ],
        }
    }
    assert collect_loss([], None, upstream=lying).is_lossy is True

    overstated = {
        "loss": {
            "schemaVersion": 2,
            "stagesRecorded": [STAGE_UPSTREAM],
            "lossy": True,
            "events": [],
        }
    }
    assert collect_loss([], None, upstream=overstated).is_lossy is False, (
        "a boolean with no records behind it is not evidence either; "
        "scripts/check_lod_report.py is what fails the producer for lying"
    )

    old_shape = {"droppedParts": 7, "droppedPartDetails": ["a"] * 7, "lossy": False}
    with pytest.raises(LossSchemaError):
        collect_loss([], None, upstream=old_shape)


def test_the_lossy_flag_covers_every_stage_not_just_triangulation(
    sample_dataset: Dataset,
) -> None:
    """Regression: 'low' reported lossy=false while simplification had dropped 20 parts.

    Same shape as the phase 1 bug — a loss path with no counter — so the flag is checked against
    each stage that can lose geometry, including one upstream of this process entirely. The
    ledger also has to say *which* stages were asked: a level with no upstream records is not a
    level that lost nothing upstream.
    """
    simplification = simplify_dataset(sample_dataset, LOD_LOW)
    assert simplification.is_lossy, "low drops a part; the fixture for this test is wrong"
    assert collect_loss([], simplification, upstream=None).is_lossy is True

    clean = simplify_dataset(sample_dataset, LOD_HIGH)
    assert not clean.is_lossy
    ledger = collect_loss([], clean, upstream=None)
    assert ledger.is_lossy is False
    assert STAGE_UPSTREAM not in ledger.stages_recorded, (
        "no upstream file was given, and the ledger has to admit that rather than imply zero"
    )

    upstream = {
        "loss": {
            "schemaVersion": 2,
            "stagesRecorded": [STAGE_UPSTREAM],
            "events": [
                {
                    "stage": STAGE_UPSTREAM,
                    "kind": "dropped_islet",
                    "count": 7,
                    "details": [f"islet {index}" for index in range(7)],
                }
            ],
        }
    }
    with_upstream = collect_loss([], clean, upstream=upstream)
    assert with_upstream.is_lossy is True, (
        "geometry dropped before this build still has to reach the manifest"
    )
    assert set(with_upstream.stages_recorded) == {
        STAGE_UPSTREAM,
        STAGE_SIMPLIFICATION,
        "triangulation",
    }


def test_the_manifest_states_the_two_flags_a_renderer_needs(sample_dataset: Dataset) -> None:
    """Nothing on the Unity side had a field to read for "is this level safe to click".

    ``topologyChanges`` is a block of counts, and asking a client to decide whether 30 merges is
    a problem is asking it to re-derive a policy. Phases 4 and 5 read these two instead.
    """
    from geometry_api.build import build_meshes

    high = simplify_dataset(sample_dataset, LOD_HIGH)
    high_manifest = build_manifest(
        sample_dataset, build_meshes(high.dataset, lod=LOD_HIGH), LOD_HIGH, high
    )
    assert high_manifest["topologyChanged"] is False
    assert high_manifest["pickingUnsafe"] is False, (
        "high keeps every part and hole; it is the level picking can trust"
    )

    low = simplify_dataset(sample_dataset, LOD_LOW)
    low_manifest = build_manifest(
        sample_dataset, build_meshes(low.dataset, lod=LOD_LOW), LOD_LOW, low
    )
    assert low_manifest["topologyChanged"] is True
    assert low_manifest["pickingUnsafe"] is True, (
        "30 merges draw 30 straits as land, and a click there selects the wrong province"
    )


def test_a_triangulation_loss_at_high_makes_the_level_unsafe_to_click(
    sample_dataset: Dataset,
) -> None:
    """B1 of the fifth review round, on the flag a renderer actually reads.

    The counterexample it was found with: simplification at ``high`` is clean, and a part is lost
    one stage later, in triangulation. The manifest said ``lossy: true`` and
    ``pickingUnsafe: false`` in the same breath, because the flag was derived from
    ``SimplifyResult`` and triangulation is not in it. Phase 4/5 were told to gate picking on the
    second of those two.
    """
    from dataclasses import replace

    from geometry_api.build import build_meshes
    from geometry_api.triangulate import GeometryLoss

    high = simplify_dataset(sample_dataset, LOD_HIGH)
    entries = build_meshes(high.dataset, lod=LOD_HIGH)
    assert build_manifest(sample_dataset, entries, LOD_HIGH, high)["pickingUnsafe"] is False

    # The synthetic loss: one part reached triangulation and produced no triangles. Injected
    # rather than provoked so the test states the case it is about instead of depending on a
    # province that happens to be small enough this year.
    damaged = [replace(entries[0], loss=GeometryLoss(skipped_parts=1)), *entries[1:]]
    manifest = build_manifest(sample_dataset, damaged, LOD_HIGH, high)

    assert manifest["lossy"] is True
    assert manifest["pickingUnsafe"] is True, (
        "the part is missing from the mesh a click lands on, whichever stage lost it"
    )
    assert manifest["topologyChanged"] is True
    assert manifest["simplification"]["topologyChanged"] is False, (
        "the simplification block still reports its own stage; the top-level flags are the ones "
        "that span the chain"
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
