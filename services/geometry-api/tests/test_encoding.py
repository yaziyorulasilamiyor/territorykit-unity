"""TKMS v1: round-trip, winding, index width, and every rule docs/mesh-format.md states.

The format document was written in Phase 0 without an implementation behind it. Each rule it
states is asserted here, in the direction the document assigns it — some are the encoder's job,
some the decoder's.
"""

from __future__ import annotations

import struct

import numpy as np
import pytest
from conftest import MeshCase
from helpers import triangle_signed_areas

from geometry_api.encoding import (
    FLAG_UINT32_INDICES,
    HEADER_SIZE,
    HEADER_STRUCT,
    MAGIC,
    UINT16_MAX_VERTEX_COUNT,
    VERSION,
    MeshFormatError,
    decode_tkms,
    encode_tkms,
)


def _strip_mesh(vertex_count: int) -> tuple[np.ndarray, np.ndarray]:
    """A zig-zag triangle strip with exactly ``vertex_count`` vertices, none degenerate."""
    xs = np.arange(vertex_count, dtype=np.float64) * 0.5
    ys = np.resize(np.array([0.0, 1.0]), vertex_count)
    vertices = np.column_stack([xs, ys])
    starts = np.arange(vertex_count - 2, dtype=np.uint32)
    indices = np.column_stack([starts, starts + 1, starts + 2]).reshape(-1)
    return vertices, indices


def _payload_length(vertex_count: int, index_count: int, *, uint32: bool) -> int:
    return HEADER_SIZE + vertex_count * 2 * 4 + index_count * (4 if uint32 else 2)


def _patch(payload: bytes, offset: int, raw: bytes) -> bytes:
    return payload[:offset] + raw + payload[offset + len(raw) :]


def _header_only(**overrides: object) -> bytes:
    fields: dict[str, object] = {
        "magic": MAGIC,
        "version": VERSION,
        "flags": 0,
        "vertex_count": 3,
        "index_count": 3,
        "min_x": 0.0,
        "min_y": 0.0,
        "max_x": 1.0,
        "max_y": 1.0,
    }
    fields.update(overrides)
    return HEADER_STRUCT.pack(*fields.values())


# --- round-trip -------------------------------------------------------------------------


def test_roundtrip_preserves_vertices_and_indices(hole_mesh: MeshCase) -> None:
    payload = encode_tkms(hole_mesh.mesh.vertices, hole_mesh.mesh.indices)
    decoded = decode_tkms(payload)

    assert decoded.vertex_count == hole_mesh.mesh.vertex_count
    assert decoded.triangle_count == hole_mesh.mesh.triangle_count
    assert np.array_equal(decoded.vertices, hole_mesh.mesh.vertices.astype(np.float32))

    # The same triangles come back; only the corner order within a triangle may have changed,
    # because the encoder turns counter-clockwise ones around.
    source = np.sort(np.asarray(hole_mesh.mesh.indices).reshape(-1, 3), axis=1)
    result = np.sort(decoded.indices.reshape(-1, 3), axis=1)
    assert np.array_equal(source, result)


def test_roundtrip_is_byte_stable(hole_mesh: MeshCase) -> None:
    """Encoding an already-encoded mesh must be a fixed point — Phase 3's cache needs that."""
    once = encode_tkms(hole_mesh.mesh.vertices, hole_mesh.mesh.indices)
    decoded = decode_tkms(once)
    twice = encode_tkms(decoded.vertices, decoded.indices)
    assert once == twice


def test_roundtrip_over_every_province(sample_meshes: list[MeshCase]) -> None:
    for case in sample_meshes:
        payload = encode_tkms(case.mesh.vertices, case.mesh.indices)
        decoded = decode_tkms(payload)
        assert decoded.vertex_count == case.mesh.vertex_count, case.name
        assert decoded.triangle_count == case.mesh.triangle_count, case.name
        assert np.array_equal(decoded.vertices, case.mesh.vertices.astype(np.float32)), case.name


def test_payload_length_matches_the_documented_formula(hole_mesh: MeshCase) -> None:
    payload = encode_tkms(hole_mesh.mesh.vertices, hole_mesh.mesh.indices)
    decoded = decode_tkms(payload)
    assert len(payload) == _payload_length(
        decoded.vertex_count, decoded.triangle_count * 3, uint32=decoded.uses_uint32_indices
    )


def test_encoder_writes_exactly_the_declared_length(hole_mesh: MeshCase) -> None:
    payload = encode_tkms(hole_mesh.mesh.vertices, hole_mesh.mesh.indices)
    assert decode_tkms(payload).bytes_consumed == len(payload)


def test_bytes_consumed_excludes_trailing_padding(hole_mesh: MeshCase) -> None:
    """The decoder tolerates padding; ``bytes_consumed`` is how a caller tells it apart."""
    payload = encode_tkms(hole_mesh.mesh.vertices, hole_mesh.mesh.indices)
    padded = payload + b"\x00\x00\x00\x00"
    decoded = decode_tkms(padded)

    assert decoded.bytes_consumed == len(payload)
    assert decoded.bytes_consumed < len(padded)


def test_header_bbox_matches_the_stored_vertices(sample_meshes: list[MeshCase]) -> None:
    for case in sample_meshes:
        decoded = decode_tkms(encode_tkms(case.mesh.vertices, case.mesh.indices))
        min_x, min_y = decoded.vertices.min(axis=0)
        max_x, max_y = decoded.vertices.max(axis=0)
        assert decoded.bbox == pytest.approx((min_x, min_y, max_x, max_y)), case.name


# --- winding ----------------------------------------------------------------------------


def test_every_encoded_triangle_is_clockwise(
    sample_meshes: list[MeshCase], hole_mesh: MeshCase, multipolygon_mesh: MeshCase
) -> None:
    for case in [hole_mesh, multipolygon_mesh, *sample_meshes]:
        decoded = decode_tkms(encode_tkms(case.mesh.vertices, case.mesh.indices))
        areas = triangle_signed_areas(decoded.vertices, decoded.indices)
        assert np.all(areas < 0), f"{case.name}: {int((areas >= 0).sum())} triangles not clockwise"


def test_winding_normalization_is_per_triangle_not_a_blind_flip() -> None:
    """A mesh that already contains both windings must come out uniformly clockwise."""
    vertices = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    counter_clockwise = [0, 1, 2]
    clockwise = [0, 2, 3][::-1]
    indices = np.array(counter_clockwise + clockwise, dtype=np.uint32)
    assert np.any(triangle_signed_areas(vertices, indices) > 0)
    assert np.any(triangle_signed_areas(vertices, indices) < 0)

    decoded = decode_tkms(encode_tkms(vertices, indices))
    assert np.all(triangle_signed_areas(decoded.vertices, decoded.indices) < 0)


def test_encoder_rejects_triangles_that_collapse_in_float32() -> None:
    """A triangle with no orientation cannot be turned clockwise, so it is refused."""
    vertices = np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]])
    with pytest.raises(MeshFormatError, match="zero area"):
        encode_tkms(vertices, np.array([0, 1, 2], dtype=np.uint32))


# --- index width ------------------------------------------------------------------------


def test_encode_uses_uint16_at_the_threshold() -> None:
    vertices, indices = _strip_mesh(UINT16_MAX_VERTEX_COUNT)
    payload = encode_tkms(vertices, indices)
    decoded = decode_tkms(payload)

    assert decoded.flags & FLAG_UINT32_INDICES == 0
    assert decoded.vertex_count == 65_535
    assert len(payload) == _payload_length(65_535, len(indices), uint32=False)


def test_encode_switches_to_uint32_above_the_threshold() -> None:
    vertices, indices = _strip_mesh(UINT16_MAX_VERTEX_COUNT + 1)
    payload = encode_tkms(vertices, indices)
    decoded = decode_tkms(payload)

    assert decoded.flags & FLAG_UINT32_INDICES == FLAG_UINT32_INDICES
    assert decoded.vertex_count == 65_536
    assert len(payload) == _payload_length(65_536, len(indices), uint32=True)
    assert np.array_equal(decoded.vertices, vertices.astype(np.float32))
    assert int(decoded.indices.max()) == 65_535


def test_mugla_stays_under_the_uint16_limit(sample_meshes: list[MeshCase]) -> None:
    """The regression guard on the real dataset's worst case.

    Mugla sits at 60,478 of the 65,535 vertices a uint16 index buffer can address — 92.3% of
    the space, 5,057 vertices of headroom. If a dataset refresh pushes it over, this fails with
    a number instead of Unity rendering a corrupted mesh.
    """
    mugla = next(case for case in sample_meshes if case.territory.name == "Muğla")
    decoded = decode_tkms(encode_tkms(mugla.mesh.vertices, mugla.mesh.indices))

    assert decoded.vertex_count == 60_478
    assert decoded.flags & FLAG_UINT32_INDICES == 0
    assert decoded.vertex_count / UINT16_MAX_VERTEX_COUNT < 0.95


def test_every_province_fits_in_uint16_indices(sample_meshes: list[MeshCase]) -> None:
    for case in sample_meshes:
        decoded = decode_tkms(encode_tkms(case.mesh.vertices, case.mesh.indices))
        assert decoded.flags & FLAG_UINT32_INDICES == 0, case.name


# --- encoder input validation -----------------------------------------------------------


def test_encode_rejects_index_out_of_range() -> None:
    vertices, indices = _strip_mesh(8)
    indices = indices.copy()
    indices[0] = 8
    with pytest.raises(MeshFormatError, match="out of range"):
        encode_tkms(vertices, indices)


def test_encode_rejects_index_count_not_a_multiple_of_three() -> None:
    vertices, indices = _strip_mesh(8)
    with pytest.raises(MeshFormatError, match="multiple of 3"):
        encode_tkms(vertices, indices[:-1])


def test_encode_rejects_empty_geometry() -> None:
    vertices, _ = _strip_mesh(8)
    with pytest.raises(MeshFormatError, match="at least one triangle"):
        encode_tkms(vertices, np.array([], dtype=np.uint32))


@pytest.mark.parametrize("bad_value", [np.nan, np.inf, -np.inf])
def test_encode_rejects_non_finite_vertices(bad_value: float) -> None:
    vertices, indices = _strip_mesh(8)
    vertices = vertices.copy()
    vertices[3, 1] = bad_value
    with pytest.raises(MeshFormatError, match="NaN or infinity"):
        encode_tkms(vertices, indices)


def test_encode_rejects_wrong_vertex_shape() -> None:
    with pytest.raises(MeshFormatError, match=r"\(N, 2\)"):
        encode_tkms(np.zeros((4, 3)), np.array([0, 1, 2], dtype=np.uint32))


def test_encode_rejects_too_few_vertices() -> None:
    with pytest.raises(MeshFormatError, match="at least 3 vertices"):
        encode_tkms(np.zeros((2, 2)), np.array([0, 1, 1], dtype=np.uint32))


# --- decoder validation, one test per rule in docs/mesh-format.md ------------------------


def test_decode_rejects_a_payload_shorter_than_the_header() -> None:
    with pytest.raises(MeshFormatError, match="shorter than the 32-byte header"):
        decode_tkms(b"TKMS" + bytes(10))


def test_decode_rejects_bad_magic(hole_mesh: MeshCase) -> None:
    payload = encode_tkms(hole_mesh.mesh.vertices, hole_mesh.mesh.indices)
    with pytest.raises(MeshFormatError, match="bad magic"):
        decode_tkms(_patch(payload, 0, b"TKMX"))


def test_decode_rejects_unknown_version(hole_mesh: MeshCase) -> None:
    payload = encode_tkms(hole_mesh.mesh.vertices, hole_mesh.mesh.indices)
    with pytest.raises(MeshFormatError, match="unsupported TKMS version 2"):
        decode_tkms(_patch(payload, 4, struct.pack("<H", 2)))


def test_decode_rejects_a_truncated_body(hole_mesh: MeshCase) -> None:
    payload = encode_tkms(hole_mesh.mesh.vertices, hole_mesh.mesh.indices)
    with pytest.raises(MeshFormatError, match="the header declares"):
        decode_tkms(payload[:-2])


def test_decode_ignores_trailing_bytes(hole_mesh: MeshCase) -> None:
    """docs/mesh-format.md: extra bytes past the declared length are ignored (alignment padding).

    Deliberately asymmetric with the truncation test above: short payloads are invalid, long
    ones are not.
    """
    payload = encode_tkms(hole_mesh.mesh.vertices, hole_mesh.mesh.indices)
    padded = decode_tkms(payload + b"\xde\xad\xbe\xef")
    original = decode_tkms(payload)

    assert np.array_equal(padded.vertices, original.vertices)
    assert np.array_equal(padded.indices, original.indices)


def test_decode_ignores_unknown_flag_bits(hole_mesh: MeshCase) -> None:
    """docs/mesh-format.md: bits other than bit0 are undefined in v1 and must be ignored."""
    payload = encode_tkms(hole_mesh.mesh.vertices, hole_mesh.mesh.indices)
    flags = struct.unpack_from("<H", payload, 6)[0]
    patched = _patch(payload, 6, struct.pack("<H", flags | 0b1010_1010_1010_1010 & ~0b1))

    decoded = decode_tkms(patched)
    original = decode_tkms(payload)
    assert np.array_equal(decoded.vertices, original.vertices)
    assert np.array_equal(decoded.indices, original.indices)
    assert decoded.flags != original.flags, "the raw flags are surfaced, just not acted on"


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf")])
def test_decode_rejects_non_finite_coordinates(hole_mesh: MeshCase, bad_value: float) -> None:
    payload = encode_tkms(hole_mesh.mesh.vertices, hole_mesh.mesh.indices)
    with pytest.raises(MeshFormatError, match="NaN or infinity"):
        decode_tkms(_patch(payload, HEADER_SIZE, struct.pack("<f", bad_value)))


def test_decode_rejects_index_count_not_a_multiple_of_three(hole_mesh: MeshCase) -> None:
    payload = encode_tkms(hole_mesh.mesh.vertices, hole_mesh.mesh.indices)
    index_count = struct.unpack_from("<I", payload, 12)[0]
    with pytest.raises(MeshFormatError, match="not a multiple of 3"):
        decode_tkms(_patch(payload, 12, struct.pack("<I", index_count - 1)))


def test_decode_rejects_an_index_past_the_vertex_count(hole_mesh: MeshCase) -> None:
    payload = encode_tkms(hole_mesh.mesh.vertices, hole_mesh.mesh.indices)
    decoded = decode_tkms(payload)
    first_index_offset = HEADER_SIZE + decoded.vertex_count * 2 * 4
    patched = _patch(payload, first_index_offset, struct.pack("<H", decoded.vertex_count))
    with pytest.raises(MeshFormatError, match="out of range"):
        decode_tkms(patched)


def test_decode_rejects_empty_geometry() -> None:
    with pytest.raises(MeshFormatError, match="at least one triangle"):
        decode_tkms(_header_only(index_count=0))


def test_decode_rejects_uint16_indices_above_the_vertex_limit() -> None:
    """vertexCount > 65535 without flag bit0 is invalid, and is caught from the header alone."""
    with pytest.raises(MeshFormatError, match="uint32 index flag is not set"):
        decode_tkms(_header_only(vertex_count=UINT16_MAX_VERTEX_COUNT + 1, flags=0))
