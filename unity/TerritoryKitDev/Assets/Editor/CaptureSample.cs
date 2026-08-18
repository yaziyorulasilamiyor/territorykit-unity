using System;
using System.IO;
using TerritoryKit.Unity;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

/// <summary>
/// Opens the BasicMap sample, loads it against a running geometry API and writes a PNG.
/// Development harness only; not part of the published package.
/// </summary>
/// <remarks>
/// Driven by <c>scripts/capture_sample.ps1</c>. Runs in batchmode <em>without</em>
/// <c>-nographics</c>, because the point is to render, and without <c>-quit</c>, because the
/// load is asynchronous and this script decides when it is done.
/// <para>
/// The scene is opened rather than rebuilt in code on purpose: a screenshot produced from
/// objects this script created would prove the renderer works and say nothing about whether the
/// sample scene does.
/// </para>
/// </remarks>
public static class CaptureSample
{
    private const string ScenePath = "Assets/Samples/BasicMap/BasicMap.unity";

    private static TerritoryMapRenderer _renderer;
    private static string _outputPath;
    private static int _width;
    private static int _height;
    private static double _deadline;
    private static bool _loadStarted;

    public static void Run()
    {
        try
        {
            _outputPath = Argument("-captureOutput", "capture.png");
            _width = int.Parse(Argument("-captureWidth", "1600"));
            _height = int.Parse(Argument("-captureHeight", "1200"));
            double timeout = double.Parse(Argument("-captureTimeout", "120"));

            if (!File.Exists(ScenePath))
            {
                Fail("sample scene not found at " + ScenePath +
                     "; capture_sample.ps1 copies it in from the package before this runs");
                return;
            }

            EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
            _renderer = UnityEngine.Object.FindAnyObjectByType<TerritoryMapRenderer>();
            if (_renderer == null)
            {
                Fail("the sample scene has no TerritoryMapRenderer");
                return;
            }

            string baseUrl = Argument("-captureBaseUrl", null);
            if (!string.IsNullOrEmpty(baseUrl))
            {
                _renderer.BaseUrl = baseUrl;
            }

            string lod = Argument("-captureLod", null);
            if (!string.IsNullOrEmpty(lod))
            {
                _renderer.Lod = lod;
            }

            _deadline = EditorApplication.timeSinceStartup + timeout;

            // Pumped from EditorApplication.update rather than awaited inline: in batchmode a
            // blocking wait would stop the editor loop that UnityWebRequest needs to progress.
            EditorApplication.update += Tick;
        }
        catch (Exception exception)
        {
            Fail(exception.ToString());
        }
    }

    private static void Tick()
    {
        try
        {
            if (!_loadStarted)
            {
                _loadStarted = true;
                _renderer.LoadOnStart = false;
                _ = _renderer.LoadAsync();
                return;
            }

            if (_renderer.DrawnCount > 0)
            {
                EditorApplication.update -= Tick;
                Capture();
                return;
            }

            if (EditorApplication.timeSinceStartup > _deadline)
            {
                EditorApplication.update -= Tick;
                Fail("timed out waiting for territories; is the geometry API running?");
            }
        }
        catch (Exception exception)
        {
            EditorApplication.update -= Tick;
            Fail(exception.ToString());
        }
    }

    private static void Capture()
    {
        Camera camera = _renderer.TargetCamera != null ? _renderer.TargetCamera : Camera.main;
        if (camera == null)
        {
            Fail("the sample scene has no camera to render from");
            return;
        }

        camera.aspect = _width / (float)_height;
        _renderer.FrameCamera();

        var target = new RenderTexture(_width, _height, 24, RenderTextureFormat.ARGB32)
        {
            antiAliasing = 8
        };
        var texture = new Texture2D(_width, _height, TextureFormat.RGBA32, false);
        try
        {
            camera.targetTexture = target;
            camera.Render();

            RenderTexture previous = RenderTexture.active;
            RenderTexture.active = target;
            texture.ReadPixels(new Rect(0, 0, _width, _height), 0, 0);
            texture.Apply();
            RenderTexture.active = previous;

            int lit = 0;
            Color32[] pixels = texture.GetPixels32();
            foreach (Color32 pixel in pixels)
            {
                if (pixel.r > 40 || pixel.g > 40 || pixel.b > 40)
                {
                    lit++;
                }
            }

            float coverage = lit / (float)pixels.Length;

            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(_outputPath)));
            File.WriteAllBytes(_outputPath, texture.EncodeToPNG());

            // Reported so the log carries evidence the image is not blank -- a black PNG is
            // still a PNG, and a reversed placement would produce exactly that.
            Debug.Log(string.Format(
                "CAPTURE_OK territories={0} missing={1} coverage={2:P2} safety=\"{3}\" path={4}",
                _renderer.DrawnCount, _renderer.MissingIds.Count, coverage,
                _renderer.Safety, Path.GetFullPath(_outputPath)));

            if (coverage < 0.01f)
            {
                Fail("rendered image is effectively blank (coverage " +
                     coverage.ToString("P2") + ")");
                return;
            }
        }
        finally
        {
            camera.targetTexture = null;
            UnityEngine.Object.DestroyImmediate(texture);
            target.Release();
            UnityEngine.Object.DestroyImmediate(target);
        }

        EditorApplication.Exit(0);
    }

    private static void Fail(string message)
    {
        Debug.LogError("CAPTURE_FAILED " + message);
        EditorApplication.Exit(1);
    }

    private static string Argument(string name, string fallback)
    {
        string[] args = Environment.GetCommandLineArgs();
        for (int i = 0; i < args.Length - 1; i++)
        {
            if (string.Equals(args[i], name, StringComparison.Ordinal))
            {
                return args[i + 1];
            }
        }

        return fallback;
    }
}
