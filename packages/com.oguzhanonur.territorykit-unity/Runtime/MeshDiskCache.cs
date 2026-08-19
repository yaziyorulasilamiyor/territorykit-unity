using System;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using UnityEngine;

namespace TerritoryKit.Unity
{
    /// <summary>
    /// A disk cache for raw TKMS bytes, keyed by dataset, revision, territory and level.
    /// </summary>
    /// <remarks>
    /// <b>No ETag, no 304, no TTL.</b> Mesh URLs are pinned to an immutable revision
    /// (<c>docs/api.md</c>) — a hit for a given key is valid forever, so there is no "is this
    /// still current" question for this class to ask. This is the disk cache phase 4's report
    /// named as the carried decision for phase 5; <see cref="TerritoryClient"/> stays cache-free
    /// by default and only consults one of these when a caller explicitly hands it one.
    /// <para>
    /// <b>Integrity is the reader's job, not this class's.</b> A cached file is exactly the bytes
    /// a server response would contain, so the same <c>MeshDecoder.Decode</c> validation that
    /// rejects a bad network payload rejects a corrupt cache entry the same way — no separate
    /// checksum is kept. What this class guarantees on its own is narrower: a reader can never
    /// observe a <em>partially written</em> file. <see cref="Write"/> writes to a uniquely named
    /// temp file first and only makes the entry visible with one rename, so a process killed
    /// mid-write leaves an orphaned temp file next to the cache, never a torn one at the real
    /// path.
    /// </para>
    /// </remarks>
    public sealed class MeshDiskCache
    {
        private readonly string _rootDirectory;

        /// <param name="rootDirectory">
        /// Cache root. Defaults to <c>Application.persistentDataPath/TerritoryKitCache</c> when
        /// null. Taking this as a parameter, rather than always reading the Unity API, is what
        /// lets a test point the cache at a throwaway temp directory instead of real user data.
        /// </param>
        public MeshDiskCache(string rootDirectory = null)
        {
            _rootDirectory = string.IsNullOrEmpty(rootDirectory)
                ? Path.Combine(Application.persistentDataPath, "TerritoryKitCache")
                : rootDirectory;
        }

        public string RootDirectory => _rootDirectory;

        /// <summary>Reads a cached entry. False on a miss; never throws for a file that is not there.</summary>
        public bool TryRead(string datasetId, string revisionId, string territoryId, string lod,
            out byte[] bytes)
        {
            string path = PathFor(datasetId, revisionId, territoryId, lod);
            try
            {
                if (!File.Exists(path))
                {
                    bytes = null;
                    return false;
                }

                bytes = File.ReadAllBytes(path);
                return true;
            }
            catch (IOException)
            {
                // Another writer mid-rename, or the file vanished between Exists and Read. A
                // cache is allowed to miss; it is not allowed to fail the caller for this.
                bytes = null;
                return false;
            }
        }

        /// <summary>
        /// Writes an entry atomically: a uniquely named temp file, then a rename onto the final
        /// path. A reader can only ever see the old state (a miss, or a previous write) or the
        /// complete new file — never a partial one, and two writers racing for the same key
        /// cannot corrupt each other because each writes its own temp file first.
        /// </summary>
        public void Write(string datasetId, string revisionId, string territoryId, string lod,
            byte[] bytes)
        {
            if (bytes == null) throw new ArgumentNullException(nameof(bytes));

            string path = PathFor(datasetId, revisionId, territoryId, lod);
            Directory.CreateDirectory(Path.GetDirectoryName(path) ?? _rootDirectory);

            // Eight hex characters, not a full GUID: this only has to be unique among writers
            // racing for one key at one instant, and every character here counts against the
            // 260-char MAX_PATH the digest length above is already budgeted against.
            string tempPath = path + "." + Guid.NewGuid().ToString("N").Substring(0, 8) + ".tmp";
            File.WriteAllBytes(tempPath, bytes);
            try
            {
                if (File.Exists(path))
                {
                    // File.Replace is the atomic step on the common case (re-writing a key that
                    // is already cached, e.g. a second writer losing the race harmlessly).
                    File.Replace(tempPath, path, null);
                }
                else
                {
                    File.Move(tempPath, path);
                }
            }
            finally
            {
                if (File.Exists(tempPath))
                {
                    File.Delete(tempPath);
                }
            }
        }

        /// <summary>Deletes a cache entry, e.g. after it failed to decode. Best-effort.</summary>
        public void Evict(string datasetId, string revisionId, string territoryId, string lod)
        {
            string path = PathFor(datasetId, revisionId, territoryId, lod);
            try
            {
                if (File.Exists(path))
                {
                    File.Delete(path);
                }
            }
            catch (IOException)
            {
                // A stubborn file becomes a future miss followed by a future overwrite attempt,
                // not a crash — eviction is a hint, not a promise.
            }
        }

        private string PathFor(string datasetId, string revisionId, string territoryId, string lod)
        {
            return Path.Combine(_rootDirectory, Encode(datasetId), Encode(revisionId),
                Encode(lod), Encode(territoryId) + ".tkms");
        }

        /// <summary>
        /// Encodes one key component as the lowercase hex SHA-256 of its UTF-8 bytes.
        /// </summary>
        /// <remarks>
        /// Hashing rather than sanitising, because every character-substitution scheme collides
        /// and a collision here is not a crash — it is one territory's mesh served under another
        /// territory's name. TKMS carries no territory id inside the payload, so
        /// <c>MeshDecoder</c> would validate the wrong-but-well-formed mesh and hand it back
        /// happily. The substitution this replaced had four separate ways to reach that:
        /// <list type="bullet">
        /// <item>replacing each invalid character with <c>_</c> maps <c>a:b</c> and <c>a_b</c> to
        /// one file;</item>
        /// <item>Windows and macOS filesystems are case-insensitive, so <c>A</c> and <c>a</c> are
        /// one file — the same collision <c>geometry_api.build</c> already had to solve on the
        /// server side (<c>tests/test_build.py</c>);</item>
        /// <item>Windows reserved device names (<c>CON</c>, <c>NUL</c>, <c>COM1</c>, <c>AUX</c>,
        /// <c>PRN</c>, <c>LPT9</c>…) are refused whatever the extension, so those ids could never
        /// be cached at all;</item>
        /// <item>a component that is exactly <c>.</c> or <c>..</c> survives an
        /// invalid-character filter untouched and walks up out of the cache root.</item>
        /// </list>
        /// A lowercase hex digest has none of those properties: it is case-stable, never a
        /// reserved name, never a relative path element, and distinct for distinct inputs. The
        /// cost is that the cache directory is no longer human-readable, which is the right trade
        /// for a directory nobody is meant to read by hand.
        /// <para>
        /// <b>Truncated to <see cref="DigestBytes"/> bytes, and why.</b> Four full 64-character
        /// digests plus a temp-file suffix produce paths around 360 characters, over the 260-char
        /// <c>MAX_PATH</c> that Windows still enforces for this API by default — the first
        /// version of this method was written with full digests and every cache write failed with
        /// <c>DirectoryNotFoundException</c>. 96 bits puts the birthday bound near 2^48 distinct
        /// ids <em>within a single parent directory</em>, which no dataset approaches, and keeps
        /// a full key under 200 characters including the cache root.
        /// </para>
        /// Base64url would be shorter still and is deliberately not used: it is case-sensitive,
        /// so on a case-insensitive filesystem two digests differing only in case would collide —
        /// reintroducing the exact bug this method exists to remove.
        /// </remarks>
        private static string Encode(string value)
        {
            // Distinct from Encode("") — a null and an empty id are different keys, and neither
            // should be able to take the other's entry.
            byte[] bytes = value == null
                ? new byte[] { 0xFF }
                : Encoding.UTF8.GetBytes(value);

            using (var sha = SHA256.Create())
            {
                byte[] digest = sha.ComputeHash(bytes);
                var builder = new StringBuilder(DigestBytes * 2);
                for (int i = 0; i < DigestBytes; i++)
                {
                    builder.Append(digest[i].ToString("x2", CultureInfo.InvariantCulture));
                }

                return builder.ToString();
            }
        }

        /// <summary>Bytes of SHA-256 kept per key component — 96 bits, 24 hex characters.</summary>
        private const int DigestBytes = 12;
    }
}
