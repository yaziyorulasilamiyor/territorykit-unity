"""WGS84 degrees to dataset-local metres, and back.

The pipeline is fixed by ``docs/projection.md``::

    WGS84 (lon, lat) -> Web Mercator (EPSG:3857) -> subtract origin -> multiply by cos(originLat)

Subtracting the origin is what makes float32 usable on the Unity side: absolute Mercator
coordinates for Turkey run to millions of metres, and quantising those accumulates into visible
jitter once Unity's transform and depth maths get hold of them.

The ``cos(originLat)`` factor is a deliberate approximation. It is exact only at the origin
latitude and drifts as the dataset extends north or south of it; ``tests/test_projection.py``
measures that drift and asserts the band it must stay in, so replacing this model with a proper
equal-area projection is a conscious decision rather than a silent one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from numpy.typing import ArrayLike, NDArray
from shapely.geometry.base import BaseGeometry

EARTH_RADIUS_M = 6378137.0
"""WGS84 semi-major axis, used as the spherical radius by EPSG:3857."""

MAX_LATITUDE_DEG = 85.051128779806604
"""Web Mercator cut-off; y tends to infinity at the poles."""


class ProjectionError(ValueError):
    """Raised for coordinates the projection cannot represent."""


@dataclass(frozen=True)
class Origin:
    """The local coordinate space of one dataset.

    Defined at dataset level, never per mesh — every mesh in a dataset is interpreted in the
    same space, so Unity fetches this once.
    """

    lon: float
    lat: float
    x_merc: float = field(init=False)
    y_merc: float = field(init=False)
    scale: float = field(init=False)

    def __post_init__(self) -> None:
        _validate_lat(self.lat)
        if not -180.0 <= self.lon <= 180.0:
            raise ProjectionError(f"origin longitude {self.lon} is outside [-180, 180]")
        scale = math.cos(math.radians(self.lat))
        if scale <= 0.0:
            raise ProjectionError(f"origin latitude {self.lat} gives a non-positive scale factor")
        x_merc, y_merc = mercator(self.lon, self.lat)
        object.__setattr__(self, "x_merc", float(x_merc))
        object.__setattr__(self, "y_merc", float(y_merc))
        object.__setattr__(self, "scale", scale)


def mercator(lon: ArrayLike, lat: ArrayLike) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """WGS84 degrees to Web Mercator metres."""
    lon_array = np.asarray(lon, dtype=np.float64)
    lat_array = np.asarray(lat, dtype=np.float64)
    _validate_lat(lat_array)
    x = EARTH_RADIUS_M * np.radians(lon_array)
    y = EARTH_RADIUS_M * np.log(np.tan(np.pi / 4.0 + np.radians(lat_array) / 2.0))
    return x, y


def inverse_mercator(x: ArrayLike, y: ArrayLike) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Web Mercator metres back to WGS84 degrees."""
    x_array = np.asarray(x, dtype=np.float64)
    y_array = np.asarray(y, dtype=np.float64)
    lon = np.degrees(x_array / EARTH_RADIUS_M)
    lat = np.degrees(2.0 * np.arctan(np.exp(y_array / EARTH_RADIUS_M)) - np.pi / 2.0)
    return lon, lat


def project(
    lon: ArrayLike, lat: ArrayLike, origin: Origin
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """WGS84 degrees to dataset-local metres."""
    x_merc, y_merc = mercator(lon, lat)
    return (x_merc - origin.x_merc) * origin.scale, (y_merc - origin.y_merc) * origin.scale


def unproject(
    x: ArrayLike, y: ArrayLike, origin: Origin
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Dataset-local metres back to WGS84 degrees."""
    x_array = np.asarray(x, dtype=np.float64)
    y_array = np.asarray(y, dtype=np.float64)
    return inverse_mercator(
        x_array / origin.scale + origin.x_merc,
        y_array / origin.scale + origin.y_merc,
    )


def project_coords(coords: ArrayLike, origin: Origin) -> NDArray[np.float64]:
    """Project an (N, 2) array of lon/lat pairs into local metres."""
    array = _as_coordinate_array(coords)
    x, y = project(array[:, 0], array[:, 1], origin)
    return np.column_stack([x, y])


def unproject_coords(coords: ArrayLike, origin: Origin) -> NDArray[np.float64]:
    """Inverse of :func:`project_coords`."""
    array = _as_coordinate_array(coords)
    lon, lat = unproject(array[:, 0], array[:, 1], origin)
    return np.column_stack([lon, lat])


def project_geometry(geometry: BaseGeometry, origin: Origin) -> BaseGeometry:
    """Project a shapely geometry, preserving its ring and part structure."""
    return shapely.transform(geometry, lambda coords: project_coords(coords, origin))


def _as_coordinate_array(coords: ArrayLike) -> NDArray[np.float64]:
    array = np.asarray(coords, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] < 2:
        raise ProjectionError(f"expected an (N, 2) coordinate array, got shape {array.shape}")
    return array


def _validate_lat(lat: ArrayLike) -> None:
    lat_array = np.asarray(lat, dtype=np.float64)
    if not np.all(np.isfinite(lat_array)):
        raise ProjectionError("latitude contains NaN or infinity")
    if np.any(np.abs(lat_array) > MAX_LATITUDE_DEG):
        raise ProjectionError(
            f"latitude outside the Web Mercator range of +/-{MAX_LATITUDE_DEG} degrees"
        )
