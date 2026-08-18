using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;

namespace TerritoryKit.Unity
{
    /// <summary>
    /// Loads a dataset and draws every territory: one GameObject, one Mesh, one draw call each.
    /// </summary>
    /// <remarks>
    /// This is Phase 4's whole scope — download, decode, draw. There is no pooling, no viewport
    /// streaming, no LOD switching and no picking; a territory that scrolls off screen still
    /// exists and is still drawn. Phase 5 replaces the flat "load everything once" behaviour
    /// here with a streamer that consumes the same client and the same placement.
    /// <para>
    /// Colours come from a <see cref="MaterialPropertyBlock"/> over one shared material rather
    /// than from a material per territory, because 81 material instances would be 81 more
    /// things for Phase 5 to undo.
    /// </para>
    /// </remarks>
    [AddComponentMenu("TerritoryKit/Territory Map Renderer")]
    public sealed class TerritoryMapRenderer : MonoBehaviour
    {
        [Header("Server")]
        [SerializeField]
        private string baseUrl = "http://localhost:8000";

        [SerializeField]
        private string datasetId = "tr-adm1";

        [Tooltip("Detail level to draw. 'high' is the default because its simplification step " +
                 "changes nothing, so anything that looks wrong on screen is this client's fault " +
                 "rather than the simplifier's.")]
        [SerializeField]
        private string lod = "high";

        [Header("Presentation")]
        [SerializeField]
        private Camera targetCamera;

        [SerializeField]
        private bool frameCameraOnLoad = true;

        [Tooltip("Fixed seed, so the same territory gets the same colour on every run and two " +
                 "screenshots can be compared.")]
        [SerializeField]
        private int colourSeed = 20260818;

        [SerializeField]
        private bool loadOnStart = true;

        private readonly List<GameObject> _territoryObjects = new List<GameObject>();
        private readonly List<Mesh> _meshes = new List<Mesh>();
        private CancellationTokenSource _lifetime;
        private Material _material;

        /// <summary>Root that carries the placement rotation; territories are its children.</summary>
        public Transform MapRoot { get; private set; }

        /// <summary>Metadata of the loaded dataset, or null before a load completes.</summary>
        public DatasetInfo Dataset { get; private set; }

        /// <summary>Verdict for the level being drawn, recorded at load time.</summary>
        public LodSafety Safety { get; private set; }

        /// <summary>Number of territories currently drawn.</summary>
        public int DrawnCount => _territoryObjects.Count;

        /// <summary>Ids the server reported as missing from the batch, if any.</summary>
        public List<string> MissingIds { get; } = new List<string>();

        public string BaseUrl
        {
            get => baseUrl;
            set => baseUrl = value;
        }

        public string DatasetId
        {
            get => datasetId;
            set => datasetId = value;
        }

        public string Lod
        {
            get => lod;
            set => lod = value;
        }

        public Camera TargetCamera
        {
            get => targetCamera;
            set => targetCamera = value;
        }

        /// <summary>Whether Start() kicks off a load. Off for callers that drive LoadAsync themselves.</summary>
        public bool LoadOnStart
        {
            get => loadOnStart;
            set => loadOnStart = value;
        }

        /// <summary>
        /// Test seam: invoked after the batch returns and before ownership of its meshes passes
        /// to <see cref="Draw"/>. Exists so a test can cancel in exactly that window, which is
        /// otherwise only reachable by racing the decode.
        /// </summary>
        internal Action AfterBatchObserver { get; set; }

        private async void Start()
        {
            if (!loadOnStart)
            {
                return;
            }

            try
            {
                await LoadAsync().ConfigureAwait(true);
            }
            catch (OperationCanceledException)
            {
                // The component was destroyed mid-load; nothing to report.
            }
            catch (Exception exception)
            {
                Debug.LogException(exception, this);
            }
        }

        private void OnDestroy()
        {
            _lifetime?.Cancel();
            _lifetime?.Dispose();
            _lifetime = null;
            Clear();

            if (_material != null)
            {
                Destroy(_material);
                _material = null;
            }
        }

        /// <summary>
        /// Fetches metadata, then every territory's mesh in one batch request, and draws them.
        /// </summary>
        public async Task LoadAsync(CancellationToken cancellationToken = default)
        {
            _lifetime?.Cancel();
            _lifetime?.Dispose();
            _lifetime = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            CancellationToken token = _lifetime.Token;

            var client = new TerritoryClient(baseUrl);

            // Metadata first, and not only for the revision id: this is where the level's
            // lossy / topologyChanged / pickingUnsafe flags arrive, before a single mesh is
            // downloaded.
            Dataset = await client.GetDatasetAsync(datasetId, token).ConfigureAwait(true);
            Safety = LodPolicy.Describe(Dataset, lod);
            if (!Safety.IsSafeForPicking)
            {
                Debug.LogWarning("[TerritoryKit] " + Safety +
                                 ". Phase 4 only draws, so this does not affect what you see; " +
                                 "it will affect Phase 5 picking.", this);
            }

            List<TerritorySummary> territories =
                await client.GetAllTerritoriesAsync(datasetId, lod, token).ConfigureAwait(true);

            var ids = new List<string>(territories.Count);
            foreach (TerritorySummary territory in territories)
            {
                ids.Add(territory.id);
            }

            Dictionary<string, Mesh> meshes = await client.GetMeshBatchAsync(
                datasetId, Dataset.revisionId, ids, lod, MissingIds, token).ConfigureAwait(true);

            AfterBatchObserver?.Invoke();

            // The meshes exist now but nothing owns them yet, and Task.Run does not stop once
            // it has started -- so a cancellation raised any time after the download (OnDestroy,
            // or a second LoadAsync) lands here, past the point where the meshes were created.
            // Until Draw has taken them, this method is what has to destroy them.
            bool owned = false;
            try
            {
                token.ThrowIfCancellationRequested();
                Draw(meshes);
                owned = true;
            }
            finally
            {
                if (!owned)
                {
                    foreach (Mesh mesh in meshes.Values)
                    {
                        if (mesh != null)
                        {
                            DestroyObject(mesh);
                        }
                    }
                }
            }

            if (frameCameraOnLoad)
            {
                FrameCamera();
            }
        }

        /// <summary>Frames <see cref="TargetCamera"/> on the dataset's local-metre bounds.</summary>
        public void FrameCamera()
        {
            Camera camera = targetCamera != null ? targetCamera : Camera.main;
            if (camera == null || Dataset == null || Dataset.boundsLocal == null ||
                Dataset.boundsLocal.Length < 4)
            {
                return;
            }

            TerritoryMapPlacement.FrameBounds(camera, Dataset.boundsLocal[0], Dataset.boundsLocal[1],
                Dataset.boundsLocal[2], Dataset.boundsLocal[3]);
        }

        private void Draw(Dictionary<string, Mesh> meshes)
        {
            Clear();
            EnsureRoot();
            EnsureMaterial();

            var random = new System.Random(colourSeed);
            var block = new MaterialPropertyBlock();

            var ids = new List<string>(meshes.Keys);
            ids.Sort(StringComparer.Ordinal);

            try
            {
                foreach (string id in ids)
                {
                    Mesh mesh = meshes[id];
                    var go = new GameObject(id);
                    go.transform.SetParent(MapRoot, false);

                    go.AddComponent<MeshFilter>().sharedMesh = mesh;
                    var renderer = go.AddComponent<MeshRenderer>();
                    renderer.sharedMaterial = _material;

                    block.Clear();
                    Color colour = NextColour(random);
                    // Built-in unlit reads _Color, URP unlit reads _BaseColor. Setting both
                    // costs nothing and means the sample is not pipeline-specific.
                    block.SetColor(ColourProperty, colour);
                    block.SetColor(BaseColourProperty, colour);
                    renderer.SetPropertyBlock(block);

                    _territoryObjects.Add(go);
                    _meshes.Add(mesh);
                }
            }
            catch
            {
                // Half a map is not a state anything downstream expects. Drop what was built so
                // the caller's cleanup only has the meshes this never reached to deal with.
                Clear();
                throw;
            }
        }

        private static readonly int ColourProperty = Shader.PropertyToID("_Color");

        private static readonly int BaseColourProperty = Shader.PropertyToID("_BaseColor");

        private static Color NextColour(System.Random random)
        {
            // Spread hues but keep saturation and value in a band, so neighbouring provinces
            // stay distinguishable without the result looking like static.
            return Color.HSVToRGB((float)random.NextDouble(),
                0.45f + (float)random.NextDouble() * 0.25f,
                0.70f + (float)random.NextDouble() * 0.25f);
        }

        private void EnsureRoot()
        {
            if (MapRoot != null)
            {
                return;
            }

            var root = new GameObject("Territories");
            root.transform.SetParent(transform, false);
            // The single place the map is laid flat; children keep identity local transforms.
            root.transform.localRotation = TerritoryMapPlacement.RootRotation;
            MapRoot = root.transform;
        }

        private void EnsureMaterial()
        {
            if (_material != null)
            {
                return;
            }

            // Built-in first, URP second: the package ships no shader of its own, and asking for
            // one that does not exist is what turns a scene magenta.
            Shader shader = Shader.Find("Unlit/Color") ??
                            Shader.Find("Universal Render Pipeline/Unlit");
            if (shader == null)
            {
                Debug.LogError("[TerritoryKit] no unlit shader found; territories will not draw", this);
                return;
            }

            _material = new Material(shader) { name = "TerritoryKit Unlit" };
            _material.enableInstancing = true;
        }

        /// <summary>Destroys every drawn territory and its mesh.</summary>
        public void Clear()
        {
            foreach (GameObject go in _territoryObjects)
            {
                if (go != null)
                {
                    DestroyObject(go);
                }
            }

            foreach (Mesh mesh in _meshes)
            {
                if (mesh != null)
                {
                    DestroyObject(mesh);
                }
            }

            _territoryObjects.Clear();
            _meshes.Clear();
        }

        private static void DestroyObject(UnityEngine.Object target)
        {
            if (Application.isPlaying)
            {
                Destroy(target);
            }
            else
            {
                DestroyImmediate(target);
            }
        }
    }
}
