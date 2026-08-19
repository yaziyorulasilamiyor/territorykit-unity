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

        // ---- key collisions ---------------------------------------------------------------
        //
        // These are the four ways the previous "replace invalid characters with _" scheme let
        // two distinct keys land on one file. A collision here is not a crash: TKMS carries no
        // territory id inside the payload, so MeshDecoder validates the wrong-but-well-formed
        // mesh and hands it back as if it were the right one. The server side already had to
        // solve the same two filesystem problems in geometry_api.build; these port its
        // tests/test_build.py cases to the client.

        [Test]
        public void TwoIdsThatDifferOnlyByAnInvalidCharacterDoNotShareAFile()
        {
            // 'a:b' sanitised to 'a_b' under the old scheme, which is also what 'a_b' sanitised
            // to -- one file for two territories.
            _cache.Write("ds", "rev1", "a:b", "high", new byte[] { 1 });
            _cache.Write("ds", "rev1", "a_b", "high", new byte[] { 2 });

            _cache.TryRead("ds", "rev1", "a:b", "high", out byte[] colon);
            _cache.TryRead("ds", "rev1", "a_b", "high", out byte[] underscore);

            Assert.AreEqual(new byte[] { 1 }, colon);
            Assert.AreEqual(new byte[] { 2 }, underscore);
        }

        [Test]
        public void TwoIdsThatDifferOnlyInCaseDoNotShareAFile()
        {
            // Windows and macOS filesystems are case-insensitive, so an id used verbatim as a
            // filename makes 'TR-34' and 'tr-34' the same entry. This is the client-side twin of
            // test_build.py::...case-insensitive collision test.
            _cache.Write("ds", "rev1", "TR-34", "high", new byte[] { 1 });
            _cache.Write("ds", "rev1", "tr-34", "high", new byte[] { 2 });

            _cache.TryRead("ds", "rev1", "TR-34", "high", out byte[] upper);
            _cache.TryRead("ds", "rev1", "tr-34", "high", out byte[] lower);

            Assert.AreEqual(new byte[] { 1 }, upper);
            Assert.AreEqual(new byte[] { 2 }, lower, "case alone must distinguish two entries");

            string[] everything = Directory.GetFiles(_root, "*", SearchOption.AllDirectories);
            Assert.AreEqual(2, everything.Length, "two ids must produce two files, not one");
        }

        [TestCase("CON")]
        [TestCase("prn")]
        [TestCase("NUL")]
        [TestCase("COM1")]
        [TestCase("lpt9")]
        [TestCase("AUX")]
        public void WindowsReservedDeviceNamesAreStillCacheable(string reserved)
        {
            // Windows refuses these names whatever the extension, so an id used verbatim could
            // never be cached at all -- every write would throw and every read would miss.
            Assert.DoesNotThrow(() => _cache.Write("ds", "rev1", reserved, "high", new byte[] { 7 }));

            Assert.IsTrue(_cache.TryRead("ds", "rev1", reserved, "high", out byte[] read));
            Assert.AreEqual(new byte[] { 7 }, read);
        }

        [TestCase(".")]
        [TestCase("..")]
        public void RelativePathComponentsDoNotEscapeOrCollapse(string component)
        {
            // '.' and '..' contain no invalid characters, so an invalid-character filter passes
            // them through untouched and the resulting path walks out of the cache root.
            _cache.Write(component, "rev1", component, "high", new byte[] { 3 });

            string[] everything = Directory.GetFiles(_root, "*", SearchOption.AllDirectories);
            Assert.AreEqual(1, everything.Length);
            StringAssert.StartsWith(_root, Path.GetFullPath(everything[0]));
            Assert.IsTrue(_cache.TryRead(component, "rev1", component, "high", out _));
        }

        [Test]
        public void ANullIdAndAnEmptyIdAreDifferentKeys()
        {
            _cache.Write("ds", "rev1", null, "high", new byte[] { 1 });
            _cache.Write("ds", "rev1", string.Empty, "high", new byte[] { 2 });

            _cache.TryRead("ds", "rev1", null, "high", out byte[] fromNull);
            _cache.TryRead("ds", "rev1", string.Empty, "high", out byte[] fromEmpty);

            Assert.AreEqual(new byte[] { 1 }, fromNull);
            Assert.AreEqual(new byte[] { 2 }, fromEmpty);
        }

        [Test]
        public void AFullKeyStaysWellUnderTheWindowsPathLimit()
        {
            // Regression gate, not a style preference: the first hashed version of this class
            // used full 64-character SHA-256 digests, which put a cache path around 360
            // characters and made every single write throw DirectoryNotFoundException against
            // Windows' 260-char MAX_PATH. Measured from a realistic persistentDataPath-length
            // root rather than the short temp path these tests otherwise use.
            // A stand-in the length of a real one -- "C:\Users\dalki\AppData\LocalLow\
            // DefaultCompany\TerritoryKitDev\TerritoryKitCache" is 79 characters -- built inside
            // the temp directory so the test needs no permissions outside it.
            const int realisticRootLength = 79;
            string padded = _root;
            while (padded.Length < realisticRootLength)
            {
                padded += "x";
            }

            var cache = new MeshDiskCache(padded);
            cache.Write("tr-adm1", "3eaa661b1cb8ccbd269d57465f218d63025fe126a12db190c5753ab4e809333d",
                "probe-id", "high", new byte[0]);

            // Write() would already have thrown if the path were unusable; assert the headroom
            // explicitly so a future digest-length change is caught here rather than in the field.
            string[] written = Directory.GetFiles(padded, "*", SearchOption.AllDirectories);
            Assert.AreEqual(1, written.Length);
            Assert.Less(written[0].Length, 260,
                "a cache path must stay inside Windows' MAX_PATH: " + written[0]);

            Directory.Delete(padded, recursive: true);
        }
    }
}
