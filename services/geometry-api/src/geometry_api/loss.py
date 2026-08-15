"""The one place that decides whether something was lost.

This module exists because the same bug has now been found three times, each time wearing a
different hat:

1. Phase 1 — a loss path with no counter: triangles were removed after their part had already
   been recorded as present, so a part could be emptied while every counter still read zero.
2. Phase 2, first review — the top-level ``lossy`` meant "triangulation lost something", so a
   level where simplification had dropped 20 parts and normalization 7 islets still reported
   ``lossy: false``.
3. Phase 2, second review — ``collect_loss`` asked ``SimplifyResult.loss.is_lossy`` instead of
   looking at the parts that had actually been dropped, and believed an upstream block that
   wrote ``{"droppedParts": 7, "lossy": false}``.

Every one of them is the same mistake: asking a *cause counter* or a *boolean someone
maintained by hand* instead of asking the records of what was actually lost. So there is now
exactly one derivation, here, and every caller uses it. A field named ``lossy`` arriving from
anywhere — a stage, a manifest, an upstream JSON file — is data to be checked, never an answer
to be trusted; if it disagrees with the records, the records win.

A "record" is any entry whose key names something lost: a count, an area, or a list of details.
That is what ``LOSS_KEY_PREFIXES`` recognises, and it is why adding a fourth loss source needs
no change here — name its field ``droppedX`` / ``skippedX`` and the flag follows on its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

LOSS_KEY_PREFIXES: tuple[str, ...] = ("dropped", "skipped", "lost", "removed", "degenerate")
"""Key prefixes that mark a field as a record of something lost.

Deliberately a naming convention rather than a list of known fields: a list would have to be
updated by the same person who forgets to update the flag. Fields describing what *changed*
without being lost — ``topologyChanges``, ``partCount`` — do not match, which is the point.
"""


def loss_records(block: Mapping[str, Any] | None) -> dict[str, Any]:
    """The entries of one loss block that record something lost, booleans excluded."""
    if not block:
        return {}
    return {
        key: value
        for key, value in block.items()
        if key.startswith(LOSS_KEY_PREFIXES) and not isinstance(value, bool)
    }


def records_show_loss(block: Mapping[str, Any] | None) -> bool:
    """True when this block's records say something was lost.

    A non-zero count or area, or a non-empty list of details, is loss. A negative count is
    nonsense but it is still a claim that something happened, so it reads as loss here and is
    rejected as malformed by ``scripts/check_lod_report.py`` — silently treating it as "nothing
    lost" is exactly the failure this module exists to prevent.
    """
    for value in loss_records(block).values():
        if isinstance(value, (int, float)):
            if value != 0:
                return True
        elif isinstance(value, (str, list, tuple, dict, set)):
            if len(value) > 0:
                return True
        elif value is not None:
            return True
    return False


def derive_lossy(*blocks: Mapping[str, Any] | None) -> bool:
    """Whether any of these loss blocks lost anything. The only way to answer that question."""
    return any(records_show_loss(block) for block in blocks)
