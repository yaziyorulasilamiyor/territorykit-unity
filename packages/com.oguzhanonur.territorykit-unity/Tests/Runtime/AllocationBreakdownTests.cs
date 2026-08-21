using System;
using NUnit.Framework;
using Unity.Collections;
using UnityEngine;
using UnityEngine.Networking;

namespace TerritoryKit.Unity.Tests
{
    /// <summary>
    /// Splits the phase 5 "~110 KB of garbage per loaded region" finding into the four stages a
    /// <see cref="ViewportStreamer"/> tick actually runs, so the phase 6 report can name a source
    /// instead of a total. Phase 5 could not do this itself: a single tick with a real-sized mesh
    /// already triggers one gen-0 collection, and <see cref="AllocationMeasurement"/> can only
    /// trust a window with none. Each stage below is isolated and looped on its own so it stays
    /// under that threshold and the heap-occupancy gauge stays valid.
    /// </summary>
    /// <remarks>
    /// Not a budget gate — these are report numbers, not a pass/fail line the codebase has
    /// promised to hold. If a number here is surprising, it is surprising the way a profiler
    /// result is surprising, not the way a broken test is.
    /// </remarks>
    public class AllocationBreakdownTests
    {
        /// <summary>
        /// One province-scale mesh, sized to phase 5's measured "real ortalama": 2,967 vertices,
        /// ~41 KB encoded. Built as an unshared triangle fan -- geometry is irrelevant here, only
        /// the byte size and vertex/index counts the decoder actually walks.
        /// </summary>
        private static byte[] RealisticMeshPayload()
        {
            const int vertexCount = 2967; // divisible by 3
            int triangleCount = vertexCount / 3;
            var vertices = new float[vertexCount * 2];
            var indices = new int[triangleCount * 3];
            for (int t = 0; t < triangleCount; t++)
            {
                float x = t;
                int v = t * 3;
                vertices[v * 2 + 0] = x;
                vertices[v * 2 + 1] = 0f;
                vertices[v * 2 + 2] = x;
                vertices[v * 2 + 3] = 1f;
                vertices[v * 2 + 4] = x + 0.5f;
                vertices[v * 2 + 5] = 0f;
                indices[v + 0] = v;
                indices[v + 1] = v + 1;
                indices[v + 2] = v + 2;
            }

            return TkmsFixture.Build(vertices, indices);
        }

        /// <summary>A representative /viewport page: 50 ids, the kind of page size a pan produces.</summary>
        private static string RealisticViewportPageJson()
        {
            var sb = new System.Text.StringBuilder();
            sb.Append("{\"revisionId\":\"abc123\",\"lod\":\"high\",\"territoryIds\":[");
            for (int i = 0; i < 50; i++)
            {
                if (i > 0) sb.Append(',');
                sb.Append('"').Append(i.ToString("D2")).Append('"');
            }

            sb.Append("],\"nextCursor\":\"\",\"scanTruncated\":false}");
            return sb.ToString();
        }

        [Test]
        public void JsonParseOfAViewportPage()
        {
            string json = RealisticViewportPageJson();
            AllocationMeasurement.BytesPerIteration(
                "JSON parse (/viewport page, 50 ids)", 200,
                () => JsonUtility.FromJson<ViewportPage>(json));
        }

        [Test]
        public void TkmsDecodeOfARealisticMesh()
        {
            byte[] bytes = RealisticMeshPayload();
            AllocationMeasurement.BytesPerIteration(
                "TKMS decode (2,967 vertices, NativeArray in/out)", 100,
                () =>
                {
                    var native = new NativeArray<byte>(bytes, Allocator.Persistent);
                    TkmsMeshData data = MeshDecoder.Decode(native);
                    data.Dispose();
                    native.Dispose();
                });
        }

        [Test]
        public void ManagedByteCopyForTheDiskCache()
        {
            // DecodeAndMaybeCopy's payload.ToArray() -- only paid when a MeshDiskCache is
            // configured, once per network response, not once per stage above.
            byte[] bytes = RealisticMeshPayload();
            var native = new NativeArray<byte>(bytes, Allocator.Persistent);
            try
            {
                AllocationMeasurement.BytesPerIteration(
                    "managed byte[] copy for disk cache (" + bytes.Length + " B payload)", 5,
                    () =>
                    {
                        byte[] copy = native.ToArray();
                        GC.KeepAlive(copy);
                    });
            }
            finally
            {
                native.Dispose();
            }
        }

        [Test]
        public void MeshApplyOfARealisticMesh()
        {
            byte[] bytes = RealisticMeshPayload();
            var native = new NativeArray<byte>(bytes, Allocator.Persistent);
            TkmsMeshData data = MeshDecoder.Decode(native);
            native.Dispose();
            var mesh = new Mesh();
            try
            {
                AllocationMeasurement.BytesPerIteration(
                    "Mesh.Apply (SetVertexBufferData/SetIndexBufferData, pooled mesh reused)", 100,
                    () => MeshDecoder.Apply(mesh, data));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(mesh);
                data.Dispose();
            }
        }

        [Test]
        public void MeshUrlConstruction()
        {
            const string baseUrl = "http://127.0.0.1:8000";
            AllocationMeasurement.BytesPerIteration(
                "mesh URL construction (StringBuilder + Uri.EscapeDataString)", 500,
                () =>
                {
                    string url = baseUrl
                        + "/v1/datasets/" + System.Uri.EscapeDataString("tr-adm1")
                        + "/revisions/" + System.Uri.EscapeDataString("abc123def456")
                        + "/mesh/" + System.Uri.EscapeDataString("34")
                        + "?lod=" + System.Uri.EscapeDataString("high");
                    GC.KeepAlive(url);
                });
        }

        [Test]
        public void UnityWebRequestObjectCreation()
        {
            const string url = "http://127.0.0.1:8000/v1/datasets/tr-adm1/revisions/abc123/mesh/34?lod=high";
            AllocationMeasurement.BytesPerIteration(
                "UnityWebRequest.Get object graph (request + DownloadHandlerBuffer, not sent)", 200,
                () =>
                {
                    using (UnityWebRequest request = UnityWebRequest.Get(url))
                    {
                        GC.KeepAlive(request);
                    }
                });
        }
    }
}
