using System.Collections;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace TerritoryKit.Unity.Tests
{
    /// <summary>
    /// The two things cancellation must not do: leak meshes, and touch Unity from a worker thread.
    /// </summary>
    /// <remarks>
    /// Both claims are invisible from outside the package, so both are asserted through internal
    /// seams rather than by racing the implementation. A timing-based version of either test
    /// would pass while the bug was present, which is how both survived the first round.
    /// </remarks>
    public class CancellationSafetyTests
    {
        private MockGeometryServer _server;
        private GameObject _host;

        private const string DatasetJson = @"{
            ""id"": ""cancel-fixture"",
            ""revisionId"": ""rev1"",
            ""boundsLocal"": [0.0, 0.0, 200.0, 100.0],
            ""territoryCount"": 2,
            ""levels"": [
                { ""lod"": ""high"", ""lossy"": false, ""topologyChanged"": false,
                  ""pickingUnsafe"": false, ""simplification"": { ""topologyChanged"": false } }
            ]
        }";

        [SetUp]
        public void SetUp()
        {
            _server = new MockGeometryServer { DatasetJson = DatasetJson };
            _server.TerritoryPages.Add(
                @"{""items"":[{""id"":""a"",""bboxLocal"":[0,0,100,100]},
                              {""id"":""b"",""bboxLocal"":[100,0,200,100]}],""nextCursor"":""""}");
            _server.Meshes["a"] = TkmsFixture.Quad();
            _server.Meshes["b"] = TkmsFixture.Triangle();
            _host = new GameObject("host");
        }

        [TearDown]
        public void TearDown()
        {
            if (_host != null) Object.DestroyImmediate(_host);
            _server?.Dispose();
            _server = null;
        }

        /// <summary>
        /// Counts meshes named after the fixture's territories. Narrower than a total mesh
        /// count, which would also move whenever Unity loads a built-in mesh.
        /// </summary>
        private static int CountTerritoryMeshes()
        {
            int count = 0;
            foreach (Mesh mesh in Resources.FindObjectsOfTypeAll<Mesh>())
            {
                if (mesh != null && (mesh.name == "a" || mesh.name == "b"))
                {
                    count++;
                }
            }

            return count;
        }

        [UnityTest]
        public IEnumerator CancellingAfterTheBatchDestroysTheMeshesItProduced()
        {
            Assert.AreEqual(0, CountTerritoryMeshes(), "the fixture leaked from an earlier test");

            var renderer = _host.AddComponent<TerritoryMapRenderer>();
            renderer.LoadOnStart = false;
            renderer.BaseUrl = _server.BaseUrl;
            renderer.DatasetId = "cancel-fixture";
            renderer.Lod = "high";

            // Cancel in the exact window the review named: the meshes exist, Draw has not taken
            // them yet. Task.Run does not unwind once started, so this is reachable in
            // production through OnDestroy or a second LoadAsync.
            var cts = new CancellationTokenSource();
            renderer.AfterBatchObserver = () => cts.Cancel();

            Task task = renderer.LoadAsync(cts.Token);
            while (!task.IsCompleted)
            {
                yield return null;
            }

            Assert.IsTrue(task.IsCanceled || task.IsFaulted, "the load should not have succeeded");
            Assert.AreEqual(0, renderer.DrawnCount, "nothing should have been drawn");

            // Destroy() is deferred to end of frame in play mode, so give it one.
            yield return null;
            yield return null;

            Assert.AreEqual(0, CountTerritoryMeshes(),
                "meshes decoded before the cancellation were never handed to Draw, so nothing " +
                "else was ever going to destroy them");

            cts.Dispose();
        }

        [UnityTest]
        public IEnumerator ASuccessfulLoadStillOwnsItsMeshes()
        {
            // The control for the test above: proves the cleanup path is not simply destroying
            // meshes on every load.
            var renderer = _host.AddComponent<TerritoryMapRenderer>();
            renderer.LoadOnStart = false;
            renderer.BaseUrl = _server.BaseUrl;
            renderer.DatasetId = "cancel-fixture";
            renderer.Lod = "high";

            Task task = renderer.LoadAsync();
            while (!task.IsCompleted)
            {
                yield return null;
            }

            if (task.IsFaulted)
            {
                throw task.Exception.InnerException ?? task.Exception;
            }

            Assert.AreEqual(2, renderer.DrawnCount);
            yield return null;
            Assert.AreEqual(2, CountTerritoryMeshes());
        }

        [UnityTest]
        public IEnumerator AbortIsNeverIssuedFromTheCancellingThread()
        {
            // CancellationToken.Register would run on whichever thread called Cancel(), and both
            // request.isDone and request.Abort() are Unity APIs. Cancelling from a worker thread
            // is what tells the two designs apart: polling issues the abort on the thread that
            // started the request, a Register callback issues it here.
            int mainThreadId = Thread.CurrentThread.ManagedThreadId;
            int abortThreadId = -1;
            int cancelThreadId = -1;

            _server.DelaySeconds = 5;
            var client = new TerritoryClient(_server.BaseUrl)
            {
                TimeoutSeconds = 30,
                AbortObserver = () => abortThreadId = Thread.CurrentThread.ManagedThreadId
            };

            var cts = new CancellationTokenSource();
            Task<Mesh> task = client.GetMeshAsync("cancel-fixture", "rev1", "a", "high", cts.Token);

            yield return null;
            Task cancelling = Task.Run(() =>
            {
                cancelThreadId = Thread.CurrentThread.ManagedThreadId;
                cts.Cancel();
            });

            while (!cancelling.IsCompleted)
            {
                yield return null;
            }

            // Checked before anything else: if the abort is issued from a Register callback,
            // Cancel() is where the Unity API call lands, and its failure surfaces here rather
            // than as a thread id mismatch.
            Assert.IsFalse(cancelling.IsFaulted,
                "cancelling from a worker thread threw: " +
                (cancelling.Exception?.InnerException?.Message ?? "unknown"));

            float deadline = Time.realtimeSinceStartup + 5f;
            while (!task.IsCompleted && Time.realtimeSinceStartup < deadline)
            {
                yield return null;
            }

            Assert.IsTrue(task.IsCompleted, "cancelling must abort the request, not wait it out");
            Assert.AreNotEqual(mainThreadId, cancelThreadId,
                "the test has to cancel from a worker thread or it proves nothing");
            Assert.AreNotEqual(-1, abortThreadId, "no abort was issued");
            Assert.AreEqual(mainThreadId, abortThreadId,
                "Abort() reached a Unity API from the thread that called Cancel()");

            cts.Dispose();
        }
    }
}
