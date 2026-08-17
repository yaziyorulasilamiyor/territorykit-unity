"""The two scripts in ``scripts/``: the geoBoundaries normalization and the CI report checker.

Both are live code with no test behind them until now. The normalization silently rewrites
country codes and deletes sub-threshold rings from every dataset that enters the chain, and the
checker is the only thing standing between a malformed ``lod-report.json`` and a green CI run —
so "it worked when I ran it" was the entire guarantee for both.

The checker tests are written as **mutations of a healthy report**: each one takes a report that
passes, breaks exactly one thing, and asserts the checker now objects. Three of these mutations
passed before this round, which is how they were chosen.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import check_lod_report
import pytest
from build_lod import RING_AREA_FLOOR, BuildLodError, normalize_geoboundaries
from check_lod_report import LOD_LEVELS, check

from geometry_api.loss import SCHEMA_VERSION, STAGES, kind_of

# --------------------------------------------------------------------------------------------
# normalize_geoboundaries
# --------------------------------------------------------------------------------------------

_BIG_RING = [[30.0, 39.0], [31.0, 39.0], [31.0, 40.0], [30.0, 40.0], [30.0, 39.0]]
"""One square degree — far above the 1e-9 deg² floor."""

_ISLET_RING = [
    [32.0, 39.0],
    [32.00001, 39.0],
    [32.00001, 39.00001],
    [32.0, 39.00001],
    [32.0, 39.0],
]
"""1e-10 deg² — below the floor, and a real islet in the source data."""


def _feature(name: str, geometry: dict[str, Any], group: str = "TUR") -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {"shapeName": name, "shapeGroup": group},
        "geometry": geometry,
    }


def _write(tmp_path: Path, *features: dict[str, Any]) -> Path:
    source = tmp_path / "source.geojson"
    source.write_text(
        json.dumps({"type": "FeatureCollection", "features": list(features)}), encoding="utf-8"
    )
    return source


def test_alpha3_country_codes_are_rewritten_and_counted(tmp_path: Path) -> None:
    """The reason the chain needs this step at all: 81 of 81 features fail the import without it."""
    source = _write(tmp_path, _feature("Ankara", {"type": "Polygon", "coordinates": [_BIG_RING]}))
    destination = tmp_path / "normalized.geojson"

    result = normalize_geoboundaries(source, destination, "TR")

    assert result.source_country_code == "TUR"
    assert result.country_code == "TR"
    assert result.country_codes_rewritten == 1
    assert result.feature_count == 1

    written = json.loads(destination.read_text(encoding="utf-8"))
    assert written["features"][0]["properties"]["shapeGroup"] == "TR"


def test_sub_threshold_islets_are_dropped_counted_and_described(tmp_path: Path) -> None:
    """An islet leaving the dataset is a real change to the data, so it has to be visible."""
    source = _write(
        tmp_path,
        _feature("Muğla", {"type": "MultiPolygon", "coordinates": [[_BIG_RING], [_ISLET_RING]]}),
    )
    destination = tmp_path / "normalized.geojson"

    result = normalize_geoboundaries(source, destination, "TR")

    assert result.dropped_parts == 1
    assert len(result.dropped_part_details) == 1
    assert "Muğla" in result.dropped_part_details[0]
    assert result.ledger().is_lossy is True
    assert result.ledger().count("dropped_islet") == 1

    written = json.loads(destination.read_text(encoding="utf-8"))
    assert len(written["features"][0]["geometry"]["coordinates"]) == 1, "the islet must be gone"


def test_sub_threshold_interior_rings_are_dropped_counted_and_described(tmp_path: Path) -> None:
    """The counter added last round, which no test called. TUR ADM1 has no holes, so only a
    fixture can reach it — and an uncounted filter is the exact shape of the phase 1 bug."""
    hole = [[30.1, 39.1], [30.10001, 39.1], [30.10001, 39.10001], [30.1, 39.10001], [30.1, 39.1]]
    source = _write(
        tmp_path, _feature("Ankara", {"type": "Polygon", "coordinates": [_BIG_RING, hole]})
    )

    result = normalize_geoboundaries(source, tmp_path / "n.geojson", "TR")

    assert result.dropped_holes == 1
    assert len(result.dropped_hole_details) == 1
    assert result.dropped_parts == 0
    assert result.ledger().is_lossy is True
    assert result.ledger().count("dropped_source_hole") == 1
    assert result.ledger().count("dropped_islet") == 0, (
        "a discarded interior ring and a discarded islet are different kinds; a single "
        "'something was dropped' counter is what made them interchangeable"
    )


def test_a_real_enclave_is_kept(tmp_path: Path) -> None:
    """The filter has to be a floor, not a hole remover."""
    enclave = [[30.2, 39.2], [30.4, 39.2], [30.4, 39.4], [30.2, 39.4], [30.2, 39.2]]
    source = _write(
        tmp_path, _feature("Ankara", {"type": "Polygon", "coordinates": [_BIG_RING, enclave]})
    )
    destination = tmp_path / "n.geojson"

    result = normalize_geoboundaries(source, destination, "TR")

    assert result.dropped_holes == 0
    assert result.ledger().is_lossy is False
    written = json.loads(destination.read_text(encoding="utf-8"))
    assert len(written["features"][0]["geometry"]["coordinates"]) == 2


def test_a_lossless_normalization_reports_itself_as_lossless(tmp_path: Path) -> None:
    source = _write(tmp_path, _feature("Ankara", {"type": "Polygon", "coordinates": [_BIG_RING]}))

    record = normalize_geoboundaries(source, tmp_path / "n.geojson", "TR").as_dict()

    assert record["loss"]["lossy"] is False
    assert record["loss"]["events"] == [], (
        "an empty ledger, not a set of zero counters: there is nothing to under-report"
    )
    assert record["loss"]["stagesRecorded"] == ["upstream"], (
        "this step can only speak for itself, and has to say so"
    )


def test_the_source_file_is_never_modified(tmp_path: Path) -> None:
    source = _write(
        tmp_path,
        _feature("Muğla", {"type": "MultiPolygon", "coordinates": [[_BIG_RING], [_ISLET_RING]]}),
    )
    before = source.read_bytes()

    normalize_geoboundaries(source, tmp_path / "n.geojson", "TR")

    assert source.read_bytes() == before, "normalization writes a copy; the input is data"


def test_a_file_mixing_two_countries_is_rejected(tmp_path: Path) -> None:
    """The importer's cross-check is moved here, not dropped."""
    source = _write(
        tmp_path,
        _feature("Ankara", {"type": "Polygon", "coordinates": [_BIG_RING]}, group="TUR"),
        _feature("Sofia", {"type": "Polygon", "coordinates": [_BIG_RING]}, group="BGR"),
    )

    with pytest.raises(BuildLodError, match="more than.*one country"):
        normalize_geoboundaries(source, tmp_path / "n.geojson", "TR")


def test_a_feature_that_is_entirely_sub_threshold_stops_the_build(tmp_path: Path) -> None:
    """Dropping every part of a region is not a normalization, it is losing the region."""
    source = _write(
        tmp_path, _feature("Speck", {"type": "MultiPolygon", "coordinates": [[_ISLET_RING]]})
    )

    with pytest.raises(BuildLodError, match=str(RING_AREA_FLOOR)):
        normalize_geoboundaries(source, tmp_path / "n.geojson", "TR")


def test_unsupported_geometry_types_are_rejected(tmp_path: Path) -> None:
    source = _write(tmp_path, _feature("Line", {"type": "LineString", "coordinates": _BIG_RING}))

    with pytest.raises(BuildLodError, match="unsupported geometry"):
        normalize_geoboundaries(source, tmp_path / "n.geojson", "TR")


def test_a_document_without_features_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "empty.geojson"
    source.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")

    with pytest.raises(BuildLodError, match="FeatureCollection"):
        normalize_geoboundaries(source, tmp_path / "n.geojson", "TR")


# --------------------------------------------------------------------------------------------
# check_lod_report
#
# Written as **mutations of a healthy report**: each one takes a report that passes, breaks
# exactly one thing, and asserts the checker now objects. The mutations were chosen by taking a
# real report and breaking it until CI stayed green — every one below was green at some point.
# --------------------------------------------------------------------------------------------

_ORIGIN_AREA = 784_232_074_335.0
"""The 81 provinces in local metres, so the mutations argue with realistic magnitudes."""


def _event(
    kind: str, count: int, area: float = 0.0, stage: str = "simplification"
) -> dict[str, Any]:
    return {
        "stage": stage,
        "kind": kind,
        "count": count,
        "area": area,
        "details": [f"{kind} {index}" for index in range(count)]
        if kind_of(kind).needs_detail
        else [],
    }


_UPSTREAM_EVENTS = [_event("dropped_islet", 7, stage="upstream")]


def _ledger(events: list[dict[str, Any]], lossy: bool | None = None) -> dict[str, Any]:
    """A serialised ledger. ``lossy`` defaults to the truth so a mutation has to state its lie."""
    derived = any(kind_of(item["kind"]).category == "loss" for item in events)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "stages": list(STAGES),
        "stagesRecorded": list(STAGES),
        "events": events,
        "removedArea": sum(
            item["area"] for item in events if kind_of(item["kind"]).side == "removed"
        ),
        "addedArea": sum(item["area"] for item in events if kind_of(item["kind"]).side == "added"),
        "lossy": derived if lossy is None else lossy,
    }


def _simplification(
    lod: str,
    parts: int,
    events: list[dict[str, Any]],
    holes: int = 0,
    source_holes: int = 0,
    dropped_area: float = 0.0,
) -> dict[str, Any]:
    removed = sum(item["area"] for item in events if kind_of(item["kind"]).side == "removed")
    added = sum(item["area"] for item in events if kind_of(item["kind"]).side == "added")
    return {
        "lod": lod,
        "tolerance": 0.0005,
        "sourceVertexCount": 366_157,
        "vertexCount": 100_000,
        "sourcePartCount": 705,
        "partCount": parts,
        "sourceHoleCount": source_holes,
        "holeCount": holes,
        "droppedPartArea": dropped_area,
        "largestDroppedPartArea": dropped_area,
        "droppedParts": [],
        "areaBudget": {
            "sourceArea": _ORIGIN_AREA,
            "outputArea": _ORIGIN_AREA + added - removed,
            "removedArea": removed,
            "addedArea": added,
            "retainedAreaRatio": (_ORIGIN_AREA - removed) / _ORIGIN_AREA,
            "minPartRetainedAreaRatio": 0.156,
            "severeShrinkParts": 23,
        },
        "topologyChanges": {
            "merges": sum(item["count"] for item in events if item["kind"] == "part_merge"),
            "splits": sum(item["count"] for item in events if item["kind"] == "part_split"),
            "created": sum(item["count"] for item in events if item["kind"] == "part_created"),
            "droppedHoles": sum(item["count"] for item in events if item["kind"] == "dropped_hole"),
            "netPartChange": 0,
            "mergeAddedArea": 0.0,
            "regions": [],
        },
        "topologyChanged": False,
        "loss": _ledger(events),
    }


def _level(
    lod: str,
    vertices: int,
    parts: int,
    events: list[dict[str, Any]],
    holes: int = 0,
    source_holes: int = 0,
    dropped_area: float = 0.0,
) -> dict[str, Any]:
    simplification = _simplification(
        lod, parts, events, holes=holes, source_holes=source_holes, dropped_area=dropped_area
    )
    all_events = events + _UPSTREAM_EVENTS
    return {
        "territoryCount": 81,
        "vertices": vertices,
        "triangles": vertices - 1_000,
        "bytes": vertices * 14,
        "simplification": simplification,
        "loss": _ledger(all_events),
        "lossy": any(kind_of(item["kind"]).category == "loss" for item in all_events),
        **_flags(all_events),
    }


def _flags(all_events: list[dict[str, Any]]) -> dict[str, bool]:
    """The two client flags, over every stage's events — what the build now writes.

    Not the simplification slice. That is what the fifth round's blocker was: the flags answered
    "did the simplifier change the topology" while claiming to answer "can this mesh answer a
    click". Because the chain's normalization drops seven islets, every level of a healthy report
    is now picking-unsafe — including ``high``, whose simplification is spotless.
    """
    return {
        "topologyChanged": any(kind_of(item["kind"]).changes_topology for item in all_events),
        "pickingUnsafe": any(kind_of(item["kind"]).picking_unsafe for item in all_events),
    }


_HIGH_EVENTS: list[dict[str, Any]] = [
    _event("boundary_retreat", 81, 4_859_841.0),
    _event("boundary_advance", 81, 5_276_764.0),
]
_MEDIUM_EVENTS: list[dict[str, Any]] = [
    _event("boundary_retreat", 81, 139_961_441.0),
    _event("boundary_advance", 81, 143_109_355.0),
    _event("dropped_part", 1, 684.6),
    _event("part_merge", 3, 15_906_005.0),
    _event("part_split", 3, 553.9),
]
_LOW_EVENTS: list[dict[str, Any]] = [
    _event("boundary_retreat", 81, 1_236_362_656.0),
    _event("boundary_advance", 80, 1_087_133_554.0),
    _event("dropped_part", 1, 684.6),
    _event("part_merge", 30, 269_842_393.0),
    _event("part_split", 11, 23_882.8),
    _event("artifact_hole_removed", 22, 11_636.3),
]


def _healthy_report() -> dict[str, Any]:
    """A report shaped like the one the real chain writes, and that the checker accepts."""
    return {
        "source": "turkey-provinces.geojson",
        "buildDate": "2026-01-01T00:00:00Z",
        "country": "TR",
        "adminLevel": "ADM1",
        "normalization": {
            "featureCount": 81,
            "sourceCountryCode": "TUR",
            "countryCode": "TR",
            "countryCodesRewritten": 81,
            "loss": {
                "schemaVersion": SCHEMA_VERSION,
                "stages": list(STAGES),
                "stagesRecorded": ["upstream"],
                "events": copy.deepcopy(_UPSTREAM_EVENTS),
                "removedArea": 0.0,
                "addedArea": 0.0,
                "lossy": True,
            },
        },
        "levels": {
            "high": _level("high", 240_379, 705, copy.deepcopy(_HIGH_EVENTS)),
            "medium": _level(
                "medium", 85_926, 704, copy.deepcopy(_MEDIUM_EVENTS), dropped_area=684.6
            ),
            "low": _level("low", 30_753, 685, copy.deepcopy(_LOW_EVENTS), dropped_area=684.6),
        },
        "vertexRatioToHigh": {"high": 1.0, "medium": 0.357, "low": 0.128},
    }


def _report_with_a_lossless_normalization() -> dict[str, Any]:
    """The same report with the seven dropped islets taken out of the chain.

    The real geoBoundaries import drops them, which makes every level lossy and therefore
    picking-unsafe. That is honest, but it also means the healthy report cannot isolate a case
    about one stage: ``high`` is already unsafe before the mutation lands. This is the same
    ladder with a clean upstream, where ``high`` is safe and a single injected event is the only
    thing that can change that.
    """
    report = _healthy_report()
    report["normalization"]["loss"]["events"] = []
    report["normalization"]["loss"]["lossy"] = False
    for lod in LOD_LEVELS:
        loss = report["levels"][lod]["loss"]
        loss["events"] = [item for item in loss["events"] if item["stage"] != "upstream"]
        report["levels"][lod]["lossy"] = loss["lossy"] = any(
            kind_of(item["kind"]).category == "loss" for item in loss["events"]
        )
        report["levels"][lod].update(_flags(loss["events"]))
    return report


def test_the_healthy_report_passes() -> None:
    """Without this, every mutation below could be passing for the wrong reason."""
    assert check(_healthy_report()) == []


def test_the_healthy_chain_calls_every_level_picking_unsafe() -> None:
    """Not a detail of the fixture — the conclusion the fifth round's fix forces.

    The import drops seven real islets (2,0–6,1 m²), so every level is ``lossy: true``, so no
    level can claim a click is reliable. ``high``'s *simplification* is still spotless, and that
    is what its ``simplification.topologyChanged`` reports; the top-level flag answers about the
    mesh, and the mesh is missing seven islets.
    """
    levels = _healthy_report()["levels"]
    assert [levels[lod]["pickingUnsafe"] for lod in LOD_LEVELS] == [True, True, True]
    assert check(_healthy_report()) == []

    without_upstream = _report_with_a_lossless_normalization()["levels"]
    assert without_upstream["high"]["pickingUnsafe"] is False, (
        "and with nothing dropped upstream, high is safe again — the flag tracks the chain"
    )


# ---- the mutation that gave this round its blocker ----------------------------------------


def test_a_vanished_source_hole_with_no_record_is_caught() -> None:
    """B1, on the CI side. One source hole in, none out, and nothing recorded.

    This is the exact counterexample: a Polygon with one real enclave whose output has the same
    exterior ring and no enclave. Every earlier version of this script only asked whether
    ``holeCount > sourceHoleCount`` — whether a hole had been *invented* — so a hole that was
    subtracted produced ``droppedHoles: 0``, ``lossy: false`` and an empty failure list.
    """
    report = _healthy_report()
    report["levels"]["low"]["simplification"]["sourceHoleCount"] = 1
    report["levels"]["low"]["simplification"]["holeCount"] = 0

    failures = check(report)

    assert any("hole count moved by 1" in failure for failure in failures), failures


def test_a_vanished_source_hole_that_is_recorded_passes() -> None:
    """The other direction: recorded properly, the same geometry is a legitimate report.

    Without this, the check above could be satisfied by rejecting every report whose hole count
    moved, which would make the level unbuildable rather than honest.
    """
    report = _healthy_report()
    simplification = report["levels"]["low"]["simplification"]
    simplification["sourceHoleCount"] = 1
    simplification["holeCount"] = 0
    hole = _event("dropped_hole", 1, 40_000.0)

    for events in (simplification["loss"]["events"], report["levels"]["low"]["loss"]["events"]):
        events.append(copy.deepcopy(hole))
    _refresh(report, "low")

    assert check(report) == []


def _refresh(report: dict[str, Any], lod: str) -> None:
    """Recompute the derived halves of a level after mutating its events.

    A mutation is supposed to break one thing. Without this, appending an event would also make
    the area budget, the topologyChanges block and the flags disagree, and the test would pass on
    whichever failure the checker happened to report first.
    """
    level = report["levels"][lod]
    simplification = level["simplification"]
    events = simplification["loss"]["events"]
    removed = sum(item["area"] for item in events if kind_of(item["kind"]).side == "removed")
    added = sum(item["area"] for item in events if kind_of(item["kind"]).side == "added")
    source = simplification["areaBudget"]["sourceArea"]
    simplification["areaBudget"].update(
        {
            "outputArea": source + added - removed,
            "removedArea": removed,
            "addedArea": added,
            "retainedAreaRatio": (source - removed) / source,
        }
    )
    simplification["loss"].update({"removedArea": removed, "addedArea": added})
    simplification["topologyChanges"].update(
        {
            "merges": sum(item["count"] for item in events if item["kind"] == "part_merge"),
            "splits": sum(item["count"] for item in events if item["kind"] == "part_split"),
            "created": sum(item["count"] for item in events if item["kind"] == "part_created"),
            "droppedHoles": sum(item["count"] for item in events if item["kind"] == "dropped_hole"),
        }
    )
    all_events = level["loss"]["events"]
    level["lossy"] = any(kind_of(item["kind"]).category == "loss" for item in all_events)
    level["loss"]["lossy"] = level["lossy"]
    level.update(_flags(all_events))


# ---- the naming convention this round removed --------------------------------------------


@pytest.mark.parametrize("kind", ["removedRings", "collapsedParts", "discarded_holes"])
def test_an_event_kind_outside_the_schema_fails_ci(kind: str) -> None:
    """The fourth review round's finding: the convention decided by *name*.

    ``removedRings`` matched the prefix list and set the flag; ``collapsedParts`` and
    ``discarded_holes`` describe the same kind of event in words nobody had listed, so they read
    as "nothing happened" and CI accepted both. Now an unrecognised kind is a failure, whichever
    way it is spelled — including the way that used to work.
    """
    report = _healthy_report()
    report["levels"]["low"]["loss"]["events"].append(
        {"stage": "simplification", "kind": kind, "count": 4, "details": ["a", "b", "c", "d"]}
    )

    failures = check(report)

    assert any("unknown loss event kind" in failure for failure in failures), failures


def test_a_report_written_against_the_old_schema_is_refused() -> None:
    """The prefix-convention block was version 1. A checker that guesses is one that misreads."""
    report = _healthy_report()
    report["levels"]["high"]["loss"] = {
        "droppedParts": 0,
        "droppedPartDetails": [],
        "lossy": False,
    }

    failures = check(report)

    assert any("schemaVersion" in failure for failure in failures), failures


def test_the_schema_is_shared_with_the_build_rather_than_copied() -> None:
    """Y2: one definition, two derivations.

    The previous version kept its own copy of the loss-record rule and argued the duplication was
    the point. It was not: the copies could drift, and a checker that has drifted the same way as
    the build agrees with it for the wrong reason. What stays independent is the derivation —
    every ``lossy`` flag, every identity and every area sum in this script is recomputed here.
    """
    from geometry_api import loss as build_side

    assert check_lod_report.SCHEMA_VERSION is build_side.SCHEMA_VERSION
    assert check_lod_report.STAGES is build_side.STAGES
    assert not any(
        name.endswith("_PREFIXES") or "PREFIX" in name for name in vars(check_lod_report)
    ), "a prefix list here is a second copy of the schema, whatever it is called"


# ---- the mutations from the previous round, still caught ----------------------------------


def test_a_count_with_no_details_behind_it_is_caught() -> None:
    """Mutation 1: a loss count with nothing a reviewer could look up.

    Seven of what, in which province, how big? The schema marks which kinds owe detail, so this
    is now refused at the point the event is parsed rather than by a bespoke check.
    """
    report = _healthy_report()
    report["levels"]["low"]["loss"]["events"] = [
        item if item["kind"] != "dropped_islet" else {**item, "details": []}
        for item in report["levels"]["low"]["loss"]["events"]
    ]

    failures = check(report)

    assert any("cannot be reviewed" in failure for failure in failures), failures


def test_a_negative_loss_count_is_caught() -> None:
    """Mutation 2: a negative count, which arithmetic on any total silently absorbs."""
    report = _healthy_report()
    report["levels"]["low"]["loss"]["events"].append(
        {"stage": "simplification", "kind": "dropped_part", "count": -7, "details": []}
    )

    failures = check(report)

    assert any("not a smaller loss" in failure for failure in failures), failures


def test_a_level_that_never_asked_a_stage_is_caught() -> None:
    """Mutation 3: no upstream records at all. This was the actual shape of every early report."""
    report = _healthy_report()
    for lod in LOD_LEVELS:
        loss = report["levels"][lod]["loss"]
        loss["stagesRecorded"] = ["simplification", "triangulation"]
        loss["events"] = [item for item in loss["events"] if item["stage"] != "upstream"]
        report["levels"][lod]["lossy"] = loss["lossy"] = any(
            kind_of(item["kind"]).category == "loss" for item in loss["events"]
        )

    failures = check(report)

    assert any("no records at all from stage(s) upstream" in f for f in failures), failures


def test_a_level_with_no_loss_block_at_all_is_caught() -> None:
    report = _healthy_report()
    for lod in LOD_LEVELS:
        del report["levels"][lod]["loss"]

    failures = check(report)

    assert any("carries no 'loss' block" in failure for failure in failures), failures


def test_a_lossy_flag_that_contradicts_its_own_events_is_caught() -> None:
    """In both directions: a false flag over real events, and a true flag over none."""
    understated = _healthy_report()
    understated["levels"]["low"]["lossy"] = False
    assert any("the events are what happened" in f for f in check(understated))

    # For the other direction the level has to have no loss events at all, which means an
    # upstream step that dropped nothing — the stage is still asked, it just has nothing to say.
    overstated = _report_with_a_lossless_normalization()
    assert check(overstated) == [], "a lossless upstream is a legitimate report"

    overstated["levels"]["high"]["loss"]["lossy"] = True
    assert any("the events are what happened" in f for f in check(overstated))


def test_a_normalization_that_disagrees_with_the_levels_is_caught() -> None:
    report = _healthy_report()
    report["normalization"]["loss"]["events"] = [_event("dropped_islet", 3, stage="upstream")]

    failures = check(report)

    assert any("disagree with the normalization block" in failure for failure in failures)


def test_cumulative_dropped_area_over_the_ceiling_is_caught() -> None:
    """No single part is over the 10.000 m² limit; together they are over 50.000 m²."""
    report = _healthy_report()
    report["levels"]["low"]["simplification"]["droppedPartArea"] = 60_000.0
    report["levels"]["low"]["simplification"]["largestDroppedPartArea"] = 9_000.0

    failures = check(report)

    assert any("cumulative limit" in failure for failure in failures), failures
    assert not any("over the 10000 m² limit" in failure for failure in failures)


def test_the_existing_ladder_expectations_still_hold() -> None:
    """The checks this script had before, so the new ones cannot have displaced them."""
    coarsening = _healthy_report()
    coarsening["levels"]["low"]["vertices"] = 200_000
    assert any("not strictly coarsening" in f for f in check(coarsening))

    budget = _healthy_report()
    budget["levels"]["low"]["vertices"] = 80_000
    assert any("ceiling" in f for f in check(budget))

    high_loss = _healthy_report()
    high_loss["levels"]["high"]["simplification"]["partCount"] = 700
    assert any("must preserve the source" in f for f in check(high_loss))

    silent = _healthy_report()
    for lod in LOD_LEVELS:
        silent["levels"][lod]["lossy"] = False
        silent["levels"][lod]["loss"]["lossy"] = False
    assert any("report lossy=false" in f for f in check(silent))


# ---- the identities, checked here as well as in the build ---------------------------------


def test_an_area_budget_that_does_not_balance_is_caught() -> None:
    """The level's own numbers, re-added here. A build can compute an identity and serialise
    something else; this is what makes the written report the thing under contract."""
    report = _healthy_report()
    report["levels"]["low"]["simplification"]["areaBudget"]["outputArea"] += 5_000_000.0

    failures = check(report)

    assert any("area budget does not balance" in failure for failure in failures), failures


def test_an_area_total_that_disagrees_with_its_events_is_caught() -> None:
    report = _healthy_report()
    report["levels"]["low"]["simplification"]["areaBudget"]["removedArea"] += 1_000_000.0

    failures = check(report)

    assert any("areaBudget.removedArea" in failure for failure in failures), failures


def test_a_part_count_the_events_do_not_account_for_is_caught() -> None:
    report = _healthy_report()
    report["levels"]["low"]["simplification"]["partCount"] = 690

    failures = check(report)

    assert any("part count moved by" in failure for failure in failures), failures


def test_topology_changes_drifting_from_the_events_is_caught() -> None:
    """The same numbers exist twice in a manifest: as events, and as the block a client reads."""
    report = _healthy_report()
    report["levels"]["low"]["simplification"]["topologyChanges"]["merges"] = 12

    failures = check(report)

    assert any("topologyChanges.merges is 12" in failure for failure in failures), failures


def test_a_level_claiming_picking_is_safe_after_merging_parts_is_caught() -> None:
    """Ö5: the flag phases 4 and 5 gate on cannot be an opinion."""
    report = _healthy_report()
    report["levels"]["low"]["pickingUnsafe"] = False

    failures = check(report)

    assert any("pickingUnsafe is False" in failure for failure in failures), failures


def test_a_level_with_no_client_flags_is_caught() -> None:
    report = _healthy_report()
    del report["levels"]["medium"]["pickingUnsafe"]

    failures = check(report)

    assert any("nothing to gate picking on" in failure for failure in failures), failures


# ---- the mutation that gave the fifth round its blocker -----------------------------------


def test_high_with_a_triangulation_loss_cannot_report_picking_as_safe() -> None:
    """B1: the manifest that passed this checker while contradicting itself.

    ``high``'s simplification is clean, so every check that read the simplification slice —
    including the flag check — saw an empty event list and agreed with ``pickingUnsafe: false``.
    One stage later, triangulation skipped a part: ``lossy: true`` in the same manifest. Phase 4/5
    read ``pickingUnsafe`` and would have picked against a mesh missing a province's part.
    """
    report = _report_with_a_lossless_normalization()
    high = report["levels"]["high"]
    assert check(report) == [] and high["pickingUnsafe"] is False, (
        "the case is only about triangulation, so nothing else may be making high unsafe"
    )

    # The manifest the round was found with: the loss is recorded, the flag is not updated.
    high["loss"]["events"].append(_event("skipped_part", 1, stage="triangulation"))
    high["loss"]["lossy"] = high["lossy"] = True

    failures = check(report)

    assert any("pickingUnsafe is False but the events say True" in f for f in failures), failures
    assert any("skipped_part" in f for f in failures), (
        "the failure has to name what made the level unsafe, not just that it is"
    )

    # And with the flags told the truth, the same report is a legitimate one: a level may lose a
    # part in triangulation, it may not claim clicks are still reliable afterwards.
    high.update(_flags(high["loss"]["events"]))
    assert check(report) == []


@pytest.mark.parametrize("lod", ["high", "medium", "low"])
def test_a_level_that_is_lossy_and_safe_at_once_is_caught(lod: str) -> None:
    """The invariant on its own, checked between the two booleans rather than against events.

    ``_check_client_flags`` compares each flag with the events; this compares the flags with each
    other. A report whose events and flags were written by one wrong producer that agreed with
    itself still cannot get past both.
    """
    report = _healthy_report()
    report["levels"][lod]["pickingUnsafe"] = False

    failures = check(report)

    assert report["levels"][lod]["lossy"] is True, "the healthy chain drops seven islets upstream"
    assert any("lossy is true but pickingUnsafe is false" in f for f in failures), failures


def test_a_client_flag_that_is_a_string_is_not_read_as_a_boolean() -> None:
    """``bool("false")`` is ``True``; a renderer testing truthiness would get the opposite."""
    report = _healthy_report()
    report["levels"]["low"]["pickingUnsafe"] = "true"

    failures = check(report)

    assert any("not a boolean" in failure for failure in failures), failures


def test_high_recording_any_simplification_loss_is_caught() -> None:
    """high is the level that claims to still be the source geometry."""
    report = _healthy_report()
    report["levels"]["high"]["loss"]["events"].append(_event("dropped_part", 1, 500.0))
    report["levels"]["high"]["simplification"]["loss"]["events"].append(
        _event("dropped_part", 1, 500.0)
    )
    _refresh(report, "high")

    failures = check(report)

    assert any("high recorded loss in simplification" in failure for failure in failures), failures
