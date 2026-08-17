"""TKMB v1 codec: round-trip, TOC always id-sorted regardless of request order (Z9), missing ids
carried in the container (X14), offset/length overflow rejected explicitly (Z13)."""

from __future__ import annotations

import pytest

from geometry_api import tkmb
from geometry_api.tkmb import TkmbError, decode_tkmb, encode_tkmb


def test_round_trip_identity() -> None:
    entries = {"34": b"mesh-for-34", "06": b"mesh-for-06-longer"}

    encoded = encode_tkmb(entries, missing_ids=[], entry_encoding="identity")
    decoded = decode_tkmb(encoded)

    assert decoded.entry_encoding == "identity"
    assert decoded.entries == entries
    assert decoded.missing_ids == []


def test_round_trip_gzip_flag() -> None:
    entries = {"06": b"already-gzipped-bytes"}

    encoded = encode_tkmb(entries, missing_ids=[], entry_encoding="gzip")
    decoded = decode_tkmb(encoded)

    assert decoded.entry_encoding == "gzip"
    assert decoded.entries == entries


def test_missing_ids_are_carried_in_the_container_not_a_header() -> None:
    encoded = encode_tkmb({"06": b"x"}, missing_ids=["99", "42"], entry_encoding="identity")

    decoded = decode_tkmb(encoded)

    assert decoded.missing_ids == ["42", "99"], "sorted, and present without any header at all"


def test_all_missing_still_produces_a_valid_container() -> None:
    encoded = encode_tkmb({}, missing_ids=["06", "34"], entry_encoding="identity")

    decoded = decode_tkmb(encoded)

    assert decoded.entries == {}
    assert decoded.missing_ids == ["06", "34"]


def test_duplicate_requested_ids_collapse_to_one_toc_entry() -> None:
    """encode_tkmb's input is a mapping, so a caller that deduplicates before building it (as
    routes/batch.py must) cannot produce a duplicate TOC entry even if the original request
    listed the same id twice."""
    encoded = encode_tkmb({"06": b"only-once"}, missing_ids=[], entry_encoding="identity")
    decoded = decode_tkmb(encoded)
    assert list(decoded.entries) == ["06"]


@pytest.mark.parametrize(
    "order",
    [["06", "34", "99"], ["99", "06", "34"], ["34", "99", "06"]],
)
def test_toc_order_is_always_id_ascending_regardless_of_insertion_order(order: list[str]) -> None:
    """The whole point of Z9: [A,B] and [B,A] must produce byte-identical containers."""
    entries = {territory_id: f"mesh-{territory_id}".encode() for territory_id in order}

    encoded = encode_tkmb(entries, missing_ids=[], entry_encoding="identity")

    canonical = encode_tkmb(
        {"06": entries["06"], "34": entries["34"], "99": entries["99"]},
        missing_ids=[],
        entry_encoding="identity",
    )
    assert encoded == canonical, "dict insertion order must not affect the encoded bytes"

    decoded = decode_tkmb(encoded)
    assert list(decoded.entries) == ["06", "34", "99"]


def test_offset_overflow_is_rejected_not_silently_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real 4 GiB payload is impractical to build in a test — the ceiling itself is shrunk
    instead, so the same guard code path is exercised cheaply."""
    monkeypatch.setattr(tkmb, "UINT32_MAX", 10)

    with pytest.raises(TkmbError, match="ceiling"):
        encode_tkmb({"06": b"0123456789ABCDEF"}, missing_ids=[], entry_encoding="identity")


def test_bad_magic_is_rejected() -> None:
    with pytest.raises(TkmbError, match="magic"):
        decode_tkmb(b"NOPE" + b"\x00" * 12)


def test_truncated_payload_is_rejected() -> None:
    with pytest.raises(TkmbError, match="header"):
        decode_tkmb(b"TKMB\x01\x00")


def test_unknown_version_is_rejected() -> None:
    encoded = bytearray(encode_tkmb({"06": b"x"}, missing_ids=[], entry_encoding="identity"))
    encoded[4:6] = (99).to_bytes(2, "little")

    with pytest.raises(TkmbError, match="version"):
        decode_tkmb(bytes(encoded))


def test_unknown_entry_encoding_is_rejected_at_encode_time() -> None:
    with pytest.raises(TkmbError, match="entryEncoding"):
        encode_tkmb({"06": b"x"}, missing_ids=[], entry_encoding="brotli")
