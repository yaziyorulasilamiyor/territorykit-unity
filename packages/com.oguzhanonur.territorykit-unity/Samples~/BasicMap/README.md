# Basic Map

Loads a published dataset and draws every territory, seen from above.

## What you need first

The scene talks to a running geometry API that has a published revision. From the repository
root:

```bash
python scripts/build_lod.py --input "$PWD/services/geometry-api/data/datasets/turkey-provinces.geojson" --output "$PWD/services/geometry-api/data/build/tr-adm1" --clean
```

```bash
python scripts/publish_dataset.py --build-dir "$PWD/services/geometry-api/data/build/tr-adm1" --dataset-id tr-adm1 --artifacts-dir "$PWD/services/geometry-api/data/artifacts" --cache-dir "$PWD/services/geometry-api/data/cache"
```

```bash
uvicorn geometry_api.main:app --host 127.0.0.1 --app-dir services/geometry-api/src
```

Two things that will otherwise cost you an afternoon:

- **Use `127.0.0.1`, not `localhost`** — in the command above and in the scene's Base Url. On
  Windows `localhost` resolves to the IPv6 `::1` first, while uvicorn binds IPv4 only, so the
  request fails against a server that is up and serving. `scripts/capture_sample.ps1` hit exactly
  this.
- **Pass absolute paths to `build_lod.py`.** It runs part of the chain in a subprocess with a
  different working directory, so a relative `--output` resolves against the wrong root.

## Running it

Open `BasicMap.unity` and press Play. The `Territory Map` object carries a
`TerritoryMapRenderer` with four fields worth touching:

| Field | Default | Notes |
|---|---|---|
| Base Url | `http://127.0.0.1:8000` | Where the geometry API is listening |
| Dataset Id | `tr-adm1` | Must match `--dataset-id` above |
| Lod | `high` | `high`, `medium` or `low` |
| Frame Camera On Load | on | Fits the camera to the dataset bounds |

`high` is the default deliberately. Its simplification step changes nothing —
`simplification.topologyChanged` is `false` — so if something looks wrong on screen, the cause
is on the client side rather than in geometry the simplifier merged. 240,379 vertices across 81
provinces is not a meaningful load for any GPU.

## What it does not do

Phase 4 is download, decode, draw. There is no pooling, no viewport streaming, no LOD switching
and no click-to-select; every territory is loaded once and stays. Those arrive in Phase 5.

## The warning in the console

On startup the scene logs the level's picking-safety verdict, and on the Turkish dataset it will
say every level is unsafe. That is correct and not a bug in the sample: geoBoundaries
normalization drops seven real islets before simplification even begins, so no level is
topologically identical to the source. It does not affect what you see — Phase 4 only draws —
but it will affect Phase 5 picking. See `docs/mesh-format.md`.
