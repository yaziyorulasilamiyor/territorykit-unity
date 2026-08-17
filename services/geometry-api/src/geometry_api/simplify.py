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

**What this does instead.** ``topojson`` decomposes the dataset into arcs, where a boundary
two regions share is *one* arc, simplifies each arc once, and rebuilds both regions from the
shared result. Neighbours therefore cannot disagree about a boundary: they are reading the same
vertices. This is a library, not a hand-rolled algorithm — the distinction the brief cared
about was not writing a second simplification policy, and this does not.

**The area budget is what makes the accounting closed.** Four review rounds each found a new
way for geometry to go missing while every counter read zero, because every fix so far was of
the form "count this newly discovered loss path too". A list of known loss paths cannot be
complete by construction, so this level of the module does not keep one. Instead, for every
territory it measures what actually changed — source area, output area, and the area of the two
set differences — and requires the recorded events to add up to it::

    source area  +  everything recorded as added  −  everything recorded as removed
    ==  output area

    source parts  −  output parts  ==  dropped + merges − splits − created
    source holes  −  output holes  ==  dropped holes + hole merges − hole splits

Every event that carries area declares, in ``loss.EVENT_KINDS``, which side of the first
identity it belongs on. A stage that changes geometry and writes no event breaks one of the
three, and ``_check_budget`` raises ``SimplifyError``, which fails the build. The counting
identities are the sharper half: the hole one catches a source enclave that vanished entirely,
which is a case no output ring exists to be inspected for and which every previous version of
this module reported as ``lossy: false``.

**Artifact holes.** Aggressive tolerance can pull two shores of a narrow inlet through each
other, and repairing that self-intersection leaves an interior ring the source never had — a
literal hole in the region. These are matched against the source rings and the unmatched ones
removed, which is safe by construction: shared boundaries live on *exterior* rings, so dropping
an interior ring cannot move one. Every removal is recorded.

**Loss and topology change are two different books.** When two islands of one province drift
into each other and become one part, nothing is lost — but the region's component structure did
change, and the strait between them is now drawn as land, so a click that used to fall on water
selects the province. At ``low`` this happens 30 times against 11 splits, a net 19 parts. Merges
and splits are therefore recorded with the ``change`` category, which does not set the ``lossy``
flag; only ``loss`` kinds do. See ``docs/PROJE-TALIMATI.md`` §FAZ 2, where this is written down
as a deliberate exception to "part counts stay consistent between levels".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

import shapely
import topojson
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry import shape as shapely_shape
from shapely.geometry.base import BaseGeometry

from .loader import Dataset, Territory
from .loss import (
    STAGE_SIMPLIFICATION,
    LossEvent,
    LossLedger,
    event,
)
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
Parts are identified by geometry rather than by counting, and the merges and splits are booked
as topology changes instead.
"""

DEFAULT_MAX_TOTAL_DROPPED_AREA = 50_000.0
"""Total dropped area, in square metres, across every part of every region.

Without this the per-part gate is trivially evaded: five thousand parts of 9.999 m² each pass
one at a time and take 50 km² with them. The default is five times the single-part limit, so a
handful of legitimately tiny islets fits under it while a systematic cull does not. Measured on
TUR ADM1 the worst level (``low``) drops 685 m² in total.
"""

PART_CORRESPONDENCE_AREA_RATIO = 0.1
"""The functionally-gone threshold, used for two questions in this order.

1. **Is a source part gone?** Measured as the fraction of *that part* the output still covers.
   Measured as overlapping **area**, never with ``intersects()``: touching counts as intersecting,
   and a counterexample built for an earlier review made a 1.24 km² part touch the simplified
   output at a single point, so the intersection had zero area, the part was recorded as alive,
   and the 10.000 m² gate never saw the largest loss in the build.
2. **Do a surviving source part and an output part correspond**, for counting merges and splits?
   There, against the *smaller* of the two areas, so both directions work: a sliver splitting off
   a large part overlaps only a few percent of that part but nearly all of itself.

Question 1 is settled first and its answers are removed from question 2, so a part cannot be
booked as gone and as still corresponding to something. When a remnant does survive under a
dropped part, the remnant is recorded as a *created* part — the source part it came from is no
longer being claimed as present, so the output piece needs its own line.

**This ratio decides whether a part is gone, and nothing else.** It is not a claim that a part
above it is intact. A part can survive having lost 89% of its ground, and calling that "no loss"
is the cliff an earlier review found here. What the part kept is therefore recorded separately —
see ``PART_SEVERE_SHRINK_RATIO`` and ``retainedAreaRatio`` in the manifest — and the ground it
stopped covering is on the removed side of the area budget whichever side of this threshold it
fell.
"""

PART_SEVERE_SHRINK_RATIO = 0.5
"""A surviving part that kept less than this fraction of its area gets named in the manifest.

Recorded, not gated. Simplification at ``low`` moves boundaries by up to ~210 m and a small part
can legitimately lose half its area to that; failing the build on it would mean failing every
``low`` build. What must not happen is the number being invisible, because "the part survived"
and "the part is intact" are different statements and only the first one has a threshold.
"""

BUDGET_RELATIVE_TOLERANCE = 1e-9
"""Slack in the area identity, as a fraction of the territory's source area.

Not a tolerance on the accounting — the identity is exact arithmetic over exact set operations.
It is float64 slack: province areas run to 2e10 m², and shapely's boolean operations return
areas good to roughly a part in 1e12 of the operands. A province of 2e10 m² therefore gets about
20 m² of room, which is far below the smallest loss this pipeline has ever recorded (685 m²).
"""

BUDGET_ABSOLUTE_TOLERANCE_M2 = 1.0
"""Floor for the slack above, so a small fixture is not held to sub-micrometre precision."""


@dataclass(frozen=True)
class DroppedPart:
    """A polygon part simplification removed, with enough detail to judge whether that mattered."""

    territory_id: str
    territory_name: str
    area: float
    """Square metres of source ground the output no longer covers, in the local projection.

    The part's *lost* area rather than its original area. They differ only when a part fell
    below the correspondence ratio while still overlapping the output slightly, and the budget
    identity needs the number that actually left.
    """

    source_area: float = 0.0
    """What the part covered before, so a reader can see how much of it the number above is."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "territoryId": self.territory_id,
            "territoryName": self.territory_name,
            "area": self.area,
            "sourceArea": self.source_area,
        }

    def describe(self) -> str:
        return f"{self.territory_name} ({self.territory_id}): {self.area:.1f} m² lost"


@dataclass(frozen=True)
class TopologyChange:
    """How one region's component structure changed. Not a loss — see the module docstring.

    ``merges`` and ``splits`` are *contributions*, not event counts: three source parts becoming
    one output part contributes 2 merges, and one source part becoming three output parts
    contributes 2 splits. ``created`` counts output parts no source part corresponds to. Counted
    that way the three sum, with the dropped parts, to the change in part count — which event
    counts would not, and which is asserted in ``_check_budget``.
    """

    territory_id: str
    territory_name: str
    merges: int
    splits: int
    created: int = 0

    @property
    def total(self) -> int:
        return self.merges + self.splits + self.created

    def as_dict(self) -> dict[str, Any]:
        return {
            "territoryId": self.territory_id,
            "territoryName": self.territory_name,
            "merges": self.merges,
            "splits": self.splits,
            "created": self.created,
        }


@dataclass(frozen=True)
class TerritoryAccount:
    """Everything that happened to one territory, measured rather than inferred.

    The three area figures are taken from the projected geometries directly and owe nothing to
    the event list, which is what lets ``_check_budget`` use them to audit it.
    """

    territory_id: str
    territory_name: str
    events: tuple[LossEvent, ...]
    source_area: float
    output_area: float
    measured_removed_area: float
    measured_added_area: float
    source_part_count: int
    part_count: int
    source_hole_count: int
    hole_count: int
    dropped_parts: tuple[DroppedPart, ...]
    topology_change: TopologyChange | None
    dropped_hole_count: int
    hole_merges: int
    hole_splits: int
    artifact_holes_removed: int
    min_retained_area_ratio: float
    severe_shrink_count: int
    merge_added_area: float

    @property
    def ledger(self) -> LossLedger:
        return LossLedger.of(self.events)

    @property
    def merges(self) -> int:
        return self.topology_change.merges if self.topology_change else 0

    @property
    def splits(self) -> int:
        return self.topology_change.splits if self.topology_change else 0

    @property
    def created(self) -> int:
        return self.topology_change.created if self.topology_change else 0


@dataclass(frozen=True)
class SimplifyResult:
    """A simplified dataset plus what changed getting there."""

    dataset: Dataset
    lod: str
    tolerance: float
    ledger: LossLedger
    source_vertex_count: int
    vertex_count: int
    source_part_count: int
    part_count: int
    source_hole_count: int
    hole_count: int
    source_area: float = 0.0
    output_area: float = 0.0
    removed_area: float = 0.0
    """Source ground the output does not cover, measured over every territory."""
    added_area: float = 0.0
    """Ground the output covers that the source did not."""
    min_retained_area_ratio: float = 1.0
    severe_shrink_count: int = 0
    merge_added_area: float = 0.0
    """How much land the merges invented — the size of the false bridges, in square metres."""
    dropped_parts: tuple[DroppedPart, ...] = ()
    topology_changes: tuple[TopologyChange, ...] = ()

    @property
    def loss(self) -> GeometryLoss:
        """The two counters that are genuinely losses, for callers that want a short summary.

        ``artifact_hole_removed`` is deliberately absent: removing an interior ring the source
        never had puts ground back that belongs to the region, so it is a repair recorded as a
        ``change``, not a loss. The old version counted it into ``skipped_rings`` and made every
        ``low`` build look lossy for a reason that was not loss.
        """
        return GeometryLoss(
            skipped_parts=self.ledger.count("dropped_part"),
            skipped_rings=self.ledger.count("dropped_hole"),
        )

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
    def created_parts(self) -> int:
        return sum(change.created for change in self.topology_changes)

    @property
    def dropped_hole_count(self) -> int:
        return self.ledger.count("dropped_hole")

    @property
    def is_lossy(self) -> bool:
        """From the ledger's loss events and from nothing else. See ``loss.py``."""
        return self.ledger.is_lossy

    @property
    def topology_changed(self) -> bool:
        """True when *this stage* changed the component or enclave structure of the source.

        Read off the ledger through ``loss.py``'s per-kind declaration rather than from a list of
        counters here. The counters missed ``hole_merge`` and ``hole_split`` — two source enclaves
        becoming one leaves ``dropped_hole`` at zero — so a level could rearrange enclaves and
        report an unchanged topology.

        Stage-scoped on purpose: this is what the ``simplification`` block reports. The manifest's
        top-level flag spans triangulation and normalization too, because those can remove a part
        this stage kept; see ``build._client_flags``.
        """
        return self.ledger.topology_changed

    @property
    def retained_area_ratio(self) -> float:
        """Fraction of the source area the output still covers, over the whole level."""
        if self.source_area <= 0.0:
            return 1.0
        return (self.source_area - self.removed_area) / self.source_area

    def _changes_by_size(self) -> list[TopologyChange]:
        """Busiest region first, then by id so the manifest stays byte-reproducible."""
        return sorted(self.topology_changes, key=lambda c: (-c.total, c.territory_id))

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
            # The area budget, in the manifest so a consumer can re-derive the identity without
            # re-running the build. "Nothing was lost" and "the shape is the same" are different
            # claims and these are the numbers that separate them.
            "areaBudget": {
                "sourceArea": self.source_area,
                "outputArea": self.output_area,
                "removedArea": self.removed_area,
                "addedArea": self.added_area,
                "retainedAreaRatio": self.retained_area_ratio,
                "minPartRetainedAreaRatio": self.min_retained_area_ratio,
                "severeShrinkParts": self.severe_shrink_count,
            },
            # Deliberately kept out of the loss ledger's ``loss`` category: merging two islands
            # into one part loses no ground. It is reported because the part count moving is
            # still a change a client can be surprised by, and because ``mergeAddedArea`` is the
            # size of the land bridge a click can now land on.
            "topologyChanges": {
                "merges": self.merges,
                "splits": self.splits,
                "created": self.created_parts,
                "droppedHoles": self.dropped_hole_count,
                "netPartChange": self.merges - self.splits - self.created_parts,
                "mergeAddedArea": self.merge_added_area,
                "regions": [change.as_dict() for change in self._changes_by_size()],
            },
            "topologyChanged": self.topology_changed,
            "loss": self.ledger.as_manifest_dict(),
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

    Raises ``SimplifyError`` when the per-level area or component-count identity does not
    balance, which means something changed the geometry without recording it.
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

    accounts: list[TerritoryAccount] = []
    rebuilt: list[Territory] = []
    for territory in territories:
        geometry, hole_report = _resolve_holes(simplified[territory.id], territory.geometry)
        if geometry.is_empty or geometry.area <= 0.0:
            raise SimplifyError(
                f"territory '{territory.id}' ({territory.name}) simplified away entirely at lod "
                f"'{lod}' (tolerance {tolerance}); the level is too coarse for this dataset"
            )
        accounts.append(account_for(territory, geometry, origin, hole_report))
        rebuilt.append(replace(territory, geometry=geometry))

    result_dataset = replace(dataset, territories=tuple(rebuilt))
    if len(result_dataset) != len(dataset):
        raise SimplifyError(
            f"lod '{lod}' changed the territory count from {len(dataset)} to "
            f"{len(result_dataset)}; regions must never appear or disappear"
        )

    # The identities first: a build whose books do not balance has no business being judged
    # against a threshold, because the numbers the threshold reads cannot be trusted yet.
    check_budget(accounts, lod)

    dropped = [part for account in accounts for part in account.dropped_parts]
    _check_dropped_area(dropped, lod, tolerance, max_dropped_part_area, max_total_dropped_area)

    return SimplifyResult(
        dataset=result_dataset,
        lod=lod,
        tolerance=tolerance,
        ledger=LossLedger.of(item for account in accounts for item in account.events),
        source_vertex_count=_vertex_count(source_geometries.values()),
        vertex_count=_vertex_count(t.geometry for t in rebuilt),
        source_part_count=sum(account.source_part_count for account in accounts),
        part_count=sum(account.part_count for account in accounts),
        source_hole_count=sum(account.source_hole_count for account in accounts),
        hole_count=sum(account.hole_count for account in accounts),
        source_area=sum(account.source_area for account in accounts),
        output_area=sum(account.output_area for account in accounts),
        removed_area=sum(account.measured_removed_area for account in accounts),
        added_area=sum(account.measured_added_area for account in accounts),
        min_retained_area_ratio=min(
            (account.min_retained_area_ratio for account in accounts), default=1.0
        ),
        severe_shrink_count=sum(account.severe_shrink_count for account in accounts),
        merge_added_area=sum(account.merge_added_area for account in accounts),
        dropped_parts=tuple(dropped),
        topology_changes=tuple(
            account.topology_change for account in accounts if account.topology_change
        ),
    )


# ------------------------------------------------------------------------------------------
# The budget. Everything below measures; nothing below is allowed to assume.
# ------------------------------------------------------------------------------------------


def check_budget(accounts: list[TerritoryAccount], lod: str) -> None:
    """Assert the identities in the module docstring, per territory, and raise on the first gap.

    Per territory rather than over the level as a whole, so the tolerance stays proportional to
    the geometry being checked and a failure names the province rather than the country.

    What this can and cannot see, stated plainly because the point of the exercise is to stop
    overclaiming: the area attribution is built from the same source-part / output-part
    decomposition that produces the measurements, so on a build where every stage behaves the
    two sides agree by construction. What it catches is a stage that changes the geometry
    *outside* that decomposition — anything that mutates the result after the accounting has
    run, a part type ``_parts`` does not understand, or an event with an area on the wrong side
    of the budget. The counting identities are the ones with teeth: they compare structure the
    events claim against structure the geometry actually has, so a source hole that disappeared
    with nothing recorded fails here even though no output ring exists to be inspected.
    """
    for account in accounts:
        ledger = account.ledger
        slack = max(BUDGET_ABSOLUTE_TOLERANCE_M2, BUDGET_RELATIVE_TOLERANCE * account.source_area)
        where = f"lod '{lod}', territory '{account.territory_id}' ({account.territory_name})"

        expected = account.source_area + ledger.added_area - ledger.removed_area
        balance = expected - account.output_area
        if abs(balance) > slack:
            raise SimplifyError(
                f"{where}: the area budget does not balance. Source {account.source_area:.3f} m² "
                f"+ {ledger.added_area:.3f} m² recorded as added − {ledger.removed_area:.3f} m² "
                f"recorded as removed = {expected:.3f} m², "
                f"but the output covers {account.output_area:.3f} m² "
                f"({balance:+.3f} m², over the {slack:.3f} m² of float64 slack). Something "
                f"changed this geometry without recording it. Records: {ledger.describe()}"
            )

        for label, recorded, measured in (
            ("removed", ledger.removed_area, account.measured_removed_area),
            ("added", ledger.added_area, account.measured_added_area),
        ):
            if abs(recorded - measured) > slack:
                raise SimplifyError(
                    f"{where}: {recorded:.3f} m² recorded as {label}, but measuring the source "
                    f"against the output directly gives {measured:.3f} m² "
                    f"({recorded - measured:+.3f} m²). The event list does not describe the "
                    f"geometry it is attached to. Records: {ledger.describe()}"
                )

        part_change = account.source_part_count - account.part_count
        accounted = len(account.dropped_parts) + account.merges - account.splits - account.created
        if part_change != accounted:
            raise SimplifyError(
                f"{where}: the part count moved by {part_change} "
                f"({account.source_part_count} → {account.part_count}) but the records account "
                f"for {accounted} ({len(account.dropped_parts)} dropped, {account.merges} "
                f"merges, {account.splits} splits, {account.created} created)"
            )

        hole_change = account.source_hole_count - account.hole_count
        hole_accounted = account.dropped_hole_count + account.hole_merges - account.hole_splits
        if hole_change != hole_accounted:
            raise SimplifyError(
                f"{where}: the hole count moved by {hole_change} "
                f"({account.source_hole_count} → {account.hole_count}) but the records account "
                f"for {hole_accounted} ({account.dropped_hole_count} dropped, "
                f"{account.hole_merges} merges, {account.hole_splits} splits). A source enclave "
                f"that disappears leaves no output ring to inspect, so this count is the only "
                f"thing that sees it."
            )


@dataclass(frozen=True)
class HoleReport:
    """What matching the output's interior rings against the source's found."""

    dropped: tuple[Polygon, ...] = ()
    """Source interior rings with no counterpart left. The region now covers this ground."""
    artifacts: tuple[Polygon, ...] = ()
    """Interior rings simplification invented, already removed from the geometry."""
    merges: int = 0
    splits: int = 0
    source_hole_count: int = 0
    hole_count: int = 0


def _resolve_holes(geometry: BaseGeometry, source: BaseGeometry) -> tuple[BaseGeometry, HoleReport]:
    """Match interior rings to the source's, drop the invented ones, and report both directions.

    A hole that corresponds to a source hole is a real enclave and is kept. A hole with no source
    hole behind it was manufactured by simplification pulling a boundary through itself, and a
    region with a hole the data never had is simply wrong. **A source hole with no output hole in
    front of it is the opposite case and the one every earlier version of this function missed:**
    it only ever walked the rings still present in the output, so a source enclave that vanished
    completely left nothing to iterate over, no counter moved, and the level reported
    ``lossy: false``. Both directions are now read off the same correspondence.

    Only interior rings are touched. Shared boundaries are exterior rings, so nothing here can
    move a vertex a neighbour also uses — that is what makes this safe to do after the arcs
    have been simplified.
    """
    source_holes = [Polygon(ring) for part in _parts(source) for ring in part.interiors]
    output_parts = _parts(geometry)
    output_holes = [Polygon(ring) for part in output_parts for ring in part.interiors]

    overlaps = _overlap_areas(source_holes, output_holes)
    outputs_per_source, sources_per_output = _correspondence(source_holes, output_holes, overlaps)

    dropped = tuple(hole for i, hole in enumerate(source_holes) if not outputs_per_source.get(i))
    artifact_indices = {j for j in range(len(output_holes)) if not sources_per_output.get(j)}
    artifacts = tuple(output_holes[j] for j in sorted(artifact_indices))

    merges = sum(len(sources) - 1 for sources in sources_per_output.values() if len(sources) > 1)
    splits = sum(len(outputs) - 1 for outputs in outputs_per_source.values() if len(outputs) > 1)

    report = HoleReport(
        dropped=dropped,
        artifacts=artifacts,
        merges=merges,
        splits=splits,
        source_hole_count=len(source_holes),
        hole_count=len(output_holes) - len(artifacts),
    )
    if not artifacts:
        return geometry, report

    kept_parts = []
    seen = 0
    for part in output_parts:
        interiors = []
        for ring in part.interiors:
            if seen not in artifact_indices:
                interiors.append(ring)
            seen += 1
        kept_parts.append(Polygon(part.exterior, interiors))
    rebuilt = kept_parts[0] if len(kept_parts) == 1 else MultiPolygon(kept_parts)
    return rebuilt, report


def account_for(
    territory: Territory,
    simplified: BaseGeometry,
    origin: Origin,
    holes: HoleReport | None = None,
) -> TerritoryAccount:
    """Measure one territory's source against its output and record every difference.

    Both geometries are projected to local metres first, so every area in the account and every
    area in the events is in square metres — the unit the gates, the manifest and the report all
    speak. Square degrees are not a unit anyone can judge an island by.

    The two books come out of one correspondence, which is why they are computed together and
    cannot drift apart: parts with no surviving counterpart are loss, parts that merged, split or
    appeared are topology change, and the ground every surviving part gained or gave up is the
    boundary movement that makes the area budget close.

    Two shortcuts that earlier versions took are gone, both shown to hide real losses:

    * An early return when the source and the output had the *same number* of parts. Source
      ``A + B`` simplifying to ``A + C`` has two parts on each side while ``B`` is gone entirely.
    * Survival tested with ``intersects()``. A part meeting the output at one point shares no
      area with it, and a 1.24 km² part rigged to touch at a single vertex counted as alive.
    """
    if holes is None:
        # Called directly (tests, and anything that wants one territory measured): do the same
        # hole resolution the level build does, so the counts in the account describe the
        # geometry it is about to return rather than assuming there were no rings to think about.
        simplified, holes = _resolve_holes(simplified, territory.geometry)
    source_local = project_geometry(territory.geometry, origin)
    output_local = project_geometry(simplified, origin)

    source_parts = _parts(source_local)
    output_parts = _parts(output_local)
    overlaps = _overlap_areas(source_parts, output_parts)

    covered_source = [
        sum(overlaps.get((i, j), 0.0) for j in range(len(output_parts)))
        for i in range(len(source_parts))
    ]
    covered_output = [
        sum(overlaps.get((i, j), 0.0) for i in range(len(source_parts)))
        for j in range(len(output_parts))
    ]

    # Step one, and the only place ``PART_CORRESPONDENCE_AREA_RATIO`` decides whether something
    # was lost: a source part the output barely covers is functionally gone.
    gone = {
        i
        for i, part in enumerate(source_parts)
        if part.area > 0.0 and covered_source[i] < PART_CORRESPONDENCE_AREA_RATIO * part.area
    }
    # Step two: match what is left. Edges touching a part booked as gone are removed first, so a
    # source part cannot be both "dropped" and "still corresponds to an output part" — which
    # would count it on both sides and make the identity in ``check_budget`` unsatisfiable. The
    # consequence is deliberate: when 9% of a part survives as a real output part, the part is
    # recorded as dropped *and* that remnant is recorded as a created part, because the source
    # part it came from is no longer being claimed as present.
    outputs_per_source, sources_per_output = _correspondence(
        source_parts,
        output_parts,
        {pair: area for pair, area in overlaps.items() if pair[0] not in gone},
    )

    dropped_holes_local = [project_geometry(hole, origin) for hole in holes.dropped]
    hole_fill = [
        sum(hole.intersection(part).area for hole in dropped_holes_local) for part in output_parts
    ]

    events: list[LossEvent] = []
    dropped_parts: list[DroppedPart] = []
    retreat_area = 0.0
    split_area = 0.0
    split_contributions = 0
    shrink_details: list[str] = []
    min_retained = 1.0

    for i, part in enumerate(source_parts):
        residual = max(0.0, part.area - covered_source[i])
        matched = outputs_per_source.get(i, ())
        if i in gone or not matched:
            dropped_parts.append(
                DroppedPart(
                    territory_id=territory.id,
                    territory_name=territory.name,
                    area=residual,
                    source_area=part.area,
                )
            )
            continue
        retained = covered_source[i] / part.area if part.area > 0.0 else 1.0
        min_retained = min(min_retained, retained)
        if retained < PART_SEVERE_SHRINK_RATIO:
            shrink_details.append(
                f"{territory.name} ({territory.id}): a part kept {retained:.1%} of its "
                f"{part.area:.1f} m²"
            )
        if len(matched) > 1:
            split_contributions += len(matched) - 1
            split_area += residual
        else:
            retreat_area += residual

    advance_area = 0.0
    merge_area = 0.0
    merge_contributions = 0
    created_area = 0.0
    created_details: list[str] = []

    for j, part in enumerate(output_parts):
        residual = max(0.0, part.area - covered_output[j] - hole_fill[j])
        matched = sources_per_output.get(j, ())
        if not matched:
            created_area += residual
            created_details.append(
                f"{territory.name} ({territory.id}): an output part of {part.area:.1f} m² "
                f"matches no source part"
            )
        elif len(matched) > 1:
            merge_contributions += len(matched) - 1
            merge_area += residual
        else:
            advance_area += residual

    if dropped_parts:
        events.append(
            event(
                STAGE_SIMPLIFICATION,
                "dropped_part",
                count=len(dropped_parts),
                area=sum(part.area for part in dropped_parts),
                details=[part.describe() for part in dropped_parts],
            )
        )
    if holes.dropped:
        events.append(
            event(
                STAGE_SIMPLIFICATION,
                "dropped_hole",
                count=len(holes.dropped),
                area=sum(hole.intersection(output_local).area for hole in dropped_holes_local),
                details=[
                    f"{territory.name} ({territory.id}): an enclave of {hole.area:.1f} m² is gone; "
                    f"the region now covers it"
                    for hole in dropped_holes_local
                ],
            )
        )
    if holes.artifacts:
        events.append(
            event(
                STAGE_SIMPLIFICATION,
                "artifact_hole_removed",
                count=len(holes.artifacts),
                area=sum(project_geometry(hole, origin).area for hole in holes.artifacts),
            )
        )
    for kind, count in (("hole_merge", holes.merges), ("hole_split", holes.splits)):
        if count:
            events.append(event(STAGE_SIMPLIFICATION, kind, count=count))
    # No detail strings on these two: which regions merged or split is in
    # ``topologyChanges.regions``, named region by region, and repeating it here would be the
    # same list twice in every manifest.
    if merge_contributions or merge_area:
        events.append(
            event(STAGE_SIMPLIFICATION, "part_merge", count=merge_contributions, area=merge_area)
        )
    if split_contributions or split_area:
        events.append(
            event(STAGE_SIMPLIFICATION, "part_split", count=split_contributions, area=split_area)
        )
    if created_details:
        events.append(
            event(
                STAGE_SIMPLIFICATION,
                "part_created",
                count=len(created_details),
                area=created_area,
                details=created_details,
            )
        )
    if shrink_details:
        events.append(
            event(
                STAGE_SIMPLIFICATION,
                "severe_shrink",
                count=len(shrink_details),
                details=shrink_details,
            )
        )
    for kind, area in (("boundary_retreat", retreat_area), ("boundary_advance", advance_area)):
        if area > 0.0:
            events.append(event(STAGE_SIMPLIFICATION, kind, count=1, area=area))

    change: TopologyChange | None = None
    if merge_contributions or split_contributions or created_details:
        change = TopologyChange(
            territory_id=territory.id,
            territory_name=territory.name,
            merges=merge_contributions,
            splits=split_contributions,
            created=len(created_details),
        )

    # Measured against the geometries as wholes, with no reference to the decomposition above.
    # This is the number the event list has to reproduce.
    shared = source_local.intersection(output_local).area
    return TerritoryAccount(
        territory_id=territory.id,
        territory_name=territory.name,
        events=tuple(events),
        source_area=source_local.area,
        output_area=output_local.area,
        measured_removed_area=max(0.0, source_local.area - shared),
        measured_added_area=max(0.0, output_local.area - shared),
        source_part_count=len(source_parts),
        part_count=len(output_parts),
        source_hole_count=holes.source_hole_count,
        hole_count=holes.hole_count,
        dropped_parts=tuple(dropped_parts),
        topology_change=change,
        dropped_hole_count=len(holes.dropped),
        hole_merges=holes.merges,
        hole_splits=holes.splits,
        artifact_holes_removed=len(holes.artifacts),
        min_retained_area_ratio=min_retained,
        severe_shrink_count=len(shrink_details),
        merge_added_area=merge_area,
    )


def _overlap_areas(left: list[Any], right: list[Any]) -> dict[tuple[int, int], float]:
    """Intersection area for every left/right pair whose bounding boxes meet.

    Pairs that are absent are pairs with no overlap; the tree keeps this linear-ish instead of
    running an intersection for all L x R combinations, which matters for provinces carrying a
    hundred islands.
    """
    if not left or not right:
        return {}
    tree = shapely.STRtree(right)
    areas: dict[tuple[int, int], float] = {}
    for i, geometry in enumerate(left):
        for candidate in tree.query(geometry):
            j = int(candidate)
            area = geometry.intersection(right[j]).area
            if area > 0.0:
                areas[(i, j)] = area
    return areas


def _correspondence(
    left: list[Any], right: list[Any], overlaps: dict[tuple[int, int], float]
) -> tuple[dict[int, tuple[int, ...]], dict[int, tuple[int, ...]]]:
    """Which left items correspond to which right ones, and the reverse.

    Two pieces correspond when they overlap by at least ``PART_CORRESPONDENCE_AREA_RATIO`` of
    the smaller of the two.
    """
    forward: dict[int, list[int]] = {}
    backward: dict[int, list[int]] = {}
    for (i, j), area in sorted(overlaps.items()):
        smaller = min(left[i].area, right[j].area)
        if smaller <= 0.0 or area < PART_CORRESPONDENCE_AREA_RATIO * smaller:
            continue
        forward.setdefault(i, []).append(j)
        backward.setdefault(j, []).append(i)
    return (
        {key: tuple(value) for key, value in forward.items()},
        {key: tuple(value) for key, value in backward.items()},
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
    detail = "\n  ".join(part.describe() for part in by_size[:5])

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
        geometry = shapely_shape(feature["geometry"])
        if not geometry.is_valid:
            # Simplification can cross a ring over itself; make_valid resolves it, and the
            # interior ring it may leave behind is handled by _resolve_holes.
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


def _parts(geometry: BaseGeometry) -> list[Any]:
    if geometry.geom_type == "MultiPolygon":
        return list(geometry.geoms)
    if geometry.geom_type != "Polygon":
        # Reached only if something upstream of _surfaces_only changes. Raising keeps the area
        # budget honest: silently returning [] here would make a whole territory's geometry
        # invisible to the accounting.
        raise SimplifyError(
            f"expected Polygon or MultiPolygon in the accounting, got {geometry.geom_type!r}"
        )
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
