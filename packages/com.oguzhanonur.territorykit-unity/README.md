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

Or drop a `TerritoryMapRenderer` on a GameObject and let it load the whole dataset. The
**Basic Map** sample does exactly that.

## Coordinates

Meshes arrive in local metres relative to a per-dataset origin, never in degrees — `float32`
degrees would quantise boundaries. `TerritoryMapPlacement` holds the one definition of how that
XY plane lands in Unity: vertices keep `(x, y, 0)` and a single `+90°` rotation about X on the
root lays the map flat into world XZ, which is also what keeps TKMS's clockwise winding
front-facing to a camera above.

## Threading

`TerritoryClient` methods are called from the main thread and complete on it. In between,
parsing, validation and buffer preparation run on a worker thread; only the mesh buffer uploads
come back. Nothing here caches: `UnityWebRequest` keeps no HTTP cache and ignores ETag/304, and
because mesh URLs are pinned to an immutable revision the right answer is a revision-keyed disk
cache rather than conditional requests. That arrives in Phase 5.

## Scope

Phase 4 is download, decode, draw. Pooling, viewport streaming, LOD switching and click-to-select
are Phase 5.

## Licence

MIT. Sample data is geoBoundaries (CC BY-SA 2.0) — see the repository README for attribution.
