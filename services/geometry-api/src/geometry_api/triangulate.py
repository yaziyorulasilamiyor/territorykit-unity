"""Polygon triangulation on top of ``mapbox_earcut``.

Three traps are handled here, all of them from the project's known-pitfalls list:

**Holes.** The Python binding is not the JavaScript ``earcut(vertices, holeIndices)`` API. It
takes the cumulative *end* offset of every ring — exterior first, then each hole — so a polygon
with a 4-point exterior and a 4-point hole is passed as ``ring_ends = [4, 8]``. The binding
rejects offset arrays whose last value is not the vertex count, which catches the crude
start-versus-end mix-ups outright. It does not catch all of them: collapsing two holes into one
span (``[4, 12]`` instead of ``[4, 8, 12]``) is accepted, and — measured — yields exactly the
right total area while producing overlapping and missing triangles. That is why the two-hole
fixture is paired with a point-coverage test rather than an area test.

**MultiPolygon.** Each part is triangulated on its own and merged by offsetting its indices.
Concatenating the parts into a single earcut call would make earcut bridge across the gap and
emit triangles that span two islands.

**Winding.** Ring winding is normalized here (exterior counter-clockwise, holes clockwise) as
hygiene, so signed-area reasoning downstream is deterministic even for datasets that ignore
RFC 7946. It is *not* what makes holes work — earcut decides hole-versus-exterior from ring
order. Triangle winding is a different thing and is normalized once, at encode time, in
``encoding.py``.

**float32 quantization.** Ring coordinates are snapped to float32 *before* earcut sees them,
because float32 is what TKMS delivers. Triangulating at full float64 precision and quantizing
afterwards is what produced the only real defect found in this phase: 62 of 364,057 triangles
across 16 provinces were thin enough that the cast collapsed them to zero area, leaving faces
the encoder cannot orient. Feeding earcut the coordinates that will actually be stored removes
the class entirely — measured on the same dataset, the same vertex and triangle counts come out
with zero collapsed triangles.

Input geometry must already be in local metres (see ``projection.py``): the area assertions in
the tests are in m², and earcut behaves better on uniformly scaled coordinates than on degrees.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import mapbox_earcut
import numpy as np
from numpy.typing import NDArray
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from .loss import STAGE_TRIANGULATION, LossEvent, LossLedger, event

MIN_RING_VERTICES = 3
"""A ring with fewer distinct vertices than this encloses no area."""

DEGENERATE_AREA_EPSILON_M2 = 1e-9
"""In local metres, a triangle below a square nanometre carries no surface — it is noise."""


class TriangulationError(ValueError):
    """Raised when a geometry cannot be turned into a non-empty triangle mesh."""


@dataclass(frozen=True)
class GeometryLoss:
    """Everything that did not survive the trip from source polygon to mesh.

    One structure instead of a counter per cause, and ``skipped_parts`` is derived from the
    *outcome* — parts that went in, minus parts that came out with triangles — rather than
    incremented at each place a part can be dropped. A new way of losing a part is therefore
    counted without anyone remembering to add a flag for it, which is exactly how the earlier
    version leaked: triangles were removed after the part was already recorded as present, so a
    part could be emptied while every counter still read zero.
    """

    skipped_parts: int = 0
    skipped_rings: int = 0
    degenerate_triangles: int = 0

    def as_events(self) -> tuple[LossEvent, ...]:
        """These three counters as typed records against the schema in ``loss.py``.

        They carry no detail strings: unlike a dropped province, "three degenerate triangles"
        has nothing a reviewer could look up, and the schema marks these kinds accordingly. They
        carry no area either — the float32 grid a part falls through is smaller than a square
        millimetre, so the number worth having is the count.
        """
        return tuple(
            event(STAGE_TRIANGULATION, kind, count)
            for kind, count in (
                ("skipped_part", self.skipped_parts),
                ("skipped_ring", self.skipped_rings),
                ("degenerate_triangle", self.degenerate_triangles),
            )
            if count
        )

    @property
    def ledger(self) -> LossLedger:
        return LossLedger.of(self.as_events(), stages_recorded=[STAGE_TRIANGULATION])

    @property
    def is_lossy(self) -> bool:
        """Derived from the events, never from a counter read by hand — see ``loss.py``."""
        return self.ledger.is_lossy

    def describe(self) -> str:
        return (
            f"{self.skipped_parts} part(s), {self.skipped_rings} hole(s) and "
            f"{self.degenerate_triangles} degenerate triangle(s)"
        )


@dataclass(frozen=True)
class TriangulatedMesh:
    """Triangles in local metres. Vertices stay float64 here; ``encoding.py`` casts to float32.

    ``part_ranges`` records the half-open vertex range each MultiPolygon part occupies, which is
    what lets the tests prove no triangle spans two parts. Only parts that actually reached the
    mesh appear there, so ``part_count`` never claims a part the triangles do not back up.
    """

    vertices: NDArray[np.float64]
    indices: NDArray[np.uint32]
    part_ranges: tuple[tuple[int, int], ...]
    loss: GeometryLoss = GeometryLoss()

    @property
    def vertex_count(self) -> int:
        return int(self.vertices.shape[0])

    @property
    def triangle_count(self) -> int:
        return int(self.indices.shape[0] // 3)

    @property
    def part_count(self) -> int:
        return len(self.part_ranges)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        min_x, min_y = self.vertices.min(axis=0)
        max_x, max_y = self.vertices.max(axis=0)
        return (float(min_x), float(min_y), float(max_x), float(max_y))


def triangulate(geometry: BaseGeometry) -> TriangulatedMesh:
    """Triangulate a Polygon or MultiPolygon that is already projected to local metres."""
    parts = list(_iter_parts(geometry))
    if not parts:
        raise TriangulationError(f"{geometry.geom_type} contains no polygon parts")

    part_vertices: list[NDArray[np.float64]] = []
    part_indices: list[NDArray[np.uint32]] = []
    part_ranges: list[tuple[int, int]] = []
    vertex_offset = 0
    skipped_rings = 0
    degenerate_triangles = 0

    for part in parts:
        prepared = _prepare_part(part)
        if prepared is None:
            continue
        vertices, ring_ends, part_skipped_rings = prepared
        skipped_rings += part_skipped_rings

        indices = mapbox_earcut.triangulate_float64(vertices, ring_ends)
        # Degenerate triangles are removed here, per part, so a part that ends up with nothing
        # is never recorded as present. Doing this after the merge left exactly that hole.
        indices, dropped = _drop_degenerate_triangles(vertices, indices)
        degenerate_triangles += dropped
        if indices.size == 0:
            continue

        part_vertices.append(vertices)
        # The offset is what keeps the parts separate: every part's indices address only its
        # own slice of the merged vertex buffer.
        part_indices.append(indices.astype(np.uint32) + np.uint32(vertex_offset))
        part_ranges.append((vertex_offset, vertex_offset + len(vertices)))
        vertex_offset += len(vertices)

    if not part_vertices:
        raise TriangulationError("triangulation produced no triangles")

    mesh = TriangulatedMesh(
        vertices=np.concatenate(part_vertices),
        indices=np.concatenate(part_indices),
        part_ranges=tuple(part_ranges),
        loss=GeometryLoss(
            skipped_parts=len(parts) - len(part_ranges),
            skipped_rings=skipped_rings,
            degenerate_triangles=degenerate_triangles,
        ),
    )
    _check_every_part_carries_triangles(mesh)
    return mesh


def _check_every_part_carries_triangles(mesh: TriangulatedMesh) -> None:
    """Guards the accounting itself: a recorded part with no triangles is a silent loss."""
    starts = np.array([start for start, _ in mesh.part_ranges])
    owners = np.searchsorted(starts, mesh.indices.reshape(-1, 3)[:, 0], side="right") - 1
    counts = np.bincount(owners, minlength=len(mesh.part_ranges))
    if np.any(counts == 0):
        empty = int(np.count_nonzero(counts == 0))
        raise TriangulationError(
            f"internal invariant: {empty} recorded part(s) carry no triangles, so part_count "
            "would over-report what the mesh contains"
        )


def signed_area(coords: NDArray[np.float64]) -> float:
    """Shoelace area of a ring. Positive is counter-clockwise."""
    x = coords[:, 0]
    y = coords[:, 1]
    return float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0


def _iter_parts(geometry: BaseGeometry) -> Iterator[Polygon]:
    if isinstance(geometry, Polygon):
        yield geometry
    elif isinstance(geometry, MultiPolygon):
        yield from geometry.geoms
    else:
        raise TriangulationError(
            f"unsupported geometry type {geometry.geom_type!r}; expected Polygon or MultiPolygon"
        )


def _prepare_part(
    part: Polygon,
) -> tuple[NDArray[np.float64], NDArray[np.uint32], int] | None:
    """Flatten one part into (vertices, ring end offsets, skipped ring count)."""
    exterior = _prepare_ring(part.exterior.coords, counter_clockwise=True)
    if exterior is None:
        return None

    rings = [exterior]
    skipped_rings = 0
    for interior in part.interiors:
        hole = _prepare_ring(interior.coords, counter_clockwise=False)
        if hole is None:
            skipped_rings += 1
            continue
        rings.append(hole)

    vertices = np.ascontiguousarray(np.concatenate(rings), dtype=np.float64)
    ring_ends = np.cumsum([len(ring) for ring in rings], dtype=np.uint32)
    return vertices, ring_ends, skipped_rings


def quantize_to_storage_precision(coords: NDArray[np.float64]) -> NDArray[np.float64]:
    """Snap coordinates to the float32 grid TKMS stores them on, keeping float64 arithmetic."""
    return coords.astype(np.float32).astype(np.float64)


def _prepare_ring(
    coords: Sequence[tuple[float, ...]], counter_clockwise: bool
) -> NDArray[np.float64] | None:
    """Drop the closing vertex, quantize, drop duplicates and orient; None if no area is left."""
    ring = np.asarray(coords, dtype=np.float64)[:, :2]
    if len(ring) > 1 and np.array_equal(ring[0], ring[-1]):
        ring = ring[:-1]
    ring = quantize_to_storage_precision(ring)
    # Neighbouring vertices can coincide once snapped; the wrap-around comparison also catches
    # a duplicate spanning the seam. Keeping them would hand earcut zero-length edges.
    ring = ring[np.any(ring != np.roll(ring, -1, axis=0), axis=1)]
    if len(ring) < MIN_RING_VERTICES:
        return None

    area = signed_area(ring)
    if area == 0.0:
        # Quantization flattened the ring onto a line. It encloses nothing now, and the caller
        # counts it as lost rather than handing earcut a ring that silently contributes nothing.
        return None
    if (area >= 0.0) != counter_clockwise:
        ring = ring[::-1]
    return np.ascontiguousarray(ring)


def _drop_degenerate_triangles(
    vertices: NDArray[np.float64], indices: NDArray[np.uint32]
) -> tuple[NDArray[np.uint32], int]:
    corners = vertices[indices.reshape(-1, 3).astype(np.intp)]
    a, b, c = corners[:, 0], corners[:, 1], corners[:, 2]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
    keep = np.abs(cross) / 2.0 > DEGENERATE_AREA_EPSILON_M2
    dropped = int((~keep).sum())
    if dropped == 0:
        return indices, 0
    return indices.reshape(-1, 3)[keep].reshape(-1), dropped
