"""Dataset loading and normalization.

Reads either a plain GeoJSON ``FeatureCollection`` or a TerritoryKit ``dataset.json``
(``{"manifest": {...}, "zones": [...]}``) and normalizes both into the same in-memory model.
The format is detected from the document shape, not from the file name.

Geometry stays in WGS84 degrees at this layer; the projection to local meters happens in
``projection.py`` so that loading and projecting can be tested independently.

**Invalid geometry policy.** Self-intersecting rings are rejected by default. A bow-tie polygon
is topologically meaningless — its shoelace area is zero and earcut will happily emit a triangle
for it — so accepting one means shipping a mesh that does not represent any region. Repair is
available but must be asked for (``on_invalid="repair"``, ``--repair-invalid`` on the build CLI),
because ``make_valid`` changes the region's shape and that should be a decision, not a default.
Repaired geometry is re-checked and the territory is flagged, so the change reaches the manifest.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import shapely
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity

SourceFormat = Literal["geojson", "territorykit"]
InvalidGeometryPolicy = Literal["reject", "repair"]

SUPPORTED_GEOMETRY_TYPES = frozenset({"Polygon", "MultiPolygon"})

# Tried in order; geoBoundaries uses shapeID/shapeName, TerritoryKit-style exports use id/name.
_ID_PROPERTY_KEYS = ("shapeID", "id", "ID", "shapeISO", "ISO", "iso")
_NAME_PROPERTY_KEYS = ("shapeName", "name", "NAME", "shapeISO")


class DatasetError(ValueError):
    """Raised when a document cannot be read as a dataset, or violates the loader contract."""


@dataclass(frozen=True)
class Territory:
    """One region. ``geometry`` is a shapely Polygon or MultiPolygon in WGS84 degrees."""

    id: str
    name: str
    geometry: BaseGeometry
    level: int | None = None
    parent_id: str | None = None
    neighbor_ids: tuple[str, ...] = ()
    properties: Mapping[str, Any] = field(default_factory=dict)
    repaired: bool = False
    """True when the source geometry was invalid and ``on_invalid="repair"`` rebuilt it."""

    @property
    def part_count(self) -> int:
        if self.geometry.geom_type == "MultiPolygon":
            return len(self.geometry.geoms)
        return 1

    @property
    def hole_count(self) -> int:
        if self.geometry.geom_type == "MultiPolygon":
            return sum(len(part.interiors) for part in self.geometry.geoms)
        return len(self.geometry.interiors)


@dataclass(frozen=True)
class Dataset:
    """A collection of territories plus the metadata needed to project and attribute them."""

    id: str
    name: str
    source_format: SourceFormat
    territories: tuple[Territory, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.territories:
            raise DatasetError("dataset contains no territories")

    def __len__(self) -> int:
        return len(self.territories)

    def __iter__(self) -> Iterator[Territory]:
        return iter(self.territories)

    def by_id(self, territory_id: str) -> Territory:
        for territory in self.territories:
            if territory.id == territory_id:
                return territory
        raise KeyError(territory_id)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(min_lon, min_lat, max_lon, max_lat) over every territory, WGS84 degrees."""
        min_lon = min(t.geometry.bounds[0] for t in self.territories)
        min_lat = min(t.geometry.bounds[1] for t in self.territories)
        max_lon = max(t.geometry.bounds[2] for t in self.territories)
        max_lat = max(t.geometry.bounds[3] for t in self.territories)
        return (min_lon, min_lat, max_lon, max_lat)

    @property
    def origin_lon(self) -> float:
        """Longitude of the projection origin: the centre of the dataset bounds."""
        min_lon, _, max_lon, _ = self.bounds
        return (min_lon + max_lon) / 2.0

    @property
    def origin_lat(self) -> float:
        """Latitude of the projection origin: the centre of the dataset bounds.

        The origin is derived, not configured, so that the same input always yields the same
        local coordinate space — Phase 3's content-addressed cache depends on that.
        """
        _, min_lat, _, max_lat = self.bounds
        return (min_lat + max_lat) / 2.0


def detect_format(document: Mapping[str, Any]) -> SourceFormat:
    """Decide which of the two supported document shapes this is."""
    if document.get("type") == "FeatureCollection" and isinstance(document.get("features"), list):
        return "geojson"
    if isinstance(document.get("zones"), list):
        return "territorykit"
    raise DatasetError(
        "unrecognized dataset document: expected a GeoJSON FeatureCollection or a "
        "TerritoryKit dataset.json with a 'zones' array"
    )


def load_dataset(path: str | Path, on_invalid: InvalidGeometryPolicy = "reject") -> Dataset:
    """Load a dataset from disk, detecting its format."""
    file_path = Path(path)
    try:
        raw = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DatasetError(f"cannot read dataset file {file_path}: {exc}") from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DatasetError(f"{file_path} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise DatasetError(f"{file_path} must contain a JSON object at the top level")
    return load_document(document, fallback_id=file_path.stem, on_invalid=on_invalid)


def load_document(
    document: Mapping[str, Any],
    fallback_id: str = "dataset",
    on_invalid: InvalidGeometryPolicy = "reject",
) -> Dataset:
    """Normalize an already-parsed dataset document."""
    if on_invalid not in ("reject", "repair"):
        raise DatasetError(f"unknown on_invalid policy {on_invalid!r}; use 'reject' or 'repair'")
    source_format = detect_format(document)
    if source_format == "geojson":
        return _load_geojson(document, fallback_id, on_invalid)
    return _load_territorykit(document, fallback_id, on_invalid)


def _load_geojson(
    document: Mapping[str, Any], fallback_id: str, on_invalid: InvalidGeometryPolicy
) -> Dataset:
    metadata = document.get("metadata") or {}
    territories: list[Territory] = []
    for index, feature in enumerate(document.get("features", [])):
        if not isinstance(feature, dict):
            raise DatasetError(f"feature {index} is not an object")
        properties = feature.get("properties") or {}
        territory_id = _first_string(properties, _ID_PROPERTY_KEYS) or f"{fallback_id}-{index}"
        name = _first_string(properties, _NAME_PROPERTY_KEYS) or territory_id
        geometry, repaired = _build_geometry(feature.get("geometry"), territory_id, on_invalid)
        territories.append(
            Territory(
                id=territory_id,
                name=name,
                geometry=geometry,
                properties=dict(properties),
                repaired=repaired,
            )
        )
    _reject_duplicate_ids(territories)
    return Dataset(
        id=str(metadata.get("id") or fallback_id),
        name=str(metadata.get("name") or fallback_id),
        source_format="geojson",
        territories=tuple(territories),
        metadata=dict(metadata),
    )


def _load_territorykit(
    document: Mapping[str, Any], fallback_id: str, on_invalid: InvalidGeometryPolicy
) -> Dataset:
    manifest = document.get("manifest") or {}
    zones = document.get("zones", [])
    parent_by_child = _invert_child_ids(zones)

    territories: list[Territory] = []
    for index, zone in enumerate(zones):
        if not isinstance(zone, dict):
            raise DatasetError(f"zone {index} is not an object")
        zone_id = zone.get("id")
        if not isinstance(zone_id, str) or not zone_id:
            raise DatasetError(f"zone {index} has no usable 'id'")
        properties = zone.get("properties") or {}
        geometry, repaired = _build_geometry(zone.get("geometry"), zone_id, on_invalid)
        level = zone.get("level")
        territories.append(
            Territory(
                id=zone_id,
                name=str(properties.get("name") or zone_id),
                geometry=geometry,
                level=level if isinstance(level, int) else None,
                parent_id=parent_by_child.get(zone_id),
                neighbor_ids=tuple(zone.get("neighborIds") or ()),
                properties=dict(properties),
                repaired=repaired,
            )
        )
    _reject_duplicate_ids(territories)
    return Dataset(
        id=str(manifest.get("datasetId") or fallback_id),
        name=str(manifest.get("name") or manifest.get("datasetId") or fallback_id),
        source_format="territorykit",
        territories=tuple(territories),
        metadata=dict(manifest),
    )


def _invert_child_ids(zones: Sequence[Any]) -> dict[str, str]:
    parent_by_child: dict[str, str] = {}
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        parent_id = zone.get("id")
        if not isinstance(parent_id, str):
            continue
        for child_id in zone.get("childIds") or ():
            if isinstance(child_id, str):
                parent_by_child[child_id] = parent_id
    return parent_by_child


def _build_geometry(
    raw_geometry: Any, territory_id: str, on_invalid: InvalidGeometryPolicy
) -> tuple[BaseGeometry, bool]:
    """Returns the geometry and whether it had to be repaired to get there."""
    if not isinstance(raw_geometry, dict):
        raise DatasetError(f"territory '{territory_id}' has no geometry")
    geometry_type = raw_geometry.get("type")
    if geometry_type not in SUPPORTED_GEOMETRY_TYPES:
        raise DatasetError(
            f"territory '{territory_id}' has unsupported geometry type {geometry_type!r}; "
            f"expected one of {sorted(SUPPORTED_GEOMETRY_TYPES)}"
        )
    try:
        geometry = shape(raw_geometry)
    except (ValueError, TypeError, KeyError) as exc:
        raise DatasetError(f"territory '{territory_id}' has malformed geometry: {exc}") from exc
    if geometry.is_empty:
        raise DatasetError(f"territory '{territory_id}' has empty geometry")

    if geometry.is_valid:
        if geometry.area <= 0.0:
            raise DatasetError(f"territory '{territory_id}' encloses no area")
        return geometry, False

    reason = explain_validity(geometry)
    if on_invalid == "reject":
        raise DatasetError(
            f"territory '{territory_id}' has invalid geometry: {reason}. "
            "Pass on_invalid='repair' (build CLI: --repair-invalid) to rebuild it with "
            "make_valid, which changes the region's shape."
        )
    return _repair_geometry(geometry, territory_id, reason), True


def _repair_geometry(geometry: BaseGeometry, territory_id: str, reason: str) -> BaseGeometry:
    """Rebuild an invalid geometry, then check the result rather than trusting it."""
    repaired = shapely.make_valid(geometry)
    if repaired.geom_type == "GeometryCollection":
        # make_valid can hand back stray lines and points; only surfaces can become a mesh.
        surfaces = [part for part in repaired.geoms if part.geom_type in SUPPORTED_GEOMETRY_TYPES]
        repaired = shapely.union_all(surfaces) if surfaces else repaired

    if (
        repaired.geom_type not in SUPPORTED_GEOMETRY_TYPES
        or repaired.is_empty
        or not repaired.is_valid
        or repaired.area <= 0.0
    ):
        raise DatasetError(
            f"territory '{territory_id}' has invalid geometry ({reason}) and make_valid could "
            f"not produce a usable surface (got {repaired.geom_type}, area {repaired.area})"
        )
    return repaired


def _first_string(properties: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        value = properties.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, int):
            return str(value)
    return None


def _reject_duplicate_ids(territories: Sequence[Territory]) -> None:
    seen: set[str] = set()
    for territory in territories:
        if territory.id in seen:
            raise DatasetError(f"duplicate territory id '{territory.id}'")
        seen.add(territory.id)
