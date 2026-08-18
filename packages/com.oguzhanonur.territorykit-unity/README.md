# TerritoryKit Unity

Downloads TerritoryKit region meshes from the geometry API, decodes the TKMS binary format and
draws them as Unity meshes — one `Mesh` per territory, in local metres, with no external
dependencies.

## Requirements

Built and tested against **Unity 6000.1**. The code avoids APIs newer than Unity 2022.3 on
purpose, so 2022.3 LTS is the intended floor — but no 2022.3 install has run these tests, so
that is an intent and not a verified claim. `package.json` states the version actually tested.

## Installation

Package Manager → **Add package from git URL**:

```
https://github.com/yaziyorulasilamiyor/territorykit-unity.git?path=packages/com.oguzhanonur.territorykit-unity
```

Or add it to `Packages/manifest.json` directly:

```json
"com.oguzhanonur.territorykit-unity": "https://github.com/yaziyorulasilamiyor/territorykit-unity.git?path=packages/com.oguzhanonur.territorykit-unity"
```

## Quick start

```csharp
using TerritoryKit.Unity;

var client = new TerritoryClient("http://127.0.0.1:8000");

// Metadata first: this is where the revision id and the per-level safety flags come from.
DatasetInfo dataset = await client.GetDatasetAsync("tr-adm1");
Debug.Log(LodPolicy.Describe(dataset, "high"));

Mesh mesh = await client.GetMeshAsync(dataset.id, dataset.revisionId, "06", "high");

var root = new GameObject("Territories").transform;
root.localRotation = TerritoryMapPlacement.RootRotation;   // lays local XY flat into world XZ

var go = new GameObject("06");
go.transform.SetParent(root, false);
go.AddComponent<MeshFilter>().sharedMesh = mesh;
go.AddComponent<MeshRenderer>().sharedMaterial = new Material(Shader.Find("Unlit/Color"));
```

Or drop a `TerritoryMapRenderer` on a GameObject and let it load the whole dataset once, or a
`ViewportStreamer` to pool GameObjects/meshes and stream by camera viewport instead. The
**Basic Map** sample uses `ViewportStreamer`, with pan, zoom and click-to-highlight.

## Coordinates

Meshes arrive in local metres relative to a per-dataset origin, never in degrees — `float32`
degrees would quantise boundaries. `TerritoryMapPlacement` holds the one definition of how that
XY plane lands in Unity: vertices keep `(x, y, 0)` and a single `+90°` rotation about X on the
root lays the map flat into world XZ, which is also what keeps TKMS's clockwise winding
front-facing to a camera above.

## Threading

`TerritoryClient` methods are called from the main thread and complete on it. In between,
parsing, validation and buffer preparation run on a worker thread; only the mesh buffer uploads
come back. `GetMeshAsync`/`GetMeshBatchAsync` stay cache-free by design, matching how
`UnityWebRequest` itself keeps no HTTP cache and ignores ETag/304. `GetMeshDataAsync`/
`GetMeshDataBatchAsync` — the pair `ViewportStreamer` uses — accept an optional `MeshDiskCache`
in the constructor instead: mesh URLs are pinned to an immutable revision, so a disk hit for a
given revision+territory+level is valid forever and needs no conditional request to stay correct.

## Scope

Phase 4 was download, decode, draw — the whole dataset, once, via `TerritoryMapRenderer`. Phase 5
adds pooling (`TerritoryPool`, zero steady-state GC allocation), viewport streaming
(`ViewportStreamer`, camera-driven load/unload with LOD hysteresis), CPU-side picking
(`TerritoryPicker`, no `MeshCollider`) and the disk cache above. `TerritoryMapRenderer` is
unchanged and still valid for the "load everything once" case; `ViewportStreamer` is the
streaming alternative. See `docs/phases/FAZ-5-RAPOR.md` for what was measured, including the
scale limits of the CPU-picking design.

## Licence

MIT. Sample data is geoBoundaries (CC BY-SA 2.0) — see the repository README for attribution.
