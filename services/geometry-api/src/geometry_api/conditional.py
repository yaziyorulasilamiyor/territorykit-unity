"""``If-None-Match`` weak comparison (RFC 9110 §8.8.3.2), used by ``routes/mesh.py`` (§11.3).

The server always emits **strong** ETags (§11.1) — computed at publish time from exact bytes, no
``W/`` prefix. Comparison is still done with the *weak* function, because RFC 9110 requires a GET
conditional request to use it regardless of how the server's own tags are tagged: multiple
comma-separated values, whitespace, a leading ``W/`` on the client's copy, and ``*`` all have to
be handled the same way here as a compliant cache would.
"""

from __future__ import annotations


def _opaque(etag: str) -> str:
    value = etag.strip()
    return value[2:] if value.startswith("W/") else value


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """True if ``etag`` (the server's current strong tag) satisfies ``if_none_match``."""
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    target = _opaque(etag)
    return any(_opaque(candidate) == target for candidate in if_none_match.split(","))
