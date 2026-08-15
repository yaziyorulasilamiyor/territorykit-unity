"""Topology-preserving simplification: one dataset in, one detail level out.

**Why this is not TerritoryKit's simplifier.** The phase brief called for
``territory geometry simplify --strategy topology-safe`` and for this module to stay a
placeholder. That was tried first and measured, not assumed. The strategy simplifies every
ring of every zone independently with Douglas-Peucker (see
``vendor/territorykit/packages/generators/src/geometry-simplification.ts``); nothing ties a
boundary shared by two provinces to a single simplified result.

Measured on its ``high`` output, whose 81 geometries are all valid so no repair step can be
blamed for the numbers: 32 of the 197 neighbouring province pairs end up with a gap or an
overlap, 0.0061 km² of gap and 0.0187 km² of overlap, against zero for the source. ``low`` is far
worse (161 pairs, 58.1 km² of gap) but its output also contains 23 invalid geometries, so those
figures carry the repair with them. ``docs/territorykit-simplification-finding.md`` has both, and
``scripts/repro_territorykit_finding.py`` reproduces every number in one command.

Its ``topologyAudit.sharedBoundaryMismatchCount`` is *not* cited as evidence: it is a difference
of shared-segment counts, and simplification legitimately turns many short shared segments into
fewer long ones, so a correct simplifier scores just as high. At ``low`` TerritoryKit scores
48204 on that formula; the raw topojson simplifier output scores 47357 and the repaired,
measured-crack-free pipeline output scores 47358. All three are the same number as far as the
metric is concerned, which is the point — it cannot separate broken output from sound output.

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

**Loss and topology change are two different books.** When two islands of one province drift
into each other and become one part, nothing is lost — but the region's component structure did
change, and the strait between them is now drawn as land, so a click that used to fall on water
selects the province. At ``low`` this happens 30 times against 11 splits, a net 19 parts. Merges
and splits are therefore counted into ``TopologyChange``, which is *not* a loss record and does
not set the ``lossy`` flag. Only parts that actually vanished reach ``GeometryLoss`` and
``dropped_parts``. See ``docs/PROJE-TALIMATI.md`` §FAZ 2, where this is written down as a
deliberate exception to "part counts stay consistent between levels".
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
from .loss import records_show_loss
from .projection import Origin, project_geometry
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


DEFAULT_MAX_DROPPED_PART_AREA = 10_000.0
"""Largest *single* part, in square metres, simplification may drop before the build fails.

The gate is on area because a count says nothing about significance: a hundred slivers are not
one island. This limit answers "did something worth seeing vanish?"; the companion limit below
answers "did a lot of small things add up to something worth seeing?". Neither replaces the
other, and a build has to clear both.

The count is also the more alarming of the two numbers and the less meaningful. At ``low`` the
part count falls by 20, but only **one** part actually disappears (Artvin, 685 m²); the other 19
are the net of 30 merges and 11 splits as the gaps between parts close, which loses no area.
``_analyse_parts`` therefore identifies vanished parts by geometry rather than by counting, and
books the merges and splits as a topology change instead.
"""

DEFAULT_MAX_TOTAL_DROPPED_AREA = 50_000.0
"""Total dropped area, in square metres, across every part of every region.

Without this the per-part gate is trivially evaded: five thousand parts of 9.999 m² each pass
one at a time and take 50 km² with them. The default is five times the single-part limit, so a
handful of legitimately tiny islets fits under it while a systematic cull does not. Measured on
TUR ADM1 the worst level (``low``) drops 685 m² in total.
"""

PART_SURVIVAL_AREA_RATIO = 0.1
"""How much of a source part must still be covered before it counts as having survived.

Survival is measured as overlapping **area**, not with ``intersects()``. Touching counts as
intersecting, and a counterexample built for this review made a 1.24 km² part touch the
simplified output at a single point: the intersection had zero area, the part was recorded as
alive, and the 10.000 m² gate never saw the largest loss in the build.

The threshold is a ratio rather than "any area at all" because simplification legitimately moves
boundaries — at ``low``, parts that merge into a neighbour can end up with 39% of the area they
started with. Below 10% is not a moved boundary; it is a part that is gone.

The same ratio decides whether a source part and an output part *correspond* for topology
accounting, there against the smaller of the two areas so that a small fragment splitting off a
large part is still recognised as coming from it.
"""


@dataclass(frozen=True)
class DroppedPart:
    """A polygon part simplification removed, with enough detail to judge whether that mattered."""

    territory_id: str
    territory_name: str
    area: float
    """Square metres in the dataset's local projection, not square degrees."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "territoryId": self.territory_id,
            "territoryName": self.territory_name,
            "area": self.area,
        }


@dataclass(frozen=True)
class TopologyChange:
    """How one region's component structure changed. Not a loss — see the module docstring.

    ``merges`` and ``splits`` are *contributions*, not event counts: three source parts becoming
    one output part contributes 2 merges, and one source part becoming three output parts
    contributes 2 splits. Counted that way they sum to the change in part count, which event
    counts would not.
    """

    territory_id: str
    territory_name: str
    merges: int
    splits: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "territoryId": self.territory_id,
            "territoryName": self.territory_name,
            "merges": self.merges,
            "splits": self.splits,
        }


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
    dropped_parts: tuple[DroppedPart, ...] = ()
    topology_changes: tuple[TopologyChange, ...] = ()

    @property
    def vertex_ratio(self) -> float:
        if self.source_vertex_count == 0:
            return 0.0
        return self.vertex_count / self.source_vertex_count

    @property
    def dropped_area(self) -> float:
        return sum(part.area for part in self.dropped_parts)

    @property
    def largest_dropped_part(self) -> DroppedPart | None:
        return max(self.dropped_parts, key=lambda part: part.area, default=None)

    @property
    def merges(self) -> int:
        return sum(change.merges for change in self.topology_changes)

    @property
    def splits(self) -> int:
        return sum(change.splits for change in self.topology_changes)

    @property
    def is_lossy(self) -> bool:
        """Derived from this stage's records by ``loss.records_show_loss``, never from a flag.

        Wider than ``self.loss.is_lossy``, which only sees the three counters: a build with a
        dropped part but a part count that happens to balance out is lossy, and reading the
        counter alone said it was not.
        """
        return records_show_loss(self.as_manifest_dict())

    def _changes_by_size(self) -> list[TopologyChange]:
        """Busiest region first, then by id so the manifest stays byte-reproducible."""
        return sorted(self.topology_changes, key=lambda c: (-(c.merges + c.splits), c.territory_id))

    def as_manifest_dict(self) -> dict[str, Any]:
        largest = self.largest_dropped_part
        return {
            "lod": self.lod,
            "tolerance": self.tolerance,
            "sourceVertexCount": self.source_vertex_count,
            "vertexCount": self.vertex_count,
            "sourcePartCount": self.source_part_count,
            "partCount": self.part_count,
            "sourceHoleCount": self.source_hole_count,
            "holeCount": self.hole_count,
            "droppedPartArea": self.dropped_area,
            "largestDroppedPartArea": largest.area if largest else 0.0,
            # Sorted largest first: if this list is ever truncated by a reader, the parts worth
            # noticing are the ones that survive.
            "droppedParts": [
                part.as_dict() for part in sorted(self.dropped_parts, key=lambda p: -p.area)
            ],
            # Deliberately *not* a "dropped"/"skipped" key: merging two islands into one part
            # loses no ground, so this must not set the lossy flag. It is reported because the
            # part count moving is still a change a client can be surprised by.
            "topologyChanges": {
                "merges": self.merges,
                "splits": self.splits,
                "netPartChange": self.merges - self.splits,
                "regions": [change.as_dict() for change in self._changes_by_size()],
            },
            **self.loss.as_manifest_dict(),
        }


def simplify_dataset(
    dataset: Dataset,
    lod: str,
    max_dropped_part_area: float = DEFAULT_MAX_DROPPED_PART_AREA,
    max_total_dropped_area: float = DEFAULT_MAX_TOTAL_DROPPED_AREA,
) -> SimplifyResult:
    """Simplify every territory to ``lod``, keeping shared boundaries identical.

    ``high`` is not a pass-through: it is a real simplification at a fine tolerance. What makes
    it the level that "preserves the source" is that it is measured to keep every part and hole,
    which the build CLI's lossy gate then enforces.

    ``max_dropped_part_area`` and ``max_total_dropped_area`` are the two area gates described on
    ``DEFAULT_MAX_DROPPED_PART_AREA`` and ``DEFAULT_MAX_TOTAL_DROPPED_AREA``; pass
    ``float("inf")`` to either to record that kind of loss without failing.
    """
    if lod not in LOD_TOLERANCES:
        raise SimplifyError(f"unknown lod {lod!r}; available: {', '.join(LOD_LEVELS)}")

    tolerance = LOD_TOLERANCES[lod]
    # Sorted so the arc decomposition sees the same input order every run; topojson's output
    # ordering follows its input, and Phase 3's cache keys off byte-identical builds.
    territories = sorted(dataset.territories, key=lambda t: t.id)
    source_geometries = {territory.id: territory.geometry for territory in territories}

    simplified = _simplify_geometries(source_geometries, tolerance)
    origin = Origin(lon=dataset.origin_lon, lat=dataset.origin_lat)

    skipped_rings = 0
    dropped: list[DroppedPart] = []
    changes: list[TopologyChange] = []
    rebuilt: list[Territory] = []
    for territory in territories:
        geometry = simplified[territory.id]
        geometry, holes_removed = _drop_artifact_holes(geometry, territory.geometry)
        if geometry.is_empty or geometry.area <= 0.0:
            raise SimplifyError(
                f"territory '{territory.id}' ({territory.name}) simplified away entirely at lod "
                f"'{lod}' (tolerance {tolerance}); the level is too coarse for this dataset"
            )
        skipped_rings += holes_removed
        territory_dropped, change = _analyse_parts(territory, geometry, origin)
        dropped.extend(territory_dropped)
        if change is not None:
            changes.append(change)
        rebuilt.append(replace(territory, geometry=geometry))

    # Derived from the outcome, and from the *right* outcome. This used to be "parts in minus
    # parts out", which called 19 merges at 'low' a loss of 19 parts when nothing had been lost
    # at all — the count moved for a reason that is now booked under topologyChanges instead.
    # What lands here is the number of source parts with no surviving counterpart, which is the
    # same list ``dropped`` and its areas come from, so the two can never disagree.
    loss = GeometryLoss(skipped_parts=len(dropped), skipped_rings=skipped_rings)

    result_dataset = replace(dataset, territories=tuple(rebuilt))
    if len(result_dataset) != len(dataset):
        raise SimplifyError(
            f"lod '{lod}' changed the territory count from {len(dataset)} to "
            f"{len(result_dataset)}; regions must never appear or disappear"
        )

    _check_dropped_area(dropped, lod, tolerance, max_dropped_part_area, max_total_dropped_area)

    return SimplifyResult(
        dropped_parts=tuple(dropped),
        topology_changes=tuple(changes),
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


def _analyse_parts(
    territory: Territory, simplified: BaseGeometry, origin: Origin
) -> tuple[list[DroppedPart], TopologyChange | None]:
    """Match source parts to output parts by overlapping area, and read off both books.

    Two things come out of the same correspondence, which is why they are computed together and
    cannot drift apart: the parts with no surviving counterpart (loss), and the parts that merged
    or split (topology change, not loss).

    Two earlier shortcuts are gone, both of which were shown to hide real losses:

    * There was an early return when the source and the output had the *same number* of parts.
      Source ``A + B`` simplifying to ``A + C`` has two parts on each side while ``B`` is gone
      entirely, and the detector returned an empty list.
    * Survival was tested with ``intersects()``. A part that meets the output at one point or
      along one edge shares no area with it, and a 1.24 km² part rigged to touch at a single
      vertex was counted as alive.

    Areas are reported in local metres rather than square degrees, because square degrees are
    not a unit anyone can judge an island by. The *matching* is done in source coordinates,
    where a ratio of areas means the same thing either way.
    """
    source_parts = _parts(territory.geometry)
    output_parts = _parts(simplified)
    overlaps = _overlap_areas(source_parts, output_parts)

    dropped = []
    for i, part in enumerate(source_parts):
        covered = sum(overlaps.get((i, j), 0.0) for j in range(len(output_parts)))
        if part.area > 0.0 and covered >= PART_SURVIVAL_AREA_RATIO * part.area:
            continue
        dropped.append(
            DroppedPart(
                territory_id=territory.id,
                territory_name=territory.name,
                area=project_geometry(part, origin).area,
            )
        )

    change = _topology_change(territory, source_parts, output_parts, overlaps)
    return dropped, change


def _overlap_areas(
    source_parts: list[Any], output_parts: list[Any]
) -> dict[tuple[int, int], float]:
    """Intersection area for every source/output pair whose bounding boxes meet.

    Pairs that are absent are pairs with no overlap; the tree keeps this linear-ish instead of
    running an intersection for all S x O combinations, which matters for provinces carrying a
    hundred islands.
    """
    if not output_parts:
        return {}
    tree = shapely.STRtree(output_parts)
    areas: dict[tuple[int, int], float] = {}
    for i, source in enumerate(source_parts):
        for candidate in tree.query(source):
            j = int(candidate)
            area = source.intersection(output_parts[j]).area
            if area > 0.0:
                areas[(i, j)] = area
    return areas


def _topology_change(
    territory: Territory,
    source_parts: list[Any],
    output_parts: list[Any],
    overlaps: dict[tuple[int, int], float],
) -> TopologyChange | None:
    """Count merge and split contributions from the source/output correspondence.

    A source part and an output part correspond when they overlap by at least
    ``PART_SURVIVAL_AREA_RATIO`` of the *smaller* of the two. Against the smaller area rather
    than the source area so that both directions work: a sliver splitting off a large part
    overlaps only a few percent of that part, but nearly all of itself.
    """
    sources_per_output: dict[int, int] = {}
    outputs_per_source: dict[int, int] = {}
    for (i, j), area in overlaps.items():
        smaller = min(source_parts[i].area, output_parts[j].area)
        if smaller <= 0.0 or area < PART_SURVIVAL_AREA_RATIO * smaller:
            continue
        sources_per_output[j] = sources_per_output.get(j, 0) + 1
        outputs_per_source[i] = outputs_per_source.get(i, 0) + 1

    merges = sum(count - 1 for count in sources_per_output.values() if count > 1)
    splits = sum(count - 1 for count in outputs_per_source.values() if count > 1)
    if not merges and not splits:
        return None
    return TopologyChange(
        territory_id=territory.id,
        territory_name=territory.name,
        merges=merges,
        splits=splits,
    )


def _check_dropped_area(
    dropped: list[DroppedPart],
    lod: str,
    tolerance: float,
    limit: float,
    total_limit: float,
) -> None:
    """Two gates, because one part of 50 km² and 5000 parts of 10 m² are both worth stopping."""
    by_size = sorted(dropped, key=lambda p: -p.area)
    detail = "\n  ".join(
        f"{part.territory_name} ({part.territory_id}): {part.area:.1f} m²" for part in by_size[:5]
    )

    oversized = [part for part in by_size if part.area > limit]
    if oversized:
        raise SimplifyError(
            f"lod '{lod}' (tolerance {tolerance}) dropped {len(oversized)} part(s) larger than "
            f"the {limit:.0f} m² limit, which is too big to lose without saying so. Raise "
            f"--max-lost-area to accept it:\n  {detail}"
        )

    total = sum(part.area for part in dropped)
    if total > total_limit:
        raise SimplifyError(
            f"lod '{lod}' (tolerance {tolerance}) dropped {len(dropped)} part(s) totalling "
            f"{total:.1f} m², over the {total_limit:.0f} m² cumulative limit. No single part was "
            f"large enough to fail on its own, which is exactly why this limit exists. Raise "
            f"--max-total-lost-area to accept it:\n  {detail}"
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
