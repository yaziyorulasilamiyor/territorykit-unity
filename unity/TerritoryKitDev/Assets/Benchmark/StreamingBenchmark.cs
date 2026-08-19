using System.Collections;
using System.Globalization;
using System.IO;
using System.Text;
using TerritoryKit.Unity;
using Unity.Profiling;
using UnityEngine;
using UnityEngine.Rendering;

namespace TerritoryKitDev.Benchmark
{
    /// <summary>
    /// Measures draw calls, batches and frame rate for a live <see cref="ViewportStreamer"/>,
    /// and writes the numbers to a file.
    /// </summary>
    /// <remarks>
    /// This exists because the render and memory <see cref="ProfilerRecorder"/> counters collect
    /// no samples under <c>-batchmode</c>: they report <c>Valid == true</c> and zero samples, so
    /// a batchmode test can neither measure draw calls nor honestly claim it did. In a
    /// Development Player with a real render loop they work normally, which is what this harness
    /// runs in. It is deliberately part of the dev project rather than the package — nothing here
    /// ships to a consumer.
    /// <para>
    /// The scene is built by <c>BuildBenchmark.Build</c> and driven by
    /// <c>scripts/measure_render.ps1</c>, which starts the geometry API first.
    /// </para>
    /// </remarks>
    public sealed class StreamingBenchmark : MonoBehaviour
    {
        [SerializeField] private string baseUrl = "http://127.0.0.1:8000";
        [SerializeField] private string datasetId = "tr-adm1";
        [SerializeField] private string outputPath = "benchmark-result.txt";

        [Tooltip("Seconds to let the first viewport load settle before sampling begins.")]
        [SerializeField] private float settleSeconds = 8f;

        [Tooltip("Seconds of steady-state sampling at rest.")]
        [SerializeField] private float idleSampleSeconds = 5f;

        [Tooltip("Seconds of sampling while the camera pans continuously.")]
        [SerializeField] private float panSampleSeconds = 5f;

        private ViewportStreamer _streamer;
        private Camera _camera;

        private IEnumerator Start()
        {
            // Both are needed to get an uncapped rate: vSyncCount alone leaves targetFrameRate at
            // the platform default, which on Windows standalone still syncs to the swap chain and
            // pins the result at exactly 60.0 -- which is what the first run reported.
            QualitySettings.vSyncCount = 0;
            Application.targetFrameRate = 1000;

            var cameraObject = new GameObject("Benchmark Camera");
            _camera = cameraObject.AddComponent<Camera>();
            _camera.clearFlags = CameraClearFlags.SolidColor;
            _camera.backgroundColor = new Color(0.09f, 0.11f, 0.14f);

            var host = new GameObject("Streamer");
            _streamer = host.AddComponent<ViewportStreamer>();
            _streamer.BaseUrl = baseUrl;
            _streamer.DatasetId = datasetId;
            _streamer.TargetCamera = _camera;

            float deadline = Time.realtimeSinceStartup + 30f;
            while (_streamer.Dataset == null && Time.realtimeSinceStartup < deadline)
            {
                yield return null;
            }

            var report = new StringBuilder();
            if (_streamer.Dataset == null)
            {
                report.AppendLine("FAILED: dataset metadata never arrived from " + baseUrl);
                Write(report.ToString());
                Application.Quit(1);
                yield break;
            }

            float[] bounds = _streamer.Dataset.boundsLocal;
            TerritoryMapPlacement.FrameBounds(_camera, bounds[0], bounds[1], bounds[2], bounds[3]);

            float settleUntil = Time.realtimeSinceStartup + settleSeconds;
            while (Time.realtimeSinceStartup < settleUntil)
            {
                yield return null;
            }

            report.AppendLine("dataset=" + datasetId + " revision=" + _streamer.Dataset.revisionId);
            report.AppendLine("lod=" + _streamer.CurrentLod + " visible=" + _streamer.VisibleCount);
            report.AppendLine("graphics=" + SystemInfo.graphicsDeviceType +
                              " srpBatcher=" + (GraphicsSettings.useScriptableRenderPipelineBatching ? "on" : "off"));

            yield return Sample("idle", idleSampleSeconds, report, pan: false);
            yield return Sample("pan", panSampleSeconds, report, pan: true);

            report.AppendLine("visibleAtEnd=" + _streamer.VisibleCount);
            TerritoryPoolStats stats = _streamer.PoolStats;
            report.AppendLine("poolCreated=" + stats.TotalGameObjectsCreated +
                              " poolFree=" + stats.FreeGameObjects);

            Write(report.ToString());
            Application.Quit(0);
        }

        private IEnumerator Sample(string label, float seconds, StringBuilder report, bool pan)
        {
            // StartNew, not the constructor: `new ProfilerRecorder(...)` creates the recorder in a
            // stopped state, so it stays Valid while collecting exactly zero samples -- which is
            // indistinguishable from "this counter does not work here" and is why the first run
            // reported every render statistic as unavailable.
            using var drawCalls = ProfilerRecorder.StartNew(ProfilerCategory.Render, "Draw Calls Count", 300);
            using var batches = ProfilerRecorder.StartNew(ProfilerCategory.Render, "Batches Count", 300);
            using var setPass = ProfilerRecorder.StartNew(ProfilerCategory.Render, "SetPass Calls Count", 300);
            using var triangles = ProfilerRecorder.StartNew(ProfilerCategory.Render, "Triangles Count", 300);
            // The frame rate is pinned to the display by vsync, so it says nothing about
            // headroom. CPU main-thread frame time does: it is what the frame actually costs,
            // independent of how long the swap chain then waits.
            using var cpuFrame = ProfilerRecorder.StartNew(
                ProfilerCategory.Internal, "CPU Main Thread Frame Time", 300);

            // One frame for the recorders to attach before anything is counted.
            yield return null;

            int frames = 0;
            float elapsed = 0f;
            float worstFrame = 0f;
            float until = Time.realtimeSinceStartup + seconds;
            while (Time.realtimeSinceStartup < until)
            {
                if (pan)
                {
                    // A steady drift across the map, so territories genuinely enter and leave.
                    _camera.transform.position += new Vector3(Time.deltaTime * 40000f, 0f, 0f);
                }

                frames++;
                elapsed += Time.unscaledDeltaTime;
                if (Time.unscaledDeltaTime > worstFrame)
                {
                    worstFrame = Time.unscaledDeltaTime;
                }

                yield return null;
            }

            report.AppendLine(
                label + ": fps=" + Format(frames / Mathf.Max(elapsed, 0.0001f)) +
                " frames=" + frames.ToString(CultureInfo.InvariantCulture) +
                " worstFrameMs=" + Format(worstFrame * 1000f) +
                " drawCalls=" + Describe(drawCalls) +
                " batches=" + Describe(batches) +
                " setPass=" + Describe(setPass) +
                " triangles=" + Describe(triangles) +
                " cpuFrameMs=" + DescribeMilliseconds(cpuFrame) +
                " visible=" + _streamer.VisibleCount);
        }

        private static string Describe(ProfilerRecorder recorder)
        {
            if (!recorder.Valid || recorder.Count == 0)
            {
                return "unavailable";
            }

            long max = long.MinValue;
            long min = long.MaxValue;
            long total = 0;
            for (int i = 0; i < recorder.Count; i++)
            {
                long value = recorder.GetSample(i).Value;
                if (value > max) max = value;
                if (value < min) min = value;
                total += value;
            }

            return "median~" + (total / recorder.Count).ToString(CultureInfo.InvariantCulture) +
                   "[" + min.ToString(CultureInfo.InvariantCulture) + ".." +
                   max.ToString(CultureInfo.InvariantCulture) + "]";
        }

        /// <summary>Same as <see cref="Describe"/> but converts nanosecond samples to milliseconds.</summary>
        private static string DescribeMilliseconds(ProfilerRecorder recorder)
        {
            if (!recorder.Valid || recorder.Count == 0)
            {
                return "unavailable";
            }

            long max = long.MinValue;
            long total = 0;
            for (int i = 0; i < recorder.Count; i++)
            {
                long value = recorder.GetSample(i).Value;
                if (value > max) max = value;
                total += value;
            }

            double meanMs = total / (double)recorder.Count / 1_000_000.0;
            double maxMs = max / 1_000_000.0;
            return Format((float)meanMs) + "avg/" + Format((float)maxMs) + "max";
        }

        private static string Format(float value)
        {
            return value.ToString("F1", CultureInfo.InvariantCulture);
        }

        private void Write(string text)
        {
            string path = Path.IsPathRooted(outputPath)
                ? outputPath
                : Path.Combine(Application.dataPath, "..", outputPath);
            File.WriteAllText(path, text);
            Debug.Log("BENCHMARK RESULT\n" + text);
        }
    }
}
