"""Geometry assertions shared by the test modules.

These are deliberately independent implementations — they must not reuse the production
helpers in ``geometry_api``, or a bug in those helpers would validate itself.
"""

from __future__ import annotations

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry


def triangle_signed_areas(vertices: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Signed area of each triangle. Positive is counter-clockwise, negative is clockwise."""
    corners = np.asarray(vertices, dtype=np.float64)[np.asarray(indices).reshape(-1, 3)]
    a, b, c = corners[:, 0], corners[:, 1], corners[:, 2]
    cross = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
    return 0.5 * cross


def random_points_inside(
    geometry: BaseGeometry, count: int, seed: int = 0, max_rounds: int = 500
) -> np.ndarray:
    """Rejection-sample ``count`` points strictly inside ``geometry``.

    Points in holes and between MultiPolygon parts are excluded by shapely, which is exactly
    what the coverage test needs: every sampled point must land in the meshed area.
    """
    rng = np.random.default_rng(seed)
    min_x, min_y, max_x, max_y = geometry.bounds
    shapely.prepare(geometry)
    collected: list[np.ndarray] = []
    found = 0
    batch = max(count * 4, 512)
    for _ in range(max_rounds):
        xs = rng.uniform(min_x, max_x, batch)
        ys = rng.uniform(min_y, max_y, batch)
        inside = shapely.contains_xy(geometry, xs, ys)
        if inside.any():
            collected.append(np.column_stack([xs[inside], ys[inside]]))
            found += int(inside.sum())
        if found >= count:
            return np.concatenate(collected)[:count]
    raise AssertionError(
        f"could not sample {count} interior points after {max_rounds} rounds "
        f"(geometry area {geometry.area}, bounds {geometry.bounds})"
    )


def count_containing_triangles(
    vertices: np.ndarray, indices: np.ndarray, points: np.ndarray
) -> np.ndarray:
    """For each point, how many triangles contain it.

    A point exactly on a shared edge would count for both neighbours, but the sampled points
    are random floats, so landing exactly on an edge has probability zero in practice.

    The bounding-box prefilter is what makes this affordable: without it, Mugla's ~120k
    triangles times 1000 points would dominate the test run.
    """
    corners = np.asarray(vertices, dtype=np.float64)[np.asarray(indices).reshape(-1, 3)]
    min_x = corners[:, :, 0].min(axis=1)
    max_x = corners[:, :, 0].max(axis=1)
    min_y = corners[:, :, 1].min(axis=1)
    max_y = corners[:, :, 1].max(axis=1)

    counts = np.zeros(len(points), dtype=np.int64)
    for i, (px, py) in enumerate(points):
        candidates = corners[(min_x <= px) & (px <= max_x) & (min_y <= py) & (py <= max_y)]
        if len(candidates) == 0:
            continue
        a, b, c = candidates[:, 0], candidates[:, 1], candidates[:, 2]
        d1 = (px - b[:, 0]) * (a[:, 1] - b[:, 1]) - (a[:, 0] - b[:, 0]) * (py - b[:, 1])
        d2 = (px - c[:, 0]) * (b[:, 1] - c[:, 1]) - (b[:, 0] - c[:, 0]) * (py - c[:, 1])
        d3 = (px - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (py - a[:, 1])
        has_negative = (d1 < 0) | (d2 < 0) | (d3 < 0)
        has_positive = (d1 > 0) | (d2 > 0) | (d3 > 0)
        counts[i] = int(np.count_nonzero(~(has_negative & has_positive)))
    return counts
