"""Conditional/negotiated request header parsing used by ``routes/mesh.py``: ``If-None-Match``
weak comparison (RFC 9110 §8.8.3.2) and ``Accept-Encoding`` quality-value parsing (§12.5.3).
"""

from __future__ import annotations


def _opaque(etag: str) -> str:
    value = etag.strip()
    return value[2:] if value.startswith("W/") else value


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """True if ``etag`` (the server's current strong tag) satisfies ``if_none_match``.

    The server always emits **strong** ETags (§11.1) — computed at publish time from exact
    bytes, no ``W/`` prefix. Comparison is still done with the *weak* function, because RFC 9110
    requires a GET conditional request to use it regardless of how the server's own tags are
    tagged: multiple comma-separated values, whitespace, a leading ``W/`` on the client's copy,
    and ``*`` all have to be handled the same way here as a compliant cache would.
    """
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    target = _opaque(etag)
    return any(_opaque(candidate) == target for candidate in if_none_match.split(","))


def accepts_gzip(accept_encoding: str) -> bool:
    """RFC 9110 §12.5.3: ``gzip`` is acceptable unless a quality value says otherwise.

    A naive ``"gzip" in accept_encoding`` treats ``gzip;q=0`` — the syntax the spec defines for
    *rejecting* a coding — as acceptance, because the substring ``"gzip"`` is still present. This
    parses codings and their weights properly: an explicit ``gzip`` entry (any quality) wins over
    a ``*`` entry; a q-value of exactly ``0`` means "not acceptable", any other value (including
    a malformed one, treated as the default ``1``) means acceptable.
    """
    if not accept_encoding:
        return False
    gzip_q: float | None = None
    star_q: float | None = None
    for raw_item in accept_encoding.split(","):
        item = raw_item.strip()
        if not item:
            continue
        coding, *params = item.split(";")
        coding = coding.strip().lower()
        quality = 1.0
        for param in params:
            key, _, value = param.strip().partition("=")
            if key.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 1.0
        if coding == "gzip":
            gzip_q = quality
        elif coding == "*":
            star_q = quality
    if gzip_q is not None:
        return gzip_q > 0
    if star_q is not None:
        return star_q > 0
    return False
