"""Builds a minimal, internally-consistent ``build_lod.py``-shaped output on disk.

Used by ``test_publish_validation.py`` and ``test_publish_atomicity.py`` to exercise
``scripts/publish_dataset.py`` without running the real geoBoundaries/TerritoryKit chain — this
fixture is deliberately lossless and change-free at every level (no loss events anywhere), which
is enough to satisfy every identity ``manifest_validation.check()`` recomputes while keeping the
fixture small and easy to mutate one field at a time in a test.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from geometry_api.loss import SCHEMA_VERSION, STAGES

LOD_LEVELS = ("high", "medium", "low")

# high > medium > low, and low/high <= 0.25 (manifest_validation.LOW_VERTEX_BUDGET).
_VERTEX_COUNTS = {"high": 1000, "medium": 200, "low": 100}
_TRIANGLE_COUNTS = {"high": 900, "medium": 180, "low": 90}
_BYTE_COUNTS = {"high": 14_032, "medium": 2_832, "low": 1_432}


def _empty_loss(stages_recorded: tuple[str, ...] = STAGES) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "stages": list(STAGES),
        "stagesRecorded": list(stages_recorded),
        "events": [],
        "removedArea": 0.0,
        "addedArea": 0.0,
        "lossy": False,
    }


def _simplification(lod: str, vertices: int) -> dict[str, Any]:
    return {
        "lod": lod,
        "tolerance": 0.0,
        "sourceVertexCount": vertices,
        "vertexCount": vertices,
        "sourcePartCount": 1,
        "partCount": 1,
        "sourceHoleCount": 0,
        "holeCount": 0,
        "droppedPartArea": 0.0,
        "largestDroppedPartArea": 0.0,
        "droppedParts": [],
        "areaBudget": {
            "sourceArea": 1_000_000.0,
            "outputArea": 1_000_000.0,
            "removedArea": 0.0,
            "addedArea": 0.0,
            "retainedAreaRatio": 1.0,
            "minPartRetainedAreaRatio": 1.0,
            "severeShrinkParts": 0,
        },
        "topologyChanges": {
            "merges": 0,
            "splits": 0,
            "created": 0,
            "droppedHoles": 0,
            "netPartChange": 0,
            "mergeAddedArea": 0.0,
            "regions": [],
        },
        "topologyChanged": False,
        "loss": _empty_loss(),
    }


def _level(lod: str, territory_count: int = 1) -> dict[str, Any]:
    return {
        "territoryCount": territory_count,
        "vertices": _VERTEX_COUNTS[lod],
        "triangles": _TRIANGLE_COUNTS[lod],
        "bytes": _BYTE_COUNTS[lod],
        "simplification": _simplification(lod, _VERTEX_COUNTS[lod]),
        "loss": _empty_loss(),
        "lossy": False,
        "topologyChanged": False,
        "pickingUnsafe": False,
    }


def healthy_report(territory_count: int = 1) -> dict[str, Any]:
    """A ``lod-report.json``-shaped dict that ``manifest_validation.check()`` accepts."""
    return {
        "source": "fixture.geojson",
        "buildDate": "2026-01-01T00:00:00Z",
        "country": "TR",
        "adminLevel": "ADM1",
        "normalization": {
            "featureCount": 1,
            "sourceCountryCode": "TUR",
            "countryCode": "TR",
            "countryCodesRewritten": 1,
            "loss": _empty_loss(stages_recorded=("upstream",)),
        },
        "levels": {lod: _level(lod, territory_count) for lod in LOD_LEVELS},
        "vertexRatioToHigh": {
            lod: _VERTEX_COUNTS[lod] / _VERTEX_COUNTS["high"] for lod in LOD_LEVELS
        },
    }


def _territory_entry(territory_id: str, content: bytes, level: dict[str, Any]) -> dict[str, Any]:
    """An ``index.json`` ``territories[]`` entry whose ``byteLength``/``sha256`` are computed
    from ``content`` — always in agreement with the actual bytes on disk, so a test has to
    deliberately break that agreement rather than trip over an accidental one."""
    return {
        "id": territory_id,
        "name": f"Territory {territory_id}",
        "file": f"{territory_id}.tkms",
        "vertexCount": level["vertices"],
        "triangleCount": level["triangles"],
        "partCount": 1,
        "indexFormat": "uint16",
        "byteLength": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bboxLocal": [-1.0, -1.0, 1.0, 1.0],
        "lossy": False,
        "lossEvents": [],
        "repaired": False,
        "parentId": None,
        "administrativeLevel": 1,
        "neighborIds": [],
    }


def manifest_for_level(
    lod: str,
    level: dict[str, Any],
    dataset_id: str = "fixture",
    territory_contents: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    """An ``index.json``-shaped dict whose comparison fields exactly match ``level``.

    ``publish_dataset.py``'s cross-check (``manifest_validation.check_report_matches_build``)
    compares these fields against ``report["levels"][lod]`` field for field, so the two must
    agree by construction here — a test mutates one side afterwards to break that agreement.
    """
    contents = territory_contents or {"T1": f"tkms-fixture-{lod}".encode("ascii")}
    return {
        "datasetId": dataset_id,
        "datasetName": dataset_id,
        "sourceFormat": "geojson",
        "lod": level["simplification"]["lod"],
        "meshFormat": "TKMS",
        "meshFormatVersion": 1,
        "origin": {
            "lon": 35.0,
            "lat": 39.0,
            "projection": "webmercator-local-meters",
            "scale": 1.0,
        },
        "boundsWgs84": [34.0, 38.0, 36.0, 40.0],
        "boundsLocal": [-1000.0, -1000.0, 1000.0, 1000.0],
        "territoryCount": level["territoryCount"],
        "totals": {
            "vertices": level["vertices"],
            "triangles": level["triangles"],
            "bytes": level["bytes"],
            "repairedTerritories": 0,
        },
        "lossy": level["lossy"],
        "loss": level["loss"],
        "topologyChanged": level["topologyChanged"],
        "pickingUnsafe": level["pickingUnsafe"],
        "simplification": level["simplification"],
        "sourceMetadata": {},
        "territories": [
            _territory_entry(territory_id, content, level)
            for territory_id, content in contents.items()
        ],
    }


def write_healthy_build(build_dir: Path, dataset_id: str = "fixture") -> dict[str, Any]:
    """Write a full, internally-consistent build_lod.py-shaped tree under ``build_dir``, with a
    single territory ``T1``.

    Returns the report dict that was written, so a test can mutate a deep-copied variant and
    write it back over ``lod-report.json`` without needing to re-read it from disk.
    """
    report = healthy_report()
    for lod in LOD_LEVELS:
        level_dir = build_dir / lod
        level_dir.mkdir(parents=True)
        content = f"tkms-fixture-{lod}".encode("ascii")
        manifest = manifest_for_level(lod, report["levels"][lod], dataset_id, {"T1": content})
        (level_dir / "index.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        (level_dir / "T1.tkms").write_bytes(content)
    (build_dir / "lod-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    return copy.deepcopy(report)


def bump_territory_content(
    build_dir: Path, lod: str = "high", territory_id: str = "T1", suffix: str = "-v2"
) -> None:
    """Rewrite one territory's ``.tkms`` bytes (to produce a distinct revision hash on the next
    publish) and update that level's ``index.json`` ``byteLength``/``sha256`` to match.

    ``_validate_manifest_matches_meshes`` now enforces that a published manifest's per-territory
    ``byteLength``/``sha256`` agree with the actual file — tests that used to bump a fixture's
    content by writing raw bytes directly to the ``.tkms`` file (to produce "a second, distinct
    revision" for retention/registry/lease tests unrelated to manifest validation) must go
    through this instead, or they trip the very check they are not testing.
    """
    level_dir = build_dir / lod
    new_content = f"tkms-fixture-{lod}-{territory_id}{suffix}".encode("ascii")
    (level_dir / f"{territory_id}.tkms").write_bytes(new_content)

    manifest_path = level_dir / "index.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["territories"]:
        if entry["id"] == territory_id:
            entry["byteLength"] = len(new_content)
            entry["sha256"] = hashlib.sha256(new_content).hexdigest()
            break
    else:
        raise ValueError(f"no territory {territory_id!r} in {manifest_path}")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def write_healthy_build_with_territories(
    build_dir: Path, territory_ids: tuple[str, ...], dataset_id: str = "fixture"
) -> dict[str, Any]:
    """Like :func:`write_healthy_build`, but with ``len(territory_ids)`` distinct territories,
    each with its own content — for tests that need to delete or swap one territory's mesh file
    independently of another's (``test_publish_validation.py``'s manifest-vs-mesh tests)."""
    report = healthy_report(territory_count=len(territory_ids))
    for lod in LOD_LEVELS:
        level_dir = build_dir / lod
        level_dir.mkdir(parents=True)
        contents = {tid: f"tkms-fixture-{lod}-{tid}".encode("ascii") for tid in territory_ids}
        manifest = manifest_for_level(lod, report["levels"][lod], dataset_id, contents)
        (level_dir / "index.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        for territory_id, content in contents.items():
            (level_dir / f"{territory_id}.tkms").write_bytes(content)
    (build_dir / "lod-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    return copy.deepcopy(report)
