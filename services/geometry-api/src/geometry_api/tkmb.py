"""TKMB v1 — the mesh batch container (``docs/mesh-format.md`` §TKMB, FAZ-3-PLAN.md §10.2).

::

    Header — 16 bytes, little-endian:
      0   char[4]   magic = "TKMB"
      4   uint16    version = 1
      6   uint16    flags        (bit0: 1 = entries are gzip TKMS, 0 = identity TKMS)
      8   uint32    foundCount
      12  uint32    missingCount

    TOC — foundCount records, territoryId ascending:
      uint16 idLength, char[idLength] territoryId (UTF-8), uint32 offset, uint32 length

    Missing — missingCount records, territoryId ascending:
      uint16 idLength, char[idLength] territoryId

    Payload — TOC order (i.e. id-ascending): each territory's TKMS bytes, gzip'd if flags bit0=1

``offset`` is relative to the start of the *payload* section (i.e. the first byte after the
missing section), never the start of the file — a decoder adds the header+TOC+missing length to
find the real position.

**TOC order is always id-ascending, never request order (§10.2, Z9).** The batch cache key
(``cache.py``) already ignores the order territoryIds were requested in — ``[A,B]`` and ``[B,A]``
hash to the same key — so the bytes they produce must also be identical, or the same cache entry
would represent two different responses depending on who asked first.

**Missing ids live in the container, not a header (§10.2, X14).** A territory that was requested
but not found is recorded here, not in an ``X-TKMB-Missing-Ids`` response header a proxy could
drop — the container is self-describing.

**``entryEncoding`` is not HTTP content negotiation (§10.1).** Whether entries are individually
gzip'd is a batch-format concept carried in ``flags``, decided by the request body, not derived
from the ``Accept-Encoding`` header — see ``routes/batch.py``.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

MAGIC = b"TKMB"
VERSION = 1
HEADER_SIZE = 16

ENTRY_ENCODING_IDENTITY = "identity"
ENTRY_ENCODING_GZIP = "gzip"
_ENTRY_ENCODINGS = (ENTRY_ENCODING_IDENTITY, ENTRY_ENCODING_GZIP)

UINT32_MAX = 2**32 - 1
"""Ceiling for both ``offset`` and ``length``. Exposed (not inlined) so a test can shrink it
rather than needing to actually build a multi-gigabyte payload to exercise the overflow path."""


class TkmbError(ValueError):
    """Raised on encode when the payload cannot fit the format, or on decode when the bytes are
    not a well-formed TKMB container."""


@dataclass(frozen=True)
class TkmbContainer:
    entry_encoding: str
    entries: dict[str, bytes]
    """territoryId -> its TKMS bytes, exactly as stored (still gzip'd if entry_encoding is gzip)."""
    missing_ids: list[str]
    """Ascending. Ids that were requested but had nothing to put in ``entries``."""


def encode_tkmb(
    entries: Mapping[str, bytes], missing_ids: Iterable[str], entry_encoding: str
) -> bytes:
    if entry_encoding not in _ENTRY_ENCODINGS:
        raise TkmbError(
            f"unknown entryEncoding {entry_encoding!r}; expected one of {_ENTRY_ENCODINGS}"
        )

    sorted_ids = sorted(entries)
    sorted_missing = sorted(set(missing_ids))

    payload = bytearray()
    toc = bytearray()
    for territory_id in sorted_ids:
        data = entries[territory_id]
        offset = len(payload)
        length = len(data)
        if offset > UINT32_MAX or length > UINT32_MAX or offset + length > UINT32_MAX:
            raise TkmbError(
                f"batch payload exceeds the {UINT32_MAX} byte TKMB offset/length ceiling at "
                f"territory {territory_id!r}; split the request into a smaller batch"
            )
        id_bytes = territory_id.encode("utf-8")
        toc += struct.pack("<H", len(id_bytes)) + id_bytes + struct.pack("<II", offset, length)
        payload += data

    missing_section = bytearray()
    for territory_id in sorted_missing:
        id_bytes = territory_id.encode("utf-8")
        missing_section += struct.pack("<H", len(id_bytes)) + id_bytes

    flags = 1 if entry_encoding == ENTRY_ENCODING_GZIP else 0
    header = MAGIC + struct.pack("<HHII", VERSION, flags, len(sorted_ids), len(sorted_missing))

    return bytes(header + toc + missing_section + payload)


def decode_tkmb(data: bytes) -> TkmbContainer:
    if len(data) < HEADER_SIZE:
        raise TkmbError(f"payload is {len(data)} bytes, shorter than the {HEADER_SIZE}-byte header")
    if data[0:4] != MAGIC:
        raise TkmbError(f"bad magic {data[0:4]!r}, expected {MAGIC!r}")
    version, flags, found_count, missing_count = struct.unpack_from("<HHII", data, 4)
    if version != VERSION:
        raise TkmbError(f"unsupported TKMB version {version}, this reader understands {VERSION}")
    entry_encoding = ENTRY_ENCODING_GZIP if flags & 0b1 else ENTRY_ENCODING_IDENTITY

    offset = HEADER_SIZE
    toc_records: list[tuple[str, int, int]] = []
    for _ in range(found_count):
        territory_id, offset = _read_id(data, offset)
        if offset + 8 > len(data):
            raise TkmbError("TOC truncated: missing offset/length")
        entry_offset, entry_length = struct.unpack_from("<II", data, offset)
        offset += 8
        toc_records.append((territory_id, entry_offset, entry_length))

    missing_ids: list[str] = []
    for _ in range(missing_count):
        territory_id, offset = _read_id(data, offset)
        missing_ids.append(territory_id)

    payload_start = offset
    entries: dict[str, bytes] = {}
    for territory_id, entry_offset, entry_length in toc_records:
        start = payload_start + entry_offset
        end = start + entry_length
        if entry_offset < 0 or entry_length < 0 or end > len(data):
            raise TkmbError(f"entry {territory_id!r}: offset/length out of range")
        entries[territory_id] = data[start:end]

    return TkmbContainer(entry_encoding=entry_encoding, entries=entries, missing_ids=missing_ids)


def _read_id(data: bytes, offset: int) -> tuple[str, int]:
    if offset + 2 > len(data):
        raise TkmbError("truncated: missing id length")
    (id_length,) = struct.unpack_from("<H", data, offset)
    offset += 2
    if offset + id_length > len(data):
        raise TkmbError("truncated: id shorter than declared")
    territory_id = data[offset : offset + id_length].decode("utf-8")
    return territory_id, offset + id_length
