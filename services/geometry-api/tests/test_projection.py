"""Projection: implementation error (round-trip) and model error (cos(originLat) drift).

These are two different numbers and are kept in two differently named tests on purpose. The
round-trip test measures whether the code is faithful to its own formulas; the scale test
measures how wrong those formulas are as a model of the Earth.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from geometry_api.loader import Dataset
from geometry_api.projection import (
    EARTH_RADIUS_M,
    MAX_LATITUDE_DEG,
    Origin,
    ProjectionError,
    inverse_mercator,
    mercator,
    project,
    project_coords,
    project_geometry,
    unproject,
    unproject_coords,
)

ANKARA = (32.8597, 39.9334)
ISTANBUL = (28.9784, 41.0082)


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance on the same sphere the projection assumes."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def test_matches_the_worked_example_in_docs_projection_md() -> None:
    origin = Origin(lon=ANKARA[0], lat=ANKARA[1])
    assert origin.x_merc == pytest.approx(3_657_925.07, abs=0.01)
    assert origin.y_merc == pytest.approx(4_856_268.86, abs=0.01)
    assert origin.scale == pytest.approx(0.766791, abs=1e-6)

    x, y = project(ISTANBUL[0], ISTANBUL[1], origin)
    assert float(x) == pytest.approx(-331_303.09, abs=0.01)
    assert float(y) == pytest.approx(120_602.72, abs=0.01)


def test_origin_projects_to_the_local_origin() -> None:
    origin = Origin(lon=ANKARA[0], lat=ANKARA[1])
    x, y = project(ANKARA[0], ANKARA[1], origin)
    assert float(x) == pytest.approx(0.0, abs=1e-9)
    assert float(y) == pytest.approx(0.0, abs=1e-9)


def test_mercator_inverse_is_exact_for_a_grid() -> None:
    lons = np.linspace(-179.0, 179.0, 37)
    lats = np.linspace(-84.0, 84.0, 37)
    x, y = mercator(lons, lats)
    back_lon, back_lat = inverse_mercator(x, y)
    assert np.allclose(back_lon, lons, atol=1e-10)
    assert np.allclose(back_lat, lats, atol=1e-10)


def _probe_points(dataset: Dataset) -> np.ndarray:
    probes: list[tuple[float, float]] = []
    for territory in dataset:
        min_lon, min_lat, max_lon, max_lat = territory.geometry.bounds
        centroid = territory.geometry.centroid
        probes.extend(
            [
                (centroid.x, centroid.y),
                (min_lon, min_lat),
                (min_lon, max_lat),
                (max_lon, min_lat),
                (max_lon, max_lat),
            ]
        )
    return np.array(probes, dtype=np.float64)


def test_roundtrip_precision_under_one_meter(sample_dataset: Dataset) -> None:
    """The contract measured on the real path: project -> **float32** -> unproject.

    float32 is what TKMS stores, so a round-trip that stays in float64 measures only that the
    two formulas invert each other. Including the cast is what makes the number mean anything
    to a Unity client reading a mesh back.
    """
    origin = Origin(lon=sample_dataset.origin_lon, lat=sample_dataset.origin_lat)
    source = _probe_points(sample_dataset)

    stored = project_coords(source, origin).astype(np.float32).astype(np.float64)
    roundtripped = unproject_coords(stored, origin)
    errors = np.array(
        [
            _haversine_m(lon, lat, back_lon, back_lat)
            for (lon, lat), (back_lon, back_lat) in zip(source, roundtripped, strict=True)
        ]
    )

    assert len(errors) == 81 * 5
    assert errors.max() < 1.0, f"worst round-trip error {errors.max():.4f} m"
    # The quantization floor: a float32 step is a few centimetres at Turkey's distances from
    # the origin. Two-sided, so a change that improves *or* degrades this is noticed.
    assert 0.01 < errors.max() < 0.1, f"expected centimetre-scale, measured {errors.max():.4f} m"
    assert float(np.percentile(errors, 95)) < 0.05


def test_float64_roundtrip_is_exact_which_is_why_the_cast_must_be_measured(
    sample_dataset: Dataset,
) -> None:
    """Without the float32 cast the error is ~1e-9 m — the formulas, not the pipeline."""
    origin = Origin(lon=sample_dataset.origin_lon, lat=sample_dataset.origin_lat)
    source = _probe_points(sample_dataset)

    roundtripped = unproject_coords(project_coords(source, origin), origin)
    errors = [
        _haversine_m(lon, lat, back_lon, back_lat)
        for (lon, lat), (back_lon, back_lat) in zip(source, roundtripped, strict=True)
    ]
    assert max(errors) < 1e-6


def test_scale_error_across_dataset_bbox(sample_dataset: Dataset) -> None:
    """The cos(originLat) model is exact only at the origin; measure how far it drifts.

    This asserts a *band*, not a ceiling. If someone swaps in an equal-area projection the
    lower bound fails and forces the change to be deliberate.
    """
    origin = Origin(lon=sample_dataset.origin_lon, lat=sample_dataset.origin_lat)
    min_lon, min_lat, _, max_lat = sample_dataset.bounds

    probe_lats = np.linspace(min_lat, max_lat, 25)
    east_west_errors: list[float] = []
    north_south_errors: list[float] = []

    for lat in probe_lats:
        # A ~1 km segment in each direction, measured on the sphere and in local metres.
        d_lon = math.degrees(1000.0 / (EARTH_RADIUS_M * math.cos(math.radians(lat))))
        d_lat = math.degrees(1000.0 / EARTH_RADIUS_M)

        for axis, errors in (("lon", east_west_errors), ("lat", north_south_errors)):
            end_lon = min_lon + (d_lon if axis == "lon" else 0.0)
            end_lat = lat + (d_lat if axis == "lat" else 0.0)
            true_m = _haversine_m(min_lon, lat, end_lon, end_lat)

            (x0, y0), (x1, y1) = project_coords(
                np.array([[min_lon, lat], [end_lon, end_lat]]), origin
            )
            local_m = math.hypot(x1 - x0, y1 - y0)
            errors.append(local_m / true_m - 1.0)

    d_lon_origin = math.degrees(1000.0 / (EARTH_RADIUS_M * math.cos(math.radians(origin.lat))))
    (ox0, oy0), (ox1, oy1) = project_coords(
        np.array([[min_lon, origin.lat], [min_lon + d_lon_origin, origin.lat]]), origin
    )
    error_at_origin = (
        math.hypot(ox1 - ox0, oy1 - oy0)
        / _haversine_m(min_lon, origin.lat, min_lon + d_lon_origin, origin.lat)
        - 1.0
    )
    assert abs(error_at_origin) < 1e-9, "the model is exact at the origin latitude"

    worst = max(abs(e) for e in east_west_errors)
    assert 0.03 < worst < 0.06, (
        f"expected the documented ~5% drift over this bbox, measured {worst:.4f}"
    )

    assert east_west_errors[0] < 0, "south of the origin the local metre is too short"
    assert east_west_errors[-1] > 0, "north of the origin the local metre is too long"
    # Conformality is a point property; over a finite 1 km north-south segment the scale
    # varies slightly along the segment, which leaves a second-order residual of ~5e-5.
    assert np.allclose(east_west_errors, north_south_errors, atol=1e-4), (
        "Mercator is conformal: the drift must be the same in both axes, so shapes are "
        "preserved and only scale is wrong"
    )


def test_scale_error_grows_with_distance_from_origin(sample_dataset: Dataset) -> None:
    origin = Origin(lon=sample_dataset.origin_lon, lat=sample_dataset.origin_lat)
    min_lon, min_lat, _, max_lat = sample_dataset.bounds

    offsets = np.linspace(0.0, min(max_lat - origin.lat, origin.lat - min_lat), 12)
    errors: list[float] = []
    for offset in offsets:
        lat = origin.lat + offset
        d_lon = math.degrees(1000.0 / (EARTH_RADIUS_M * math.cos(math.radians(lat))))
        true_m = _haversine_m(min_lon, lat, min_lon + d_lon, lat)
        (x0, y0), (x1, y1) = project_coords(
            np.array([[min_lon, lat], [min_lon + d_lon, lat]]), origin
        )
        errors.append(abs(math.hypot(x1 - x0, y1 - y0) / true_m - 1.0))

    assert all(b >= a - 1e-9 for a, b in zip(errors[:-1], errors[1:], strict=True)), errors


def test_project_geometry_preserves_parts_and_holes(multipolygon_dataset: Dataset) -> None:
    territory = multipolygon_dataset.territories[0]
    origin = Origin(lon=multipolygon_dataset.origin_lon, lat=multipolygon_dataset.origin_lat)
    projected = project_geometry(territory.geometry, origin)

    assert projected.geom_type == "MultiPolygon"
    assert len(projected.geoms) == 3
    assert sum(len(part.interiors) for part in projected.geoms) == 1
    assert projected.area > 0


def test_unproject_reverses_project_for_arrays() -> None:
    origin = Origin(lon=ANKARA[0], lat=ANKARA[1])
    lons = np.linspace(26.0, 44.0, 19)
    lats = np.linspace(36.0, 42.0, 19)
    x, y = project(lons, lats, origin)
    back_lon, back_lat = unproject(x, y, origin)
    assert np.allclose(back_lon, lons, atol=1e-9)
    assert np.allclose(back_lat, lats, atol=1e-9)


@pytest.mark.parametrize("bad_lat", [MAX_LATITUDE_DEG + 0.001, -90.0, float("nan")])
def test_rejects_latitudes_outside_the_mercator_range(bad_lat: float) -> None:
    with pytest.raises(ProjectionError):
        mercator(0.0, bad_lat)


def test_rejects_out_of_range_origin_longitude() -> None:
    with pytest.raises(ProjectionError, match="longitude"):
        Origin(lon=200.0, lat=39.0)


def test_rejects_malformed_coordinate_array() -> None:
    origin = Origin(lon=ANKARA[0], lat=ANKARA[1])
    with pytest.raises(ProjectionError, match=r"\(N, 2\)"):
        project_coords(np.array([1.0, 2.0, 3.0]), origin)
