using System;
using System.IO;
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

            string tempPath = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
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
            return Path.Combine(_rootDirectory, Sanitize(datasetId), Sanitize(revisionId),
                Sanitize(lod), Sanitize(territoryId) + ".tkms");
        }

        /// <summary>
        /// Ids come from the server, not free-form user input, but sanitising costs nothing and
        /// turns a hypothetical hostile id into an odd-looking cache miss instead of a path
        /// escape.
        /// </summary>
        private static string Sanitize(string value)
        {
            if (string.IsNullOrEmpty(value))
            {
                return "_";
            }

            char[] invalid = Path.GetInvalidFileNameChars();
            var builder = new StringBuilder(value.Length);
            foreach (char c in value)
            {
                builder.Append(Array.IndexOf(invalid, c) >= 0 ? '_' : c);
            }

            return builder.ToString();
        }
    }
}
