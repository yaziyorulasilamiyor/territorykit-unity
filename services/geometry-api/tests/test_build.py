"""The build CLI: it must produce every mesh, describe them accurately, and repeat itself."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import SAMPLE_DATASET_PATH

from geometry_api.build import (
    MANIFEST_FILENAME,
    UINT16_WARNING_RATIO,
    BuildError,
    build_manifest,
    build_meshes,
    main,
)
from geometry_api.encoding import UINT16_MAX_VERTEX_COUNT, decode_tkms
from geometry_api.loader import Dataset, load_document

_FLAT_DEGENERATE_POLYGON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"id": "flat", "name": "Flat"},
            "geometry": {
                # Three points on one parallel stay collinear through the projection, so there
                # is no triangle to make.
                "type": "Polygon",
                "coordinates": [[[30.0, 39.0], [31.0, 39.0], [32.0, 39.0], [30.0, 39.0]]],
            },
        }
    ],
}


def _read_manifest(output_dir: Path) -> dict:
    return json.loads((output_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))


def test_builds_every_province(sample_dataset: Dataset, tmp_path: Path) -> None:
    assert main(["--input", str(SAMPLE_DATASET_PATH), "--output", str(tmp_path), "--quiet"]) == 0

    manifest = _read_manifest(tmp_path)
    assert manifest["territoryCount"] == 81
    assert len(list(tmp_path.glob("*.tkms"))) == 81
    assert manifest["totals"]["vertices"] == 365_481
    assert manifest["totals"]["triangles"] == 364_057


def test_output_is_byte_identical_across_runs(sample_dataset: Dataset, tmp_path: Path) -> None:
    """Phase 3's content-addressed cache assumes the same input yields the same bytes."""
    first, second = tmp_path / "a", tmp_path / "b"
    for target in (first, second):
        assert main(["--input", str(SAMPLE_DATASET_PATH), "--output", str(target), "--quiet"]) == 0

    produced = sorted(path.name for path in first.iterdir())
    assert produced == sorted(path.name for path in second.iterdir())
    for name in produced:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_every_written_mesh_decodes_and_matches_its_manifest_entry(
    sample_dataset: Dataset, tmp_path: Path
) -> None:
    assert main(["--input", str(SAMPLE_DATASET_PATH), "--output", str(tmp_path), "--quiet"]) == 0
    manifest = _read_manifest(tmp_path)

    for entry in manifest["territories"]:
        payload = (tmp_path / entry["file"]).read_bytes()
        decoded = decode_tkms(payload)
        assert decoded.vertex_count == entry["vertexCount"], entry["name"]
        assert decoded.triangle_count == entry["triangleCount"], entry["name"]
        assert len(payload) == entry["byteLength"], entry["name"]
        assert decoded.uses_uint32_indices == (entry["indexFormat"] == "uint32"), entry["name"]
        assert decoded.bbox == pytest.approx(tuple(entry["bboxLocal"])), entry["name"]


def test_written_meshes_carry_no_trailing_bytes(sample_dataset: Dataset, tmp_path: Path) -> None:
    """The decoder tolerates padding. Our own pipeline must never produce any.

    Leniency in the reader is for other people's encoders; a stray byte in a file we wrote
    would mean the length maths drifted from the format.
    """
    assert main(["--input", str(SAMPLE_DATASET_PATH), "--output", str(tmp_path), "--quiet"]) == 0

    files = sorted(tmp_path.glob("*.tkms"))
    assert len(files) == 81
    for path in files:
        raw = path.read_bytes()
        assert decode_tkms(raw).bytes_consumed == len(raw), path.name


def test_manifest_records_the_origin_and_attribution(
    sample_dataset: Dataset, tmp_path: Path
) -> None:
    assert main(["--input", str(SAMPLE_DATASET_PATH), "--output", str(tmp_path), "--quiet"]) == 0
    manifest = _read_manifest(tmp_path)

    assert manifest["origin"]["projection"] == "webmercator-local-meters"
    assert manifest["origin"]["lon"] == pytest.approx(sample_dataset.origin_lon)
    assert manifest["origin"]["lat"] == pytest.approx(sample_dataset.origin_lat)
    assert manifest["meshFormat"] == "TKMS"
    assert manifest["meshFormatVersion"] == 1
    assert manifest["sourceMetadata"]["license"] == "CC BY-SA 2.0", (
        "the dataset licence has to survive into the build output"
    )


def test_warns_when_a_mesh_approaches_the_uint16_ceiling(
    sample_dataset: Dataset, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--input", str(SAMPLE_DATASET_PATH), "--output", str(tmp_path), "--quiet"]) == 0
    warnings = [line for line in capsys.readouterr().err.splitlines() if "uint16" in line]

    assert len(warnings) == 1, "Mugla is the only province near the ceiling"
    assert "Muğla" in warnings[0]
    assert "5057 vertices of headroom" in warnings[0]
    assert UINT16_WARNING_RATIO < 60_478 / UINT16_MAX_VERTEX_COUNT < 1.0


def test_territorykit_ids_become_safe_filenames(territorykit_dataset: Dataset) -> None:
    entries = build_meshes(territorykit_dataset)
    filenames = {entry.territory.id: entry.filename for entry in entries}

    assert filenames["tr:34:fatih"] == "tr_34_fatih.tkms", "colons are not valid on every platform"
    assert len(set(filenames.values())) == len(filenames)


def test_colliding_sanitized_ids_stay_distinct() -> None:
    document = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": identifier},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[30.0, 39.0], [30.5, 39.0], [30.5, 39.5], [30.0, 39.5], [30.0, 39.0]]
                    ],
                },
            }
            for identifier in ("a:b", "a/b", "a_b")
        ],
    }
    entries = build_meshes(load_document(document))
    assert len({entry.filename for entry in entries}) == 3


def test_a_failing_territory_fails_the_whole_build() -> None:
    dataset = load_document(_FLAT_DEGENERATE_POLYGON)
    with pytest.raises(BuildError, match="1 of 1 territories failed"):
        build_meshes(dataset)


def test_nothing_is_written_when_the_build_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "broken.geojson"
    source.write_text(json.dumps(_FLAT_DEGENERATE_POLYGON), encoding="utf-8")
    output = tmp_path / "out"

    assert main(["--input", str(source), "--output", str(output)]) == 1
    assert not output.exists(), "a failed build must not leave a directory that looks complete"
    assert "error:" in capsys.readouterr().err


def test_missing_input_reports_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--input", str(tmp_path / "absent.geojson"), "--output", str(tmp_path)]) == 1
    assert "cannot read dataset file" in capsys.readouterr().err


def test_unknown_lod_is_rejected(territorykit_dataset: Dataset) -> None:
    with pytest.raises(BuildError, match="unknown lod"):
        build_meshes(territorykit_dataset, lod="ultra")


def test_manifest_bounds_cover_every_mesh(territorykit_dataset: Dataset) -> None:
    entries = build_meshes(territorykit_dataset)
    manifest = build_manifest(territorykit_dataset, entries, "high")
    min_x, min_y, max_x, max_y = manifest["boundsLocal"]

    assert min_x < max_x and min_y < max_y
    for entry in entries:
        assert min_x <= entry.bounds_local[0] and entry.bounds_local[2] <= max_x
        assert min_y <= entry.bounds_local[1] and entry.bounds_local[3] <= max_y


def test_report_table_lists_every_territory(
    sample_dataset: Dataset, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--input", str(SAMPLE_DATASET_PATH), "--output", str(tmp_path)]) == 0
    stdout = capsys.readouterr().out
    assert "territory" in stdout and "triangles" in stdout
    assert stdout.count("uint16") == 81, "one row per province, all of them uint16 today"
