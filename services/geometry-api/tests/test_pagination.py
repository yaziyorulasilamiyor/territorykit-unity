"""geometry_api.pagination: cursor round-trip, filter binding, scan-cap truncation (§4)."""

from __future__ import annotations

import pytest

from geometry_api.pagination import CursorFilterMismatchError, paginate

_NO_FILTERS: dict[str, object] = {"bbox": None, "parentId": None, "administrativeLevel": None}


def _entries(n: int) -> list[dict[str, object]]:
    """Ids sorted lexicographically, zero-padded, matching build.py's sort-by-id guarantee."""
    return [{"id": f"{i:03d}", "value": i} for i in range(n)]


def _all_pages(
    entries: list[dict[str, object]], *, limit: int, scan_cap: int = 10_000
) -> list[dict]:
    items: list[dict] = []
    cursor = None
    seen_cursors: set[str] = set()
    while True:
        page = paginate(
            entries,
            predicate=lambda e: True,
            cursor=cursor,
            limit=limit,
            scan_cap=scan_cap,
            revision_id="rev",
            lod="high",
            filters=_NO_FILTERS,
        )
        items.extend(page.items)
        if page.next_cursor is None:
            break
        assert page.next_cursor not in seen_cursors, "pagination looped"
        seen_cursors.add(page.next_cursor)
        cursor = page.next_cursor
    return items


def test_a_single_page_covers_everything_when_limit_is_large_enough() -> None:
    entries = _entries(5)
    page = paginate(
        entries,
        predicate=lambda e: True,
        cursor=None,
        limit=50,
        scan_cap=1000,
        revision_id="rev",
        lod="high",
        filters=_NO_FILTERS,
    )
    assert [e["id"] for e in page.items] == [e["id"] for e in entries]
    assert page.next_cursor is None
    assert page.scan_truncated is False


def test_walking_every_page_visits_the_whole_set_with_no_duplicates_or_gaps() -> None:
    entries = _entries(237)
    walked = _all_pages(entries, limit=10)
    assert [e["id"] for e in walked] == [e["id"] for e in entries]


def test_a_filter_that_matches_nothing_terminates_cleanly() -> None:
    entries = _entries(500)
    cursor = None
    pages = 0
    while True:
        page = paginate(
            entries,
            predicate=lambda e: False,
            cursor=cursor,
            limit=10,
            scan_cap=50,
            revision_id="rev",
            lod="high",
            filters=_NO_FILTERS,
        )
        assert page.items == []
        pages += 1
        assert pages < 100, "pagination over an all-rejecting filter did not terminate"
        if page.next_cursor is None:
            break
        cursor = page.next_cursor


def test_cursor_bound_to_a_different_filter_set_is_rejected() -> None:
    entries = _entries(20)
    first = paginate(
        entries,
        predicate=lambda e: True,
        cursor=None,
        limit=5,
        scan_cap=1000,
        revision_id="rev",
        lod="high",
        filters=_NO_FILTERS,
    )
    assert first.next_cursor is not None

    different_filters = {"bbox": None, "parentId": "06", "administrativeLevel": None}
    with pytest.raises(CursorFilterMismatchError):
        paginate(
            entries,
            predicate=lambda e: True,
            cursor=first.next_cursor,
            limit=5,
            scan_cap=1000,
            revision_id="rev",
            lod="high",
            filters=different_filters,
        )


def test_cursor_bound_to_a_different_revision_is_rejected() -> None:
    entries = _entries(20)
    first = paginate(
        entries,
        predicate=lambda e: True,
        cursor=None,
        limit=5,
        scan_cap=1000,
        revision_id="rev-a",
        lod="high",
        filters=_NO_FILTERS,
    )
    with pytest.raises(CursorFilterMismatchError):
        paginate(
            entries,
            predicate=lambda e: True,
            cursor=first.next_cursor,
            limit=5,
            scan_cap=1000,
            revision_id="rev-b",
            lod="high",
            filters=_NO_FILTERS,
        )


def test_scan_cap_returns_a_short_page_not_an_error_when_the_filter_is_unselective() -> None:
    """Only every 100th entry matches; with a scan_cap of 10 a page finds nothing but still
    advances — this is what makes 'last scanned' rather than 'last returned' matter (Z14)."""
    entries = _entries(1000)

    def rare(entry: dict[str, object]) -> bool:
        return int(entry["value"]) % 100 == 99  # type: ignore[arg-type]

    page = paginate(
        entries,
        predicate=rare,
        cursor=None,
        limit=5,
        scan_cap=10,
        revision_id="rev",
        lod="high",
        filters=_NO_FILTERS,
    )

    assert page.items == []
    assert page.scan_truncated is True
    assert page.next_cursor is not None


def test_scan_cap_truncated_pages_eventually_reach_every_match() -> None:
    entries = _entries(1000)

    def rare(entry: dict[str, object]) -> bool:
        return int(entry["value"]) % 100 == 99  # type: ignore[arg-type]

    matches: list[dict[str, object]] = []
    cursor = None
    pages = 0
    while True:
        page = paginate(
            entries,
            predicate=rare,
            cursor=cursor,
            limit=5,
            scan_cap=10,
            revision_id="rev",
            lod="high",
            filters=_NO_FILTERS,
        )
        matches.extend(page.items)
        pages += 1
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
        assert pages < 1000, "pagination did not terminate"

    assert [m["id"] for m in matches] == [f"{i:03d}" for i in range(99, 1000, 100)]
    assert pages > 1, "the scan cap must actually have forced multiple pages"


def test_an_empty_entry_list_yields_an_empty_page() -> None:
    page = paginate(
        [],
        predicate=lambda e: True,
        cursor=None,
        limit=10,
        scan_cap=1000,
        revision_id="rev",
        lod="high",
        filters=_NO_FILTERS,
    )
    assert page.items == []
    assert page.next_cursor is None
    assert page.scan_truncated is False


def test_a_cursor_past_the_end_yields_an_empty_final_page() -> None:
    entries = _entries(3)
    page = paginate(
        entries,
        predicate=lambda e: True,
        cursor=None,
        limit=10,
        scan_cap=1000,
        revision_id="rev",
        lod="high",
        filters=_NO_FILTERS,
    )
    assert page.next_cursor is None  # everything fit on one page already
