"""Content-addressed revision ids for published builds (FAZ-3-PLAN.md §3.1).

One function, two callers: ``scripts/publish_dataset.py`` uses it to name a revision directory
after what is inside it, and ``geometry_api.registry`` reuses it as an integrity check — a
revision directory that no longer hashes to its own name has been altered after publishing
(FAZ-3-PLAN.md §3.5a). Deliberately the same computation for both, so "the id is right" and "the
content is intact" are the same claim rather than two schemes that could disagree.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

LOD_LEVELS = ("high", "medium", "low")


def compute_revision_id(root: Path) -> str:
    """Hash every file under ``root/{high,medium,low}`` into one revision id.

    ``root`` must already contain the exact bytes to be published — this is called against a
    staging snapshot *after* copying, gzip generation and etag computation, never against the
    build directory before it (FAZ-3-PLAN.md §3.1, §3.2): hashing the source before copying would
    leave a window where the source could change between the hash and the copy, so the directory
    name and the published bytes could disagree.

    Every record is length-prefixed before being folded in, so the byte stream has no ambiguous
    boundaries — concatenating ``("ab", "c")`` and ``("a", "bc")`` without a length prefix would
    hash identically, which a content-addressing scheme cannot allow. Each file's own content is
    reduced to a 32-byte digest before joining the running hash, so this never holds more than one
    file's bytes in memory at a time regardless of how large a mesh directory gets.
    """
    hasher = hashlib.sha256()
    for lod in LOD_LEVELS:
        level_dir = root / lod
        for path in sorted(level_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix().encode("utf-8")
            content = path.read_bytes()
            hasher.update(len(rel).to_bytes(4, "big"))
            hasher.update(rel)
            hasher.update(len(content).to_bytes(8, "big"))
            hasher.update(hashlib.sha256(content).digest())
    return hasher.hexdigest()
