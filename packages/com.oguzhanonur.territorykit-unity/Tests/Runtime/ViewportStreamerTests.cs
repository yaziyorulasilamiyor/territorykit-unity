using System;
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
            if (_host != null) UnityEngine.Object.DestroyImmediate(_host);
            if (_camera != null) UnityEngine.Object.DestroyImmediate(_camera.gameObject);
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

        // ---- superseded ticks, failure recovery, batch chunking ----------------------------

        /// <summary>
        /// The accounting that has to balance no matter how the ticks interleave: every
        /// GameObject the pool ever made is either sitting free or checked out by exactly one
        /// visible territory.
        /// </summary>
        private static void AssertPoolAccountingBalances(ViewportStreamer streamer, string when)
        {
            TerritoryPoolStats stats = streamer.PoolStats;
            Assert.AreEqual(stats.TotalGameObjectsCreated,
                stats.FreeGameObjects + streamer.VisibleCount,
                when + ": every pooled GameObject must be either free or held by one visible " +
                "territory -- a leaked slot or a double release shows up here");
            Assert.AreEqual(stats.TotalMeshesCreated, stats.FreeMeshes + streamer.VisibleCount,
                when + ": same for meshes");
        }

        [UnityTest]
        public IEnumerator ATickSupersededAfterItsFetchCommitsNothing()
        {
            // The window the generation guard exists for, entered deterministically rather than
            // by racing: once Task.Run has started decoding, cancelling the token does not unwind
            // it, so the decode finishes and the await hands a perfectly valid dictionary back to
            // a tick that is no longer current. Before the fix there was no check at all between
            // the mesh batch returning and the commit, so that tick committed -- releasing
            // territories the newer tick had just made visible, overwriting newer entries under
            // the same id (leaking both the slot and the mesh data), and firing TickObserver for
            // a result nobody asked for.
            //
            // A timing-based version of this test passes against the broken code, because the
            // client's own cancellation handling catches the far more common network-path case
            // first. That was measured, not assumed: with the guards removed and supersession
            // driven by camera movement plus a slow server, the test still went green.
            _server.Meshes["a"] = Tile(0f, 0f, 100f, 100f);
            _server.ViewportPages.Add(@"{""territoryIds"":[""a""],""nextCursor"":""""}");

            var streamer = CreateStreamer();
            yield return WaitForDataset(streamer);

            bool tickObserverFired = false;
            streamer.TickObserver = () => tickObserverFired = true;
            // Fires after the meshes are fetched and decoded, before the tick asks whether it may
            // still commit them -- exactly where a real supersession lands.
            streamer.AfterFetchObserver = () => streamer.ForceSupersedeForTests();

            TerritoryMapPlacement.FrameBounds(_camera, 0f, 0f, 100f, 100f, padding: 1f);

            float deadline = Time.realtimeSinceStartup + 5f;
            while (Time.realtimeSinceStartup < deadline)
            {
                yield return null;
            }

            Assert.AreEqual(0, streamer.VisibleCount,
                "a tick that lost its claim between fetching and committing must commit nothing");
            Assert.IsFalse(tickObserverFired,
                "and must not report a completion it did not perform");
            AssertPoolAccountingBalances(streamer, "after a superseded tick was discarded");
        }

        [UnityTest]
        public IEnumerator OverlappingTicksLeaveConsistentStateAndTheLastRequestedView()
        {
            // The integration counterpart to the test above: real supersession, driven by moving
            // the camera faster than the server answers, ending in exactly the last view asked
            // for with the pool fully accounted for.
            for (int i = 0; i < 6; i++)
            {
                _server.Meshes["t" + i] = Tile(i * 100f, 0f, i * 100f + 100f, 100f);
            }

            var streamer = CreateStreamer();
            yield return WaitForDataset(streamer);

            _server.DelaySeconds = 0.25;

            for (int i = 0; i < 6; i++)
            {
                _server.ViewportPages.Clear();
                _server.ViewportPages.Add(@"{""territoryIds"":[""t" + i + @"""],""nextCursor"":""""}");
                TerritoryMapPlacement.FrameBounds(_camera, i * 100f, 0f, i * 100f + 100f, 100f, padding: 1f);

                // Long enough for a tick to start, far too short for one to finish.
                float until = Time.realtimeSinceStartup + 0.05f;
                while (Time.realtimeSinceStartup < until)
                {
                    yield return null;
                }
            }

            // Let every straggler land, including the ones cancelled mid-flight.
            _server.DelaySeconds = 0;
            float deadline = Time.realtimeSinceStartup + 8f;
            while (Time.realtimeSinceStartup < deadline)
            {
                yield return null;
            }

            AssertPoolAccountingBalances(streamer, "after six overlapping ticks");
            Assert.AreEqual(1, streamer.VisibleCount,
                "the last camera position shows exactly one territory; a stale tick that " +
                "committed would leave more or fewer");
            Assert.IsTrue(streamer.TryPick(
                new Vector2(_camera.pixelWidth / 2f, _camera.pixelHeight / 2f), out string id, out _));
            Assert.AreEqual("t5", id, "the visible territory must be the last one requested");
        }

        [UnityTest]
        public IEnumerator AFailedRequestIsRetriedWithoutWaitingForTheCameraToMove()
        {
            // Tick() records the box it requested before the fetch starts and skips any later
            // tick still inside it. Nothing used to clear that record when the fetch failed, so a
            // single server error made the streamer go permanently silent at that camera
            // position -- it would only wake up if the user happened to pan far enough to leave
            // the box.
            LogAssert.ignoreFailingMessages = true; // the failing tick logs the exception, by design
            try
            {
                _server.Meshes["a"] = Tile(0f, 0f, 100f, 100f);
                _server.ViewportPages.Add(@"{""territoryIds"":[""a""],""nextCursor"":""""}");

                var streamer = CreateStreamer();
                yield return WaitForDataset(streamer);

                _server.ForcedStatus = 500;
                _server.ForcedBody = @"{""error"":{""code"":""internal"",""message"":""boom""}}";
                TerritoryMapPlacement.FrameBounds(_camera, 0f, 0f, 100f, 100f, padding: 1f);

                int requestsBefore = 0;
                float failDeadline = Time.realtimeSinceStartup + 5f;
                while (Time.realtimeSinceStartup < failDeadline)
                {
                    lock (_server.Requests)
                    {
                        requestsBefore = _server.Requests.Count;
                    }

                    if (requestsBefore > 1)
                    {
                        break;
                    }

                    yield return null;
                }

                Assert.Greater(requestsBefore, 1, "the failing request should have been attempted");
                Assert.AreEqual(0, streamer.VisibleCount, "nothing should be visible after a failure");

                // Recover the server but leave the camera exactly where it is.
                _server.ForcedStatus = 0;
                _server.ForcedBody = null;

                yield return WaitForTick(streamer);

                Assert.AreEqual(1, streamer.VisibleCount,
                    "the streamer must re-request the same view once the server recovers, " +
                    "rather than waiting for the camera to move");
                AssertPoolAccountingBalances(streamer, "after recovering from a failed request");
            }
            finally
            {
                LogAssert.ignoreFailingMessages = false;
            }
        }

        // ---- allocation gates over the whole tick -------------------------------------------
        //
        // TerritoryPoolTests gates the pool's checkout/release cycle at 0 bytes. That is a true
        // statement about the pool and was a misleading one about the component that drives it:
        // an idle camera still ticked ~5 times a second, and each tick allocated a fresh
        // thresholds array before deciding it had nothing to do.

        /// <summary>Budget for a tick that decides the camera has not moved far enough to matter.</summary>
        /// <remarks>
        /// Zero, measured. The skip path reads the camera, hits the cached thresholds array,
        /// compares two structs and returns; every value on it is a struct or a cached reference,
        /// there is no string formatting, and nothing is boxed.
        /// </remarks>
        private const long IdleTickBudgetBytes = 0;

        [UnityTest]
        public IEnumerator AnIdleCameraTickAllocatesNothing()
        {
            _server.Meshes["a"] = Tile(0f, 0f, 100f, 100f);
            _server.ViewportPages.Add(@"{""territoryIds"":[""a""],""nextCursor"":""""}");

            var streamer = CreateStreamer();
            yield return WaitForDataset(streamer);

            TerritoryMapPlacement.FrameBounds(_camera, 0f, 0f, 100f, 100f, padding: 1f);
            yield return WaitForTick(streamer);

            int requestsBefore;
            lock (_server.Requests)
            {
                requestsBefore = _server.Requests.Count;
            }

            // Everything below is the steady state: the camera has not moved, so every tick
            // should reach the "still inside the box we already requested" branch and stop.
            double perTick = AllocationMeasurement.BytesPerIteration(
                "idle streamer tick", 500, () => streamer.TickForTests());

            lock (_server.Requests)
            {
                Assert.AreEqual(requestsBefore, _server.Requests.Count,
                    "an idle camera must issue no requests -- otherwise this measured the " +
                    "request path and the budget below means something else entirely");
            }

            Assert.LessOrEqual(perTick, IdleTickBudgetBytes,
                $"an idle camera must not allocate: {perTick:F2} bytes/tick, budget " +
                $"{IdleTickBudgetBytes}");
        }

        /// <summary>Budget for a tick that actually decides to issue a request.</summary>
        /// <remarks>
        /// Measured at <b>4.0–7.7 KB per request</b> across four runs (Unity 6000.1.1f1, Windows
        /// Editor). Two things widen that into a range rather than a figure, and both mean it is
        /// an <em>upper bound</em>: <c>GC.GetTotalMemory</c> is process-wide, so the in-process
        /// <see cref="MockGeometryServer"/> handling the very same request is charged to it too;
        /// and the gauge moves in 4 KB heap pages, so a 100-iteration window quantises. Not zero
        /// regardless, and not reducible to zero without restructuring the request path away from
        /// async/await. Per issued request it covers, in rough order of size: the
        /// <c>UnityWebRequest</c> and its <c>DownloadHandlerBuffer</c>; the async state-machine
        /// boxes for the five nested methods a viewport call goes through (<c>RunTickAsync</c>,
        /// <c>GetAllViewportIdsAsync</c>, <c>GetViewportAsync</c>, <c>GetJsonAsync</c>,
        /// <c>SendAsync</c>) plus their <c>TaskCompletionSource</c>; the linked
        /// <c>CancellationTokenSource</c> and its registration; and the URL, built from a
        /// <c>StringBuilder</c> and four <c>float.ToString("R")</c> results in
        /// <c>FormatBbox</c>.
        /// <para>
        /// The distinction that matters is that this is charged <em>per request</em>, not per
        /// frame. At the default 0.2 s tick interval a continuously panning camera issues at most
        /// five requests a second — about 25 KB/s while the user is actually dragging — and a
        /// camera at rest issues none and allocates nothing at all, which is what
        /// <see cref="AnIdleCameraTickAllocatesNothing"/> pins at exactly zero.
        /// </para>
        /// The budget sits above the top of the observed range — tight enough that a regression
        /// doubling the real cost fails, loose enough that the measurement noise described above
        /// does not make this flaky.
        /// </remarks>
        private const long RequestingTickBudgetBytes = 16384;

        [UnityTest]
        public IEnumerator APanningTickStaysWithinItsPerRequestAllocationBudget()
        {
            _server.ViewportPages.Add(@"{""territoryIds"":[],""nextCursor"":""""}");

            var streamer = CreateStreamer();
            yield return WaitForDataset(streamer);

            // FrameBounds first, and not as a formality: it is what puts the camera *above* the
            // ground plane looking down. Without it the camera sits at the origin, inside the
            // plane, every corner ray runs parallel to it, CameraGroundBounds returns null, and
            // Tick() returns before issuing anything -- the first version of this test measured
            // that early return and reported a flattering zero.
            TerritoryMapPlacement.FrameBounds(_camera, 0f, 0f, 1000f, 1000f, padding: 1f);
            yield return WaitForTick(streamer);

            int requestsBefore;
            lock (_server.Requests)
            {
                requestsBefore = _server.Requests.Count;
            }

            // Each move puts the camera outside the last requested box, so every tick issues a
            // request rather than skipping.
            double perTick = AllocationMeasurement.BytesPerIteration(
                "requesting streamer tick", 100, () =>
                {
                    _camera.transform.position += new Vector3(5000f, 0f, 0f);
                    streamer.TickForTests();
                });

            int requestsAfter;
            lock (_server.Requests)
            {
                requestsAfter = _server.Requests.Count;
            }

            Assert.Greater(requestsAfter, requestsBefore,
                "this test only means anything if the ticks it measured actually issued requests");

            Assert.LessOrEqual(perTick, RequestingTickBudgetBytes,
                $"a request-issuing tick allocated {perTick:F2} bytes, budget " +
                $"{RequestingTickBudgetBytes}");
        }

        [UnityTest]
        public IEnumerator AViewportWithMoreIdsThanTheBatchLimitIsFetchedInChunks()
        {
            // The real server answers 400 batch_too_large above 200 distinct ids
            // (geometry_api/config.py), and MockGeometryServer enforces the same limit -- so an
            // unchunked client fails this outright rather than merely being impolite.
            const int territoryCount = 250;
            var ids = new System.Text.StringBuilder();
            for (int i = 0; i < territoryCount; i++)
            {
                string id = "t" + i.ToString("D3");
                _server.Meshes[id] = Tile(i * 10f, 0f, i * 10f + 8f, 100f);
                if (i > 0) ids.Append(',');
                ids.Append('"').Append(id).Append('"');
            }

            _server.ViewportPages.Add(@"{""territoryIds"":[" + ids + @"],""nextCursor"":""""}");

            var streamer = CreateStreamer();
            yield return WaitForDataset(streamer);

            TerritoryMapPlacement.FrameBounds(_camera, 0f, 0f, territoryCount * 10f, 100f, padding: 1f);
            yield return WaitForTick(streamer);

            Assert.AreEqual(territoryCount, streamer.VisibleCount,
                "every territory in the viewport must end up loaded, across however many batches");

            lock (_server.BatchSizes)
            {
                Assert.Greater(_server.BatchSizes.Count, 1, "250 ids cannot be one batch");
                foreach (int size in _server.BatchSizes)
                {
                    Assert.LessOrEqual(size, _server.MaxBatchTerritories,
                        "every batch must respect the server's limit");
                }
            }

            AssertPoolAccountingBalances(streamer, "after a chunked fetch");
        }
    }
}
