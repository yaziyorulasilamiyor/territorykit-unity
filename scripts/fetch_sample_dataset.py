"""Downloads a sample GeoJSON dataset (Turkey ADM1 province boundaries) for local
development and testing. Output is git-ignored — never commit fetched data.

Tries the primary source first (geoBoundaries gbOpen TUR ADM1, pinned to a fixed
commit for reproducibility), then a simplified fallback from the same commit, and
finally falls back to a hand-written 3-polygon fixture so the pipeline can still be
exercised fully offline.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

OUTPUT_DIR = (
    Path(__file__).resolve().parent.parent / "services" / "geometry-api" / "data" / "datasets"
)
OUTPUT_FILE = OUTPUT_DIR / "turkey-provinces.geojson"

GEOBOUNDARIES_COMMIT = "9469f09"

PRIMARY_SOURCE_URL = (
    "https://github.com/wmgeolab/geoBoundaries/raw/"
    f"{GEOBOUNDARIES_COMMIT}/releaseData/gbOpen/TUR/ADM1/geoBoundaries-TUR-ADM1.geojson"
)
FALLBACK_SOURCE_URL = (
    "https://github.com/wmgeolab/geoBoundaries/raw/"
    f"{GEOBOUNDARIES_COMMIT}/releaseData/gbOpen/TUR/ADM1/geoBoundaries-TUR-ADM1_simplified.geojson"
)

GEOBOUNDARIES_METADATA = {
    "source": "geoBoundaries gbOpen TUR ADM1 (OpenStreetMap)",
    "license": "CC BY-SA 2.0",
    "attribution": "© OpenStreetMap contributors, via geoBoundaries (wmgeolab)",
    "sourceUrl": "https://www.geoboundaries.org/",
    "sourceCommit": GEOBOUNDARIES_COMMIT,
}

FALLBACK_FEATURE_COLLECTION = {
    "type": "FeatureCollection",
    "metadata": {
        "source": "hand-written fixture (remote sources unavailable)",
        "license": "CC0-1.0",
        "attribution": "TerritoryKit.Unity project",
    },
    "features": [
        {
            "type": "Feature",
            "properties": {"id": "fixture-a", "name": "Fixture A"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    # exterior ring: counter-clockwise (RFC 7946)
                    [[32.0, 39.0], [32.5, 39.0], [32.5, 39.5], [32.0, 39.5], [32.0, 39.0]]
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {"id": "fixture-b", "name": "Fixture B"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[32.5, 39.0], [33.0, 39.0], [33.0, 39.5], [32.5, 39.5], [32.5, 39.0]]
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {"id": "fixture-c", "name": "Fixture C (with hole)"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    # exterior ring: counter-clockwise (RFC 7946)
                    [[33.0, 39.0], [34.0, 39.0], [34.0, 40.0], [33.0, 40.0], [33.0, 39.0]],
                    # hole ring: clockwise (RFC 7946) — reversed winding vs. exterior
                    [[33.4, 39.4], [33.4, 39.6], [33.6, 39.6], [33.6, 39.4], [33.4, 39.4]],
                ],
            },
        },
    ],
}


def fetch_url(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    data: dict
    source: str
    try:
        data = fetch_url(PRIMARY_SOURCE_URL)
        source = "geoBoundaries (primary)"
        data["metadata"] = GEOBOUNDARIES_METADATA
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"Primary source failed ({exc}); trying simplified fallback.", file=sys.stderr)
        try:
            data = fetch_url(FALLBACK_SOURCE_URL)
            source = "geoBoundaries (simplified fallback)"
            data["metadata"] = GEOBOUNDARIES_METADATA
        except (urllib.error.URLError, TimeoutError, ValueError) as exc2:
            print(f"Simplified fallback failed ({exc2}); using fixture.", file=sys.stderr)
            data = FALLBACK_FEATURE_COLLECTION
            source = "hand-written fixture"

    OUTPUT_FILE.write_text(json.dumps(data), encoding="utf-8")
    feature_count = len(data.get("features", []))
    print(f"Wrote {feature_count} features from {source} to {OUTPUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
