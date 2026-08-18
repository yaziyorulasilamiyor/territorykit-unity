using System.Collections;
using System.Globalization;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace TerritoryKit.Unity.Tests
{
    /// <summary>
    /// Drives a real <see cref="ViewportStreamer"/> against <see cref="MockGeometryServer"/>: the
    /// camera decides which territories load and unload, a LOD change reloads everything visible,
    /// and none of it leaks pool capacity.
    /// </summary>
    /// <remarks>
    /// The mock server is a transport fake, not a spatial one — it returns whatever
    /// <see cref="MockGeometryServer.ViewportPages"/> page is queued regardless of the requested
    /// bbox. Tests that need "the camera only sees these ids" drive that directly through the
    /// queue, the way a real server's spatial filter would; the bbox the streamer itself computed
    /// and sent is checked separately, from the request log.
    /// </remarks>
    public class ViewportStreamerTests
    {
        private MockGeometryServer _server;
        private GameObject _host;
        private Camera _camera;

        private const string DatasetJson = @"{
            ""id"": ""streamer-fixture"",
            ""revisionId"": ""rev1"",
            ""boundsLocal"": [0.0, 0.0, 400.0, 100.0],
            ""territoryCount"": 2,
            ""levels"": [
                { ""lod"": ""high"", ""lossy"": false, ""topologyChanged"": false,
                  ""pickingUnsafe"": false, ""simplification"": { ""topologyChanged"": false } },
                { ""lod"": ""medium"", ""lossy"": false, ""topologyChanged"": false,
                  ""pickingUnsafe"": false, ""simplification"": { ""topologyChanged"": false } }
            ]
        }";

        private static byte[] Tile(float x0, float y0, float x1, float y1)
        {
            var vertices = new[] { x0, y0, x0, y1, x1, y1, x1, y0 };
            return TkmsFixture.Build(vertices, new[] { 0, 1, 2, 0, 2, 3 });
        }

        [SetUp]
        public void SetUp()
        {
            _server = new MockGeometryServer { DatasetJson = DatasetJson };
            _host = new GameObject("host");
            var cameraObject = new GameObject("camera");
            _camera = cameraObject.AddComponent<Camera>();
            _camera.aspect = 1f;
        }

        [TearDown]
        public void TearDown()
        {
            if (_host != null) Object.DestroyImmediate(_host);
            if (_camera != null) Object.DestroyImmediate(_camera.gameObject);
            _server?.Dispose();
            _server = null;
        }

        private ViewportStreamer CreateStreamer()
        {
            var streamer = _host.AddComponent<ViewportStreamer>();
            streamer.BaseUrl = _server.BaseUrl;
            streamer.DatasetId = "streamer-fixture";
            streamer.TargetCamera = _camera;
            streamer.TickIntervalSeconds = 0f; // eligible to tick every Update in tests
            streamer.ViewportMarginRatio = 0f; // exact bbox, no padding, so fixtures stay predictable
            return streamer;
        }

        /// <summary>
        /// Waits for one real fetch attempt (<c>RunTickAsync</c> completing) to finish — not for
        /// any particular number of <c>Update()</c> calls, since with a zero tick interval most
        /// frames are eligible to tick but skip silently once the camera's view is already
        /// covered by the last request.
        /// </summary>
        private IEnumerator WaitForTick(ViewportStreamer streamer)
        {
            bool fired = false;
            streamer.TickObserver = () => fired = true;

            float deadline = Time.realtimeSinceStartup + 5f;
            while (!fired && Time.realtimeSinceStartup < deadline)
            {
                yield return null;
            }

            Assert.IsTrue(fired, "no completed fetch observed within the deadline");
            streamer.TickObserver = null;
        }

        private IEnumerator WaitForDataset(ViewportStreamer streamer)
        {
            float deadline = Time.realtimeSinceStartup + 5f;
            while (streamer.Dataset == null && Time.realtimeSinceStartup < deadline)
            {
                yield return null;
            }

            Assert.IsNotNull(streamer.Dataset, "dataset metadata never arrived");
        }

        private static string FindLastViewportRequest(MockGeometryServer server)
        {
            string last = null;
            foreach (string request in server.Requests)
            {
                if (request.Contains("/viewport"))
                {
                    last = request;
                }
            }

            return last;
        }

        [UnityTest]
        public IEnumerator TheStreamerSendsTheCamerasFramedBoxToViewport()
        {
            _server.ViewportPages.Add(@"{""territoryIds"":[],""nextCursor"":""""}");
            var streamer = CreateStreamer();
            yield return WaitForDataset(streamer);

            TerritoryMapPlacement.FrameBounds(_camera, 10f, 20f, 30f, 70f, padding: 1f);
            yield return WaitForTick(streamer);

            string request = FindLastViewportRequest(_server);
            Assert.IsNotNull(request);
            StringAssert.Contains("lod=high", request);

            // aspect=1 and a 20x50 box: half-height must grow to cover the width (padding=1), so
            // the sent box is centred on (20, 45) but taller than the input box -- same aspect
            // fit GroundPlaneTests.CameraGroundBoundsRoundTripsThroughFrameBounds pins. Only the
            // centre is asserted here; the exact fit math is that test's job.
            string bbox = ExtractBboxParam(request);
            float[] parts = ParseBbox(bbox);
            float centreX = (parts[0] + parts[2]) / 2f;
            float centreY = (parts[1] + parts[3]) / 2f;
            Assert.AreEqual(20f, centreX, 1f);
            Assert.AreEqual(45f, centreY, 1f);
        }

        private static string ExtractBboxParam(string request)
        {
            int start = request.IndexOf("bbox=") + "bbox=".Length;
            int end = request.IndexOf('&', start);
            return end < 0 ? request.Substring(start) : request.Substring(start, end - start);
        }

        private static float[] ParseBbox(string bbox)
        {
            string[] pieces = bbox.Split(',');
            var result = new float[pieces.Length];
            for (int i = 0; i < pieces.Length; i++)
            {
                result[i] = float.Parse(pieces[i], CultureInfo.InvariantCulture);
            }

            return result;
        }

        [UnityTest]
        public IEnumerator PanningTheCameraLoadsNewTerritoriesAndUnloadsOnesLeftBehind()
        {
            _server.Meshes["a"] = Tile(0f, 0f, 100f, 100f);
            _server.Meshes["b"] = Tile(300f, 0f, 400f, 100f);

            var streamer = CreateStreamer();
            yield return WaitForDataset(streamer);

            _server.ViewportPages.Add(@"{""territoryIds"":[""a""],""nextCursor"":""""}");
            TerritoryMapPlacement.FrameBounds(_camera, 0f, 0f, 100f, 100f, padding: 1f);
            yield return WaitForTick(streamer);

            Assert.AreEqual(1, streamer.VisibleCount);

            _server.ViewportPages.Clear();
            _server.ViewportPages.Add(@"{""territoryIds"":[""b""],""nextCursor"":""""}");
            TerritoryMapPlacement.FrameBounds(_camera, 300f, 0f, 400f, 100f, padding: 1f);
            yield return WaitForTick(streamer);

            Assert.AreEqual(1, streamer.VisibleCount,
                "panning away must unload the old territory, not accumulate it");
        }

        [UnityTest]
        public IEnumerator AChangeInLodReloadsEveryVisibleTerritory()
        {
            _server.Meshes["a"] = Tile(0f, 0f, 100f, 100f);
            _server.ViewportPages.Add(@"{""territoryIds"":[""a""],""nextCursor"":""""}");

            var streamer = CreateStreamer();
            yield return WaitForDataset(streamer);

            TerritoryMapPlacement.FrameBounds(_camera, 0f, 0f, 100f, 100f, padding: 1f);
            yield return WaitForTick(streamer);

            Assert.AreEqual("high", streamer.CurrentLod);
            Assert.AreEqual(1, streamer.VisibleCount);
            int requestsBeforeLodChange = _server.Requests.Count;

            // Push the camera past the default high->medium threshold (60,000) but not as far as
            // medium->low (180,000), without moving the framed centre -- isolates the LOD change
            // from a bbox change and lands on exactly one level, not two.
            _camera.orthographicSize = 100_000f;
            yield return WaitForTick(streamer);

            Assert.AreEqual("medium", streamer.CurrentLod);
            Assert.AreEqual(1, streamer.VisibleCount, "the same territory must still be visible, just at a new level");
            Assert.Greater(_server.Requests.Count, requestsBeforeLodChange,
                "a LOD change must issue a fresh mesh fetch even when the id set does not change");
        }

        [UnityTest]
        public IEnumerator RepeatedPanningLeavesThePoolAtAStableCapacity()
        {
            _server.Meshes["a"] = Tile(0f, 0f, 100f, 100f);
            _server.Meshes["b"] = Tile(300f, 0f, 400f, 100f);

            var streamer = CreateStreamer();
            yield return WaitForDataset(streamer);

            for (int i = 0; i < 10; i++)
            {
                _server.ViewportPages.Clear();
                _server.ViewportPages.Add(@"{""territoryIds"":[""a""],""nextCursor"":""""}");
                TerritoryMapPlacement.FrameBounds(_camera, 0f, 0f, 100f, 100f, padding: 1f);
                yield return WaitForTick(streamer);

                _server.ViewportPages.Clear();
                _server.ViewportPages.Add(@"{""territoryIds"":[""b""],""nextCursor"":""""}");
                TerritoryMapPlacement.FrameBounds(_camera, 300f, 0f, 400f, 100f, padding: 1f);
                yield return WaitForTick(streamer);
            }

            TerritoryPoolStats stats = streamer.PoolStats;
            Assert.LessOrEqual(stats.TotalGameObjectsCreated, 48,
                "10 back-and-forth pans over a warm pool of 48 must not grow it");
            Assert.AreEqual(stats.TotalGameObjectsCreated, stats.TotalMeshesCreated);
        }

        [UnityTest]
        public IEnumerator PickingResolvesAVisibleTerritoryAtItsScreenPosition()
        {
            _server.Meshes["a"] = Tile(0f, 0f, 100f, 100f);
            _server.ViewportPages.Add(@"{""territoryIds"":[""a""],""nextCursor"":""""}");

            var streamer = CreateStreamer();
            yield return WaitForDataset(streamer);

            TerritoryMapPlacement.FrameBounds(_camera, 0f, 0f, 100f, 100f, padding: 1f);
            yield return WaitForTick(streamer);

            Assert.AreEqual(1, streamer.VisibleCount);
            var screenCentre = new Vector2(_camera.pixelWidth / 2f, _camera.pixelHeight / 2f);

            bool hit = streamer.TryPick(screenCentre, out string id, out LodSafety safety);

            Assert.IsTrue(hit);
            Assert.AreEqual("a", id);
            Assert.AreEqual("high", safety.Lod);
        }
    }
}
