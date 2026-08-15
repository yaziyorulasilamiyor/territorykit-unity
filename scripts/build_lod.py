"""Builds the three detail levels from a raw geoBoundaries GeoJSON, end to end.

    python scripts/build_lod.py --input <geoBoundaries.geojson> --output <dir>

The chain:

    geoBoundaries GeoJSON
      -> normalize            (this script; see below)
      -> territory import geoboundaries      (vendor/territorykit CLI)
    dataset.json
      -> python -m geometry_api.build --lod {high,medium,low}
    TKMS meshes x 3 levels

**Why normalization is needed.** The files geoBoundaries publishes are not accepted by
TerritoryKit's own geoboundaries importer. Two things stop it, and both are in the source data
rather than in anything this project does:

* ``properties.shapeGroup`` holds an ISO alpha-3 code (``TUR``) and the importer requires
  alpha-2, so every feature fails with ``SOURCE_COUNTRY_MISMATCH`` — 81 of 81 here. The
  importer's own example command hits this.
* Seven rings in Muğla and İstanbul are real islets — 2.0 to 6.1 m² in this project's local
  projection — that fall under the importer's 1e-9 deg² area floor, and it rejects them as
  ``GEOMETRY_RING_ZERO_AREA`` with ``repairable: false``, which stops the whole import.

Both fixes are applied to a copy; the source file is never modified. Every dropped part is
counted and printed, and lands in ``lod-report.json`` — an islet disappearing is a real change
to the data and is not something to do quietly.

**Determinism.** ``--build-date`` is pinned and passed to the TerritoryKit importer, which
otherwise stamps the current time into the dataset and makes every run differ. With it pinned
the whole chain is byte-reproducible, which the phase 2 determinism test asserts.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
TERRITORY_CLI = REPO_ROOT / "vendor" / "territorykit" / "packages" / "cli" / "dist" / "index.mjs"
GEOMETRY_API = REPO_ROOT / "services" / "geometry-api"

LOD_LEVELS = ("high", "medium", "low")

RING_AREA_FLOOR = 1e-9
"""TerritoryKit's zero-area threshold in square degrees; rings below it stop the import."""

DEFAULT_BUILD_DATE = "2026-01-01T00:00:00Z"
"""Pinned so the importer does not stamp a timestamp and break byte-reproducibility."""


class BuildLodError(RuntimeError):
    """Raised when a step of the chain fails. The message says which step and why."""


@dataclass
class Normalization:
    """What normalizing the source cost, so the report can state it rather than imply it."""

    feature_count: int
    source_country_code: str | None
    country_code: str
    country_codes_rewritten: int
    dropped_parts: int
    dropped_part_details: list[str]
    dropped_holes: int = 0
    dropped_hole_details: list[str] = field(default_factory=list)
    """Interior rings below the area floor.

    Zero on this dataset — geoBoundaries TUR ADM1 has no holes at all — but the filter that
    removes them ran without counting them, which is the same silent-loss shape phase 1 spent two
    review rounds closing. Counted here so the day a dataset with enclaves arrives, the number
    exists before anyone has to go looking for it.
    """

    def as_dict(self) -> dict[str, Any]:
        """The loss records first, then a ``lossy`` derived from them and from nothing else.

        Mirrors ``geometry_api.loss.derive_lossy`` rather than importing it: this script runs the
        geometry package as a subprocess precisely so it does not depend on it being importable
        here. The duplication is not left to trust — ``scripts/check_lod_report.py`` recomputes
        the flag from the same records and fails CI if the two ever disagree, and nothing
        downstream reads this boolean to decide anything (``collect_loss`` ignores it and looks
        at the counts).
        """
        records: dict[str, Any] = {
            "droppedParts": self.dropped_parts,
            "droppedPartDetails": self.dropped_part_details,
            "droppedHoles": self.dropped_holes,
            "droppedHoleDetails": self.dropped_hole_details,
        }
        return {
            "featureCount": self.feature_count,
            "sourceCountryCode": self.source_country_code,
            "countryCode": self.country_code,
            "countryCodesRewritten": self.country_codes_rewritten,
            **records,
            "lossy": any(bool(value) for value in records.values()),
        }


def _ring_area(ring: list[list[float]]) -> float:
    """Shoelace area in square degrees. Only used to compare against TerritoryKit's floor."""
    total = 0.0
    for index in range(len(ring) - 1):
        current, following = ring[index], ring[index + 1]
        total += current[0] * following[1] - following[0] * current[1]
    return abs(total / 2.0)


def normalize_geoboundaries(source: Path, destination: Path, country: str) -> Normalization:
    """Write a copy TerritoryKit's importer will accept, and report what changed.

    The country code is set to the alpha-2 code the caller asked for rather than derived from
    ``shapeGroup``, because there is no way to derive one from the other: geoBoundaries writes
    alpha-3 and truncating ``TUR`` gives ``TU``, not ``TR``. The importer's cross-check is not
    lost, only moved — every feature must already agree on one ``shapeGroup``, so a file mixing
    two countries is still rejected here instead of being quietly relabelled.
    """
    document = json.loads(source.read_text(encoding="utf-8"))
    features = document.get("features")
    if not isinstance(features, list) or not features:
        raise BuildLodError(f"{source} is not a GeoJSON FeatureCollection with features")

    groups = {
        feature.get("properties", {}).get("shapeGroup")
        for feature in features
        if isinstance(feature.get("properties"), dict)
    }
    groups.discard(None)
    if len(groups) > 1:
        raise BuildLodError(
            f"{source} mixes several shapeGroup values ({sorted(groups)}); it holds more than "
            f"one country and cannot be imported as '{country}'"
        )
    source_country = next(iter(groups), None)

    rewritten = 0
    dropped = 0
    details: list[str] = []
    dropped_holes = 0
    hole_details: list[str] = []

    for feature in features:
        properties = feature.setdefault("properties", {})
        if properties.get("shapeGroup") != country:
            properties["shapeGroup"] = country
            rewritten += 1

        geometry = feature.get("geometry") or {}
        name = properties.get("shapeName", "?")
        if geometry.get("type") == "Polygon":
            parts = [geometry["coordinates"]]
        elif geometry.get("type") == "MultiPolygon":
            parts = list(geometry["coordinates"])
        else:
            raise BuildLodError(f"feature '{name}' has unsupported geometry {geometry.get('type')}")

        kept = []
        for part in parts:
            if _ring_area(part[0]) <= RING_AREA_FLOOR:
                dropped += 1
                details.append(f"{name}: islet of {_ring_area(part[0]):.2e} deg^2")
                continue
            holes = []
            for ring in part[1:]:
                ring_area = _ring_area(ring)
                if ring_area > RING_AREA_FLOOR:
                    holes.append(ring)
                else:
                    dropped_holes += 1
                    hole_details.append(f"{name}: interior ring of {ring_area:.2e} deg^2")
            kept.append([part[0]] + holes)

        if not kept:
            raise BuildLodError(f"every part of '{name}' is below the {RING_AREA_FLOOR} area floor")

        if len(kept) == 1 and geometry["type"] == "Polygon":
            geometry["coordinates"] = kept[0]
        else:
            geometry["type"] = "MultiPolygon"
            geometry["coordinates"] = kept

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(document), encoding="utf-8")
    return Normalization(
        feature_count=len(features),
        source_country_code=source_country,
        country_code=country,
        country_codes_rewritten=rewritten,
        dropped_parts=dropped,
        dropped_part_details=details,
        dropped_holes=dropped_holes,
        dropped_hole_details=hole_details,
    )


def run_step(command: list[str], step: str, cwd: Path | None = None) -> str:
    """Run a subprocess and turn a failure into a message that says which step broke."""
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", check=False, cwd=cwd
        )
    except OSError as exc:
        raise BuildLodError(f"{step}: could not run {command[0]}: {exc}") from exc

    if completed.returncode != 0:
        output = (completed.stdout or "") + (completed.stderr or "")
        raise BuildLodError(f"{step} failed (exit {completed.returncode}):\n{output.strip()}")
    return completed.stdout


def import_dataset(normalized: Path, output: Path, country: str, level: str, date: str) -> Path:
    """Run TerritoryKit's importer. Its JSON output is checked, not just its exit code."""
    if not TERRITORY_CLI.exists():
        raise BuildLodError(
            f"TerritoryKit CLI not built at {TERRITORY_CLI}.\n"
            f"Build it first:\n"
            f"  cd vendor/territorykit\n"
            f"  corepack pnpm install\n"
            f'  corepack pnpm --filter "@territory-kit/cli..." build'
        )

    stdout = run_step(
        [
            "node",
            str(TERRITORY_CLI),
            "import",
            "geoboundaries",
            "--country",
            country,
            "--admin-level",
            level,
            "--input",
            str(normalized),
            "--output",
            str(output),
            "--build-date",
            date,
            "--force",
        ],
        "territory import geoboundaries",
    )

    try:
        result = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise BuildLodError(f"importer produced output that is not JSON: {exc}") from exc
    if not result.get("ok"):
        issues = json.dumps(result.get("issues", [])[:5], indent=2, ensure_ascii=False)
        raise BuildLodError(f"importer reported failure. First issues:\n{issues}")

    dataset = output / "dataset.json"
    if not dataset.exists():
        raise BuildLodError(f"importer reported success but wrote no dataset.json to {output}")
    return dataset


def build_level(dataset: Path, output: Path, lod: str, upstream_loss: Path) -> dict[str, Any]:
    """Run the geometry pipeline for one level and hand back its manifest.

    ``upstream_loss`` carries the normalization losses forward, so each level's manifest accounts
    for everything dropped on the way to it and not only for what its own two stages dropped.
    """
    run_step(
        [
            sys.executable,
            "-m",
            "geometry_api.build",
            "--input",
            str(dataset),
            "--output",
            str(output),
            "--lod",
            lod,
            "--upstream-loss",
            str(upstream_loss),
            "--clean",
            "--quiet",
        ],
        f"geometry_api.build --lod {lod}",
        # The package lives under services/geometry-api; run there so `-m` resolves it whether
        # or not it happens to be installed into the interpreter running this script.
        cwd=GEOMETRY_API,
    )
    manifest_path = output / "index.json"
    if not manifest_path.exists():
        raise BuildLodError(f"build for lod '{lod}' wrote no index.json to {output}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/build_lod.py",
        description="Build high/medium/low TKMS meshes from a geoBoundaries GeoJSON.",
    )
    parser.add_argument("--input", required=True, type=Path, help="raw geoBoundaries .geojson")
    parser.add_argument("--output", required=True, type=Path, help="output root directory")
    parser.add_argument("--country", default="TR", help="ISO 3166-1 alpha-2 code (default: TR)")
    parser.add_argument("--admin-level", default="ADM1", help="ADM0..ADM5 (default: ADM1)")
    parser.add_argument(
        "--build-date",
        default=DEFAULT_BUILD_DATE,
        help=f"pinned import timestamp (default: {DEFAULT_BUILD_DATE}); changing it changes the "
        f"output bytes",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="remove the output directory first so it describes exactly one run",
    )
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"error: {args.input} does not exist", file=sys.stderr)
        return 1

    if args.clean and args.output.exists():
        shutil.rmtree(args.output)

    normalized = args.output / "normalized.geojson"
    dataset_dir = args.output / "dataset"

    try:
        normalization = normalize_geoboundaries(args.input, normalized, args.country)
        print(
            f"normalized {normalization.feature_count} features "
            f"({normalization.country_codes_rewritten} country codes rewritten "
            f"{normalization.source_country_code!r} -> {normalization.country_code!r}, "
            f"{normalization.dropped_parts} sub-threshold parts and "
            f"{normalization.dropped_holes} interior rings dropped)"
        )
        for detail in normalization.dropped_part_details + normalization.dropped_hole_details:
            print(f"  dropped {detail}")

        # Written before the builds so each level can fold it into its own manifest.
        upstream_loss_path = args.output / "upstream-loss.json"
        upstream_loss_path.write_text(
            json.dumps(normalization.as_dict(), indent=2, sort_keys=True, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )

        dataset = import_dataset(
            normalized, dataset_dir, args.country, args.admin_level, args.build_date
        )
        print(f"imported dataset -> {dataset}")

        levels: dict[str, Any] = {}
        for lod in LOD_LEVELS:
            manifest = build_level(dataset, args.output / lod, lod, upstream_loss_path)
            levels[lod] = {
                "territoryCount": manifest["territoryCount"],
                "vertices": manifest["totals"]["vertices"],
                "triangles": manifest["totals"]["triangles"],
                "bytes": manifest["totals"]["bytes"],
                "simplification": manifest["simplification"],
                # The whole per-level loss block, not just the flag it produced. Carrying only
                # the flag meant the checker had nothing to check it against: a level could
                # claim lossy=true with no upstream block behind it, or an upstream block whose
                # counts contradicted its own boolean, and CI would pass.
                "loss": manifest["loss"],
                "lossy": manifest["lossy"],
            }
            print(
                f"built lod '{lod}': {manifest['totals']['vertices']} vertices, "
                f"{manifest['totals']['triangles']} triangles, "
                f"{manifest['totals']['bytes']} bytes"
            )
    except BuildLodError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    high_vertices = levels["high"]["vertices"]
    report = {
        "source": str(args.input),
        "buildDate": args.build_date,
        "country": args.country,
        "adminLevel": args.admin_level,
        "normalization": normalization.as_dict(),
        "levels": levels,
        "vertexRatioToHigh": {
            lod: (levels[lod]["vertices"] / high_vertices if high_vertices else 0.0)
            for lod in LOD_LEVELS
        },
    }
    report_path = args.output / "lod-report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
