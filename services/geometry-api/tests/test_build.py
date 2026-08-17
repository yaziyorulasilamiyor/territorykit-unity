"""The build CLI: it must produce every mesh, describe them accurately, and repeat itself."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from conftest import SAMPLE_DATASET_PATH
from shapely.geometry import shape

from geometry_api.build import (
    MANIFEST_FILENAME,
    UINT16_WARNING_RATIO,
    BuildError,
    MeshEntry,
    _print_index_warnings,
    build_manifest,
    build_meshes,
    main,
)
from geometry_api.encoding import UINT16_MAX_VERTEX_COUNT, decode_tkms
from geometry_api.loader import Dataset, Territory, load_document


def _square(min_lon: float, min_lat: float, size: float) -> dict:
    max_lon, max_lat = min_lon + size, min_lat + size
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [min_lon, min_lat],
                [max_lon, min_lat],
                [max_lon, max_lat],
                [min_lon, max_lat],
                [min_lon, min_lat],
            ]
        ],
    }


# A valid polygon far from the origin, small enough that float32 collapses every one of its
# vertices onto the same point. Nothing is left to triangulate.
_UNBUILDABLE_DOCUMENT = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"id": "anchor"}, "geometry": _square(25.0, 36.0, 1.0)},
        {"type": "Feature", "properties": {"id": "speck"}, "geometry": _square(44.9, 41.9, 1e-9)},
    ],
}


def _read_manifest(output_dir: Path) -> dict:
    return json.loads((output_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))


def test_builds_every_province(sample_dataset: Dataset, tmp_path: Path) -> None:
    assert main(["--input", str(SAMPLE_DATASET_PATH), "--output", str(tmp_path), "--quiet"]) == 0

    manifest = _read_manifest(tmp_path)
    assert manifest["territoryCount"] == 81
    assert len(list(tmp_path.glob("*.tkms"))) == 81
    # Phase 2 simplifies at 'high' too (tolerance 5e-05), so these are below the 365_481 /
    # 364_057 the unsimplified phase 1 pipeline produced. What makes 'high' still the level
    # that preserves the source is the assertion below, not the vertex count.
    assert manifest["totals"]["vertices"] == 240_423
    assert manifest["totals"]["triangles"] == 238_999

    simplification = manifest["simplification"]
    assert simplification["lod"] == "high"
    assert simplification["partCount"] == simplification["sourcePartCount"] == 712
    assert simplification["holeCount"] == simplification["sourceHoleCount"] == 0
    assert manifest["lossy"] is False
    assert [item["kind"] for item in manifest["loss"]["events"]] == [
        "boundary_advance",
        "boundary_retreat",
    ], "high moves boundaries and loses nothing; both facts have to be on the record"
    assert manifest["topologyChanged"] is False
    assert manifest["pickingUnsafe"] is False


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


def test_simplification_moves_the_largest_mesh_off_the_uint16_ceiling(
    sample_dataset: Dataset, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Phase 1 left Muğla at 92.3% of the uint16 index space with 5057 vertices of headroom.

    Simplifying at 'high' is what removed that risk, so this asserts the headroom rather than
    the warning: no province is near the ceiling any more, and none needs uint32 indices.
    """
    assert main(["--input", str(SAMPLE_DATASET_PATH), "--output", str(tmp_path), "--quiet"]) == 0
    assert [line for line in capsys.readouterr().err.splitlines() if "uint16" in line] == []

    manifest = _read_manifest(tmp_path)
    largest = max(manifest["territories"], key=lambda entry: entry["vertexCount"])
    assert largest["name"] == "Muğla"
    assert largest["vertexCount"] == 34_466
    assert largest["vertexCount"] / UINT16_MAX_VERTEX_COUNT < UINT16_WARNING_RATIO
    assert all(entry["indexFormat"] == "uint16" for entry in manifest["territories"])


def test_still_warns_when_a_mesh_does_approach_the_uint16_ceiling(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No province trips the warning any more, so prove the warning itself still works.

    Without this, the check above would keep passing if the warning were deleted outright.
    """
    near_ceiling = int(UINT16_MAX_VERTEX_COUNT * UINT16_WARNING_RATIO) + 1
    entry = MeshEntry(
        territory=Territory(id="crowded", name="Crowded", geometry=shape(_square(32.0, 39.0, 1.0))),
        filename="crowded.tkms",
        payload=b"",
        vertex_count=near_ceiling,
        triangle_count=1,
        part_count=1,
        bounds_local=(0.0, 0.0, 1.0, 1.0),
        uses_uint32_indices=False,
    )

    _print_index_warnings([entry], sys.stderr)
    warning = capsys.readouterr().err

    assert "Crowded" in warning
    assert f"{UINT16_MAX_VERTEX_COUNT - near_ceiling} vertices of headroom" in warning


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


def test_ids_differing_only_in_case_get_separate_files(tmp_path: Path) -> None:
    """Windows and macOS are case-insensitive: "A" and "a" are one file, not two.

    A case-sensitive collision check let the second territory overwrite the first while the
    manifest still listed both, so the first entry pointed at the wrong mesh.
    """
    document = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": "A"}, "geometry": _square(30.0, 39.0, 0.5)},
            {"type": "Feature", "properties": {"id": "a"}, "geometry": _square(31.0, 39.0, 0.7)},
        ],
    }
    source = tmp_path / "case.geojson"
    source.write_text(json.dumps(document), encoding="utf-8")
    output = tmp_path / "out"

    assert main(["--input", str(source), "--output", str(output), "--quiet"]) == 0
    manifest = _read_manifest(output)

    files = {entry["file"] for entry in manifest["territories"]}
    assert len(files) == 2
    assert len({name.casefold() for name in files}) == 2, "case alone must not distinguish them"
    assert len(list(output.glob("*.tkms"))) == 2, "both meshes must survive on disk"

    for entry in manifest["territories"]:
        decoded = decode_tkms((output / entry["file"]).read_bytes())
        assert decoded.vertex_count == entry["vertexCount"], (
            f"{entry['id']} points at a mesh that is not its own"
        )


@pytest.mark.parametrize("reserved", ["CON", "prn", "NUL", "COM1", "lpt9", "AUX"])
def test_windows_reserved_names_are_avoided(reserved: str, tmp_path: Path) -> None:
    document = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": reserved},
                "geometry": _square(30.0, 39.0, 0.5),
            }
        ],
    }
    entries = build_meshes(load_document(document))
    stem = entries[0].filename.removesuffix(".tkms")
    assert stem.upper() != reserved.upper(), f"{reserved} is a reserved device name on Windows"

    source = tmp_path / "reserved.geojson"
    source.write_text(json.dumps(document), encoding="utf-8")
    assert main(["--input", str(source), "--output", str(tmp_path / "out"), "--quiet"]) == 0


def test_trailing_dots_are_stripped_from_filenames() -> None:
    document = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": "tr.34."},
                "geometry": _square(30.0, 39.0, 0.5),
            }
        ],
    }
    entries = build_meshes(load_document(document))
    assert entries[0].filename == "tr.34.tkms", "Windows cannot create a name ending in a dot"


def test_geometry_lost_to_quantization_fails_the_high_level_build(
    fixtures_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The high level claims to be the source geometry, so losing a hole is a failure."""
    source = fixtures_dir / "collapsing-hole.geojson"
    assert main(["--input", str(source), "--output", str(tmp_path / "out"), "--quiet"]) == 1

    error = capsys.readouterr().err
    assert "lost geometry to float32 quantization" in error
    assert "--allow-lossy" in error
    assert not (tmp_path / "out").exists()


def test_allow_lossy_accepts_the_loss_and_records_it(
    fixtures_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = fixtures_dir / "collapsing-hole.geojson"
    output = tmp_path / "out"
    assert main(["--input", str(source), "--output", str(output), "--quiet", "--allow-lossy"]) == 0

    manifest = _read_manifest(output)
    entry = manifest["territories"][0]
    assert entry["lossy"] is True
    assert [(item["kind"], item["count"]) for item in entry["lossEvents"]] == [("skipped_ring", 1)]
    assert manifest["lossy"] is True
    assert _event_counts(manifest)["skipped_ring"] == 1
    assert "lost 0 part(s), 1 hole(s) and 0 degenerate triangle(s)" in capsys.readouterr().out


def test_a_part_lost_to_degenerate_triangles_also_fails_the_build(
    fixtures_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The loss path that reached the manifest as zeroes before.

    A part whose triangles are all degenerate used to leave every counter at 0 and lossy: false
    while the part itself was gone.
    """
    source = fixtures_dir / "vanishing-part.geojson"
    assert main(["--input", str(source), "--output", str(tmp_path / "out"), "--quiet"]) == 1
    assert "degenerate triangle" in capsys.readouterr().err

    output = tmp_path / "allowed"
    assert main(["--input", str(source), "--output", str(output), "--quiet", "--allow-lossy"]) == 0
    entry = _read_manifest(output)["territories"][0]

    assert entry["partCount"] == 2, "the manifest must describe the mesh, not the input"
    counts = {item["kind"]: item["count"] for item in entry["lossEvents"]}
    assert counts["skipped_part"] == 1
    assert counts["degenerate_triangle"] > 0
    assert _read_manifest(output)["lossy"] is True


def test_lossless_builds_record_zero_loss(sample_dataset: Dataset, tmp_path: Path) -> None:
    assert main(["--input", str(SAMPLE_DATASET_PATH), "--output", str(tmp_path), "--quiet"]) == 0
    manifest = _read_manifest(tmp_path)

    assert manifest["lossy"] is False
    assert not [item for item in manifest["loss"]["events"] if item["category"] == "loss"], (
        "a lossless build has no loss events at all, rather than a set of zeroed counters"
    )
    assert manifest["totals"]["repairedTerritories"] == 0
    for entry in manifest["territories"]:
        assert entry["lossEvents"] == [], entry["name"]
        assert entry["lossy"] is False, entry["name"]
        assert entry["repaired"] is False


def _event_counts(manifest: dict) -> dict[str, int]:
    return {item["kind"]: item["count"] for item in manifest["loss"]["events"]}


def test_repair_invalid_flag_is_recorded_in_the_manifest(
    fixtures_dir: Path, tmp_path: Path
) -> None:
    source = fixtures_dir / "bowtie.geojson"
    output = tmp_path / "out"
    assert main(["--input", str(source), "--output", str(output)]) == 1, "rejected by default"

    assert main(["--input", str(source), "--output", str(output), "--repair-invalid"]) == 0
    manifest = _read_manifest(output)
    assert manifest["territories"][0]["repaired"] is True
    assert manifest["totals"]["repairedTerritories"] == 1


def test_clean_removes_meshes_from_an_earlier_run(
    territorykit_dataset: Dataset, tmp_path: Path, fixtures_dir: Path
) -> None:
    output = tmp_path / "out"
    assert (
        main(
            [
                "--input",
                str(fixtures_dir / "territorykit-dataset.json"),
                "--output",
                str(output),
                "--quiet",
            ]
        )
        == 0
    )
    stale = output / "left-over.tkms"
    stale.write_bytes(b"not a mesh")
    unrelated = output / "notes.txt"
    unrelated.write_text("keep me", encoding="utf-8")

    assert (
        main(
            [
                "--input",
                str(fixtures_dir / "multipolygon.geojson"),
                "--output",
                str(output),
                "--quiet",
                "--clean",
            ]
        )
        == 0
    )

    assert not stale.exists(), "a clean build must not leave meshes from another dataset"
    assert unrelated.exists(), "only files this tool writes may be deleted"
    manifest = _read_manifest(output)
    assert {entry["file"] for entry in manifest["territories"]} == {
        path.name for path in output.glob("*.tkms")
    }


def test_a_failing_territory_fails_the_whole_build() -> None:
    dataset = load_document(_UNBUILDABLE_DOCUMENT)
    with pytest.raises(BuildError, match="1 of 2 territories failed"):
        build_meshes(dataset)


def test_nothing_is_written_when_the_build_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "broken.geojson"
    source.write_text(json.dumps(_UNBUILDABLE_DOCUMENT), encoding="utf-8")
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
