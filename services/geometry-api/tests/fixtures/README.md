# Test fixtures

Hand-written geometries. These are **code, not data**: the fetched sample dataset
(`data/datasets/turkey-provinces.geojson`, geoBoundaries TUR ADM1) contains **zero** interior
rings, so hole handling cannot be tested against it. These files are committed on purpose; the
`.gitignore` rule targets downloaded datasets, not a few hundred bytes of test input.

| File | What it exercises |
|---|---|
| `polygon-with-hole.geojson` | Fixture C — single hole; hole exclusion |
| `polygon-with-two-holes.geojson` | Fixture D — two holes; proves the earcut ring **end**-offset math (a single hole passes either way if start/end offsets are confused) |
| `multipolygon.geojson` | Three disjoint parts; index offsetting and part isolation |
| `territorykit-dataset.json` | TerritoryKit `dataset.json` shape (`manifest` + `zones`) for loader format detection |

Ring winding follows RFC 7946: exterior counter-clockwise, holes clockwise.
