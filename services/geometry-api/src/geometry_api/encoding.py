"""TKMS v1 encoder and decoder.

The wire format is specified in ``docs/mesh-format.md`` and is fixed; this module is its only
implementation and every rule the document states is enforced here in at least one direction.

Two decisions live here rather than upstream:

**Triangle winding.** Every triangle is turned clockwise at encode time, per triangle, by
computing its signed area and swapping two indices when it comes out counter-clockwise. This is
the single choke point through which all mesh bytes pass, so anything produced later — Phase 2's
simplified LODs, Phase 3's batch container — inherits the guarantee without having to remember
it. earcut currently emits counter-clockwise triangles for both input windings, so in practice
the swap is systematic; it is still computed per triangle, because a blind flip would silently
invert every face the day that changes.

The guarantee is about the mesh's own XY space. How Unity maps (x, y) onto its axes decides
whether that reads as a front face on screen, which is Phase 4's problem — the contract is
pinned here so Phase 4 has something fixed to build against.

**Index width.** ``flags`` bit 0 is derived from the vertex count, never passed in, so it cannot
be forgotten. The threshold is the one the spec states: uint32 above 65535 vertices.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

MAGIC = b"TKMS"
VERSION = 1
HEADER_SIZE = 32
HEADER_STRUCT = struct.Struct("<4sHHIIffff")

FLAG_UINT32_INDICES = 0b1
UINT16_MAX_VERTEX_COUNT = 65535
"""Above this many vertices the index buffer must be uint32 (Unity's IndexFormat limit)."""

MAX_VERTEX_COUNT = 0xFFFFFFFF


class MeshFormatError(ValueError):
    """Raised for any payload or mesh that violates docs/mesh-format.md."""


@dataclass(frozen=True)
class DecodedMesh:
    """The result of decoding a TKMS payload."""

    vertices: NDArray[np.float32]
    indices: NDArray[np.uint32]
    bbox: tuple[float, float, float, float]
    flags: int
    version: int
    bytes_consumed: int
    """Header plus body, as the header declares it.

    The decoder tolerates trailing bytes but reports how many it actually used, so a caller can
    tell padding from payload — and so our own pipeline can be held to producing none.
    """

    @property
    def vertex_count(self) -> int:
        return int(self.vertices.shape[0])

    @property
    def triangle_count(self) -> int:
        return int(self.indices.shape[0] // 3)

    @property
    def uses_uint32_indices(self) -> bool:
        return bool(self.flags & FLAG_UINT32_INDICES)


def encode_tkms(vertices: NDArray[np.floating], indices: NDArray[np.integer]) -> bytes:
    """Encode a triangle mesh as a TKMS v1 payload.

    ``vertices`` are local metres as (N, 2); ``indices`` is a flat triangle list. Winding and
    index width are decided here, not by the caller.
    """
    vertex_array = _validate_vertices(vertices)
    index_array = _validate_indices(indices, vertex_count=vertex_array.shape[0])
    index_array = _to_clockwise(vertex_array, index_array)

    vertex_count = vertex_array.shape[0]
    flags = FLAG_UINT32_INDICES if vertex_count > UINT16_MAX_VERTEX_COUNT else 0
    min_x, min_y = vertex_array.min(axis=0)
    max_x, max_y = vertex_array.max(axis=0)

    header = HEADER_STRUCT.pack(
        MAGIC,
        VERSION,
        flags,
        vertex_count,
        int(index_array.shape[0]),
        float(min_x),
        float(min_y),
        float(max_x),
        float(max_y),
    )
    index_dtype = "<u4" if flags & FLAG_UINT32_INDICES else "<u2"
    return header + vertex_array.astype("<f4").tobytes() + index_array.astype(index_dtype).tobytes()


def decode_tkms(payload: bytes, strict: bool = False) -> DecodedMesh:
    """Decode a TKMS v1 payload.

    By default this checks everything that makes a payload *unreadable or dangerous to trust*:
    magic, version, declared length, index alignment and range, non-finite coordinates, the
    uint32 flag, and the bounding box. The bbox is checked hard — finite, correctly ordered, and
    equal to the real extent of the decoded vertices — because Phase 3 and Phase 5 cull with it,
    and a wrong box makes a region silently disappear from the screen rather than look wrong.

    ``strict=True`` additionally checks the two rules that make a mesh *render* correctly:
    clockwise winding and no zero-area triangles. These are the encoder's contract, so the
    default reader does not pay for them on every mesh; build pipelines and tests should.
    """
    if len(payload) < HEADER_SIZE:
        raise MeshFormatError(f"payload is {len(payload)} bytes, shorter than the 32-byte header")

    magic, version, flags, vertex_count, index_count, min_x, min_y, max_x, max_y = (
        HEADER_STRUCT.unpack_from(payload, 0)
    )
    if magic != MAGIC:
        raise MeshFormatError(f"bad magic {magic!r}, expected {MAGIC!r}")
    if version != VERSION:
        raise MeshFormatError(f"unsupported TKMS version {version}, this reader implements v1")

    uses_uint32 = bool(flags & FLAG_UINT32_INDICES)
    if vertex_count > UINT16_MAX_VERTEX_COUNT and not uses_uint32:
        raise MeshFormatError(
            f"vertexCount {vertex_count} exceeds {UINT16_MAX_VERTEX_COUNT} but the uint32 index "
            "flag is not set"
        )
    if index_count == 0 or vertex_count == 0:
        raise MeshFormatError("empty geometry: a mesh must carry at least one triangle")
    if index_count % 3 != 0:
        raise MeshFormatError(f"indexCount {index_count} is not a multiple of 3")

    index_element_size = 4 if uses_uint32 else 2
    expected_length = HEADER_SIZE + vertex_count * 2 * 4 + index_count * index_element_size
    if len(payload) < expected_length:
        raise MeshFormatError(
            f"payload is {len(payload)} bytes, the header declares {expected_length}"
        )
    # Trailing bytes beyond the declared length are ignored on purpose (alignment padding),
    # per docs/mesh-format.md. Short payloads are rejected above; the asymmetry is deliberate.

    vertex_end = HEADER_SIZE + vertex_count * 2 * 4
    vertices = (
        np.frombuffer(payload, dtype="<f4", count=vertex_count * 2, offset=HEADER_SIZE)
        .reshape(-1, 2)
        .copy()
    )
    if not np.all(np.isfinite(vertices)):
        raise MeshFormatError("vertex coordinates contain NaN or infinity")

    indices = np.frombuffer(
        payload, dtype="<u4" if uses_uint32 else "<u2", count=index_count, offset=vertex_end
    ).astype(np.uint32)
    if int(indices.max()) >= vertex_count:
        raise MeshFormatError(
            f"index {int(indices.max())} is out of range for {vertex_count} vertices"
        )

    _validate_header_bbox((min_x, min_y, max_x, max_y), vertices)
    if strict:
        _validate_render_rules(vertices, indices)

    return DecodedMesh(
        vertices=vertices,
        indices=indices,
        bbox=(min_x, min_y, max_x, max_y),
        flags=int(flags),
        version=int(version),
        bytes_consumed=expected_length,
    )


def _validate_header_bbox(
    bbox: tuple[float, float, float, float], vertices: NDArray[np.float32]
) -> None:
    if not all(np.isfinite(value) for value in bbox):
        raise MeshFormatError(f"header bbox contains NaN or infinity: {bbox}")
    min_x, min_y, max_x, max_y = bbox
    if min_x > max_x or min_y > max_y:
        raise MeshFormatError(
            f"header bbox is inverted: min ({min_x}, {min_y}) is past max ({max_x}, {max_y})"
        )

    actual = (
        float(vertices[:, 0].min()),
        float(vertices[:, 1].min()),
        float(vertices[:, 0].max()),
        float(vertices[:, 1].max()),
    )
    if bbox != actual:
        raise MeshFormatError(
            f"header bbox {bbox} does not match the decoded vertices {actual}; a bbox that lies "
            "makes the region vanish from viewport culling instead of merely looking wrong"
        )


def _validate_render_rules(vertices: NDArray[np.float32], indices: NDArray[np.uint32]) -> None:
    """The strict-mode checks: the rules that decide whether a valid payload renders correctly."""
    corners = vertices.astype(np.float64)[indices.reshape(-1, 3)]
    a, b, c = corners[:, 0], corners[:, 1], corners[:, 2]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])

    degenerate = int(np.count_nonzero(cross == 0.0))
    if degenerate:
        raise MeshFormatError(f"{degenerate} triangle(s) have zero area")
    counter_clockwise = int(np.count_nonzero(cross > 0.0))
    if counter_clockwise:
        raise MeshFormatError(
            f"{counter_clockwise} triangle(s) wind counter-clockwise; TKMS requires clockwise"
        )


def _validate_vertices(vertices: NDArray[np.floating]) -> NDArray[np.float32]:
    array = np.asarray(vertices, dtype=np.float32)
    if array.ndim != 2 or array.shape[1] != 2:
        raise MeshFormatError(f"vertices must be an (N, 2) array, got shape {array.shape}")
    if array.shape[0] < 3:
        raise MeshFormatError("a mesh needs at least 3 vertices")
    if array.shape[0] > MAX_VERTEX_COUNT:
        raise MeshFormatError(f"vertexCount {array.shape[0]} overflows the uint32 header field")
    if not np.all(np.isfinite(array)):
        raise MeshFormatError("vertex coordinates contain NaN or infinity")
    return array


def _validate_indices(indices: NDArray[np.integer], vertex_count: int) -> NDArray[np.uint32]:
    """Validate before casting, never after.

    ``astype(np.uint32)`` is a silent reinterpretation: 0.9 becomes 0 and 2**32 becomes 0, and
    both then encode as a perfectly valid TKMS payload pointing at the wrong vertex. So the dtype
    and the range are checked while the values are still what the caller passed.
    """
    array = np.asarray(indices).reshape(-1)
    if not np.issubdtype(array.dtype, np.integer):
        raise MeshFormatError(
            f"indices must be an integer array, got dtype {array.dtype}; a float index cannot be "
            "rounded silently"
        )
    if array.size == 0:
        raise MeshFormatError("empty geometry: a mesh must carry at least one triangle")
    if array.size % 3 != 0:
        raise MeshFormatError(f"indexCount {array.size} is not a multiple of 3")
    if int(array.min()) < 0:
        raise MeshFormatError(f"negative vertex index {int(array.min())}")
    if int(array.max()) >= vertex_count:
        raise MeshFormatError(
            f"index {int(array.max())} is out of range for {vertex_count} vertices"
        )
    return array.astype(np.uint32)


def _to_clockwise(vertices: NDArray[np.float32], indices: NDArray[np.uint32]) -> NDArray[np.uint32]:
    """Turn every counter-clockwise triangle around, and reject ones with no orientation."""
    triangles = indices.reshape(-1, 3).copy()
    corners = vertices.astype(np.float64)[triangles]
    a, b, c = corners[:, 0], corners[:, 1], corners[:, 2]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])

    degenerate = int(np.count_nonzero(cross == 0.0))
    if degenerate:
        raise MeshFormatError(
            f"{degenerate} triangle(s) have zero area once cast to float32 and cannot be "
            "oriented; drop them before encoding"
        )

    counter_clockwise = cross > 0.0
    triangles[counter_clockwise, 1], triangles[counter_clockwise, 2] = (
        triangles[counter_clockwise, 2],
        triangles[counter_clockwise, 1].copy(),
    )
    return triangles.reshape(-1)
