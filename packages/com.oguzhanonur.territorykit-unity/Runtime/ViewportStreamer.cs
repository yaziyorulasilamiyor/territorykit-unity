using System;
using System.Collections.Generic;
using System.Globalization;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

namespace TerritoryKit.Unity
{
    /// <summary>
    /// Streams territories by camera viewport: pools GameObjects and meshes instead of drawing
    /// the whole dataset once, picks a detail level with hysteresis, and keeps decoded mesh data
    /// around for <see cref="TerritoryPicker"/>.
    /// </summary>
    /// <remarks>
    /// <b>Ticking, not per-frame polling.</b> The camera's ground bbox is only recomputed and
    /// compared every <see cref="TickIntervalSeconds"/>; a tick is skipped entirely when the
    /// camera's current view is still fully inside the padded box the last request already
    /// covered, so a request only goes out when the camera has moved far enough to matter.
    /// <para>
    /// <b>Concurrency.</b> At most one <c>/viewport</c> + mesh-batch fetch is ever in flight. A
    /// tick that decides to fetch cancels whatever fetch is still running and starts a new one
    /// from the camera's current position. Cancellation alone does not stop dispatched
    /// <c>Task.Run</c> work, so each tick also carries a generation number and refuses to write
    /// anything once a newer tick has started — see <c>_tickGeneration</c>. The commit itself is
    /// transactional: a tick prepares its new slots in full before handing any of them to
    /// <c>_visible</c>, and releases everything it took if preparation fails.
    /// </para>
    /// <para>
    /// <b>A LOD change invalidates everything currently visible</b> — a mesh loaded at one level
    /// is the wrong geometry for another, so switching levels re-fetches every visible territory
    /// rather than trying to patch individual ones.
    /// </para>
    /// </remarks>
    [AddComponentMenu("TerritoryKit/Viewport Streamer")]
    public sealed class ViewportStreamer : MonoBehaviour
    {
        [Header("Server")]
        [SerializeField]
        private string baseUrl = "http://127.0.0.1:8000";

        [SerializeField]
        private string datasetId = "tr-adm1";

        [Header("Presentation")]
        [SerializeField]
        private Camera targetCamera;

        [Tooltip("Root that carries the placement rotation. Created automatically if left empty.")]
        [SerializeField]
        private Transform mapRoot;

        [SerializeField]
        private int colourSeed = 20260818;

        [Tooltip("GameObjects/Meshes preallocated before the first tick.")]
        [SerializeField]
        private int warmPoolSize = 48;

        [Header("Viewport")]
        [Tooltip("Extra fraction of the visible box fetched on every side, so a small pan does not immediately need another request.")]
        [SerializeField, Range(0f, 1f)]
        private float viewportMarginRatio = 0.15f;

        [SerializeField]
        private float tickIntervalSeconds = 0.2f;

        [Header("LOD hysteresis (orthographic size; tune per scene scale)")]
        [Tooltip("These four numbers are a starting point, not a measured constant -- what counts as \"zoomed out\" depends on the scene's scale.")]
        [SerializeField]
        private float highToMediumCoarsenAt = 60000f;

        [SerializeField]
        private float highToMediumRefineAt = 45000f;

        [SerializeField]
        private float mediumToLowCoarsenAt = 180000f;

        [SerializeField]
        private float mediumToLowRefineAt = 140000f;

        // A readonly struct: Dictionary<string, VisibleTerritory> then stores it inline in the
        // dictionary's own entry array instead of as a separate heap object per visible
        // territory -- panning repeatedly adds and removes entries, so this is on the same
        // steady-state path TerritoryPool avoids allocating on.
        private readonly struct VisibleTerritory
        {
            public VisibleTerritory(PooledTerritory slot, TkmsMeshData data)
            {
                Slot = slot;
                Data = data;
            }

            public PooledTerritory Slot { get; }

            public TkmsMeshData Data { get; }
        }

        private readonly Dictionary<string, VisibleTerritory> _visible = new Dictionary<string, VisibleTerritory>();
        private readonly Dictionary<string, Color> _colourOverrides = new Dictionary<string, Color>();

        // Not a field initializer: MaterialPropertyBlock's constructor is a Unity engine call
        // and Unity forbids those in a MonoBehaviour's implicit constructor / field
        // initializers, only allowing them from Awake onward.
        private MaterialPropertyBlock _scratchBlock;

        private TerritoryClient _client;
        private TerritoryPool _pool;
        private Material _material;
        private Transform _mapRootTransform;
        private CancellationTokenSource _lifetime;
        private CancellationTokenSource _inFlightCts;
        private float _nextTickTime;
        private LocalBounds? _lastRequestedBounds;
        private string _lastRequestedLod;

        /// <summary>
        /// Incremented every time a tick starts a fetch. A running tick carries the value it was
        /// started with and refuses to touch any shared state once this has moved past it.
        /// </summary>
        /// <remarks>
        /// The cancellation token alone is not enough. <c>Task.Run</c> work already dispatched
        /// does not unwind when its token is cancelled, so a superseded tick keeps running to
        /// completion and arrives at its commit block after a newer tick has already committed —
        /// at which point it would release territories the newer tick just made visible,
        /// overwrite newer entries under the same id (leaking the newer slot and mesh data), and
        /// fire <see cref="TickObserver"/> for a result nobody asked for. Checking the generation
        /// as well closes that: cancellation is a request to stop, the generation is proof of
        /// who is allowed to write.
        /// </remarks>
        private int _tickGeneration;

        private LodHysteresis.Threshold[] _thresholds;
        private float _cachedHighToMediumCoarsenAt;
        private float _cachedHighToMediumRefineAt;
        private float _cachedMediumToLowCoarsenAt;
        private float _cachedMediumToLowRefineAt;

        /// <summary>
        /// Matches the server's <c>batch_max_territories</c> (<c>geometry_api/config.py</c>),
        /// which answers <c>400 batch_too_large</c> above it. A viewport holding more than this
        /// many territories — a district- or neighbourhood-level dataset zoomed out — would
        /// otherwise fail the whole tick on an HTTP error long before memory became the limit.
        /// The server deduplicates with <c>sorted(set(...))</c> before checking, so chunking by
        /// distinct id count is the right unit.
        /// </summary>
        private const int MaxBatchTerritories = 200;

        public string BaseUrl { get => baseUrl; set => baseUrl = value; }

        public string DatasetId { get => datasetId; set => datasetId = value; }

        public Camera TargetCamera { get => targetCamera; set => targetCamera = value; }

        public float TickIntervalSeconds { get => tickIntervalSeconds; set => tickIntervalSeconds = value; }

        public float ViewportMarginRatio { get => viewportMarginRatio; set => viewportMarginRatio = value; }

        /// <summary>
        /// Disk cache used for mesh fetches. Set before <c>Start</c> runs — assigning it later
        /// has no effect on an already-constructed <see cref="TerritoryClient"/>.
        /// </summary>
        public MeshDiskCache DiskCache { get; set; }

        /// <summary>Metadata of the loaded dataset, or null before the first fetch completes.</summary>
        public DatasetInfo Dataset { get; private set; }

        /// <summary>The level everything currently visible was loaded at.</summary>
        public string CurrentLod { get; private set; } = LodHysteresis.LevelsFinestFirst[0];

        /// <summary>Number of territories currently checked out of the pool and drawn.</summary>
        public int VisibleCount => _visible.Count;

        /// <summary>Root transform territories are parented under.</summary>
        public Transform MapRoot => _mapRootTransform;

        /// <summary>Point-in-time pool counters, for the phase report and tests.</summary>
        public TerritoryPoolStats PoolStats => _pool != null ? _pool.Stats : default;

        /// <summary>
        /// Test seam: invoked exactly when a tick's fetch genuinely completes and its diff has
        /// been applied to <c>_visible</c> and the pool. Never invoked for a tick that was
        /// skipped (nothing to do) or superseded/cancelled by a later one -- a caller waiting on
        /// this can trust that when it fires, the visible set actually changed.
        /// </summary>
        internal Action TickObserver { get; set; }

        /// <summary>
        /// Test seam: invoked after a tick's meshes have been fetched and decoded, and before it
        /// checks whether it is still allowed to commit them.
        /// </summary>
        /// <remarks>
        /// This is the window the generation guard exists for, and it is not reachable by
        /// timing. Once <c>Task.Run</c> has started decoding, cancelling the token does not
        /// unwind it: the decode runs to completion and the await returns a perfectly valid
        /// dictionary to a tick that is no longer current. Reproducing that by racing a real
        /// server means landing a cancellation inside a window of a few milliseconds, and a test
        /// that tries would pass against the broken code most of the time — which is exactly the
        /// mistake the phase 4 review caught in the cancellation tests.
        /// </remarks>
        internal Action AfterFetchObserver { get; set; }

        /// <summary>
        /// Test seam: bumps the generation as though a newer tick had started, without starting
        /// one. Lets a test put a running tick into the superseded state deterministically.
        /// </summary>
        internal void ForceSupersedeForTests()
        {
            _tickGeneration++;
        }

        /// <summary>
        /// Test seam: runs one tick synchronously, bypassing the <see cref="Update"/> interval.
        /// </summary>
        /// <remarks>
        /// Exists for the allocation gate. Measuring the tick through the normal frame loop would
        /// fold in the test runner's and the engine's own per-frame allocations, which would
        /// swamp the number being measured; calling the tick directly makes the measurement
        /// about this component and nothing else.
        /// </remarks>
        internal void TickForTests()
        {
            Tick();
        }

        private async void Start()
        {
            _lifetime = new CancellationTokenSource();
            _scratchBlock = new MaterialPropertyBlock();
            EnsureMapRoot();
            EnsureMaterial();

            if (_material == null)
            {
                // EnsureMaterial has already logged what went wrong. Stop cleanly rather than
                // constructing a pool with a null material: this is an async void method, so the
                // ArgumentNullException that used to follow could not be caught by anyone and
                // took the whole component down with a second, less informative error. Found by
                // running an actual standalone player, where Shader.Find returns null unless the
                // shader is in Always Included Shaders -- see the package README.
                enabled = false;
                return;
            }

            _pool = new TerritoryPool(_mapRootTransform, _material);
            _pool.WarmUp(warmPoolSize);
            _client = new TerritoryClient(baseUrl, DiskCache);

            try
            {
                Dataset = await _client.GetDatasetAsync(datasetId, _lifetime.Token).ConfigureAwait(true);
            }
            catch (OperationCanceledException)
            {
                // Destroyed before the first metadata fetch returned.
            }
            catch (Exception exception)
            {
                // Everything else -- server down, DNS failure, a 404 for the dataset id, a body
                // that is not the JSON this expects. This is an async void method, so an
                // exception escaping here goes to Unity's unhandled-task path: the component
                // simply never ticks again, with a stack trace that names the transport rather
                // than the thing the reader can fix. TerritoryMapRenderer has always caught
                // broadly here; this brings the two in line.
                Debug.LogError(
                    "[TerritoryKit] could not load dataset '" + datasetId + "' from " + baseUrl +
                    ", so nothing will stream. Check that the geometry API is running and that " +
                    "the dataset id exists (GET " + baseUrl + "/v1/datasets). On Windows prefer " +
                    "127.0.0.1 over localhost, which resolves to ::1 first. Underlying error: " +
                    exception.Message, this);
                enabled = false;
            }
        }

        private void Update()
        {
            if (Dataset == null || targetCamera == null || Time.unscaledTime < _nextTickTime)
            {
                return;
            }

            _nextTickTime = Time.unscaledTime + tickIntervalSeconds;
            Tick();
        }

        private void Tick()
        {
            LocalBounds? rawBounds = TerritoryMapPlacement.CameraGroundBounds(targetCamera, _mapRootTransform);
            if (rawBounds == null || rawBounds.Value.IsEmpty)
            {
                return;
            }

            float zoomMetric = targetCamera.orthographic
                ? targetCamera.orthographicSize
                : Vector3.Distance(targetCamera.transform.position, _mapRootTransform.position);
            // Hysteresis reasons from what was last *requested*, not what has actually landed
            // and been applied (CurrentLod) -- see the comment below on why those two can differ
            // for as long as a fetch is in flight.
            string referenceLod = _lastRequestedLod ?? CurrentLod;
            string nextLod = LodHysteresis.SelectLevel(LodHysteresis.LevelsFinestFirst,
                BuildThresholds(), referenceLod, zoomMetric);

            float span = Mathf.Max(rawBounds.Value.MaxX - rawBounds.Value.MinX,
                rawBounds.Value.MaxY - rawBounds.Value.MinY);
            LocalBounds paddedBounds = rawBounds.Value.Expanded(span * viewportMarginRatio);

            bool lodChanged = nextLod != referenceLod;
            if (!lodChanged && _lastRequestedBounds.HasValue &&
                Contains(_lastRequestedBounds.Value, rawBounds.Value))
            {
                // Still safely inside what the last request already covered -- nothing to do.
                return;
            }

            // Recorded before the fetch even starts, not when it completes (CurrentLod is only
            // ever written on success, in RunTickAsync). Without this, every frame between
            // kicking a LOD change off and it actually landing would see referenceLod still at
            // the old level, judge the target level "changed" all over again, and cancel-and-
            // restart the fetch it had just started -- a livelock that can never outrun a fetch
            // slower than one frame.
            _lastRequestedBounds = paddedBounds;
            _lastRequestedLod = nextLod;

            _inFlightCts?.Cancel();
            _inFlightCts?.Dispose();
            _inFlightCts = CancellationTokenSource.CreateLinkedTokenSource(_lifetime.Token);
            _ = RunTickAsync(paddedBounds, nextLod, _inFlightCts.Token, ++_tickGeneration);
        }

        /// <summary>
        /// The thresholds array, rebuilt only when one of the serialized fields behind it has
        /// actually changed.
        /// </summary>
        /// <remarks>
        /// This is called on every tick, including the overwhelmingly common one where the camera
        /// has not moved and nothing is fetched. Allocating a fresh two-element array there put
        /// roughly five allocations a second on an idle scene — small, but exactly the kind of
        /// steady-state garbage <see cref="TerritoryPool"/> exists to avoid, and it made the
        /// phase 5 "zero allocation" claim true of the pool while false of the component that
        /// drives it.
        /// </remarks>
        private LodHysteresis.Threshold[] BuildThresholds()
        {
            if (_thresholds != null &&
                _cachedHighToMediumCoarsenAt == highToMediumCoarsenAt &&
                _cachedHighToMediumRefineAt == highToMediumRefineAt &&
                _cachedMediumToLowCoarsenAt == mediumToLowCoarsenAt &&
                _cachedMediumToLowRefineAt == mediumToLowRefineAt)
            {
                return _thresholds;
            }

            _cachedHighToMediumCoarsenAt = highToMediumCoarsenAt;
            _cachedHighToMediumRefineAt = highToMediumRefineAt;
            _cachedMediumToLowCoarsenAt = mediumToLowCoarsenAt;
            _cachedMediumToLowRefineAt = mediumToLowRefineAt;
            _thresholds = new[]
            {
                new LodHysteresis.Threshold(highToMediumCoarsenAt, highToMediumRefineAt),
                new LodHysteresis.Threshold(mediumToLowCoarsenAt, mediumToLowRefineAt)
            };
            return _thresholds;
        }

        private static bool Contains(LocalBounds outer, LocalBounds inner)
        {
            return inner.MinX >= outer.MinX && inner.MaxX <= outer.MaxX &&
                   inner.MinY >= outer.MinY && inner.MaxY <= outer.MaxY;
        }

        private static string FormatBbox(LocalBounds bounds)
        {
            return bounds.MinX.ToString("R", CultureInfo.InvariantCulture) + "," +
                   bounds.MinY.ToString("R", CultureInfo.InvariantCulture) + "," +
                   bounds.MaxX.ToString("R", CultureInfo.InvariantCulture) + "," +
                   bounds.MaxY.ToString("R", CultureInfo.InvariantCulture);
        }

        /// <summary>
        /// Throws if this tick no longer has the right to write: either its token was cancelled,
        /// or a newer tick has started and owns the shared state now.
        /// </summary>
        private void ThrowIfSuperseded(int generation, CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            if (generation != _tickGeneration)
            {
                throw new OperationCanceledException(
                    "tick " + generation + " was superseded by tick " + _tickGeneration);
            }
        }

        /// <summary>
        /// Fetches every id in <paramref name="ids"/>, in chunks the server will accept, and
        /// disposes everything it decoded if any chunk fails or the tick is superseded.
        /// </summary>
        private async Task<Dictionary<string, TkmsMeshData>> FetchInChunksAsync(List<string> ids,
            string lod, CancellationToken token, int generation)
        {
            var fetched = new Dictionary<string, TkmsMeshData>(ids.Count);
            try
            {
                for (int start = 0; start < ids.Count; start += MaxBatchTerritories)
                {
                    int count = Mathf.Min(MaxBatchTerritories, ids.Count - start);
                    List<string> chunk = ids.GetRange(start, count);

                    Dictionary<string, TkmsMeshData> part = await _client.GetMeshDataBatchAsync(
                        datasetId, Dataset.revisionId, chunk, lod, null, token).ConfigureAwait(true);

                    // Merged before the next await so a failure later still sees everything
                    // decoded so far and can dispose it.
                    foreach (KeyValuePair<string, TkmsMeshData> pair in part)
                    {
                        fetched[pair.Key] = pair.Value;
                    }

                    ThrowIfSuperseded(generation, token);
                }

                return fetched;
            }
            catch
            {
                foreach (TkmsMeshData data in fetched.Values)
                {
                    data.Dispose();
                }

                throw;
            }
        }

        /// <summary>
        /// Fetches the ids for one tick and applies the diff.
        /// </summary>
        /// <remarks>
        /// Every await is followed by <see cref="ThrowIfSuperseded"/>, and the commit itself is
        /// transactional: new slots are checked out and filled into a local list first, and only
        /// a fully prepared set is handed over to <c>_visible</c>. A failure anywhere in
        /// preparation releases the slots it took and disposes every mesh it decoded, leaving the
        /// visible set exactly as it was rather than half-updated.
        /// <para>
        /// Preparing before releasing means a level change briefly holds two slots per territory
        /// and can grow the pool to about twice the visible count, once. That is the deliberate
        /// price of never mutating <c>_visible</c> until the new state is known to be complete;
        /// releasing first would be cheaper and would leave a hole on the failure path.
        /// </para>
        /// </remarks>
        private async Task RunTickAsync(LocalBounds bounds, string lod, CancellationToken token,
            int generation)
        {
            try
            {
                string bboxLocal = FormatBbox(bounds);
                List<string> newIds = await _client.GetAllViewportIdsAsync(datasetId, lod, bboxLocal, token)
                    .ConfigureAwait(true);
                ThrowIfSuperseded(generation, token);

                bool lodChanging = lod != CurrentLod;
                var newIdSet = new HashSet<string>(newIds);

                var toRemove = new List<string>();
                foreach (string id in _visible.Keys)
                {
                    if (lodChanging || !newIdSet.Contains(id))
                    {
                        toRemove.Add(id);
                    }
                }

                var toAdd = new List<string>();
                foreach (string id in newIdSet)
                {
                    if (lodChanging || !_visible.ContainsKey(id))
                    {
                        toAdd.Add(id);
                    }
                }

                Dictionary<string, TkmsMeshData> fetched = toAdd.Count > 0
                    ? await FetchInChunksAsync(toAdd, lod, token, generation).ConfigureAwait(true)
                    : new Dictionary<string, TkmsMeshData>();

                AfterFetchObserver?.Invoke();

                var prepared = new List<KeyValuePair<string, VisibleTerritory>>(fetched.Count);
                bool handedOver = false;
                try
                {
                    // Re-checked immediately before anything is mutated: the last await above may
                    // have completed on a frame where a newer tick had already started.
                    ThrowIfSuperseded(generation, token);

                    foreach (KeyValuePair<string, TkmsMeshData> pair in fetched)
                    {
                        PooledTerritory slot = _pool.Checkout(pair.Key);
                        try
                        {
                            MeshDecoder.Apply(slot.Mesh, pair.Value);
                            ApplyColour(slot, ColourFor(pair.Key));
                        }
                        catch
                        {
                            // This slot never made it into `prepared`, so the finally below will
                            // not see it; release it here or it is leaked.
                            _pool.Release(slot);
                            throw;
                        }

                        prepared.Add(new KeyValuePair<string, VisibleTerritory>(
                            pair.Key, new VisibleTerritory(slot, pair.Value)));
                    }

                    // The commit point. Nothing below can throw, so from here the new state is
                    // whole: outgoing territories go back to the pool, incoming ones take their
                    // place, and ownership of every fetched TkmsMeshData passes to _visible.
                    foreach (string id in toRemove)
                    {
                        ReleaseVisible(id);
                    }

                    foreach (KeyValuePair<string, VisibleTerritory> entry in prepared)
                    {
                        _visible[entry.Key] = entry.Value;
                    }

                    handedOver = true;
                    CurrentLod = lod;
                }
                finally
                {
                    if (!handedOver)
                    {
                        foreach (KeyValuePair<string, VisibleTerritory> entry in prepared)
                        {
                            _pool.Release(entry.Value.Slot);
                        }

                        // Covers prepared and unprepared alike: `prepared` holds the same
                        // TkmsMeshData instances as `fetched`, so this is the one place they are
                        // disposed and it cannot double-dispose.
                        foreach (TkmsMeshData data in fetched.Values)
                        {
                            data.Dispose();
                        }
                    }
                }

                // Fired only here, on genuine completion -- not from a catch/finally that also
                // runs when this tick was cancelled. A superseded tick has nothing to report, and
                // notifying for it anyway would let a caller (a test, in practice) observe a
                // stale tick's "done" instead of waiting for the one that actually updated state.
                TickObserver?.Invoke();
            }
            catch (OperationCanceledException)
            {
                // Superseded by a later tick, which will recompute from the camera's current
                // position, or the component is shutting down. Either way _visible was never
                // touched and the newer tick already owns _lastRequestedBounds/_lastRequestedLod,
                // so there is deliberately no rollback here.
            }
            catch (Exception exception)
            {
                // A fire-and-forget tick (`_ = RunTickAsync(...)`) has no caller to propagate to;
                // an uncaught exception here would otherwise become an unobserved task fault that
                // silently stops ticking rather than telling anyone why.
                Debug.LogException(exception, this);
                RollBackRequestState(generation);
            }
        }

        /// <summary>
        /// Forgets what the failed tick had recorded as requested, so the next tick re-requests
        /// the same view instead of treating it as already covered.
        /// </summary>
        /// <remarks>
        /// <see cref="Tick"/> writes <c>_lastRequestedBounds</c> before the fetch starts, and
        /// then skips any later tick whose camera box is still inside it. If the fetch fails —
        /// server down, a transient network error, a malformed payload — nothing ever clears that
        /// record, so the streamer stops issuing requests entirely and only wakes up if the user
        /// happens to pan far enough to leave the box. A failed request has covered nothing, so
        /// the record it left has to go with it.
        /// <para>
        /// Guarded by generation: if a newer tick has already started, these fields describe
        /// <em>its</em> request, and clearing them would make it re-fetch needlessly.
        /// </para>
        /// </remarks>
        private void RollBackRequestState(int generation)
        {
            if (generation != _tickGeneration)
            {
                return;
            }

            _lastRequestedBounds = null;
            _lastRequestedLod = null;
        }

        private void ReleaseVisible(string id)
        {
            if (!_visible.TryGetValue(id, out VisibleTerritory territory))
            {
                return;
            }

            _pool.Release(territory.Slot);
            territory.Data.Dispose();
            _visible.Remove(id);
        }

        /// <summary>Overrides one territory's colour, live if it is currently visible.</summary>
        public void SetTerritoryColor(string territoryId, Color colour)
        {
            if (string.IsNullOrEmpty(territoryId))
            {
                throw new ArgumentException("territoryId is required", nameof(territoryId));
            }

            _colourOverrides[territoryId] = colour;
            if (_visible.TryGetValue(territoryId, out VisibleTerritory territory))
            {
                ApplyColour(territory.Slot, colour);
            }
        }

        private Color ColourFor(string territoryId)
        {
            return _colourOverrides.TryGetValue(territoryId, out Color stored) ? stored : DefaultColour(territoryId, colourSeed);
        }

        private static readonly int ColourProperty = Shader.PropertyToID("_Color");
        private static readonly int BaseColourProperty = Shader.PropertyToID("_BaseColor");

        private void ApplyColour(PooledTerritory slot, Color colour)
        {
            _scratchBlock.Clear();
            // Built-in unlit reads _Color, URP unlit reads _BaseColor; setting both costs
            // nothing and keeps the sample pipeline-agnostic, matching TerritoryMapRenderer.
            _scratchBlock.SetColor(ColourProperty, colour);
            _scratchBlock.SetColor(BaseColourProperty, colour);
            slot.Renderer.SetPropertyBlock(_scratchBlock);
        }

        /// <summary>
        /// Deterministic per-id colour, stable across runs for a given <paramref name="seed"/>.
        /// </summary>
        /// <remarks>
        /// Not <c>string.GetHashCode()</c>: .NET randomises string hashing per process by
        /// default, so the same id would get a different colour every launch. FNV-1a over the
        /// UTF-16 code units gives the same number every time, on every platform.
        /// </remarks>
        internal static Color DefaultColour(string territoryId, int seed)
        {
            uint hash = 2166136261u;
            unchecked
            {
                foreach (char c in territoryId)
                {
                    hash ^= c;
                    hash *= 16777619u;
                }

                hash ^= (uint)seed;
                hash *= 16777619u;
            }

            float hue = (hash % 10007u) / 10007f;
            float saturation = 0.45f + (hash / 10007u % 4001u) / 4001f * 0.25f;
            float value = 0.70f + (hash / (10007u * 4001u) % 4001u) / 4001f * 0.25f;
            return Color.HSVToRGB(hue, saturation, value);
        }

        /// <summary>Attempts to resolve a screen point to a territory id among what is currently visible.</summary>
        public bool TryPick(Vector2 screenPoint, out string territoryId, out LodSafety safety)
        {
            safety = LodPolicy.Describe(Dataset, CurrentLod);
            territoryId = null;
            if (targetCamera == null || _mapRootTransform == null)
            {
                return false;
            }

            var candidates = new List<TerritoryPicker.Candidate>(_visible.Count);
            foreach (KeyValuePair<string, VisibleTerritory> pair in _visible)
            {
                candidates.Add(new TerritoryPicker.Candidate(pair.Key, pair.Value.Data));
            }

            return TerritoryPicker.TryPickFromScreenPoint(targetCamera, _mapRootTransform,
                screenPoint, candidates, out territoryId);
        }

        private void EnsureMapRoot()
        {
            if (_mapRootTransform != null)
            {
                return;
            }

            if (mapRoot != null)
            {
                _mapRootTransform = mapRoot;
                return;
            }

            var root = new GameObject("Territories");
            root.transform.SetParent(transform, false);
            root.transform.localRotation = TerritoryMapPlacement.RootRotation;
            _mapRootTransform = root.transform;
        }

        private void EnsureMaterial()
        {
            if (_material != null)
            {
                return;
            }

            Shader shader = Shader.Find("Unlit/Color") ?? Shader.Find("Universal Render Pipeline/Unlit");
            if (shader == null)
            {
                // Common in a built player rather than in the editor: Shader.Find only sees
                // shaders the build actually included, and a built-in shader no material in any
                // scene references is stripped. Project Settings > Graphics > Always Included
                // Shaders is the fix, so the message says so instead of leaving the reader to
                // discover that the same scene works in the editor and not in a build.
                Debug.LogError(
                    "[TerritoryKit] neither 'Unlit/Color' nor 'Universal Render Pipeline/Unlit' " +
                    "could be found, so territories cannot be drawn. In a built player this " +
                    "usually means the shader was stripped: add it to Project Settings > " +
                    "Graphics > Always Included Shaders, or assign a material yourself.", this);
                return;
            }

            _material = new Material(shader) { name = "TerritoryKit Unlit (pooled)" };
            _material.enableInstancing = true;
        }

        private void OnDestroy()
        {
            _lifetime?.Cancel();
            _inFlightCts?.Cancel();
            _inFlightCts?.Dispose();
            _lifetime?.Dispose();

            if (_pool != null)
            {
                foreach (KeyValuePair<string, VisibleTerritory> pair in _visible)
                {
                    _pool.Release(pair.Value.Slot);
                    pair.Value.Data.Dispose();
                }

                _visible.Clear();
                _pool.DestroyAll();
            }

            if (_material != null)
            {
                if (Application.isPlaying) Destroy(_material); else DestroyImmediate(_material);
                _material = null;
            }
        }
    }
}
