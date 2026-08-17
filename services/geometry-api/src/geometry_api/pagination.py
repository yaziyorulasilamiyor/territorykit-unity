"""Cursor pagination shared by ``territories`` and ``viewport`` (FAZ-3-PLAN.md §4).

**The cursor carries the filters it was issued under, not just a position** (§4.2/X8): the
opaque token encodes ``revisionId``, ``lod`` and every active filter alongside the scan position,
and the next request must present the identical set or the cursor is refused
(:class:`CursorFilterMismatchError`) rather than silently continuing under different filters.

**The position is the last item *scanned*, not the last item *returned*** (§4.2/Z14): a page that
matched nothing before hitting the scan cap still advances, because the field records where
scanning stopped, not where matches were found. Without this, an unlucky filter could re-scan the
same rejected range forever.

**Complexity, stated precisely, not oversold** (§4.3/X9): the cursor's start position is found by
binary search over the id-sorted entry list — O(log n). Filling a page from there is not: a
selective filter can require scanning arbitrarily far past the start to collect ``limit``
matches, worst case O(n). ``scan_cap`` bounds that per request; hitting it yields a page with
``scan_truncated: True`` and a valid ``next_cursor`` rather than an unbounded scan.
"""

from __future__ import annotations

import base64
import bisect
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any


class CursorError(Exception):
    """The cursor could not be decoded at all — malformed, not merely stale."""


class CursorFilterMismatchError(Exception):
    """The cursor is well-formed but was issued under different filters or a different revision."""


@dataclass(frozen=True)
class CursorState:
    revision_id: str
    lod: str
    last_scanned_id: str
    filters: dict[str, Any]


@dataclass(frozen=True)
class PageResult:
    items: list[dict[str, Any]]
    next_cursor: str | None
    scan_truncated: bool


def encode_cursor(revision_id: str, lod: str, last_scanned_id: str, filters: dict[str, Any]) -> str:
    payload = {
        "revisionId": revision_id,
        "lod": lod,
        "lastScannedId": last_scanned_id,
        "filters": filters,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> CursorState:
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding)
        payload = json.loads(raw)
        return CursorState(
            revision_id=str(payload["revisionId"]),
            lod=str(payload["lod"]),
            last_scanned_id=str(payload["lastScannedId"]),
            filters=dict(payload["filters"]),
        )
    except Exception as exc:
        raise CursorError(f"cursor is not a valid page token: {exc}") from exc


def paginate(
    entries: Sequence[dict[str, Any]],
    *,
    predicate: Callable[[dict[str, Any]], bool],
    cursor: str | None,
    limit: int,
    scan_cap: int,
    revision_id: str,
    lod: str,
    filters: dict[str, Any],
) -> PageResult:
    """``entries`` must already be sorted ascending by ``entries[i]["id"]`` (build.py guarantees
    this — territories are written in sorted order, and Phase 3 does not re-sort them)."""
    start_index = 0
    if cursor is not None:
        state = decode_cursor(cursor)
        if (state.revision_id, state.lod, state.filters) != (revision_id, lod, filters):
            raise CursorFilterMismatchError(
                "cursor was issued under a different revision, lod or filter set"
            )
        start_index = bisect.bisect_right(entries, state.last_scanned_id, key=lambda e: e["id"])

    matches: list[dict[str, Any]] = []
    scanned = 0
    index = start_index
    scan_truncated = False
    last_scanned_id: str | None = None

    while index < len(entries) and len(matches) < limit:
        if scanned >= scan_cap:
            scan_truncated = True
            break
        entry = entries[index]
        scanned += 1
        last_scanned_id = entry["id"]
        if predicate(entry):
            matches.append(entry)
        index += 1

    next_cursor = None
    if index < len(entries):
        assert last_scanned_id is not None  # at least one item was examined to reach this branch
        next_cursor = encode_cursor(revision_id, lod, last_scanned_id, filters)

    return PageResult(items=matches, next_cursor=next_cursor, scan_truncated=scan_truncated)
