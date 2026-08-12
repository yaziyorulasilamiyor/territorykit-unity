"""Shared fixtures.

The hand-written fixtures under ``tests/fixtures`` carry the cases the real dataset cannot:
geoBoundaries TUR ADM1 has zero interior rings, so hole handling has no natural test subject.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from shapely.geometry.base import BaseGeometry

from geometry_api.loader import Dataset, Territory, load_dataset
from geometry_api.projection import Origin, project_geometry
from geometry_api.triangulate import TriangulatedMesh, triangulate

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_DATASET_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "datasets" / "turkey-provinces.geojson"
)


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


@pytest.fixture(scope="session")
def sample_dataset() -> Dataset:
    """The real geoBoundaries TUR ADM1 dataset; skipped when it has not been fetched."""
    if not SAMPLE_DATASET_PATH.exists():
        pytest.skip(
            f"sample dataset missing at {SAMPLE_DATASET_PATH}; run scripts/fetch_sample_dataset.py"
        )
    return load_dataset(SAMPLE_DATASET_PATH)


@dataclass(frozen=True)
class MeshCase:
    """One territory carried through the whole pipeline, kept together for the assertions."""

    territory: Territory
    projected: BaseGeometry
    mesh: TriangulatedMesh

    @property
    def name(self) -> str:
        return self.territory.name


def build_mesh_cases(dataset: Dataset) -> list[MeshCase]:
    origin = Origin(lon=dataset.origin_lon, lat=dataset.origin_lat)
    cases = []
    for territory in dataset:
        projected = project_geometry(territory.geometry, origin)
        cases.append(MeshCase(territory, projected, triangulate(projected)))
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
