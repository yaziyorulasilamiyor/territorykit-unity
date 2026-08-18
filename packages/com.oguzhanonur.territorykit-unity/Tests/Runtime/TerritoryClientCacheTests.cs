using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace TerritoryKit.Unity.Tests
{
    /// <summary>
    /// <see cref="TerritoryClient.GetMeshDataAsync"/> and
    /// <see cref="TerritoryClient.GetMeshDataBatchAsync"/> against a real
    /// <see cref="MeshDiskCache"/> pointed at a throwaway directory, proving the three claims
    /// the design makes: a hit skips the network, a corrupt entry self-heals instead of failing
    /// the load, and a batch only fetches the ids the disk did not already have.
    /// </summary>
    public class TerritoryClientCacheTests
    {
        private MockGeometryServer _server;
        private string _cacheRoot;
        private MeshDiskCache _cache;

        [SetUp]
        public void SetUp()
        {
            _server = new MockGeometryServer();
            _cacheRoot = Path.Combine(Path.GetTempPath(),
                "tkcache-client-test-" + Guid.NewGuid().ToString("N"));
            _cache = new MeshDiskCache(_cacheRoot);
        }

        [TearDown]
        public void TearDown()
        {
            _server?.Dispose();
            _server = null;
            if (Directory.Exists(_cacheRoot))
            {
                Directory.Delete(_cacheRoot, recursive: true);
            }
        }

        [UnityTest]
        public IEnumerator ASecondRequestForTheSameKeyIsServedFromDiskWithoutTouchingTheNetwork()
        {
            _server.Meshes["a"] = TkmsFixture.Triangle();
            var client = new TerritoryClient(_server.BaseUrl, _cache);

            Task<TkmsMeshData> first = client.GetMeshDataAsync("ds", "rev1", "a", "high");
            while (!first.IsCompleted) yield return null;
            first.Result.Dispose();

            int requestsAfterFirst = _server.Requests.Count;
            Assert.Greater(requestsAfterFirst, 0, "the first load has to actually hit the network");

            // The write-through is fire-and-forget; give it a few frames to land on disk.
            float deadline = Time.realtimeSinceStartup + 5f;
            while (!_cache.TryRead("ds", "rev1", "a", "high", out _) &&
                   Time.realtimeSinceStartup < deadline)
            {
                yield return null;
            }

            Assert.IsTrue(_cache.TryRead("ds", "rev1", "a", "high", out _),
                "the first fetch should have seeded the cache");

            Task<TkmsMeshData> second = client.GetMeshDataAsync("ds", "rev1", "a", "high");
            while (!second.IsCompleted) yield return null;
            second.Result.Dispose();

            Assert.AreEqual(requestsAfterFirst, _server.Requests.Count,
                "a cache hit must not add another request to the server");
        }

        [UnityTest]
        public IEnumerator ACorruptCacheEntryIsEvictedAndTheLoadFallsBackToTheNetwork()
        {
            _server.Meshes["a"] = TkmsFixture.Triangle();
            _cache.Write("ds", "rev1", "a", "high", new byte[] { 1, 2, 3, 4 }); // not a valid TKMS payload
            var client = new TerritoryClient(_server.BaseUrl, _cache);

            Task<TkmsMeshData> task = client.GetMeshDataAsync("ds", "rev1", "a", "high");
            while (!task.IsCompleted) yield return null;

            Assert.IsFalse(task.IsFaulted, "a corrupt cache entry must not fail the load: " +
                (task.IsFaulted ? task.Exception?.InnerException?.Message : ""));
            Assert.AreEqual(1, _server.Requests.Count, "the fallback must have gone to the network");
            task.Result.Dispose();

            // Self-healed: the bad bytes are gone, and — once the write-through lands — the good
            // ones from the network fallback are there in their place.
            float deadline = Time.realtimeSinceStartup + 5f;
            bool healed = false;
            while (Time.realtimeSinceStartup < deadline)
            {
                if (_cache.TryRead("ds", "rev1", "a", "high", out byte[] onDisk) &&
                    onDisk.Length == TkmsFixture.Triangle().Length)
                {
                    healed = true;
                    break;
                }

                yield return null;
            }

            Assert.IsTrue(healed, "the corrupt entry should have been replaced by a good one");
        }

        [UnityTest]
        public IEnumerator BatchOnlyFetchesTheIdsTheDiskDidNotAlreadyHave()
        {
            // "a" on the network is a 3-vertex triangle; "a" pre-seeded on disk is a 4-vertex
            // quad. The two are only distinguishable by vertex count, which makes the result a
            // decisive marker of which source actually answered -- no need to inspect the
            // request body to prove the batch skipped "a".
            _server.Meshes["a"] = TkmsFixture.Triangle();
            _server.Meshes["b"] = TkmsFixture.Quad();
            _cache.Write("ds", "rev1", "a", "high", TkmsFixture.Quad());

            var client = new TerritoryClient(_server.BaseUrl, _cache);
            var missing = new List<string>();
            Task<Dictionary<string, TkmsMeshData>> task = client.GetMeshDataBatchAsync(
                "ds", "rev1", new[] { "a", "b" }, "high", missing);
            while (!task.IsCompleted) yield return null;

            if (task.IsFaulted) throw task.Exception.InnerException ?? task.Exception;
            Dictionary<string, TkmsMeshData> result = task.Result;

            Assert.AreEqual(2, result.Count);
            Assert.AreEqual(4, result["a"].VertexCount,
                "'a' must come from the disk-cached quad, not the network triangle");
            Assert.AreEqual(4, result["b"].VertexCount, "'b' only exists on the network, as a quad");
            Assert.AreEqual(0, missing.Count);

            foreach (TkmsMeshData data in result.Values) data.Dispose();

            // The network fetch for "b" should have seeded the cache in turn.
            float deadline = Time.realtimeSinceStartup + 5f;
            while (!_cache.TryRead("ds", "rev1", "b", "high", out _) &&
                   Time.realtimeSinceStartup < deadline)
            {
                yield return null;
            }

            Assert.IsTrue(_cache.TryRead("ds", "rev1", "b", "high", out _),
                "the batch's network half should seed the cache the same way a single fetch does");
        }
    }
}
