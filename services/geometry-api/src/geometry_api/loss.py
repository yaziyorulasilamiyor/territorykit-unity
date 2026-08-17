"""The schema every stage records geometry changes against, and the only derivation of ``lossy``.

**Why this is a schema and not a naming convention.** The previous version of this module decided
whether a field recorded a loss by looking at its *name*: any key starting with ``dropped``,
``skipped``, ``lost``, ``removed`` or ``degenerate`` was a loss record. Four review rounds in a
row found a way past that, and the fourth found the convention itself:

* ``removedRings`` set the flag, but ``collapsedParts`` and ``discarded_holes`` did not — both
  describe geometry that is gone, neither matches the prefix list, and CI accepted both.
* The prefix list existed twice, once here and once in ``scripts/check_lod_report.py``, so the
  two could drift and the "independent" CI check would still agree with a build that had gone
  wrong in the same direction.

A convention that fails open cannot be repaired by lengthening it, because the failure is that
an unrecognised name means "nothing happened". So the convention is gone. What replaces it:

* ``EVENT_KINDS`` — a closed set of kinds, each declaring what category it belongs to, which
  side of the area budget it moves, and whether a bare count is reviewable without detail.
* ``LossEvent`` — a typed record. Constructing one with a kind or a stage that is not in the
  schema raises ``LossSchemaError``. **Fail-closed**: an unknown kind is an error, never a
  silently ignored record and never a record that quietly counts as harmless.
* ``LossLedger.is_lossy`` — ``bool`` of the events whose kind is categorised as a loss. No
  boolean is read from anywhere; a ``lossy`` field arriving from a stage, a manifest or an
  upstream JSON file is data to be checked against the events, never an answer to be trusted.

**Two books, on purpose.** Two islands of one province merging into one part loses no ground, so
it must not set ``lossy`` — that is the deliberate exception written down in
``docs/PROJE-TALIMATI.md`` §FAZ 2. It is still a change a client can be surprised by, so it is
still an event, categorised ``change`` rather than ``loss``. ``is_lossy`` is therefore
``bool(self.losses)`` rather than ``bool(self.events)``: the ledger records everything that
happened, and the flag answers the narrower question of whether anything went missing.

**Sides and the area budget.** Every kind says whether the area it accounts for left the region
(``removed``), joined it (``added``), or moved neither way (``neutral``). ``simplify.py`` uses
that to check an identity per level — source area, plus everything recorded as added, minus
everything recorded as removed, has to equal the output area. A code path that changes geometry
without writing an event breaks the identity and fails the build, which is what makes this
accounting closed rather than a list of the loss paths somebody remembered.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 2
"""Bumped when the shape of a serialised event changes.

Version 1 was the prefix-convention block (``{"droppedParts": 7, "lossy": false}``). A reader
that meets a version it does not know must fail rather than guess, which is what
``scripts/check_lod_report.py`` does.
"""

CATEGORY_LOSS = "loss"
"""Geometry the source had and the output does not, in the sense a client would call missing."""

CATEGORY_CHANGE = "change"
"""The shape or component structure differs, but nothing went missing."""

CATEGORIES: tuple[str, ...] = (CATEGORY_LOSS, CATEGORY_CHANGE)

SIDE_REMOVED = "removed"
"""Area the source covered and the output does not."""

SIDE_ADDED = "added"
"""Area the output covers and the source did not."""

SIDE_NEUTRAL = "neutral"
"""Accounted for by another event's area, or carrying no area at all.

A neutral event still has an ``area`` for reporting — an artifact hole's ring area is worth
seeing — but the budget does not add it to either side, because the same square metres are
already inside the residual of the part that contains it. Counting it twice would make the
identity fail on correct builds.
"""

SIDES: tuple[str, ...] = (SIDE_REMOVED, SIDE_ADDED, SIDE_NEUTRAL)

STAGE_UPSTREAM = "upstream"
STAGE_SIMPLIFICATION = "simplification"
STAGE_TRIANGULATION = "triangulation"

STAGES: tuple[str, ...] = (STAGE_UPSTREAM, STAGE_SIMPLIFICATION, STAGE_TRIANGULATION)
"""Every stage that can change geometry, in the order the pipeline runs them.

Listed rather than inferred so a manifest missing a stage is a detectable absence: a level that
records nothing for ``upstream`` is not a level that lost nothing upstream, it is a level whose
build forgot to ask.
"""


class LossSchemaError(ValueError):
    """Raised when a record does not fit the schema.

    Never downgraded to a warning and never swallowed. The whole point of replacing the naming
    convention is that an unrecognised record stops the build instead of reading as "nothing
    happened".
    """


@dataclass(frozen=True)
class EventKind:
    """One thing that can happen to geometry, and everything the accounting needs to know."""

    name: str
    category: str
    side: str
    needs_detail: bool
    what: str
    """One sentence, written for whoever reads the manifest rather than for whoever wrote it."""

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise LossSchemaError(f"kind {self.name!r} has unknown category {self.category!r}")
        if self.side not in SIDES:
            raise LossSchemaError(f"kind {self.name!r} has unknown side {self.side!r}")


def _schema(*kinds: EventKind) -> Mapping[str, EventKind]:
    return {kind.name: kind for kind in kinds}


EVENT_KINDS: Mapping[str, EventKind] = _schema(
    # ---- losses: something the source had is not in the output ----------------------------
    EventKind(
        "dropped_part",
        CATEGORY_LOSS,
        SIDE_REMOVED,
        needs_detail=True,
        what="a source polygon part with no surviving counterpart in the output",
    ),
    EventKind(
        "dropped_hole",
        CATEGORY_LOSS,
        SIDE_ADDED,
        needs_detail=True,
        what="a source interior ring (an enclave) that is gone; the region now covers it, which "
        "is why its area is on the added side even though it is a loss",
    ),
    EventKind(
        "dropped_islet",
        CATEGORY_LOSS,
        SIDE_REMOVED,
        needs_detail=True,
        what="a source part removed before this build, under the importer's ring area floor",
    ),
    EventKind(
        "dropped_source_hole",
        CATEGORY_LOSS,
        SIDE_ADDED,
        needs_detail=True,
        what="a source interior ring removed before this build, under the importer's area floor",
    ),
    EventKind(
        "skipped_part",
        CATEGORY_LOSS,
        SIDE_REMOVED,
        needs_detail=False,
        what="a part that reached triangulation and produced no triangles",
    ),
    EventKind(
        "skipped_ring",
        CATEGORY_LOSS,
        SIDE_ADDED,
        needs_detail=False,
        what="an interior ring triangulation could not use; the mesh covers it",
    ),
    EventKind(
        "degenerate_triangle",
        CATEGORY_LOSS,
        SIDE_NEUTRAL,
        needs_detail=False,
        what="a zero-area triangle dropped from a mesh",
    ),
    # ---- changes: the shape or the component structure differs, nothing is missing ---------
    EventKind(
        "part_merge",
        CATEGORY_CHANGE,
        SIDE_ADDED,
        needs_detail=False,
        what="two or more source parts became one output part; the ground between them is now "
        "drawn as land, and the area is an upper bound on that false land bridge",
    ),
    EventKind(
        "part_split",
        CATEGORY_CHANGE,
        SIDE_REMOVED,
        needs_detail=False,
        what="one source part became several output parts",
    ),
    EventKind(
        "part_created",
        CATEGORY_CHANGE,
        SIDE_ADDED,
        needs_detail=True,
        what="an output part no source part corresponds to",
    ),
    EventKind(
        "artifact_hole_removed",
        CATEGORY_CHANGE,
        SIDE_NEUTRAL,
        needs_detail=False,
        what="an interior ring simplification invented and this pipeline removed again",
    ),
    EventKind(
        "hole_merge",
        CATEGORY_CHANGE,
        SIDE_NEUTRAL,
        needs_detail=False,
        what="two or more source interior rings became one output interior ring",
    ),
    EventKind(
        "hole_split",
        CATEGORY_CHANGE,
        SIDE_NEUTRAL,
        needs_detail=False,
        what="one source interior ring became several output interior rings",
    ),
    EventKind(
        "boundary_retreat",
        CATEGORY_CHANGE,
        SIDE_REMOVED,
        needs_detail=False,
        what="area a surviving part covered in the source and no longer covers",
    ),
    EventKind(
        "boundary_advance",
        CATEGORY_CHANGE,
        SIDE_ADDED,
        needs_detail=False,
        what="area a surviving part covers that the source did not",
    ),
    EventKind(
        "severe_shrink",
        CATEGORY_CHANGE,
        SIDE_NEUTRAL,
        needs_detail=True,
        what="a part that survived but kept only a small fraction of its area; its square metres "
        "are already counted under boundary_retreat, this names the parts worth looking at",
    ),
)
"""The closed set. Adding a loss path means adding a kind here and nowhere else.

Closed on purpose: with a naming convention, forgetting to add a name meant the record was
silently ignored. Here, emitting a kind that is not in this mapping raises.
"""

LOSS_KINDS: tuple[str, ...] = tuple(
    name for name, kind in EVENT_KINDS.items() if kind.category == CATEGORY_LOSS
)
CHANGE_KINDS: tuple[str, ...] = tuple(
    name for name, kind in EVENT_KINDS.items() if kind.category == CATEGORY_CHANGE
)


def kind_of(name: str) -> EventKind:
    """The schema entry for ``name``, or ``LossSchemaError``. The fail-closed lookup."""
    try:
        return EVENT_KINDS[name]
    except KeyError:
        raise LossSchemaError(
            f"unknown loss event kind {name!r}; the schema in geometry_api.loss lists "
            f"{', '.join(sorted(EVENT_KINDS))}. An unrecognised kind is an error, not a record "
            f"to ignore — add it to EVENT_KINDS with its category and its side of the budget."
        ) from None


@dataclass(frozen=True)
class LossEvent:
    """One recorded thing that happened to geometry, at one stage.

    ``count`` is how many of them; ``area`` is the square metres they account for, on the side
    ``kind_of(self.kind).side`` names; ``details`` names them one by one for anyone who has to
    judge whether it mattered.
    """

    stage: str
    kind: str
    count: int
    area: float = 0.0
    details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.stage not in STAGES:
            raise LossSchemaError(
                f"unknown stage {self.stage!r}; the schema lists {', '.join(STAGES)}"
            )
        spec = kind_of(self.kind)
        if self.count < 0:
            raise LossSchemaError(
                f"{self.stage}.{self.kind}: count is {self.count}. A negative count is not a "
                f"smaller loss; it cancels real losses out of any total it is added to."
            )
        if self.area < 0.0:
            raise LossSchemaError(f"{self.stage}.{self.kind}: area is {self.area}")
        if spec.needs_detail and self.count != len(self.details):
            raise LossSchemaError(
                f"{self.stage}.{self.kind}: count is {self.count} but {len(self.details)} "
                f"detail(s) were given. A bare count of this kind cannot be reviewed — how many "
                f"of what, where, how big?"
            )

    @property
    def spec(self) -> EventKind:
        return kind_of(self.kind)

    @property
    def is_loss(self) -> bool:
        return self.spec.category == CATEGORY_LOSS

    @property
    def removed_area(self) -> float:
        return self.area if self.spec.side == SIDE_REMOVED else 0.0

    @property
    def added_area(self) -> float:
        return self.area if self.spec.side == SIDE_ADDED else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "kind": self.kind,
            "category": self.spec.category,
            "side": self.spec.side,
            "count": self.count,
            "area": self.area,
            "details": list(self.details),
        }


def event(
    stage: str,
    kind: str,
    count: int,
    area: float = 0.0,
    details: Iterable[str] = (),
) -> LossEvent:
    """Build one event. Nothing else in the codebase constructs ``LossEvent`` directly."""
    return LossEvent(stage=stage, kind=kind, count=count, area=float(area), details=tuple(details))


def _sort_key(item: LossEvent) -> tuple[int, str]:
    return (STAGES.index(item.stage), item.kind)


@dataclass(frozen=True)
class LossLedger:
    """Every event one build recorded, and the one flag derived from them."""

    events: tuple[LossEvent, ...] = field(default=())
    stages_recorded: tuple[str, ...] = field(default=STAGES)
    """Which stages this build actually asked, as opposed to which ones had something to say.

    An events list cannot distinguish "the upstream stage lost nothing" from "nobody asked the
    upstream stage", and those are very different reports. A build that skipped a stage says so
    here, and ``scripts/check_lod_report.py`` fails a level that is missing one.
    """

    @classmethod
    def of(cls, events: Iterable[LossEvent], stages_recorded: Iterable[str] = STAGES) -> LossLedger:
        """One canonical event per (stage, kind), sorted, with the empty ones dropped.

        Producers emit per territory; a manifest wants one line per kind. Merging here rather
        than at each call site means every ledger in the codebase has the same shape, so
        ``count(kind)`` and the budget sums cannot depend on how finely the caller happened to
        split its records. Sorting keeps a manifest byte-reproducible whatever order the stages
        ran in, which Phase 3's content-addressed cache depends on.
        """
        merged: dict[tuple[str, str], LossEvent] = {}
        for item in events:
            if not (item.count or item.area or item.details):
                continue
            key = (item.stage, item.kind)
            current = merged.get(key)
            merged[key] = (
                item
                if current is None
                else LossEvent(
                    stage=item.stage,
                    kind=item.kind,
                    count=current.count + item.count,
                    area=current.area + item.area,
                    details=current.details + item.details,
                )
            )
        recorded = tuple(stage for stage in STAGES if stage in set(stages_recorded))
        unknown = sorted(set(stages_recorded) - set(STAGES))
        if unknown:
            raise LossSchemaError(f"unknown stage(s) {', '.join(unknown)}")
        return cls(events=tuple(sorted(merged.values(), key=_sort_key)), stages_recorded=recorded)

    def __add__(self, other: LossLedger) -> LossLedger:
        return LossLedger.of(
            [*self.events, *other.events],
            stages_recorded=(*self.stages_recorded, *other.stages_recorded),
        )

    @property
    def losses(self) -> tuple[LossEvent, ...]:
        return tuple(item for item in self.events if item.is_loss)

    @property
    def changes(self) -> tuple[LossEvent, ...]:
        return tuple(item for item in self.events if not item.is_loss)

    @property
    def is_lossy(self) -> bool:
        """``bool`` of the loss events. The only derivation of this flag in the codebase.

        Not ``bool(self.events)``: a merge is an event and not a loss. See the module docstring.
        """
        return bool(self.losses)

    @property
    def removed_area(self) -> float:
        return sum(item.removed_area for item in self.events)

    @property
    def added_area(self) -> float:
        return sum(item.added_area for item in self.events)

    def count(self, kind: str) -> int:
        """How many of one kind, across every stage. Raises on a kind outside the schema."""
        kind_of(kind)
        return sum(item.count for item in self.events if item.kind == kind)

    def area(self, kind: str) -> float:
        kind_of(kind)
        return sum(item.area for item in self.events if item.kind == kind)

    def for_stage(self, stage: str) -> tuple[LossEvent, ...]:
        if stage not in STAGES:
            raise LossSchemaError(f"unknown stage {stage!r}")
        return tuple(item for item in self.events if item.stage == stage)

    def describe(self) -> str:
        if not self.events:
            return "nothing"
        return ", ".join(
            f"{item.count} {item.kind}" + (f" ({item.area:.1f} m²)" if item.area else "")
            for item in self.events
        )

    def as_manifest_dict(self) -> dict[str, Any]:
        """The serialised ledger. ``lossy`` is written *after* the events, derived from them."""
        return {
            "schemaVersion": SCHEMA_VERSION,
            "stages": list(STAGES),
            "stagesRecorded": list(self.stages_recorded),
            "events": [item.as_dict() for item in self.events],
            "removedArea": self.removed_area,
            "addedArea": self.added_area,
            "lossy": self.is_lossy,
        }


def events_from_manifest(block: Mapping[str, Any] | None) -> tuple[LossEvent, ...]:
    """Parse a serialised ledger back into typed events, refusing anything off-schema.

    Used by ``scripts/check_lod_report.py`` to read a report some other run produced. It parses
    rather than pattern-matches, so a record the schema does not know fails CI instead of being
    skipped — which is the difference between this and the prefix convention it replaced.
    """
    if block is None:
        raise LossSchemaError("no loss block at all; the build did not record what it changed")
    version = block.get("schemaVersion")
    if version != SCHEMA_VERSION:
        raise LossSchemaError(
            f"loss block declares schemaVersion {version!r}, this reader understands "
            f"{SCHEMA_VERSION}; refusing to guess what the records mean"
        )
    raw = block.get("events")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise LossSchemaError(f"loss block has no 'events' list (got {type(raw).__name__})")

    parsed = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise LossSchemaError(f"event {index} is not an object")
        missing = [key for key in ("stage", "kind", "count") if key not in item]
        if missing:
            raise LossSchemaError(f"event {index} is missing {', '.join(missing)}")
        details = item.get("details", ())
        if isinstance(details, (str, bytes)) or not isinstance(details, Sequence):
            raise LossSchemaError(f"event {index} has a 'details' that is not a list")
        parsed.append(
            event(
                stage=str(item["stage"]),
                kind=str(item["kind"]),
                count=int(item["count"]),
                area=float(item.get("area", 0.0)),
                details=[str(entry) for entry in details],
            )
        )
    return tuple(parsed)


def ledger_from_manifest(block: Mapping[str, Any] | None) -> LossLedger:
    """Rebuild a ledger from a serialised one, keeping which stages the producer asked."""
    events = events_from_manifest(block)
    assert block is not None  # events_from_manifest raises on None
    recorded = block.get("stagesRecorded")
    if not isinstance(recorded, Sequence) or isinstance(recorded, (str, bytes)):
        raise LossSchemaError(
            "loss block has no 'stagesRecorded' list, so there is no way to tell a stage that "
            "lost nothing from a stage nobody asked"
        )
    return LossLedger.of(events, stages_recorded=[str(stage) for stage in recorded])
