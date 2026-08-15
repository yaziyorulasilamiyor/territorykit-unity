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

import pytest
from build_lod import RING_AREA_FLOOR, BuildLodError, normalize_geoboundaries
from check_lod_report import check

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
    assert result.as_dict()["lossy"] is True

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
    assert result.as_dict()["lossy"] is True


def test_a_real_enclave_is_kept(tmp_path: Path) -> None:
    """The filter has to be a floor, not a hole remover."""
    enclave = [[30.2, 39.2], [30.4, 39.2], [30.4, 39.4], [30.2, 39.4], [30.2, 39.2]]
    source = _write(
        tmp_path, _feature("Ankara", {"type": "Polygon", "coordinates": [_BIG_RING, enclave]})
    )
    destination = tmp_path / "n.geojson"

    result = normalize_geoboundaries(source, destination, "TR")

    assert result.dropped_holes == 0
    assert result.as_dict()["lossy"] is False
    written = json.loads(destination.read_text(encoding="utf-8"))
    assert len(written["features"][0]["geometry"]["coordinates"]) == 2


def test_a_lossless_normalization_reports_itself_as_lossless(tmp_path: Path) -> None:
    source = _write(tmp_path, _feature("Ankara", {"type": "Polygon", "coordinates": [_BIG_RING]}))

    record = normalize_geoboundaries(source, tmp_path / "n.geojson", "TR").as_dict()

    assert record["lossy"] is False
    assert record["droppedParts"] == 0
    assert record["droppedPartDetails"] == []
    assert record["droppedHoles"] == 0


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
# --------------------------------------------------------------------------------------------

_NORMALIZATION = {
    "featureCount": 81,
    "sourceCountryCode": "TUR",
    "countryCode": "TR",
    "countryCodesRewritten": 81,
    "droppedParts": 7,
    "droppedPartDetails": [f"islet {index}" for index in range(7)],
    "droppedHoles": 0,
    "droppedHoleDetails": [],
    "lossy": True,
}


def _simplification(lod: str, parts: int, dropped_area: float = 0.0) -> dict[str, Any]:
    return {
        "lod": lod,
        "tolerance": 0.0005,
        "sourceVertexCount": 366_157,
        "vertexCount": 100_000,
        "sourcePartCount": 705,
        "partCount": parts,
        "sourceHoleCount": 0,
        "holeCount": 0,
        "droppedPartArea": dropped_area,
        "largestDroppedPartArea": dropped_area,
        "droppedParts": [],
        "topologyChanges": {"merges": 0, "splits": 0, "netPartChange": 0, "regions": []},
        "skippedParts": 0,
        "skippedRings": 0,
        "degenerateTriangles": 0,
    }


def _level(lod: str, vertices: int, parts: int, dropped_area: float = 0.0) -> dict[str, Any]:
    simplification = _simplification(lod, parts, dropped_area)
    return {
        "territoryCount": 81,
        "vertices": vertices,
        "triangles": vertices - 1_000,
        "bytes": vertices * 14,
        "simplification": simplification,
        "loss": {
            "triangulation": {
                "skippedParts": 0,
                "skippedRings": 0,
                "degenerateTriangles": 0,
            },
            "simplification": simplification,
            "upstream": dict(_NORMALIZATION),
            "lossy": True,
        },
        "lossy": True,
    }


def _healthy_report() -> dict[str, Any]:
    """A report shaped like the one the real chain writes, and that the checker accepts."""
    return {
        "source": "turkey-provinces.geojson",
        "buildDate": "2026-01-01T00:00:00Z",
        "country": "TR",
        "adminLevel": "ADM1",
        "normalization": dict(_NORMALIZATION),
        "levels": {
            "high": _level("high", 240_379, 705),
            "medium": _level("medium", 85_926, 704),
            "low": _level("low", 30_753, 685, dropped_area=684.6),
        },
        "vertexRatioToHigh": {"high": 1.0, "medium": 0.357, "low": 0.128},
    }


def test_the_healthy_report_passes() -> None:
    """Without this, every mutation below could be passing for the wrong reason."""
    assert check(_healthy_report()) == []


def test_a_count_with_no_details_behind_it_is_caught() -> None:
    """Mutation 1: droppedParts=7, droppedPartDetails=[]. Passed CI before this round.

    A bare count cannot be reviewed by a human: seven of what, in which province, how big?
    """
    report = _healthy_report()
    report["normalization"]["droppedPartDetails"] = []
    report["levels"]["high"]["loss"]["upstream"]["droppedPartDetails"] = []

    failures = check(report)

    assert any("droppedParts" in failure and "lists 0" in failure for failure in failures), failures


def test_a_negative_loss_count_is_caught() -> None:
    """Mutation 2: droppedParts=-7 with lossy=true. Passed CI before this round.

    A negative count reads as "something happened" to a truthiness test and cancels real losses
    out of any total, which is the worst of both.
    """
    report = _healthy_report()
    report["normalization"]["droppedParts"] = -7
    report["normalization"]["droppedPartDetails"] = []
    for lod in ("high", "medium", "low"):
        report["levels"][lod]["loss"]["upstream"]["droppedParts"] = -7
        report["levels"][lod]["loss"]["upstream"]["droppedPartDetails"] = []

    failures = check(report)

    assert any("cannot be negative" in failure for failure in failures), failures


def test_a_level_missing_its_upstream_block_is_caught() -> None:
    """Mutation 3: no per-level loss.upstream at all, aggregate lossy=true. Passed CI before.

    ``build_lod.py`` copied only the flag out of each manifest, so this was not even a mutation —
    it was the actual shape of every report the chain produced.
    """
    report = _healthy_report()
    for lod in ("high", "medium", "low"):
        del report["levels"][lod]["loss"]["upstream"]

    failures = check(report)

    assert any("missing stage(s) upstream" in failure for failure in failures), failures


def test_a_level_with_no_loss_block_at_all_is_caught() -> None:
    report = _healthy_report()
    for lod in ("high", "medium", "low"):
        del report["levels"][lod]["loss"]

    failures = check(report)

    assert any("carries no 'loss' block" in failure for failure in failures), failures


def test_a_lossy_flag_that_contradicts_its_own_records_is_caught() -> None:
    """In both directions: a false flag over real records, and a true flag over none."""
    understated = _healthy_report()
    understated["normalization"]["lossy"] = False
    assert any("records are what happened" in f for f in check(understated))

    overstated = _healthy_report()
    overstated["normalization"] = {
        "featureCount": 81,
        "countryCodesRewritten": 0,
        "droppedParts": 0,
        "droppedPartDetails": [],
        "droppedHoles": 0,
        "droppedHoleDetails": [],
        "lossy": True,
    }
    assert any("records are what happened" in f for f in check(overstated))


def test_an_upstream_block_that_disagrees_with_the_normalization_is_caught() -> None:
    report = _healthy_report()
    report["levels"]["low"]["loss"]["upstream"]["droppedParts"] = 3
    report["levels"]["low"]["loss"]["upstream"]["droppedPartDetails"] = ["a", "b", "c"]

    failures = check(report)

    assert any("disagrees with the normalization block" in failure for failure in failures)


def test_cumulative_dropped_area_over_the_ceiling_is_caught() -> None:
    """No single part is over the 10.000 m² limit; together they are over 50.000 m²."""
    report = _healthy_report()
    report["levels"]["low"]["simplification"]["droppedPartArea"] = 60_000.0
    report["levels"]["low"]["simplification"]["largestDroppedPartArea"] = 9_000.0
    report["levels"]["low"]["loss"]["simplification"] = report["levels"]["low"]["simplification"]

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

    silent = copy.deepcopy(_healthy_report())
    for lod in ("high", "medium", "low"):
        silent["levels"][lod]["lossy"] = False
        silent["levels"][lod]["loss"]["lossy"] = False
    assert any("report lossy=false" in f for f in check(silent))
