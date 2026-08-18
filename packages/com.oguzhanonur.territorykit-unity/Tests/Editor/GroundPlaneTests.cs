using NUnit.Framework;
using UnityEngine;

namespace TerritoryKit.Unity.Tests
{
    /// <summary>
    /// The world&lt;-&gt;local ground-plane math shared by ViewportStreamer (a camera's visible
    /// box) and TerritoryPicker (one click point). Both read the real <c>mapRoot</c> transform
    /// rather than assuming <see cref="TerritoryMapPlacement.RootRotation"/> at the world origin,
    /// so these tests deliberately move and rotate the root to prove that.
    /// </summary>
    public class GroundPlaneTests
    {
        private GameObject _rootObject;
        private GameObject _cameraObject;
        private Camera _camera;

        [SetUp]
        public void SetUp()
        {
            _rootObject = new GameObject("map-root");
            _cameraObject = new GameObject("camera");
            _camera = _cameraObject.AddComponent<Camera>();
            _camera.aspect = 1f;
        }

        [TearDown]
        public void TearDown()
        {
            if (_rootObject != null) Object.DestroyImmediate(_rootObject);
            if (_cameraObject != null) Object.DestroyImmediate(_cameraObject);
        }

        [Test]
        public void AStraightDownRayAtTheOriginRootRecoversTheLocalPoint()
        {
            _rootObject.transform.localRotation = TerritoryMapPlacement.RootRotation;
            Vector3 world = TerritoryMapPlacement.ToWorld(120f, -340f);
            var ray = new Ray(world + Vector3.up * 500f, Vector3.down);

            bool hit = TerritoryMapPlacement.TryGroundPlanePoint(_rootObject.transform, ray, out Vector2 local);

            Assert.IsTrue(hit);
            Assert.AreEqual(120f, local.x, 1e-3f);
            Assert.AreEqual(-340f, local.y, 1e-3f);
        }

        [Test]
        public void AMovedAndRotatedRootStillRecoversTheLocalPoint()
        {
            // The point of reading mapRoot instead of assuming RootRotation at the origin: move
            // the whole map and the answer must move with it.
            _rootObject.transform.SetPositionAndRotation(new Vector3(1000f, 25f, -500f),
                TerritoryMapPlacement.RootRotation * Quaternion.Euler(0f, 40f, 0f));

            Vector2 expectedLocal = new Vector2(50f, -80f);
            Vector3 world = _rootObject.transform.TransformPoint(new Vector3(expectedLocal.x, expectedLocal.y, 0f));
            var ray = new Ray(world + _rootObject.transform.forward * -200f, _rootObject.transform.forward);

            bool hit = TerritoryMapPlacement.TryGroundPlanePoint(_rootObject.transform, ray, out Vector2 local);

            Assert.IsTrue(hit);
            Assert.AreEqual(expectedLocal.x, local.x, 1e-2f);
            Assert.AreEqual(expectedLocal.y, local.y, 1e-2f);
        }

        [Test]
        public void ARayParallelToThePlaneMisses()
        {
            _rootObject.transform.localRotation = TerritoryMapPlacement.RootRotation;
            // The ground plane's normal is world Y; a ray travelling along world X never meets it.
            var ray = new Ray(new Vector3(0f, 10f, 0f), Vector3.right);

            bool hit = TerritoryMapPlacement.TryGroundPlanePoint(_rootObject.transform, ray, out _);

            Assert.IsFalse(hit);
        }

        [Test]
        public void ANullMapRootIsAMissNotAnException()
        {
            var ray = new Ray(Vector3.up * 10f, Vector3.down);
            Assert.DoesNotThrow(() =>
            {
                bool hit = TerritoryMapPlacement.TryGroundPlanePoint(null, ray, out _);
                Assert.IsFalse(hit);
            });
        }

        [Test]
        public void CameraGroundBoundsRoundTripsThroughFrameBounds()
        {
            _rootObject.transform.localRotation = TerritoryMapPlacement.RootRotation;
            // 400x200 at aspect 1 is wider than it is tall, so FrameBounds's half-height grows
            // to cover the width (200, not the 100 the height alone would ask for) -- same fit
            // WindingPlacementTests.FrameBoundsPointsTheCameraDownAtTheCentre pins. The visible
            // box is therefore centred on the same point but taller than the input box: X
            // reproduces exactly, Y is padded out to [-150, 250].
            TerritoryMapPlacement.FrameBounds(_camera, -100f, -50f, 300f, 150f, padding: 1f);

            LocalBounds? bounds = TerritoryMapPlacement.CameraGroundBounds(_camera, _rootObject.transform);

            Assert.IsTrue(bounds.HasValue);
            Assert.AreEqual(-100f, bounds.Value.MinX, 1f);
            Assert.AreEqual(-150f, bounds.Value.MinY, 1f);
            Assert.AreEqual(300f, bounds.Value.MaxX, 1f);
            Assert.AreEqual(250f, bounds.Value.MaxY, 1f);
        }

        [Test]
        public void CameraGroundBoundsIsNullWhenEitherArgumentIsMissing()
        {
            Assert.IsNull(TerritoryMapPlacement.CameraGroundBounds(null, _rootObject.transform));
            Assert.IsNull(TerritoryMapPlacement.CameraGroundBounds(_camera, null));
        }

        [Test]
        public void ExpandedGrowsEverySideByTheMargin()
        {
            var bounds = new LocalBounds(0f, 0f, 10f, 20f);

            LocalBounds grown = bounds.Expanded(5f);

            Assert.AreEqual(-5f, grown.MinX);
            Assert.AreEqual(-5f, grown.MinY);
            Assert.AreEqual(15f, grown.MaxX);
            Assert.AreEqual(25f, grown.MaxY);
        }

        [Test]
        public void IntersectsIsTrueForOverlapAndFalseForSeparateBoxes()
        {
            var a = new LocalBounds(0f, 0f, 10f, 10f);
            var overlapping = new LocalBounds(5f, 5f, 15f, 15f);
            var touching = new LocalBounds(10f, 0f, 20f, 10f);
            var separate = new LocalBounds(20f, 20f, 30f, 30f);

            Assert.IsTrue(a.Intersects(overlapping));
            Assert.IsTrue(a.Intersects(touching), "touching edges must count as intersecting");
            Assert.IsFalse(a.Intersects(separate));
        }

        [Test]
        public void IsEmptyIsTrueOnlyWhenMinExceedsMax()
        {
            Assert.IsFalse(new LocalBounds(0f, 0f, 10f, 10f).IsEmpty);
            Assert.IsTrue(new LocalBounds(10f, 0f, 0f, 10f).IsEmpty);
        }
    }
}
