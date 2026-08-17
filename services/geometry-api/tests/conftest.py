"""Shared fixtures.

The hand-written fixtures under ``tests/fixtures`` carry the cases the real dataset cannot:
geoBoundaries TUR ADM1 has zero interior rings, so hole handling has no natural test subject.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import shapely
from shapely.geometry.base import BaseGeometry

from geometry_api.loader import Dataset, Territory, load_dataset
from geometry_api.projection import Origin, project_geometry
from geometry_api.triangulate import (
    TriangulatedMesh,
    quantize_to_storage_precision,
    triangulate,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_DATASET_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "datasets" / "turkey-provinces.geojson"
)

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
"""``scripts/`` is not a package and is not installed, but it holds two pieces of live code —
the geoBoundaries normalization and the CI report checker — that were shipping without a single
test. Put on the path here rather than in the test module so the imports there stay ordinary."""

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def hole_dataset() -> Dataset:
    """Fixture C — one hole."""
    return load_dataset(FIXTURES_DIR / "polygon-with-hole.geojson")


@pytest.fixture(scope="session")
def two_hole_dataset() -> Dataset:
    """Fixture D — two holes; the multi-hole ring-offset bugs earcut itself does not reject."""
    return load_dataset(FIXTURES_DIR / "polygon-with-two-holes.geojson")


@pytest.fixture(scope="session")
def multipolygon_dataset() -> Dataset:
    """Fixture E — three disjoint parts, the third with a hole."""
    return load_dataset(FIXTURES_DIR / "multipolygon.geojson")


@pytest.fixture(scope="session")
def territorykit_dataset() -> Dataset:
    return load_dataset(FIXTURES_DIR / "territorykit-dataset.json")


REQUIRE_SAMPLE_DATASET_ENV = "GEOMETRY_API_REQUIRE_SAMPLE_DATASET"
MISSING_DATASET_MARKER = "sample dataset missing"


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Say out loud when the real-data tests were skipped.

    A skipped test is a green run with a hole in it. Locally that is the right trade — you can
    work without a 9 MB download — but it should never be quiet about which coverage is absent.
    """
    skipped = [
        report
        for report in terminalreporter.stats.get("skipped", [])
        if MISSING_DATASET_MARKER in str(report.longrepr)
    ]
    if not skipped:
        return
    terminalreporter.write_sep("=", "real dataset not verified", yellow=True, bold=True)
    terminalreporter.write_line(
        f"{len(skipped)} test(s) skipped because {SAMPLE_DATASET_PATH} is missing — the "
        "81-province build, point coverage, uint16 ceiling and determinism checks did NOT run."
    )
    terminalreporter.write_line("Run: python scripts/fetch_sample_dataset.py")


@pytest.fixture(scope="session")
def sample_dataset() -> Dataset:
    """The real geoBoundaries TUR ADM1 dataset.

    Skipped locally when the data has not been fetched. In CI the environment variable turns
    that skip into a failure: silently skipping here would mean the province build, the coverage
    sweep, the uint16 ceiling and the determinism check never ran on the target Python at all.
    """
    if not SAMPLE_DATASET_PATH.exists():
        message = (
            f"sample dataset missing at {SAMPLE_DATASET_PATH}; run scripts/fetch_sample_dataset.py"
        )
        if os.environ.get(REQUIRE_SAMPLE_DATASET_ENV):
            pytest.fail(f"{REQUIRE_SAMPLE_DATASET_ENV} is set but the {message}")
        pytest.skip(message)
    return load_dataset(SAMPLE_DATASET_PATH)


@dataclass(frozen=True)
class MeshCase:
    """One territory carried through the whole pipeline, kept together for the assertions.

    ``quantized`` is the polygon snapped to the float32 grid TKMS stores. That, not
    ``projected``, is the surface the mesh claims to represent, so it is the reference the area
    and coverage assertions compare against; the gap between the two is measured separately.
    """

    territory: Territory
    projected: BaseGeometry
    quantized: BaseGeometry
    mesh: TriangulatedMesh

    @property
    def name(self) -> str:
        return self.territory.name


def build_mesh_cases(dataset: Dataset) -> list[MeshCase]:
    origin = Origin(lon=dataset.origin_lon, lat=dataset.origin_lat)
    cases = []
    for territory in dataset:
        projected = project_geometry(territory.geometry, origin)
        quantized = shapely.transform(projected, quantize_to_storage_precision)
        cases.append(MeshCase(territory, projected, quantized, triangulate(projected)))
    return cases


@pytest.fixture(scope="session")
def sample_meshes(sample_dataset: Dataset) -> list[MeshCase]:
    """All 81 provinces projected and triangulated once — the whole run takes ~0.2 s."""
    return build_mesh_cases(sample_dataset)


@pytest.fixture(scope="session")
def hole_mesh(hole_dataset: Dataset) -> MeshCase:
    return build_mesh_cases(hole_dataset)[0]


@pytest.fixture(scope="session")
def two_hole_mesh(two_hole_dataset: Dataset) -> MeshCase:
    return build_mesh_cases(two_hole_dataset)[0]


@pytest.fixture(scope="session")
def multipolygon_mesh(multipolygon_dataset: Dataset) -> MeshCase:
    return build_mesh_cases(multipolygon_dataset)[0]
