using System;
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
    /// Drives <see cref="TerritoryClient"/> against an in-process HTTP server, so the transport,
    /// the JSON models and the TKMB container are exercised end to end without a Python server.
    /// </summary>
    public class TerritoryClientPlayModeTests
    {
        private MockGeometryServer _server;
        private TerritoryClient _client;

        private const string DatasetJson = @"{
            ""id"": ""tr-adm1"",
            ""name"": ""Turkey provinces"",
            ""revisionId"": ""abc123"",
            ""isCurrentRevision"": true,
            ""origin"": { ""lon"": 35.2, ""lat"": 38.9, ""projection"": ""webmercator-local"", ""scale"": 0.7779 },
            ""boundsLocal"": [-100.0, -50.0, 300.0, 150.0],
            ""territoryCount"": 2,
            ""levels"": [
                { ""lod"": ""high"", ""territoryCount"": 2, ""vertexCount"": 8, ""triangleCount"": 4,
                  ""byteLength"": 200, ""lossy"": true, ""topologyChanged"": true,
                  ""pickingUnsafe"": true, ""simplification"": { ""topologyChanged"": false } },
                { ""lod"": ""low"", ""territoryCount"": 2, ""vertexCount"": 8, ""triangleCount"": 4,
                  ""byteLength"": 200, ""lossy"": false, ""topologyChanged"": false,
                  ""pickingUnsafe"": false, ""simplification"": { ""topologyChanged"": true } }
            ]
        }";

        [SetUp]
        public void SetUp()
        {
            _server = new MockGeometryServer { DatasetJson = DatasetJson };
            _server.Meshes["06"] = TkmsFixture.Quad();
            _server.Meshes["34"] = TkmsFixture.Triangle();
            _client = new TerritoryClient(_server.BaseUrl) { TimeoutSeconds = 10 };
        }

        [TearDown]
        public void TearDown()
        {
            _server?.Dispose();
            _server = null;
        }

        /// <summary>Pumps a task to completion inside a UnityTest coroutine.</summary>
        private static IEnumerator Await(Task task)
        {
            while (!task.IsCompleted)
            {
                yield return null;
            }

            if (task.IsFaulted)
            {
                throw task.Exception.InnerException ?? task.Exception;
            }
        }

        [UnityTest]
        public IEnumerator ReadsDatasetMetadataIncludingLevelFlags()
        {
            Task<DatasetInfo> task = _client.GetDatasetAsync("tr-adm1");
            yield return Await(task);

            DatasetInfo info = task.Result;
            Assert.AreEqual("tr-adm1", info.id);
            Assert.AreEqual("abc123", info.revisionId);
            Assert.AreEqual(38.9, info.origin.lat, 1e-6);
            Assert.AreEqual(4, info.boundsLocal.Length);
            Assert.AreEqual(300f, info.boundsLocal[2]);

            DatasetLevel high = info.FindLevel("high");
            Assert.IsTrue(high.pickingUnsafe);
            Assert.IsFalse(high.simplification.topologyChanged);

            // The chain-wide flag and the simplification-only flag answer different questions,
            // and this fixture is the case where they disagree.
            LodSafety safety = LodPolicy.Describe(info, "high");
            Assert.AreEqual(LodPickingSafety.Unsafe, safety.Safety);
            Assert.That(safety.Reason, Does.Contain("simplification itself changed nothing"));

            Assert.IsTrue(LodPolicy.Describe(info, "low").IsSafeForPicking);
            Assert.AreEqual(LodPickingSafety.Unknown, LodPolicy.Describe(info, "medium").Safety);
        }

        [UnityTest]
        public IEnumerator LoadsASingleMeshFromTheServer()
        {
            Task<Mesh> task = _client.GetMeshAsync("tr-adm1", "abc123", "06", "high");
            yield return Await(task);

            Mesh mesh = task.Result;
            try
            {
                Assert.AreEqual("06", mesh.name);
                Assert.AreEqual(4, mesh.vertexCount);
                Assert.AreEqual(6, mesh.GetIndexCount(0));
                Assert.AreEqual(new Vector3(100f, 100f, 0f), mesh.bounds.size);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(mesh);
            }
        }

        [UnityTest]
        public IEnumerator LoadsSeveralMeshesInOneBatchRequest()
        {
            var missing = new List<string>();
            // Requested out of order on purpose: the container's table of contents is always
            // sorted by id, never by request order.
            Task<Dictionary<string, Mesh>> task = _client.GetMeshBatchAsync(
                "tr-adm1", "abc123", new[] { "34", "06", "99" }, "high", missing);
            yield return Await(task);

            Dictionary<string, Mesh> meshes = task.Result;
            try
            {
                Assert.AreEqual(2, meshes.Count);
                Assert.AreEqual(4, meshes["06"].vertexCount);
                Assert.AreEqual(3, meshes["34"].vertexCount);
                CollectionAssert.AreEqual(new[] { "99" }, missing,
                    "ids the server could not find travel inside the container, not as an error");

                int batchRequests = _server.Requests.FindAll(r => r.Contains("/mesh/batch")).Count;
                Assert.AreEqual(1, batchRequests, "three ids must cost one request");
            }
            finally
            {
                foreach (Mesh mesh in meshes.Values)
                {
                    UnityEngine.Object.DestroyImmediate(mesh);
                }
            }
        }

        [UnityTest]
        public IEnumerator WalksEveryPageOfTheTerritoryListing()
        {
            _server.TerritoryPages.Add(
                @"{""revisionId"":""abc123"",""lod"":""high"",""items"":[
                    {""id"":""06"",""name"":""Ankara"",""bboxLocal"":[0,0,10,10]}],
                   ""nextCursor"":""1"",""scanTruncated"":false}");
            _server.TerritoryPages.Add(
                @"{""revisionId"":""abc123"",""lod"":""high"",""items"":[
                    {""id"":""34"",""name"":""Istanbul"",""bboxLocal"":[0,0,10,10]}],
                   ""nextCursor"":"""",""scanTruncated"":false}");

            Task<List<TerritorySummary>> task = _client.GetAllTerritoriesAsync("tr-adm1", "high");
            yield return Await(task);

            Assert.AreEqual(2, task.Result.Count);
            Assert.AreEqual("06", task.Result[0].id);
            Assert.AreEqual("Istanbul", task.Result[1].name);
        }

        [UnityTest]
        public IEnumerator ReportsServerErrorsWithTheApiErrorCode()
        {
            _server.ForcedStatus = 410;
            _server.ForcedBody =
                @"{""error"":{""code"":""revision_gone"",""message"":""revision was pruned""}}";

            Task<Mesh> task = _client.GetMeshAsync("tr-adm1", "old", "06", "high");
            yield return WaitForCompletion(task);

            Assert.IsTrue(task.IsFaulted);
            var error = task.Exception.InnerException as TerritoryApiException;
            Assert.IsNotNull(error, "a protocol error must surface as TerritoryApiException");
            Assert.AreEqual(410, error.ResponseCode);
            Assert.AreEqual("revision_gone", error.ErrorCode);
        }

        [UnityTest]
        public IEnumerator ReportsCorruptPayloadsAsFormatErrors()
        {
            _server.Meshes["06"] = new byte[] { 1, 2, 3, 4, 5, 6, 7, 8 };

            Task<Mesh> task = _client.GetMeshAsync("tr-adm1", "abc123", "06", "high");
            yield return WaitForCompletion(task);

            Assert.IsTrue(task.IsFaulted);
            Assert.IsInstanceOf<TkmsFormatException>(task.Exception.InnerException,
                "a truncated body must be rejected, not crash");
        }

        [UnityTest]
        public IEnumerator CancellationAbortsTheRequestInsteadOfWaitingForIt()
        {
            _server.DelaySeconds = 5;
            var cts = new CancellationTokenSource();

            Task<Mesh> task = _client.GetMeshAsync("tr-adm1", "abc123", "06", "high", cts.Token);
            yield return null;
            cts.Cancel();

            float deadline = Time.realtimeSinceStartup + 3f;
            while (!task.IsCompleted && Time.realtimeSinceStartup < deadline)
            {
                yield return null;
            }

            Assert.IsTrue(task.IsCompleted,
                "cancelling must abort the in-flight request rather than wait out the delay");
            Assert.IsFalse(task.IsCompletedSuccessfully);
            cts.Dispose();
        }

        /// <summary>Waits without rethrowing, for tests that assert on the failure itself.</summary>
        private static IEnumerator WaitForCompletion(Task task)
        {
            while (!task.IsCompleted)
            {
                yield return null;
            }
        }
    }
}
