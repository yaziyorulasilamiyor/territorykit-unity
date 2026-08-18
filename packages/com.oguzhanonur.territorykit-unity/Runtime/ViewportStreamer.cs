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
    /// from the camera's current position — the superseded fetch's own cleanup (see
    /// <see cref="TerritoryClient.GetMeshDataBatchAsync"/>) disposes anything it had already
    /// decoded. Visible-set bookkeeping (<c>_visible</c>, the pool) is only ever mutated after
    /// <em>both</em> awaits in one tick succeed, so a cancelled tick leaves no partial state for
    /// the next tick to untangle.
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

        private async void Start()
        {
            _lifetime = new CancellationTokenSource();
            _scratchBlock = new MaterialPropertyBlock();
            EnsureMapRoot();
            EnsureMaterial();
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
            _ = RunTickAsync(paddedBounds, nextLod, _inFlightCts.Token);
        }

        private LodHysteresis.Threshold[] BuildThresholds()
        {
            return new[]
            {
                new LodHysteresis.Threshold(highToMediumCoarsenAt, highToMediumRefineAt),
                new LodHysteresis.Threshold(mediumToLowCoarsenAt, mediumToLowRefineAt)
            };
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
        /// Fetches the ids for one tick and applies the diff. Mutates <see cref="_visible"/> and
        /// the pool only after both awaits below succeed — see the threading remarks on the
        /// class.
        /// </summary>
        private async Task RunTickAsync(LocalBounds bounds, string lod, CancellationToken token)
        {
            try
            {
                string bboxLocal = FormatBbox(bounds);
                List<string> newIds = await _client.GetAllViewportIdsAsync(datasetId, lod, bboxLocal, token)
                    .ConfigureAwait(true);
                token.ThrowIfCancellationRequested();

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

                Dictionary<string, TkmsMeshData> fetched;
                if (toAdd.Count > 0)
                {
                    fetched = await _client.GetMeshDataBatchAsync(datasetId, Dataset.revisionId,
                        toAdd, lod, null, token).ConfigureAwait(true);
                }
                else
                {
                    fetched = new Dictionary<string, TkmsMeshData>();
                }

                // No more awaits from here on: a cancellation could only have landed in one of
                // the two calls above, before anything below ran.
                foreach (string id in toRemove)
                {
                    ReleaseVisible(id);
                }

                foreach (KeyValuePair<string, TkmsMeshData> pair in fetched)
                {
                    PooledTerritory slot = _pool.Checkout(pair.Key);
                    MeshDecoder.Apply(slot.Mesh, pair.Value);
                    ApplyColour(slot, ColourFor(pair.Key));
                    _visible[pair.Key] = new VisibleTerritory(slot, pair.Value);
                }

                CurrentLod = lod;
                // Fired only here, on genuine completion -- not from a catch/finally that also
                // runs when this tick was cancelled. A superseded tick has nothing to report, and
                // notifying for it anyway would let a caller (a test, in practice) observe a
                // stale tick's "done" instead of waiting for the one that actually updated state.
                TickObserver?.Invoke();
            }
            catch (OperationCanceledException)
            {
                // Superseded by a later tick, which will recompute from the camera's current
                // position -- _visible was never touched, so there is nothing to undo.
            }
            catch (Exception exception)
            {
                // A fire-and-forget tick (`_ = RunTickAsync(...)`) has no caller to propagate to;
                // an uncaught exception here would otherwise become an unobserved task fault that
                // silently stops ticking rather than telling anyone why.
                Debug.LogException(exception, this);
            }
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
                Debug.LogError("[TerritoryKit] no unlit shader found; territories will not draw", this);
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
