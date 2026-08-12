"""Loader: format detection, normalization and the contract violations it must reject."""

from __future__ import annotations

import json

import pytest

from geometry_api.loader import (
    Dataset,
    DatasetError,
    detect_format,
    load_dataset,
    load_document,
)

_SQUARE = [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]]


def _feature_collection(*features: dict) -> dict:
    return {"type": "FeatureCollection", "features": list(features)}


def _feature(properties: dict, geometry: dict | None = None) -> dict:
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": geometry
        if geometry is not None
        else {"type": "Polygon", "coordinates": _SQUARE},
    }


def test_detects_geojson_feature_collection() -> None:
    assert detect_format(_feature_collection()) == "geojson"


def test_detects_territorykit_dataset() -> None:
    assert detect_format({"manifest": {}, "zones": []}) == "territorykit"


def test_rejects_unrecognized_document() -> None:
    with pytest.raises(DatasetError, match="unrecognized dataset document"):
        detect_format({"something": "else"})


def test_rejects_non_json_file(tmp_path) -> None:
    path = tmp_path / "broken.geojson"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(DatasetError, match="not valid JSON"):
        load_dataset(path)


def test_rejects_missing_file(tmp_path) -> None:
    with pytest.raises(DatasetError, match="cannot read dataset file"):
        load_dataset(tmp_path / "absent.geojson")


def test_rejects_empty_feature_collection() -> None:
    with pytest.raises(DatasetError, match="no territories"):
        load_document(_feature_collection())


def test_rejects_unsupported_geometry_type() -> None:
    document = _feature_collection(
        _feature({"id": "p"}, {"type": "Point", "coordinates": [0.0, 0.0]})
    )
    with pytest.raises(DatasetError, match="unsupported geometry type"):
        load_document(document)


def test_rejects_duplicate_ids() -> None:
    document = _feature_collection(_feature({"id": "same"}), _feature({"id": "same"}))
    with pytest.raises(DatasetError, match="duplicate territory id"):
        load_document(document)


def test_rejects_missing_geometry() -> None:
    document = _feature_collection({"type": "Feature", "properties": {"id": "x"}})
    with pytest.raises(DatasetError, match="has no geometry"):
        load_document(document)


def test_geojson_id_falls_back_through_property_keys() -> None:
    document = _feature_collection(
        _feature({"shapeID": "A", "shapeName": "Adana"}),
        _feature({"ID": "B"}),
        _feature({}),
    )
    dataset = load_document(document, fallback_id="ds")
    assert [t.id for t in dataset] == ["A", "B", "ds-2"]
    assert dataset.territories[0].name == "Adana"
    assert dataset.territories[1].name == "B", "name falls back to the id when absent"


def test_territorykit_zones_normalize_with_parent_links(territorykit_dataset: Dataset) -> None:
    dataset = territorykit_dataset
    assert dataset.source_format == "territorykit"
    assert dataset.id == "territorykit-fixture"
    assert len(dataset) == 3

    fatih = dataset.by_id("tr:34:fatih")
    assert fatih.name == "Fatih"
    assert fatih.level == 3
    assert fatih.parent_id == "tr:34", "parent comes from inverting the parent's childIds"
    assert fatih.neighbor_ids == ("tr:34:besiktas",)
    assert dataset.by_id("tr:34").parent_id is None


def test_unknown_territory_id_raises_key_error(territorykit_dataset: Dataset) -> None:
    with pytest.raises(KeyError):
        territorykit_dataset.by_id("tr:99")


def test_hole_and_part_counts(
    hole_dataset: Dataset, two_hole_dataset: Dataset, multipolygon_dataset: Dataset
) -> None:
    assert hole_dataset.territories[0].hole_count == 1
    assert hole_dataset.territories[0].part_count == 1
    assert two_hole_dataset.territories[0].hole_count == 2
    assert multipolygon_dataset.territories[0].part_count == 3
    assert multipolygon_dataset.territories[0].hole_count == 1


def test_bounds_and_origin_are_the_centre_of_the_bounds(multipolygon_dataset: Dataset) -> None:
    assert multipolygon_dataset.bounds == (30.0, 39.0, 32.8, 39.8)
    assert multipolygon_dataset.origin_lon == pytest.approx(31.4)
    assert multipolygon_dataset.origin_lat == pytest.approx(39.4)


def test_territorykit_dataset_file_matches_upstream_schema(fixtures_dir) -> None:
    """Guards the fixture itself: it must keep the shape the real dataset.json has."""
    raw = json.loads((fixtures_dir / "territorykit-dataset.json").read_text(encoding="utf-8"))
    assert set(raw) == {"manifest", "zones"}
    assert {"datasetId", "schemaVersion", "name"} <= set(raw["manifest"])
    assert {"id", "level", "childIds", "neighborIds", "geometry", "bbox"} <= set(raw["zones"][0])


def test_self_intersecting_geometry_is_rejected_by_default(fixtures_dir) -> None:
    """A bow-tie has zero shoelace area, and earcut will still emit a triangle for it.

    Accepting one means shipping a mesh that represents no region at all, so the default is to
    refuse rather than to guess what the author meant.
    """
    with pytest.raises(DatasetError, match="invalid geometry: Self-intersection"):
        load_dataset(fixtures_dir / "bowtie.geojson")


def test_self_intersecting_geometry_can_be_repaired_on_request(fixtures_dir) -> None:
    dataset = load_dataset(fixtures_dir / "bowtie.geojson", on_invalid="repair")
    territory = dataset.territories[0]

    assert territory.repaired is True, "the change must be visible downstream, not silent"
    assert territory.geometry.is_valid
    assert territory.geometry.geom_type == "MultiPolygon"
    assert territory.part_count == 2, "make_valid splits the bow-tie into its two lobes"
    assert territory.geometry.area > 0


def test_repair_rejects_geometry_that_cannot_be_saved() -> None:
    """make_valid on a collapsed ring yields a line; a line is not a region."""
    document = _feature_collection(
        _feature(
            {"id": "line"},
            {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [0.0, 0.0]]]},
        )
    )
    with pytest.raises(DatasetError, match="could not produce a usable surface|encloses no area"):
        load_document(document, on_invalid="repair")


def test_unknown_invalid_policy_is_rejected() -> None:
    with pytest.raises(DatasetError, match="unknown on_invalid policy"):
        load_document(_feature_collection(_feature({"id": "a"})), on_invalid="ignore")  # type: ignore[arg-type]


def test_valid_geometry_is_not_flagged_as_repaired(multipolygon_dataset: Dataset) -> None:
    assert all(not t.repaired for t in multipolygon_dataset)


def test_real_dataset_has_no_invalid_geometry(sample_dataset: Dataset) -> None:
    """Loading the real dataset with the strict default is itself the assertion."""
    assert all(not territory.repaired for territory in sample_dataset)
    assert all(territory.geometry.is_valid for territory in sample_dataset)


def test_real_dataset_loads_all_provinces(sample_dataset: Dataset) -> None:
    assert sample_dataset.source_format == "geojson"
    assert len(sample_dataset) == 81
    assert sample_dataset.metadata["license"] == "CC BY-SA 2.0"

    multipart = [t for t in sample_dataset if t.part_count > 1]
    assert len(multipart) == 20, "20 provinces have islands"
    assert sum(t.hole_count for t in sample_dataset) == 0, (
        "geoBoundaries TUR ADM1 has no interior rings — this is why the hole fixtures exist"
    )

    mugla = next(t for t in sample_dataset if t.name == "Muğla")
    assert mugla.part_count == 256


def test_real_dataset_origin_is_inside_turkey(sample_dataset: Dataset) -> None:
    assert sample_dataset.origin_lon == pytest.approx(35.2416, abs=1e-3)
    assert sample_dataset.origin_lat == pytest.approx(38.9563, abs=1e-3)
