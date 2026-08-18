using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Unity.Collections;
using UnityEngine;
using UnityEngine.Networking;

namespace TerritoryKit.Unity
{
    /// <summary>
    /// Talks to the geometry API: dataset metadata, territory listings, and TKMS meshes.
    /// </summary>
    /// <remarks>
    /// <b>Threading.</b> Every public method must be called from the main thread and completes
    /// on it. Between those two points the work moves off: the download bytes are taken from
    /// <c>DownloadHandler.nativeData</c> with a single copy and no managed allocation, the
    /// request is disposed immediately, and decoding runs on a worker thread. Only the buffer
    /// uploads come back to the main thread.
    /// <para>
    /// <b>Caching.</b> There is none, on purpose. UnityWebRequest keeps no HTTP cache and does
    /// not act on ETag or 304 — its C# reference source has no cache at all, and the only cache
    /// Unity ships (the <c>Caching</c> class) is for AssetBundles. Every call here goes to the
    /// network. Because mesh URLs are pinned to an immutable revision, the right answer is a
    /// disk cache keyed by revision rather than conditional requests; that is Phase 5.
    /// </para>
    /// <para>
    /// <b>Compression.</b> <c>Accept-Encoding</c> is never set here. Unity sets it to the
    /// encodings it supports and decompresses transparently, and the documentation is explicit
    /// that a custom value is ignored or breaks the request. The server's prebuilt <c>.tkms.gz</c>
    /// variant is therefore picked up for free.
    /// </para>
    /// </remarks>
    public sealed class TerritoryClient
    {
        private readonly string _baseUrl;

        /// <param name="baseUrl">Root of the API, for example <c>http://localhost:8000</c>.</param>
        public TerritoryClient(string baseUrl)
        {
            if (string.IsNullOrWhiteSpace(baseUrl))
            {
                throw new ArgumentException("baseUrl is required", nameof(baseUrl));
            }

            _baseUrl = baseUrl.TrimEnd('/');
        }

        /// <summary>Seconds before a request is abandoned. Zero means no timeout.</summary>
        public int TimeoutSeconds { get; set; } = 30;

        /// <summary>
        /// Fetches dataset metadata: origin, local bounds, revision id and the per-level
        /// <c>lossy</c>/<c>topologyChanged</c>/<c>pickingUnsafe</c> flags.
        /// </summary>
        /// <remarks>
        /// This is the call to make before downloading anything. It is where the revision id
        /// that every mesh URL is built from comes from, and where a client learns whether a
        /// level can be trusted for picking — see <see cref="LodPolicy"/>.
        /// </remarks>
        public async Task<DatasetInfo> GetDatasetAsync(string datasetId,
            CancellationToken cancellationToken = default)
        {
            string url = _baseUrl + "/v1/datasets/" + Uri.EscapeDataString(datasetId);
            return await GetJsonAsync<DatasetInfo>(url, cancellationToken).ConfigureAwait(true);
        }

        /// <summary>Fetches one page of the territory listing for a level.</summary>
        public async Task<TerritoryPage> GetTerritoriesAsync(string datasetId, string lod,
            string cursor = null, int limit = 0, CancellationToken cancellationToken = default)
        {
            var url = new StringBuilder(_baseUrl)
                .Append("/v1/datasets/").Append(Uri.EscapeDataString(datasetId))
                .Append("/territories?lod=").Append(Uri.EscapeDataString(lod));
            if (limit > 0)
            {
                url.Append("&limit=").Append(limit.ToString(CultureInfo.InvariantCulture));
            }

            if (!string.IsNullOrEmpty(cursor))
            {
                url.Append("&cursor=").Append(Uri.EscapeDataString(cursor));
            }

            return await GetJsonAsync<TerritoryPage>(url.ToString(), cancellationToken)
                .ConfigureAwait(true);
        }

        /// <summary>
        /// Walks every page of the territory listing and returns the lot.
        /// </summary>
        /// <remarks>
        /// Phase 4 draws the whole dataset, so it wants every territory. Phase 5 will ask
        /// <c>/viewport</c> for the ids that are actually on screen instead.
        /// </remarks>
        public async Task<List<TerritorySummary>> GetAllTerritoriesAsync(string datasetId,
            string lod, CancellationToken cancellationToken = default)
        {
            var all = new List<TerritorySummary>();
            string cursor = null;
            do
            {
                TerritoryPage page = await GetTerritoriesAsync(datasetId, lod, cursor, 0,
                    cancellationToken).ConfigureAwait(true);
                if (page.items != null)
                {
                    all.AddRange(page.items);
                }

                cursor = page.nextCursor;
            }
            while (!string.IsNullOrEmpty(cursor));

            return all;
        }

        /// <summary>Downloads and decodes one TKMS mesh.</summary>
        /// <remarks>The returned mesh is the caller's to destroy.</remarks>
        public async Task<Mesh> GetMeshAsync(string datasetId, string revisionId,
            string territoryId, string lod, CancellationToken cancellationToken = default)
        {
            string url = _baseUrl
                + "/v1/datasets/" + Uri.EscapeDataString(datasetId)
                + "/revisions/" + Uri.EscapeDataString(revisionId)
                + "/mesh/" + Uri.EscapeDataString(territoryId)
                + "?lod=" + Uri.EscapeDataString(lod);

            NativeArray<byte> payload = await GetBytesAsync(url, cancellationToken)
                .ConfigureAwait(true);
            try
            {
                TkmsMeshData data = await Task.Run(
                    () => MeshDecoder.Decode(payload), cancellationToken).ConfigureAwait(true);
                try
                {
                    return MeshDecoder.CreateMesh(data, territoryId);
                }
                finally
                {
                    data.Dispose();
                }
            }
            finally
            {
                payload.Dispose();
            }
        }

        /// <summary>
        /// Downloads several meshes in one request using the TKMB batch container.
        /// </summary>
        /// <remarks>
        /// One request for 81 provinces rather than 81 requests. Ids that the server could not
        /// find are reported in <paramref name="missing"/> rather than thrown, because a batch
        /// URL is always valid and may legitimately return nothing.
        /// </remarks>
        public async Task<Dictionary<string, Mesh>> GetMeshBatchAsync(string datasetId,
            string revisionId, IReadOnlyList<string> territoryIds, string lod,
            List<string> missing = null, CancellationToken cancellationToken = default)
        {
            if (territoryIds == null) throw new ArgumentNullException(nameof(territoryIds));
            if (territoryIds.Count == 0)
            {
                return new Dictionary<string, Mesh>();
            }

            string url = _baseUrl
                + "/v1/datasets/" + Uri.EscapeDataString(datasetId)
                + "/revisions/" + Uri.EscapeDataString(revisionId)
                + "/mesh/batch";

            var body = new StringBuilder("{\"territoryIds\":[");
            for (int i = 0; i < territoryIds.Count; i++)
            {
                if (i > 0)
                {
                    body.Append(',');
                }

                body.Append('"').Append(EscapeJsonString(territoryIds[i])).Append('"');
            }

            // entryEncoding is not derived from Accept-Encoding; it is an explicit field, and
            // identity is what this reader implements.
            body.Append("],\"lod\":\"").Append(EscapeJsonString(lod))
                .Append("\",\"entryEncoding\":\"identity\"}");

            NativeArray<byte> payload = await PostForBytesAsync(url, body.ToString(),
                cancellationToken).ConfigureAwait(true);
            try
            {
                var decoded = await Task.Run(() => DecodeBatch(payload, missing != null),
                    cancellationToken).ConfigureAwait(true);

                if (missing != null)
                {
                    missing.Clear();
                    missing.AddRange(decoded.Missing);
                }

                var meshes = new Dictionary<string, Mesh>(decoded.Meshes.Count);
                try
                {
                    foreach (KeyValuePair<string, TkmsMeshData> pair in decoded.Meshes)
                    {
                        meshes[pair.Key] = MeshDecoder.CreateMesh(pair.Value, pair.Key);
                    }
                }
                catch
                {
                    foreach (Mesh mesh in meshes.Values)
                    {
                        UnityEngine.Object.DestroyImmediate(mesh);
                    }

                    throw;
                }
                finally
                {
                    foreach (TkmsMeshData data in decoded.Meshes.Values)
                    {
                        data.Dispose();
                    }
                }

                return meshes;
            }
            finally
            {
                payload.Dispose();
            }
        }

        private sealed class DecodedBatch
        {
            public Dictionary<string, TkmsMeshData> Meshes;
            public List<string> Missing;
        }

        /// <summary>Runs on a worker thread: container parse plus one decode per entry.</summary>
        private static DecodedBatch DecodeBatch(NativeArray<byte> payload, bool wantMissing)
        {
            TkmbContainer.Toc toc = TkmbContainer.ReadToc(payload);
            var meshes = new Dictionary<string, TkmsMeshData>(toc.Entries.Count);
            try
            {
                foreach (TkmbEntry entry in toc.Entries)
                {
                    NativeArray<byte> slice = payload.GetSubArray(entry.Offset, entry.Length);
                    meshes[entry.TerritoryId] = MeshDecoder.Decode(slice);
                }
            }
            catch
            {
                foreach (TkmsMeshData data in meshes.Values)
                {
                    data.Dispose();
                }

                throw;
            }

            return new DecodedBatch
            {
                Meshes = meshes,
                Missing = wantMissing ? toc.Missing : new List<string>()
            };
        }

        private async Task<T> GetJsonAsync<T>(string url, CancellationToken cancellationToken)
        {
            using (UnityWebRequest request = UnityWebRequest.Get(url))
            {
                await SendAsync(request, url, cancellationToken).ConfigureAwait(true);
                string text = request.downloadHandler.text;
                try
                {
                    return JsonUtility.FromJson<T>(text);
                }
                catch (Exception exception)
                {
                    throw new TerritoryApiException(
                        "could not parse the response of " + url + " as " + typeof(T).Name +
                        ": " + exception.Message, request.responseCode);
                }
            }
        }

        private async Task<NativeArray<byte>> GetBytesAsync(string url,
            CancellationToken cancellationToken)
        {
            using (UnityWebRequest request = UnityWebRequest.Get(url))
            {
                await SendAsync(request, url, cancellationToken).ConfigureAwait(true);
                return CopyOut(request);
            }
        }

        private async Task<NativeArray<byte>> PostForBytesAsync(string url, string json,
            CancellationToken cancellationToken)
        {
            using (var request = new UnityWebRequest(url, UnityWebRequest.kHttpVerbPOST))
            {
                request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(json));
                request.downloadHandler = new DownloadHandlerBuffer();
                request.SetRequestHeader("Content-Type", "application/json");
                await SendAsync(request, url, cancellationToken).ConfigureAwait(true);
                return CopyOut(request);
            }
        }

        /// <summary>
        /// Takes the downloaded bytes out of the request into memory the caller owns.
        /// </summary>
        /// <remarks>
        /// <c>nativeData</c> gives allocation-free access to the handler's own buffer, unlike
        /// <c>data</c>, which returns a fresh managed copy on every read. That buffer dies with
        /// the request, so this copies once into a persistent array and lets the request go —
        /// which is what allows decoding to continue on a worker thread.
        /// </remarks>
        private static NativeArray<byte> CopyOut(UnityWebRequest request)
        {
            NativeArray<byte>.ReadOnly source = request.downloadHandler.nativeData;
            var copy = new NativeArray<byte>(source.Length, Allocator.Persistent,
                NativeArrayOptions.UninitializedMemory);
            try
            {
                source.CopyTo(copy);
            }
            catch
            {
                copy.Dispose();
                throw;
            }

            return copy;
        }

        private async Task SendAsync(UnityWebRequest request, string url,
            CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            request.timeout = Mathf.Max(0, TimeoutSeconds);

            var completion = new TaskCompletionSource<bool>();
            UnityWebRequestAsyncOperation operation = request.SendWebRequest();

            // Abort is what makes cancellation take effect before the response arrives; without
            // it a cancelled request would still run to completion. Cancel from the main thread.
            CancellationTokenRegistration registration = cancellationToken.CanBeCanceled
                ? cancellationToken.Register(() =>
                {
                    if (!request.isDone)
                    {
                        request.Abort();
                    }
                })
                : default;

            operation.completed += _ => completion.TrySetResult(true);
            try
            {
                await completion.Task.ConfigureAwait(true);
            }
            finally
            {
                registration.Dispose();
            }

            cancellationToken.ThrowIfCancellationRequested();

            if (request.result != UnityWebRequest.Result.Success)
            {
                throw BuildError(request, url);
            }
        }

        private static TerritoryApiException BuildError(UnityWebRequest request, string url)
        {
            string errorCode = null;
            string detail = request.error;

            if (request.result == UnityWebRequest.Result.ProtocolError)
            {
                // The API answers every error with one JSON shape, so a code is usually there.
                try
                {
                    var body = JsonUtility.FromJson<ApiErrorBody>(request.downloadHandler.text);
                    if (body != null && body.error != null && !string.IsNullOrEmpty(body.error.code))
                    {
                        errorCode = body.error.code;
                        detail = body.error.code + ": " + body.error.message;
                    }
                }
                catch (Exception)
                {
                    // A non-JSON error body is still an error; fall back to UnityWebRequest.error.
                }
            }

            return new TerritoryApiException(
                request.method + " " + url + " failed (" + request.responseCode + "): " + detail,
                request.responseCode, errorCode);
        }

        private static string EscapeJsonString(string value)
        {
            if (string.IsNullOrEmpty(value))
            {
                return string.Empty;
            }

            var builder = new StringBuilder(value.Length);
            foreach (char c in value)
            {
                switch (c)
                {
                    case '"': builder.Append("\\\""); break;
                    case '\\': builder.Append("\\\\"); break;
                    case '\n': builder.Append("\\n"); break;
                    case '\r': builder.Append("\\r"); break;
                    case '\t': builder.Append("\\t"); break;
                    default:
                        if (c < 0x20)
                        {
                            builder.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                        }
                        else
                        {
                            builder.Append(c);
                        }

                        break;
                }
            }

            return builder.ToString();
        }
    }
}
