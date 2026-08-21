# Basic Map

Streams a published dataset by camera viewport, seen from above: pan, zoom, click to highlight.

## What you need first

The scene talks to a running geometry API that has a published revision. The root README's
Quick Start is the canonical clean-install flow; its data/build portion, run from the repository
root, is:

```powershell
.\.venv\Scripts\python.exe scripts\fetch_sample_dataset.py
.\.venv\Scripts\python.exe scripts\build_lod.py --input "$PWD\services\geometry-api\data\datasets\turkey-provinces.geojson" --output "$PWD\services\geometry-api\data\build\tr-adm1" --clean
```

```powershell
.\.venv\Scripts\python.exe scripts\publish_dataset.py --build-dir "$PWD\services\geometry-api\data\build\tr-adm1" --dataset-id tr-adm1 --artifacts-dir "$PWD\services\geometry-api\data\artifacts" --cache-dir "$PWD\services\geometry-api\data\cache"
```

The API's default paths are relative to `services/geometry-api`, so start it from that directory:

```powershell
cd services\geometry-api
..\..\.venv\Scripts\python.exe -m uvicorn geometry_api.main:app --host 127.0.0.1 --port 8000
```

Two things that will otherwise cost you an afternoon:

- **Use `127.0.0.1`, not `localhost`** — in the command above and in the scene's Base Url. On
  Windows `localhost` resolves to the IPv6 `::1` first, while uvicorn binds IPv4 only, so the
  request fails against a server that is up and serving. `scripts/capture_sample.ps1` hit exactly
  this.
- **Pass absolute paths to `build_lod.py`.** It runs part of the chain in a subprocess with a
  different working directory, so a relative `--output` resolves against the wrong root.

## Running it

Open `BasicMap.unity` and press Play. The `Territory Map` object carries a `ViewportStreamer`:

| Field | Default | Notes |
|---|---|---|
| Base Url | `http://127.0.0.1:8000` | Where the geometry API is listening |
| Dataset Id | `tr-adm1` | Must match `--dataset-id` above |
| Warm Pool Size | 96 | GameObjects/Meshes preallocated before the first tick |
| Viewport Margin Ratio | 0.15 | Extra fraction of the visible box fetched on every side |
| Tick Interval Seconds | 0.2 | How often the camera's view is re-checked |
| High→Medium / Medium→Low Coarsen/Refine At | 60,000 / 45,000 / 180,000 / 140,000 | Orthographic-size hysteresis thresholds — tuned for this dataset's scale, not a universal constant |

The `Map Camera` object carries a `BasicMapCameraController` (sample-only, not part of the
package): it frames the camera on the dataset once it loads, right-drag pans, the scroll wheel
zooms between Min/Max Orthographic Size, and a left click resolves through
`ViewportStreamer.TryPick` and recolours whatever it hits.

**Input backend:** works under any *Project Settings → Player → Active Input Handling* setting —
Input Manager (Old), Input System Package (New), or Both — via `ENABLE_LEGACY_INPUT_MANAGER`/
`ENABLE_INPUT_SYSTEM` conditional compilation, and does not add an Input System package
dependency. A Unity 6 project defaults to New-only, where the legacy `Input` class throws on
every read; this sample no longer assumes either backend is present. If a project somehow has
neither active, pan/zoom/click are disabled after one console warning — the map still renders.

Levels are chosen automatically as you zoom — there is no fixed Lod field to set anymore. `high`
is where you start (closest zoom): its simplification step changes nothing —
`simplification.topologyChanged` is `false` — so if something looks wrong on screen at that
level, the cause is on the client side rather than in geometry the simplifier merged.

## Don't drag territories in the Scene view

The objects under `Territories` are pooled and positioned entirely by their mesh data — their
local transforms are always identity, and `TerritoryPool` resets them on every checkout and
release. Moving one with the Scene view's Move gizmo therefore achieves nothing that survives
the next tick, and the editor's own drag maths can produce `NaN` deltas that surface as errors
from `UnityEditor.TransformManipulator` with no runtime code involved. Pan the *camera* instead
(right-drag in Play mode); to move the whole map, move the `Territory Map` object.

## The warning in the console

Clicking a territory logs the level's picking-safety verdict when it is unsafe, and on the
Turkish dataset every level is. That is correct and not a bug in the sample: geoBoundaries
normalization drops seven real islets before simplification even begins, so no level is
topologically identical to the source. Picking still resolves against whatever mesh is actually
on screen — there is no "safer" level to fall back to — the log is there so the console explains
why a click near a dropped islet's former location may resolve unexpectedly, rather than the
click just quietly working. See `docs/mesh-format.md`.
