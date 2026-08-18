using System.Collections.Generic;
using NUnit.Framework;
using Unity.Collections;
using UnityEngine;

namespace TerritoryKit.Unity.Tests
{
    /// <summary>
    /// The CPU point-in-triangle path, exercised directly against decoded fixtures -- no
    /// network, no pool, no camera required for the coordinate-level tests.
    /// </summary>
    public class TerritoryPickerTests
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

        [Test]
        public void AKnownPointInsideATriangleResolvesToItsId()
        {
            using (TkmsMeshData mesh = Decode(TkmsFixture.Triangle()))
            {
                var candidates = new List<TerritoryPicker.Candidate>
                {
                    new TerritoryPicker.Candidate("a", mesh)
                };

                // TkmsFixture.Triangle(): (0,0) -> (0,10) -> (10,0); (2, 2) is inside it.
                bool hit = TerritoryPicker.TryPick(new Vector2(2f, 2f), candidates, out string id);

                Assert.IsTrue(hit);
                Assert.AreEqual("a", id);
            }
        }

        [Test]
        public void APointOutsideEveryBoundingBoxMissesWithoutATriangleTest()
        {
            using (TkmsMeshData mesh = Decode(TkmsFixture.Triangle()))
            {
                var candidates = new List<TerritoryPicker.Candidate>
                {
                    new TerritoryPicker.Candidate("a", mesh)
                };

                bool hit = TerritoryPicker.TryPick(new Vector2(1000f, 1000f), candidates, out string id);

                Assert.IsFalse(hit);
                Assert.IsNull(id);
            }
        }

        [Test]
        public void APointInsideTheBoundingBoxButOutsideTheTriangleMisses()
        {
            // The triangle (0,0)->(0,10)->(10,0) occupies only the lower-left half of its own
            // [0,10]x[0,10] bounding box -- (9, 9) clears the bbox prefilter but is not inside
            // the triangle itself, so this is the case that actually exercises the exact test
            // rather than the bbox shortcut.
            using (TkmsMeshData mesh = Decode(TkmsFixture.Triangle()))
            {
                var candidates = new List<TerritoryPicker.Candidate>
                {
                    new TerritoryPicker.Candidate("a", mesh)
                };

                bool hit = TerritoryPicker.TryPick(new Vector2(9f, 9f), candidates, out string id);

                Assert.IsFalse(hit);
            }
        }

        [Test]
        public void APointOnTheSharedEdgeBetweenTwoTrianglesOfTheSameMeshStillHits()
        {
            // TkmsFixture.Quad() is two triangles sharing the (0,0)-(100,100) diagonal; (50, 50)
            // sits exactly on that shared edge -- the near-boundary case a strict inequality
            // test would wrongly reject.
            using (TkmsMeshData mesh = Decode(TkmsFixture.Quad()))
            {
                var candidates = new List<TerritoryPicker.Candidate>
                {
                    new TerritoryPicker.Candidate("q", mesh)
                };

                bool hit = TerritoryPicker.TryPick(new Vector2(50f, 50f), candidates, out string id);

                Assert.IsTrue(hit);
                Assert.AreEqual("q", id);
            }
        }

        [Test]
        public void TheFirstCandidateWhoseBoundingBoxRejectsIsSkippedInFavourOfTheSecond()
        {
            byte[] farAway = TkmsFixture.Build(new[] { 500f, 500f, 500f, 510f, 510f, 500f },
                TkmsFixture.TriangleIndices);

            using (TkmsMeshData a = Decode(farAway))
            using (TkmsMeshData b = Decode(TkmsFixture.Triangle()))
            {
                var candidates = new List<TerritoryPicker.Candidate>
                {
                    new TerritoryPicker.Candidate("far", a),
                    new TerritoryPicker.Candidate("near", b)
                };

                bool hit = TerritoryPicker.TryPick(new Vector2(2f, 2f), candidates, out string id);

                Assert.IsTrue(hit);
                Assert.AreEqual("near", id);
            }
        }

        [Test]
        public void AnEmptyCandidateListMisses()
        {
            bool hit = TerritoryPicker.TryPick(Vector2.zero, new List<TerritoryPicker.Candidate>(), out string id);
            Assert.IsFalse(hit);
            Assert.IsNull(id);
        }

        [Test]
        public void TryPickFromScreenPointResolvesThroughTheCameraAndGroundPlane()
        {
            var rootObject = new GameObject("map-root");
            var cameraObject = new GameObject("camera");
            try
            {
                rootObject.transform.localRotation = TerritoryMapPlacement.RootRotation;
                Camera camera = cameraObject.AddComponent<Camera>();
                camera.aspect = 1f;
                // Framed on (0,0)-(4,4) rather than the triangle's own (0,0)-(10,10) bbox: the
                // centre of that smaller box is (2, 2), comfortably inside the triangle
                // (x + y = 4 < 10) rather than sitting exactly on its hypotenuse.
                TerritoryMapPlacement.FrameBounds(camera, 0f, 0f, 4f, 4f, padding: 1f);

                using (TkmsMeshData mesh = Decode(TkmsFixture.Triangle()))
                {
                    var candidates = new List<TerritoryPicker.Candidate>
                    {
                        new TerritoryPicker.Candidate("a", mesh)
                    };

                    // The camera is centred at local (2, 2), the middle of the screen.
                    var screenPoint = new Vector2(camera.pixelWidth / 2f, camera.pixelHeight / 2f);

                    bool hit = TerritoryPicker.TryPickFromScreenPoint(camera, rootObject.transform,
                        screenPoint, candidates, out string id);

                    Assert.IsTrue(hit);
                    Assert.AreEqual("a", id);
                }
            }
            finally
            {
                Object.DestroyImmediate(rootObject);
                Object.DestroyImmediate(cameraObject);
            }
        }

        [Test]
        public void TryPickFromScreenPointMissesWithoutACamera()
        {
            bool hit = TerritoryPicker.TryPickFromScreenPoint(null, null, Vector2.zero,
                new List<TerritoryPicker.Candidate>(), out string id);
            Assert.IsFalse(hit);
        }
    }
}
