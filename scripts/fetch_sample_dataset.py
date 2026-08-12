"""Downloads a small sample GeoJSON dataset (Turkey province boundaries) for local
development and testing. Output is git-ignored — never commit fetched data.

If the remote source is unavailable, falls back to a hand-written 3-polygon
fixture so the pipeline can still be exercised offline.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "services" / "geometry-api" / "data"
OUTPUT_FILE = OUTPUT_DIR / "turkey-provinces.geojson"

SOURCE_URL = (
    "https://raw.githubusercontent.com/cihadturhan/tr-geojson/master/"
    "TR-Iller.json"
)

FALLBACK_FEATURE_COLLECTION = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"id": "fixture-a", "name": "Fixture A"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
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
                    [[33.0, 39.0], [34.0, 39.0], [34.0, 40.0], [33.0, 40.0], [33.0, 39.0]],
                    [[33.4, 39.4], [33.6, 39.4], [33.6, 39.6], [33.4, 39.6], [33.4, 39.4]],
                ],
            },
        },
    ],
}


def fetch_remote() -> dict:
    with urllib.request.urlopen(SOURCE_URL, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        data = fetch_remote()
        source = "remote"
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"Remote fetch failed ({exc}); using fallback fixture.", file=sys.stderr)
        data = FALLBACK_FEATURE_COLLECTION
        source = "fallback"

    OUTPUT_FILE.write_text(json.dumps(data), encoding="utf-8")
    feature_count = len(data.get("features", []))
    print(f"Wrote {feature_count} features from {source} source to {OUTPUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
