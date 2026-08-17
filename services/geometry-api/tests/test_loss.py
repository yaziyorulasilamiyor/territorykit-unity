"""The loss schema itself: what it accepts, what it refuses, and how the flag is derived.

Phase 2's review rounds each found a different way for geometry to disappear while the build
reported ``lossy: false``. The first three were missing counters and trusted booleans. The
fourth was the *mechanism* that was supposed to make new counters unnecessary — a naming
convention, where any field whose key started with ``dropped``/``skipped``/``lost``/``removed``/
``degenerate`` counted as a record of loss.

The failure mode of a naming convention is that an unrecognised name means "nothing happened".
These tests pin the counterexamples that proved it and the behaviour that replaces it: a closed
set of kinds, and an error — never a shrug — for anything outside it.
"""

from __future__ import annotations

import pytest

from geometry_api.loss import (
    CATEGORY_CHANGE,
    CATEGORY_LOSS,
    EVENT_KINDS,
    SCHEMA_VERSION,
    SIDE_ADDED,
    SIDE_NEUTRAL,
    SIDE_REMOVED,
    STAGE_SIMPLIFICATION,
    STAGE_TRIANGULATION,
    STAGE_UPSTREAM,
    STAGES,
    LossLedger,
    LossSchemaError,
    event,
    kind_of,
    ledger_from_manifest,
)

# --------------------------------------------------------------------------------------------
# Fail-closed. The property the naming convention did not have.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [
        "removedRings",
        "collapsedParts",
        "discarded_holes",
        "droppedParts",
        "vanished_provinces",
        "",
    ],
)
def test_a_kind_outside_the_schema_raises_instead_of_being_ignored(kind: str) -> None:
    """The fourth review round's counterexamples, all six of them now errors.

    Under the prefix convention ``removedRings`` set the flag while ``collapsedParts`` and
    ``discarded_holes`` did not — the first matched a prefix, the other two described the same
    kind of event in words nobody had listed. CI accepted all three. ``droppedParts`` is here
    too: even the *correctly* named field is refused now, because the schema is a set of kinds
    and not a set of spellings.
    """
    with pytest.raises(LossSchemaError, match="unknown loss event kind"):
        event(STAGE_SIMPLIFICATION, kind, count=1)


def test_a_kind_outside_the_schema_cannot_be_read_back_either() -> None:
    """The write side and the read side fail the same way, or a hand-edited report gets through."""
    block = {
        "schemaVersion": SCHEMA_VERSION,
        "stagesRecorded": list(STAGES),
        "events": [{"stage": STAGE_SIMPLIFICATION, "kind": "collapsedParts", "count": 4}],
    }
    with pytest.raises(LossSchemaError, match="unknown loss event kind"):
        ledger_from_manifest(block)


def test_an_unknown_stage_raises() -> None:
    with pytest.raises(LossSchemaError, match="unknown stage"):
        event("encoding", "dropped_part", count=1, details=["x"])


def test_a_negative_count_raises_rather_than_cancelling_a_real_loss() -> None:
    """``droppedParts: -7`` used to read as "something happened" and subtract from every total."""
    with pytest.raises(LossSchemaError, match="not a smaller loss"):
        event(STAGE_SIMPLIFICATION, "dropped_part", count=-7, details=["a"])


def test_a_count_with_no_detail_behind_it_raises_for_kinds_that_need_one() -> None:
    """Seven of what, in which province, how big? A bare count of these cannot be reviewed."""
    with pytest.raises(LossSchemaError, match="cannot be reviewed"):
        event(STAGE_UPSTREAM, "dropped_islet", count=7)

    # And the kinds where a count really is the whole story are still allowed to be bare.
    assert event(STAGE_TRIANGULATION, "degenerate_triangle", count=12).details == ()


def test_an_older_schema_version_is_refused_rather_than_guessed_at() -> None:
    """Version 1 was the prefix block. A reader that guesses is a reader that misreads."""
    with pytest.raises(LossSchemaError, match="refusing to guess"):
        ledger_from_manifest({"schemaVersion": 1, "events": [], "stagesRecorded": []})


def test_a_block_without_stagesrecorded_is_refused() -> None:
    """ "Nothing lost upstream" and "nobody asked upstream" are different reports."""
    with pytest.raises(LossSchemaError, match="stagesRecorded"):
        ledger_from_manifest({"schemaVersion": SCHEMA_VERSION, "events": []})


# --------------------------------------------------------------------------------------------
# The schema is internally coherent, so the budget arithmetic means something.
# --------------------------------------------------------------------------------------------


def test_every_kind_declares_a_known_category_and_side() -> None:
    for name, kind in EVENT_KINDS.items():
        assert kind.name == name, "the mapping key and the kind must agree"
        assert kind.category in (CATEGORY_LOSS, CATEGORY_CHANGE)
        assert kind.side in (SIDE_REMOVED, SIDE_ADDED, SIDE_NEUTRAL)
        assert kind.what.strip(), f"{name} has no description for whoever reads the manifest"


def test_the_kinds_a_client_must_never_see_silently_are_all_losses() -> None:
    """A guard on the schema, not on the code: these five mean geometry is missing.

    If a future edit recategorised one of them as a ``change``, the ``lossy`` flag would stop
    covering it and no other test would notice — the flag would still be correctly derived, just
    from a schema that had quietly changed its mind.
    """
    for name in (
        "dropped_part",
        "dropped_hole",
        "dropped_islet",
        "dropped_source_hole",
        "skipped_part",
    ):
        assert kind_of(name).category == CATEGORY_LOSS, name


def test_merges_and_boundary_movement_are_changes_not_losses() -> None:
    """The FAZ 2 exception, pinned in the schema: merging two islands loses no ground."""
    for name in (
        "part_merge",
        "part_split",
        "part_created",
        "boundary_retreat",
        "artifact_hole_removed",
    ):
        assert kind_of(name).category == CATEGORY_CHANGE, name


# --------------------------------------------------------------------------------------------
# The one derivation.
# --------------------------------------------------------------------------------------------


def test_the_flag_is_the_loss_events_and_nothing_else() -> None:
    assert LossLedger.of([]).is_lossy is False

    changes_only = LossLedger.of(
        [
            event(STAGE_SIMPLIFICATION, "part_merge", count=19, area=1_000.0),
            event(STAGE_SIMPLIFICATION, "boundary_retreat", count=81, area=4_860_000.0),
        ]
    )
    assert changes_only.is_lossy is False, "a merge is an event; it is not a loss"

    with_loss = changes_only + LossLedger.of(
        [event(STAGE_SIMPLIFICATION, "dropped_part", count=1, area=684.6, details=["Artvin"])]
    )
    assert with_loss.is_lossy is True


def test_a_lossy_boolean_in_the_serialised_block_is_never_read_back() -> None:
    """The third review round's bug, now impossible to express: the flag is not an input."""
    lying = {
        "schemaVersion": SCHEMA_VERSION,
        "stagesRecorded": [STAGE_UPSTREAM],
        "lossy": False,
        "events": [
            {
                "stage": STAGE_UPSTREAM,
                "kind": "dropped_islet",
                "count": 7,
                "details": [f"islet {index}" for index in range(7)],
            }
        ],
    }
    assert ledger_from_manifest(lying).is_lossy is True

    overstated = {
        "schemaVersion": SCHEMA_VERSION,
        "stagesRecorded": [STAGE_UPSTREAM],
        "lossy": True,
        "events": [],
    }
    assert ledger_from_manifest(overstated).is_lossy is False, (
        "a boolean with no events behind it is not evidence either; "
        "scripts/check_lod_report.py is what fails the producer for lying"
    )


def test_the_sides_add_up_the_way_the_budget_expects() -> None:
    ledger = LossLedger.of(
        [
            event(STAGE_SIMPLIFICATION, "boundary_retreat", count=1, area=100.0),
            event(STAGE_SIMPLIFICATION, "boundary_advance", count=1, area=250.0),
            event(STAGE_SIMPLIFICATION, "dropped_part", count=1, area=7.0, details=["a"]),
            event(STAGE_SIMPLIFICATION, "dropped_hole", count=1, area=3.0, details=["h"]),
            # Neutral: its square metres are already inside a part's residual.
            event(STAGE_SIMPLIFICATION, "artifact_hole_removed", count=2, area=999.0),
        ]
    )
    assert ledger.removed_area == pytest.approx(107.0)
    assert ledger.added_area == pytest.approx(253.0)


def test_events_of_the_same_kind_collapse_into_one_line_per_stage() -> None:
    """Producers emit per territory; a manifest wants one line per kind."""
    ledger = LossLedger.of(
        [
            event(STAGE_SIMPLIFICATION, "dropped_part", count=1, area=10.0, details=["a"]),
            event(STAGE_SIMPLIFICATION, "dropped_part", count=2, area=5.0, details=["b", "c"]),
        ]
    )
    assert len(ledger.events) == 1
    assert ledger.count("dropped_part") == 3
    assert ledger.area("dropped_part") == pytest.approx(15.0)
    assert ledger.events[0].details == ("a", "b", "c")


def test_counting_a_kind_outside_the_schema_raises() -> None:
    """Otherwise a typo in a caller reads as zero, which is the old bug with a new spelling."""
    with pytest.raises(LossSchemaError):
        LossLedger.of([]).count("droppedParts")


def test_a_round_trip_through_the_manifest_preserves_everything() -> None:
    original = LossLedger.of(
        [
            event(STAGE_UPSTREAM, "dropped_islet", count=2, details=["a", "b"]),
            event(STAGE_SIMPLIFICATION, "part_merge", count=30, area=269_842_393.0),
            event(STAGE_TRIANGULATION, "degenerate_triangle", count=4),
        ]
    )

    restored = ledger_from_manifest(original.as_manifest_dict())

    assert restored == original
    assert restored.stages_recorded == STAGES
