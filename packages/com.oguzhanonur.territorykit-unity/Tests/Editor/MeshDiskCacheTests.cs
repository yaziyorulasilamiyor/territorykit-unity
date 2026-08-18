using System;
using System.IO;
using System.Text;
using NUnit.Framework;
using TerritoryKit.Unity;

namespace TerritoryKit.Unity.Tests
{
    /// <summary>
    /// Pure disk-cache mechanics: keying, round-tripping, eviction, and that a write never
    /// leaves a reader able to see a partial file. No network, no Unity player required.
    /// </summary>
    public class MeshDiskCacheTests
    {
        private string _root;
        private MeshDiskCache _cache;

        [SetUp]
        public void SetUp()
        {
            _root = Path.Combine(Path.GetTempPath(), "tkcache-test-" + Guid.NewGuid().ToString("N"));
            _cache = new MeshDiskCache(_root);
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(_root))
            {
                Directory.Delete(_root, recursive: true);
            }
        }

        [Test]
        public void AMissingKeyIsAMissNotAnException()
        {
            bool hit = _cache.TryRead("ds", "rev1", "34", "high", out byte[] bytes);
            Assert.IsFalse(hit);
            Assert.IsNull(bytes);
        }

        [Test]
        public void WrittenBytesRoundTripExactly()
        {
            byte[] original = Encoding.UTF8.GetBytes("not really TKMS, just some bytes");
            _cache.Write("ds", "rev1", "34", "high", original);

            bool hit = _cache.TryRead("ds", "rev1", "34", "high", out byte[] read);

            Assert.IsTrue(hit);
            Assert.AreEqual(original, read);
        }

        [Test]
        public void EachKeyComponentIsPartOfTheKey()
        {
            // revisionId+territoryId+lod, as decided in phase 4's report -- a change in any one
            // component must be a different entry, not an overwrite or an accidental collision.
            _cache.Write("ds", "rev1", "34", "high", new byte[] { 1 });
            _cache.Write("ds", "rev2", "34", "high", new byte[] { 2 });
            _cache.Write("ds", "rev1", "06", "high", new byte[] { 3 });
            _cache.Write("ds", "rev1", "34", "medium", new byte[] { 4 });

            _cache.TryRead("ds", "rev1", "34", "high", out byte[] a);
            _cache.TryRead("ds", "rev2", "34", "high", out byte[] b);
            _cache.TryRead("ds", "rev1", "06", "high", out byte[] c);
            _cache.TryRead("ds", "rev1", "34", "medium", out byte[] d);

            Assert.AreEqual(new byte[] { 1 }, a);
            Assert.AreEqual(new byte[] { 2 }, b);
            Assert.AreEqual(new byte[] { 3 }, c);
            Assert.AreEqual(new byte[] { 4 }, d);
        }

        [Test]
        public void RewritingTheSameKeyReplacesIt()
        {
            _cache.Write("ds", "rev1", "34", "high", new byte[] { 1, 2, 3 });
            _cache.Write("ds", "rev1", "34", "high", new byte[] { 9, 9 });

            _cache.TryRead("ds", "rev1", "34", "high", out byte[] read);

            Assert.AreEqual(new byte[] { 9, 9 }, read);
        }

        [Test]
        public void WriteLeavesNoTempFileBehind()
        {
            _cache.Write("ds", "rev1", "34", "high", new byte[] { 1 });

            string[] everything = Directory.GetFiles(_root, "*", SearchOption.AllDirectories);
            Assert.AreEqual(1, everything.Length, "a write must leave exactly the final file, no .tmp leftovers");
            StringAssert.EndsWith(".tkms", everything[0]);
        }

        [Test]
        public void EvictRemovesTheEntryAndALaterReadMisses()
        {
            _cache.Write("ds", "rev1", "34", "high", new byte[] { 1 });
            Assert.IsTrue(_cache.TryRead("ds", "rev1", "34", "high", out _));

            _cache.Evict("ds", "rev1", "34", "high");

            Assert.IsFalse(_cache.TryRead("ds", "rev1", "34", "high", out _));
        }

        [Test]
        public void EvictingAMissingEntryDoesNotThrow()
        {
            Assert.DoesNotThrow(() => _cache.Evict("ds", "rev1", "nonexistent", "high"));
        }

        [Test]
        public void HostileTerritoryIdsCannotEscapeTheCacheRoot()
        {
            _cache.Write("ds", "rev1", "../../escape", "high", new byte[] { 1 });

            string[] everything = Directory.GetFiles(_root, "*", SearchOption.AllDirectories);
            Assert.AreEqual(1, everything.Length);
            StringAssert.StartsWith(_root, Path.GetFullPath(everything[0]),
                "a hostile id must sanitise into a file still inside the cache root");
        }
    }
}
