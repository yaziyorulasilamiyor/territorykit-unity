using NUnit.Framework;
using Unity.Collections;
using UnityEngine;

namespace TerritoryKit.Unity.Tests
{
    /// <summary>
    /// Closes the note Phase 1 left open: TKMS guarantees clockwise winding in its own XY space,
    /// and this asserts that guarantee still points the right way once the mesh is laid flat
    /// into world XZ.
    /// </summary>
    /// <remarks>
    /// This measures the <em>geometric</em> normal, which is not the same claim as "the renderer
    /// draws it". The renderer is asked separately, in the PlayMode coverage test — a derivation
    /// about handedness is exactly the kind that can be self-consistently wrong.
    /// </remarks>
    public class WindingPlacementTests
    {
        private static Vector3 FaceNormal(TkmsMeshData data, int triangle)
        {
            Vector3 a = data.Vertices[data.Indices16[triangle * 3]];
            Vector3 b = data.Vertices[data.Indices16[triangle * 3 + 1]];
            Vector3 c = data.Vertices[data.Indices16[triangle * 3 + 2]];
            return Vector3.Cross(b - a, c - a).normalized;
        }

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

        [Test]
        public void ClockwiseTrianglesHaveALocalMinusZNormal()
        {
            using (var data = Decode(TkmsFixture.Quad()))
            {
                for (int t = 0; t < data.IndexCount / 3; t++)
                {
                    Assert.AreEqual(Vector3.back, FaceNormal(data, t),
                        "triangle " + t + " is not clockwise in the mesh's own XY space");
                }
            }
        }

        [Test]
        public void ThePlacementRotationTurnsThatNormalIntoWorldUp()
        {
            using (var data = Decode(TkmsFixture.Quad()))
            {
                for (int t = 0; t < data.IndexCount / 3; t++)
                {
                    Vector3 world = TerritoryMapPlacement.RootRotation * FaceNormal(data, t);
                    Assert.AreEqual(0f, world.x, 1e-5f);
                    Assert.AreEqual(1f, world.y, 1e-5f, "triangle " + t + " faces away from a camera above");
                    Assert.AreEqual(0f, world.z, 1e-5f);
                }
            }
        }

        [Test]
        public void ReversedWindingWouldFaceAwayFromTheCamera()
        {
            // Proves the two assertions above can fail. Without this, a placement that turned
            // every normal to +Y regardless of winding would pass them.
            var reversed = new[] { 0, 2, 1, 0, 3, 2 };
            using (var data = Decode(TkmsFixture.Build(TkmsFixture.QuadVertices, reversed)))
            {
                for (int t = 0; t < data.IndexCount / 3; t++)
                {
                    Vector3 world = TerritoryMapPlacement.RootRotation * FaceNormal(data, t);
                    Assert.AreEqual(-1f, world.y, 1e-5f);
                }
            }
        }

        [Test]
        public void ThePlacementRotationMapsLocalAxesAsDocumented()
        {
            // The derivation in TerritoryMapPlacement rests on these two mappings; pin them so a
            // change to the rotation has to change this test too.
            Vector3 up = TerritoryMapPlacement.RootRotation * Vector3.up;
            Vector3 forward = TerritoryMapPlacement.RootRotation * Vector3.forward;

            Assert.AreEqual(0f, Vector3.Distance(up, Vector3.forward), 1e-5f, "local +Y must land on world +Z");
            Assert.AreEqual(0f, Vector3.Distance(forward, Vector3.down), 1e-5f, "local +Z must land on world -Y");
        }

        [Test]
        public void ToWorldPlacesLocalMetresOnTheGroundPlane()
        {
            Vector3 world = TerritoryMapPlacement.ToWorld(120f, -340f);

            Assert.AreEqual(120f, world.x, 1e-3f);
            Assert.AreEqual(0f, world.y, 1e-3f, "the map lies on the ground plane");
            Assert.AreEqual(-340f, world.z, 1e-3f, "local Y becomes world Z");
        }

        [Test]
        public void FrameBoundsPointsTheCameraDownAtTheCentre()
        {
            var go = new GameObject("cam");
            try
            {
                var camera = go.AddComponent<Camera>();
                camera.aspect = 1f;
                // 400m wide by 200m tall at aspect 1: wider than it is tall, so the fit is
                // limited by width and the half-height has to grow to 200, not the 100 the
                // height alone would ask for.
                TerritoryMapPlacement.FrameBounds(camera, -100f, -50f, 300f, 150f, 1f);

                Assert.IsTrue(camera.orthographic);
                Assert.AreEqual(200f, camera.orthographicSize, 1e-3f, "half-height must cover the width");
                Assert.AreEqual(100f, camera.transform.position.x, 1e-3f);
                Assert.AreEqual(50f, camera.transform.position.z, 1e-3f);
                Assert.Greater(camera.transform.position.y, 0f, "the camera looks down from above");
                Assert.AreEqual(0f, Vector3.Distance(camera.transform.forward, Vector3.down), 1e-5f);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
            }
        }
    }
}
