using System.Collections;
using System.Threading.Tasks;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace TerritoryKit.Unity.Tests
{
    /// <summary>
    /// Asks the renderer, not the algebra, whether the map is visible.
    /// </summary>
    /// <remarks>
    /// The EditMode winding test measures a geometric normal, which is a claim about vectors.
    /// This one draws the scene with back-face culling on and counts lit pixels, which is a
    /// claim about what a user sees. Unity is left-handed and the equivalence between "the
    /// normal points at the camera" and "the renderer treats this as a front face" is exactly
    /// where a sign error hides, so the derivation is not trusted on its own.
    /// <para>
    /// This also automates the evidence behind the phase screenshot: if the placement is ever
    /// reversed, this goes red rather than the screenshot quietly turning black.
    /// </para>
    /// </remarks>
    public class RenderCoverageTests
    {
        private const int Resolution = 256;

        private MockGeometryServer _server;
        private GameObject _host;
        private Camera _camera;

        /// <summary>
        /// Two territories that tile a 200x100 local-metre strip, so the expected coverage is a
        /// number this test can predict rather than merely "more than nothing".
        /// </summary>
        private const string DatasetJson = @"{
            ""id"": ""render-fixture"",
            ""revisionId"": ""rev1"",
            ""boundsLocal"": [0.0, 0.0, 200.0, 100.0],
            ""territoryCount"": 2,
            ""levels"": [
                { ""lod"": ""high"", ""lossy"": false, ""topologyChanged"": false,
                  ""pickingUnsafe"": false, ""simplification"": { ""topologyChanged"": false } }
            ]
        }";

        private static byte[] Tile(float x0, float x1)
        {
            // Clockwise with +X right and +Y up, exactly as the encoder emits.
            var vertices = new[] { x0, 0f, x0, 100f, x1, 100f, x1, 0f };
            return TkmsFixture.Build(vertices, new[] { 0, 1, 2, 0, 2, 3 });
        }

        private static byte[] ReversedTile(float x0, float x1)
        {
            var vertices = new[] { x0, 0f, x0, 100f, x1, 100f, x1, 0f };
            return TkmsFixture.Build(vertices, new[] { 0, 2, 1, 0, 3, 2 });
        }

        [SetUp]
        public void SetUp()
        {
            _server = new MockGeometryServer { DatasetJson = DatasetJson };
            _server.TerritoryPages.Add(
                @"{""items"":[{""id"":""a"",""bboxLocal"":[0,0,100,100]},
                              {""id"":""b"",""bboxLocal"":[100,0,200,100]}],""nextCursor"":""""}");

            _host = new GameObject("host");
            var cameraObject = new GameObject("camera");
            _camera = cameraObject.AddComponent<Camera>();
            _camera.clearFlags = CameraClearFlags.SolidColor;
            _camera.backgroundColor = Color.black;
            // Pinned rather than inherited: the camera is framed before the RenderTexture is
            // attached, so without this the expected coverage would depend on the game view.
            _camera.aspect = 1f;
        }

        [TearDown]
        public void TearDown()
        {
            if (_host != null) Object.DestroyImmediate(_host);
            if (_camera != null) Object.DestroyImmediate(_camera.gameObject);
            _server?.Dispose();
            _server = null;
        }

        private IEnumerator LoadMap()
        {
            var renderer = _host.AddComponent<TerritoryMapRenderer>();
            renderer.LoadOnStart = false; // the test drives the load itself
            renderer.BaseUrl = _server.BaseUrl;
            renderer.DatasetId = "render-fixture";
            renderer.Lod = "high";
            renderer.TargetCamera = _camera;

            Task task = renderer.LoadAsync();
            while (!task.IsCompleted)
            {
                yield return null;
            }

            if (task.IsFaulted)
            {
                throw task.Exception.InnerException ?? task.Exception;
            }

            Assert.AreEqual(2, renderer.DrawnCount);
        }

        private float RenderCoverage()
        {
            var target = new RenderTexture(Resolution, Resolution, 24, RenderTextureFormat.ARGB32);
            var texture = new Texture2D(Resolution, Resolution, TextureFormat.RGBA32, false);
            try
            {
                _camera.targetTexture = target;
                _camera.Render();

                RenderTexture previous = RenderTexture.active;
                RenderTexture.active = target;
                texture.ReadPixels(new Rect(0, 0, Resolution, Resolution), 0, 0);
                texture.Apply();
                RenderTexture.active = previous;

                Color32[] pixels = texture.GetPixels32();
                int lit = 0;
                foreach (Color32 pixel in pixels)
                {
                    if (pixel.r > 32 || pixel.g > 32 || pixel.b > 32)
                    {
                        lit++;
                    }
                }

                return lit / (float)pixels.Length;
            }
            finally
            {
                _camera.targetTexture = null;
                Object.DestroyImmediate(texture);
                target.Release();
                Object.DestroyImmediate(target);
            }
        }

        [UnityTest]
        public IEnumerator ClockwiseTkmsMeshesAreVisibleWithBackFaceCullingOn()
        {
            _server.Meshes["a"] = Tile(0f, 100f);
            _server.Meshes["b"] = Tile(100f, 200f);

            yield return LoadMap();
            yield return null;

            float coverage = RenderCoverage();

            // FrameBounds fits a 200x100 box at aspect 1 with 5% padding, giving a 210x210
            // view, so the strip should cover (200/210) * (100/210) ~= 0.45 of the frame.
            Assert.Greater(coverage, 0.40f,
                "the map is not being drawn; a reversed placement culls every triangle");
            Assert.Less(coverage, 0.50f, "the strip should not fill the whole frame");
        }

        [UnityTest]
        public IEnumerator ReversedWindingIsCulledEntirely()
        {
            // The control. Without it, a scene that drew something for any winding at all would
            // still satisfy the test above.
            _server.Meshes["a"] = ReversedTile(0f, 100f);
            _server.Meshes["b"] = ReversedTile(100f, 200f);

            yield return LoadMap();
            yield return null;

            Assert.AreEqual(0f, RenderCoverage(), 0.001f,
                "counter-clockwise triangles must be back-facing, or the culling test above " +
                "proves nothing");
        }
    }
}
