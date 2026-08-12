"""Shared fixtures.

The hand-written fixtures under ``tests/fixtures`` carry the cases the real dataset cannot:
geoBoundaries TUR ADM1 has zero interior rings, so hole handling has no natural test subject.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from geometry_api.loader import Dataset, load_dataset

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
    """Fixture D — two holes; a single hole passes even if ring start/end offsets are confused."""
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
