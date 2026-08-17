"""Reproduces both TerritoryKit findings end to end, from a raw geoBoundaries file.

    python scripts/repro_territorykit_finding.py \
      --input services/geometry-api/data/datasets/turkey-provinces.geojson \
      --work /tmp/tk-repro

It runs the whole chain itself — normalize, import, simplify with TerritoryKit, simplify with
topojson at the *same* tolerances — and prints one table per detail level with the numbers
``docs/territorykit-simplification-finding.md`` cites:

* Issue A — invalid geometries, neighbouring pairs with a gap or an overlap, the total gap and
  overlap area, and the same three numbers for one **named** pair so a reader can check a single
  case by hand instead of hunting for an affected one.
* Issue B — ``sharedBoundaryMismatchCount`` for the TerritoryKit output and for the topojson
  output, computed by the same formula, showing the metric cannot tell them apart.

Both the raw input and the normalized copy are hashed and the hashes printed, so a reader who
gets different numbers can tell immediately whether they are looking at different data.

Requirements: Node >= 22 with the TerritoryKit CLI built (see ``scripts/build_lod.py`` for the
build command), and ``pip install -e services/geometry-api`` for shapely and topojson.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from build_lod import (
    DEFAULT_BUILD_DATE,
    LOD_LEVELS,
    BuildLodError,
    import_dataset,
    normalize_geoboundaries,
    run_step_raw,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TERRITORY_CLI = REPO_ROOT / "vendor" / "territorykit" / "packages" / "cli" / "dist" / "index.mjs"

DEFAULT_NAMED_PAIR = ("Gaziantep", "Kilis")
"""One province pair whose shared boundary TerritoryKit's ``high`` output already breaks.

Named, and named by province rather than by id: zone ids are content hashes
(``tr:adm1:25984515b1...``) that nobody can look up, and "pick two neighbours and compare" is
not a reproduction — only 32 of the 197 pairs are affected at ``high``, so most choices show
nothing and read as the finding being wrong. The script checks this pair really does share a
boundary in the source before measuring it, and ``--pair`` takes any other two names.
"""


AREA_EPSILON_M2 = 1e-6
"""Below a square micrometre a pair counts as unaffected.

Not a tolerance on the finding — the defects it reports are hundreds to millions of m². It is
there because ``union_all`` on a clean coverage occasionally leaves an interior ring of ~1e-13 m²
of float noise, which made the "affected pairs" count for the *correct* simplifier flicker
between 0 and 4 across runs and made this script look non-deterministic.
"""


@dataclass(frozen=True)
class PairMetrics:
    """Gap and overlap between two regions that share a boundary in the source."""

    gap: float
    overlap: float

    @property
    def affected(self) -> bool:
        return self.gap > AREA_EPSILON_M2 or self.overlap > AREA_EPSILON_M2


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _origin_of(geometries: dict[str, Any]) -> Any:
    """The same projection origin ``loader.Dataset`` derives: the centre of the dataset bounds.

    Areas are reported in local metres rather than square degrees because a square degree is not
    a unit anyone can picture, and because every other number in this project is in m².
    """
    import shapely

    from geometry_api.projection import Origin

    min_lon, min_lat, max_lon, max_lat = shapely.union_all(list(geometries.values())).bounds
    return Origin(lon=(min_lon + max_lon) / 2.0, lat=(min_lat + max_lat) / 2.0)


def _project_all(geometries: dict[str, Any], origin: Any) -> dict[str, Any]:
    from geometry_api.projection import project_geometry

    return {name: project_geometry(geometry, origin) for name, geometry in geometries.items()}


def _interior_area(geometry: Any) -> float:
    import shapely

    parts = geometry.geoms if geometry.geom_type == "MultiPolygon" else [geometry]
    return sum(shapely.Polygon(ring).area for part in parts for ring in part.interiors)


def _pair_metrics(left: Any, right: Any) -> PairMetrics:
    """Enclosed gap and claimed-twice overlap between one pair.

    Each region's *own* interior rings are subtracted from the pair's union holes. At medium and
    low TerritoryKit emits self-intersecting rings that have to be repaired before they can be
    measured at all, and repair leaves interior rings behind; counting those as gaps between the
    two provinces would blame the simplifier for the repair.
    """
    import shapely

    union = shapely.union_all([left, right])
    gap = _interior_area(union) - _interior_area(left) - _interior_area(right)
    return PairMetrics(gap=max(0.0, gap), overlap=left.intersection(right).area)


def _shared_segment_count(geometries: dict[str, Any]) -> int:
    """TerritoryKit's own ``collectSharedSegments``, reimplemented coordinate for coordinate.

    See ``packages/generators/src/geometry-simplification.ts``: every ring segment is keyed by
    its two endpoints rounded to 9 decimals, order-independent, and the metric is the number of
    keys seen more than once. Reimplemented rather than read out of the CLI's report because the
    whole point of Issue B is to run the same formula over an output the CLI never produced.
    """
    counts: dict[str, int] = {}
    for geometry in geometries.values():
        parts = geometry.geoms if geometry.geom_type == "MultiPolygon" else [geometry]
        for part in parts:
            for ring in [part.exterior, *part.interiors]:
                coords = list(ring.coords)
                for index in range(1, len(coords)):
                    first = f"{coords[index - 1][0]:.9f},{coords[index - 1][1]:.9f}"
                    second = f"{coords[index][0]:.9f},{coords[index][1]:.9f}"
                    key = f"{first}|{second}" if first < second else f"{second}|{first}"
                    counts[key] = counts.get(key, 0) + 1
    return sum(1 for count in counts.values() if count > 1)


def _neighbour_pairs(source: dict[str, Any]) -> list[tuple[str, str]]:
    """Pairs sharing a boundary of positive length in the *source* — the 197 real ones."""
    import shapely

    ids = sorted(source)
    geometries = [source[key] for key in ids]
    tree = shapely.STRtree(geometries)

    pairs = []
    for i, geometry in enumerate(geometries):
        for candidate in tree.query(geometry):
            j = int(candidate)
            if j <= i:
                continue
            if geometry.intersection(geometries[j]).length > 0.0:
                pairs.append((ids[i], ids[j]))
    return pairs


def _load_zone_geometries(dataset_path: Path) -> dict[str, Any]:
    """Read a TerritoryKit dataset.json into shapely geometries, keyed by province name.

    Keyed by name, not by id: TerritoryKit ids are content hashes, so an id changes whenever the
    geometry does and cannot be written into a document as "look at this pair".

    Invalid geometry is repaired here *and counted* into ``_invalid_names``. Silently repairing
    it would erase one of the two findings — at medium and low the CLI emits self-intersecting
    rings alongside ``ok: true``.
    """
    import shapely
    from shapely.geometry import shape

    document = json.loads(dataset_path.read_text(encoding="utf-8"))
    geometries: dict[str, Any] = {}
    for zone in document["zones"]:
        name = zone["name"]
        if name in geometries:
            raise BuildLodError(f"two zones are both called {name!r}; cannot key by name")
        geometry = shape(zone["geometry"])
        if not geometry.is_valid:
            _invalid_names.add(name)
            geometry = shapely.make_valid(geometry)
        geometries[name] = geometry
    return geometries


_invalid_names: set[str] = set()


@dataclass(frozen=True)
class TerritoryKitRun:
    """One ``geometry simplify`` run: what it produced, and what it said about itself."""

    geometries: dict[str, Any]
    self_reported_mismatch: int
    """``topologyAudit.sharedBoundaryMismatchCount`` out of its own report."""
    exit_code: int
    ok: Any
    issues: list[Any]

    def print_verdict(self) -> None:
        """Issue B's main evidence, printed verbatim rather than summarised.

        Earlier versions of this script took the CLI's stdout, checked it for ``ok`` and threw it
        away. That left the second half of Issue B — a tool reporting success over broken output —
        as an assertion in a document with nothing behind it. These three values *are* the
        finding, so they are printed whether or not anything went wrong.
        """
        print(
            f"  {'CLI verdict':<12} exit code={self.exit_code}  ok={json.dumps(self.ok)}  "
            f"issues={json.dumps(self.issues, ensure_ascii=False)}"
        )
        print(
            f"  {'':<12} its own topologyAudit reports "
            f"sharedBoundaryMismatchCount={self.self_reported_mismatch}"
        )


def _simplify_with_territorykit(dataset: Path, work: Path, detail: str) -> TerritoryKitRun:
    """Run ``geometry simplify`` and keep its verdict alongside its output.

    Its self-reported audit number is printed next to the recomputed one because they differ
    slightly (48204 vs 48200 at ``low``): the audit runs on the in-memory geometry, the
    recomputation on the coordinates actually written to ``dataset.json``, which are serialised at
    lower precision. The gap is a rounding artefact and irrelevant to the finding — both numbers
    are far above the ~47.4k a crack-free simplifier scores — but leaving it unexplained would
    read as the reproduction failing to reproduce.
    """
    output = work / f"tk-{detail}"
    result = run_step_raw(
        [
            "node",
            str(TERRITORY_CLI),
            "geometry",
            "simplify",
            str(dataset),
            "--strategy",
            "topology-safe",
            "--detail",
            detail,
            "--output",
            str(output),
            "--force",
        ],
        f"territory geometry simplify --detail {detail}",
    )
    # Deliberately *not* raising on a non-zero exit or on ok:false. If the CLI ever starts
    # reporting the problem, that is the finding being fixed, and this script should print it
    # rather than abort. What it must never do is hide a zero exit code over broken output.
    try:
        verdict = json.loads(result.stdout)
    except json.JSONDecodeError:
        verdict = {}

    produced = output / detail / "dataset.json"
    if not produced.exists():
        raise BuildLodError(
            f"simplify wrote no {produced} (exit {result.returncode}); there is nothing to "
            f"measure:\n{(result.stdout + result.stderr).strip()}"
        )

    report = json.loads((output / "simplification-report.json").read_text(encoding="utf-8"))
    tier = next(entry for entry in report["tiers"] if entry["detail"] == detail)
    return TerritoryKitRun(
        geometries=_load_zone_geometries(produced),
        self_reported_mismatch=tier["topologyAudit"]["sharedBoundaryMismatchCount"],
        exit_code=result.returncode,
        ok=verdict.get("ok"),
        issues=list(verdict.get("issues", [])),
    )


def _simplify_with_topojson(source: dict[str, Any], detail: str) -> tuple[dict[str, Any], int]:
    """This project's simplifier, plus how many geometries the library emitted invalid.

    The raw pass exists only to count. topojson is *not* free of self-intersections at ``low`` —
    it emits 13 — and reporting 0 here because ``_simplify_geometries`` repairs them internally
    would be the same silence this document is complaining about. The difference between the two
    libraries is not that one never needs repair; it is that this pipeline repairs, then proves
    zero cracks on the decoded meshes, while ``geometry simplify`` ships 23 invalid geometries
    with ``ok: true`` and exit code 0.
    """
    import topojson as topojson_lib
    from shapely.geometry import shape

    from geometry_api.simplify import LOD_TOLERANCES, _simplify_geometries

    tolerance = LOD_TOLERANCES[detail]
    topology = topojson_lib.Topology(source, prequantize=False)
    raw = json.loads(topology.toposimplify(tolerance).to_geojson())
    invalid = sum(1 for feature in raw["features"] if not shape(feature["geometry"]).is_valid)

    return _simplify_geometries(source, tolerance), invalid


def _measure(
    label: str,
    simplified: dict[str, Any],
    origin: Any,
    pairs: list[tuple[str, str]],
    named: tuple[str, str],
    invalid: int,
    source_shared_segments: int,
    list_affected: bool = False,
) -> None:
    projected = _project_all(simplified, origin)

    affected: list[tuple[float, str]] = []
    total_gap = 0.0
    total_overlap = 0.0
    for left, right in pairs:
        metrics = _pair_metrics(projected[left], projected[right])
        total_gap += metrics.gap
        total_overlap += metrics.overlap
        if metrics.affected:
            affected.append((metrics.gap + metrics.overlap, f"{left} / {right}"))

    named_metrics = _pair_metrics(projected[named[0]], projected[named[1]])
    # Issue B's formula, verbatim from geometry-simplification.ts, on the lon/lat coordinates it
    # is defined over — not on the projected copy.
    mismatch = max(0, source_shared_segments - _shared_segment_count(simplified))

    print(f"  {label:<12} invalid={invalid:<3} affected={len(affected):>3}/{len(pairs)}")
    print(
        f"  {'':<12} gap={total_gap / 1e6:.4f} km²  overlap={total_overlap / 1e6:.4f} km²  "
        f"sharedBoundaryMismatchCount={mismatch}"
    )
    print(
        f"  {'':<12} named pair {named[0]} / {named[1]}: "
        f"gap={named_metrics.gap:.1f} m²  overlap={named_metrics.overlap:.1f} m²"
    )
    if list_affected and affected:
        print(f"  {'':<12} worst affected pairs:")
        for score, label_pair in sorted(affected, reverse=True)[:10]:
            print(f"  {'':<12}   {label_pair}: {score:.1f} m²")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/repro_territorykit_finding.py")
    parser.add_argument("--input", required=True, type=Path, help="raw geoBoundaries .geojson")
    parser.add_argument("--work", required=True, type=Path, help="scratch directory")
    parser.add_argument("--country", default="TR")
    parser.add_argument("--admin-level", default="ADM1")
    parser.add_argument("--build-date", default=DEFAULT_BUILD_DATE)
    parser.add_argument(
        "--detail",
        action="append",
        choices=LOD_LEVELS,
        help="repeatable; defaults to all three levels so 'high' and 'low' are both covered",
    )
    parser.add_argument(
        "--pair",
        nargs=2,
        metavar=("LEFT", "RIGHT"),
        default=DEFAULT_NAMED_PAIR,
        help=f"two province names to report on individually (default: "
        f"{' / '.join(DEFAULT_NAMED_PAIR)})",
    )
    parser.add_argument(
        "--list-affected",
        action="store_true",
        help="also list the worst affected pairs, which is how the default --pair was chosen",
    )
    args = parser.parse_args(argv)

    details = args.detail or list(LOD_LEVELS)
    if not args.input.exists():
        print(f"error: {args.input} does not exist", file=sys.stderr)
        return 1

    args.work.mkdir(parents=True, exist_ok=True)
    normalized = args.work / "normalized.geojson"

    try:
        normalization = normalize_geoboundaries(args.input, normalized, args.country)
        print(f"raw input        sha256 {_sha256(args.input)}")
        print(f"normalized copy  sha256 {_sha256(normalized)}")
        print(
            f"normalization    {normalization.feature_count} features, "
            f"{normalization.country_codes_rewritten} country codes rewritten, "
            f"{normalization.dropped_parts} islets dropped"
        )

        dataset = import_dataset(
            normalized, args.work / "dataset", args.country, args.admin_level, args.build_date
        )
        source = _load_zone_geometries(dataset)
        origin = _origin_of(source)
        pairs = _neighbour_pairs(source)
        source_segments = _shared_segment_count(source)
        print(
            f"source           {len(source)} zones, {len(pairs)} neighbouring pairs, "
            f"{source_segments} shared segments"
        )

        left, right = args.pair
        missing = [key for key in (left, right) if key not in source]
        if missing:
            print(f"error: unknown province name(s) {missing}", file=sys.stderr)
            print(f"       names look like: {sorted(source)[:3]}", file=sys.stderr)
            return 1
        if (left, right) not in pairs and (right, left) not in pairs:
            print(f"error: {left} and {right} do not share a boundary", file=sys.stderr)
            return 1

        for detail in details:
            print(f"\n--- {detail} ---")
            _invalid_names.clear()
            run = _simplify_with_territorykit(dataset, args.work, detail)
            _measure(
                "territorykit",
                run.geometries,
                origin,
                pairs,
                (left, right),
                len(_invalid_names),
                source_segments,
                list_affected=args.list_affected,
            )
            run.print_verdict()

            topojson_output, topojson_invalid = _simplify_with_topojson(source, detail)
            _measure(
                "topojson",
                topojson_output,
                origin,
                pairs,
                (left, right),
                topojson_invalid,
                source_segments,
            )
            print(
                f"  {'':<12} note: 'topojson' here is the make_valid'ed polygon layer, not the "
                f"final mesh. The zero-crack claim for the shipped meshes is measured after "
                f"triangulation and float32 in tests/test_lod.py; this script does not go that "
                f"far and does not claim to."
            )
    except BuildLodError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ImportError as exc:
        print(
            f"error: {exc}. Install the geometry package first:\n"
            f"  pip install -e services/geometry-api",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
