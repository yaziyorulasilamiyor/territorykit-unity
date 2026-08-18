using System;
using NUnit.Framework;
using Unity.Collections;
using UnityEngine;
using UnityEngine.Rendering;

namespace TerritoryKit.Unity.Tests
{
    /// <summary>
    /// Decoding known byte payloads into mesh data, and the body-level rejection rules the
    /// header alone cannot decide.
    /// </summary>
    public class MeshDecoderTests
    {
        private static TkmsMeshData Decode(byte[] payload)
        {
            var native = new NativeArray<byte>(payload, Allocator.Persistent);
            try
            {
                return MeshDecoder.Decode(native);
            }
            finally
            {
                native.Dispose();
            }
        }

        private static void AssertRejected(byte[] payload, string expectedFragment)
        {
            var ex = Assert.Throws<TkmsFormatException>(() =>
            {
                using (Decode(payload))
                {
                }
            });
            Assert.That(ex.Message, Does.Contain(expectedFragment));
        }

        [Test]
        public void DecodesTheKnownTrianglePayload()
        {
            using (var data = Decode(TkmsFixture.Triangle()))
            {
                Assert.AreEqual(3, data.VertexCount);
                Assert.AreEqual(3, data.IndexCount);
                Assert.AreEqual(IndexFormat.UInt16, data.IndexFormat);

                // Vertices keep the format's own XY, with Z left at zero; the map is laid flat
                // by TerritoryMapPlacement.RootRotation, not by touching the buffer.
                Assert.AreEqual(new Vector3(0f, 0f, 0f), data.Vertices[0]);
                Assert.AreEqual(new Vector3(0f, 10f, 0f), data.Vertices[1]);
                Assert.AreEqual(new Vector3(10f, 0f, 0f), data.Vertices[2]);

                Assert.AreEqual(0, data.Indices16[0]);
                Assert.AreEqual(1, data.Indices16[1]);
                Assert.AreEqual(2, data.Indices16[2]);
                Assert.IsFalse(data.Indices32.IsCreated, "uint16 mesh must not allocate a uint32 buffer");

                Assert.AreEqual(new Vector3(5f, 5f, 0f), data.Bounds.center);
                Assert.AreEqual(new Vector3(10f, 10f, 0f), data.Bounds.size);
            }
        }

        [Test]
        public void DecodesUInt32Indices()
        {
            using (var data = Decode(TkmsFixture.LargeUInt32Mesh(21846)))
            {
                Assert.AreEqual(65538, data.VertexCount);
                Assert.AreEqual(65538, data.IndexCount);
                Assert.AreEqual(IndexFormat.UInt32, data.IndexFormat);
                Assert.IsTrue(data.Indices32.IsCreated);
                Assert.IsFalse(data.Indices16.IsCreated);

                // Past the uint16 ceiling, where a 16-bit read would have wrapped.
                Assert.AreEqual(65537u, data.Indices32[65537]);
            }
        }

        [Test]
        public void UploadsUInt32IndicesToTheMesh()
        {
            using (var data = Decode(TkmsFixture.LargeUInt32Mesh(21846)))
            {
                var mesh = MeshDecoder.CreateMesh(data, "large");
                try
                {
                    Assert.AreEqual(IndexFormat.UInt32, mesh.indexFormat);
                    Assert.AreEqual(65538, mesh.vertexCount);
                    Assert.AreEqual(21846, mesh.GetIndexCount(0) / 3);
                }
                finally
                {
                    UnityEngine.Object.DestroyImmediate(mesh);
                }
            }
        }

        [Test]
        public void BuildsAMeshWithBoundsTakenFromTheHeader()
        {
            using (var data = Decode(TkmsFixture.Quad()))
            {
                var mesh = MeshDecoder.CreateMesh(data, "quad");
                try
                {
                    Assert.AreEqual(4, mesh.vertexCount);
                    Assert.AreEqual(1, mesh.subMeshCount);
                    Assert.AreEqual(6, mesh.GetIndexCount(0));
                    Assert.AreEqual(IndexFormat.UInt16, mesh.indexFormat);
                    Assert.AreEqual(MeshTopology.Triangles, mesh.GetTopology(0));

                    Assert.AreEqual(new Vector3(50f, 50f, 0f), mesh.bounds.center);
                    Assert.AreEqual(new Vector3(100f, 100f, 0f), mesh.bounds.size);

                    var triangles = mesh.triangles;
                    Assert.AreEqual(TkmsFixture.QuadIndices, triangles,
                        "index order must survive the upload unchanged, or the winding guarantee " +
                        "TKMS makes is not the winding the GPU sees");
                }
                finally
                {
                    UnityEngine.Object.DestroyImmediate(mesh);
                }
            }
        }

        [Test]
        public void IgnoresTrailingBytesWhenDecodingTheBody()
        {
            using (var withPadding = Decode(TkmsFixture.Build(
                       TkmsFixture.QuadVertices, TkmsFixture.QuadIndices,
                       new TkmsFixture.Options { TrailingBytes = 16 })))
            using (var without = Decode(TkmsFixture.Quad()))
            {
                Assert.AreEqual(without.VertexCount, withPadding.VertexCount);
                Assert.AreEqual(without.IndexCount, withPadding.IndexCount);
                for (int i = 0; i < without.VertexCount; i++)
                {
                    Assert.AreEqual(without.Vertices[i], withPadding.Vertices[i]);
                }
            }
        }

        [Test]
        public void RejectsNonFiniteVertexCoordinates()
        {
            var vertices = (float[])TkmsFixture.TriangleVertices.Clone();
            vertices[3] = float.NaN;
            // The bbox is overridden too, so the NaN check is what fires rather than the box
            // comparison downstream of it.
            AssertRejected(
                TkmsFixture.Build(vertices, TkmsFixture.TriangleIndices,
                    new TkmsFixture.Options { BboxOverride = new[] { 0f, 0f, 10f, 10f } }),
                "NaN or infinity");
        }

        [Test]
        public void RejectsIndicesPastTheVertexCount()
        {
            AssertRejected(
                TkmsFixture.Build(TkmsFixture.TriangleVertices, new[] { 0, 1, 3 }),
                "out of range for 3 vertices");
        }

        [Test]
        public void RejectsABoundingBoxThatDoesNotMatchTheVertices()
        {
            // A box wider than the geometry is still a lie: viewport culling reads it, so a
            // wrong box makes a territory vanish rather than look wrong.
            AssertRejected(
                TkmsFixture.Build(TkmsFixture.TriangleVertices, TkmsFixture.TriangleIndices,
                    new TkmsFixture.Options { BboxOverride = new[] { -1f, -1f, 11f, 11f } }),
                "does not match the decoded vertices");
        }

        [Test]
        public void RejectsWithoutLeakingNativeMemory()
        {
            // Every early throw runs through the same finally block. If it did not, the leak
            // detector would report the vertex buffer at the end of the run rather than here,
            // where the cause is still visible.
            var previous = NativeLeakDetection.Mode;
            NativeLeakDetection.Mode = NativeLeakDetectionMode.EnabledWithStackTrace;
            try
            {
                for (int i = 0; i < 32; i++)
                {
                    Assert.Throws<TkmsFormatException>(() =>
                    {
                        using (Decode(TkmsFixture.Build(
                                   TkmsFixture.TriangleVertices, new[] { 0, 1, 99 })))
                        {
                        }
                    });
                }

                GC.Collect();
                GC.WaitForPendingFinalizers();
            }
            finally
            {
                NativeLeakDetection.Mode = previous;
            }
        }

        [Test]
        public void DecodeToMeshRoundTripsASingleCall()
        {
            var payload = new NativeArray<byte>(TkmsFixture.Quad(), Allocator.Persistent);
            try
            {
                var mesh = MeshDecoder.DecodeToMesh(payload, "round-trip");
                try
                {
                    Assert.AreEqual("round-trip", mesh.name);
                    Assert.AreEqual(4, mesh.vertexCount);
                }
                finally
                {
                    UnityEngine.Object.DestroyImmediate(mesh);
                }
            }
            finally
            {
                payload.Dispose();
            }
        }
    }
}
