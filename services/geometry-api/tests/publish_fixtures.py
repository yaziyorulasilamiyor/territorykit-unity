"""Builds a minimal, internally-consistent ``build_lod.py``-shaped output on disk.

Used by ``test_publish_validation.py`` and ``test_publish_atomicity.py`` to exercise
``scripts/publish_dataset.py`` without running the real geoBoundaries/TerritoryKit chain — this
fixture is deliberately lossless and change-free at every level (no loss events anywhere), which
is enough to satisfy every identity ``manifest_validation.check()`` recomputes while keeping the
fixture small and easy to mutate one field at a time in a test.
"""

from __future__ import annotations

import copy
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


def _level(lod: str) -> dict[str, Any]:
    return {
        "territoryCount": 1,
        "vertices": _VERTEX_COUNTS[lod],
        "triangles": _TRIANGLE_COUNTS[lod],
        "bytes": _BYTE_COUNTS[lod],
        "simplification": _simplification(lod, _VERTEX_COUNTS[lod]),
        "loss": _empty_loss(),
        "lossy": False,
        "topologyChanged": False,
        "pickingUnsafe": False,
    }


def healthy_report() -> dict[str, Any]:
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
        "levels": {lod: _level(lod) for lod in LOD_LEVELS},
        "vertexRatioToHigh": {
            lod: _VERTEX_COUNTS[lod] / _VERTEX_COUNTS["high"] for lod in LOD_LEVELS
        },
    }


def manifest_for_level(
    lod: str, level: dict[str, Any], dataset_id: str = "fixture"
) -> dict[str, Any]:
    """An ``index.json``-shaped dict whose comparison fields exactly match ``level``.

    ``publish_dataset.py``'s cross-check (``manifest_validation.check_report_matches_build``)
    compares these fields against ``report["levels"][lod]`` field for field, so the two must
    agree by construction here — a test mutates one side afterwards to break that agreement.
    """
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
            {
                "id": "T1",
                "name": "Territory 1",
                "file": "T1.tkms",
                "vertexCount": level["vertices"],
                "triangleCount": level["triangles"],
                "partCount": 1,
                "indexFormat": "uint16",
                "byteLength": level["bytes"],
                "bboxLocal": [-1.0, -1.0, 1.0, 1.0],
                "lossy": False,
                "lossEvents": [],
                "repaired": False,
                "parentId": None,
                "administrativeLevel": 1,
                "neighborIds": [],
            }
        ],
    }


def write_healthy_build(build_dir: Path, dataset_id: str = "fixture") -> dict[str, Any]:
    """Write a full, internally-consistent build_lod.py-shaped tree under ``build_dir``.

    Returns the report dict that was written, so a test can mutate a deep-copied variant and
    write it back over ``lod-report.json`` without needing to re-read it from disk.
    """
    report = healthy_report()
    for lod in LOD_LEVELS:
        level_dir = build_dir / lod
        level_dir.mkdir(parents=True)
        manifest = manifest_for_level(lod, report["levels"][lod], dataset_id)
        (level_dir / "index.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        (level_dir / "T1.tkms").write_bytes(f"tkms-fixture-{lod}".encode("ascii"))
    (build_dir / "lod-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    return copy.deepcopy(report)
