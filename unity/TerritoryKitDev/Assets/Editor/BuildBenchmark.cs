using System;
using System.IO;
using TerritoryKitDev.Benchmark;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

/// <summary>
/// Builds a Windows Development Player that runs <see cref="StreamingBenchmark"/> and quits.
/// </summary>
/// <remarks>
/// Exists because the profiler counters this project needs — draw calls, batches, SetPass — are
/// valid but collect zero samples under <c>-batchmode</c>. A Development Player has a real render
/// loop and reports them normally, which makes the phase 5 draw-call figure measurable
/// automatically instead of by hand.
/// </remarks>
public static class BuildBenchmark
{
    private const string SceneAssetPath = "Assets/Benchmark/BenchmarkScene.unity";

    public static void Build()
    {
        string output = ArgumentOr("-benchmarkOutput",
            Path.Combine(Directory.GetCurrentDirectory(), "Build", "Benchmark", "Benchmark.exe"));
        string resultPath = ArgumentOr("-benchmarkResult",
            Path.Combine(Directory.GetCurrentDirectory(), "benchmark-result.txt"));
        string baseUrl = ArgumentOr("-benchmarkBaseUrl", "http://127.0.0.1:8000");
        string datasetId = ArgumentOr("-benchmarkDataset", "tr-adm1");

        EnsureUnlitShaderIsIncludedInBuilds();

        Directory.CreateDirectory(Path.GetDirectoryName(SceneAssetPath));
        Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var host = new GameObject("Benchmark");
        var benchmark = host.AddComponent<StreamingBenchmark>();

        SerializedObject serialized = new SerializedObject(benchmark);
        serialized.FindProperty("baseUrl").stringValue = baseUrl;
        serialized.FindProperty("datasetId").stringValue = datasetId;
        serialized.FindProperty("outputPath").stringValue = resultPath;
        serialized.ApplyModifiedPropertiesWithoutUndo();

        EditorSceneManager.SaveScene(scene, SceneAssetPath);

        var options = new BuildPlayerOptions
        {
            scenes = new[] { SceneAssetPath },
            locationPathName = output,
            target = BuildTarget.StandaloneWindows64,
            targetGroup = BuildTargetGroup.Standalone,
            // Development is what enables the profiler counters this whole harness is for.
            options = BuildOptions.Development | BuildOptions.AllowDebugging
        };

        UnityEditor.Build.Reporting.BuildReport report = BuildPipeline.BuildPlayer(options);
        var summary = report.summary;
        Debug.Log("BUILD RESULT: " + summary.result + " size=" + summary.totalSize +
                  " errors=" + summary.totalErrors + " output=" + output);

        EditorApplication.Exit(
            summary.result == UnityEditor.Build.Reporting.BuildResult.Succeeded ? 0 : 1);
    }

    /// <summary>
    /// Adds <c>Unlit/Color</c> to Always Included Shaders so the built player can find it.
    /// </summary>
    /// <remarks>
    /// <c>Shader.Find</c> only sees shaders the build included, and a built-in shader that no
    /// material in any scene references is stripped — so the benchmark scene, which builds its
    /// material at runtime, rendered nothing at all on the first attempt. This is a property of
    /// the consuming project's build settings rather than of the package, which is why it is
    /// fixed here and documented in the package README rather than worked around in
    /// <c>ViewportStreamer</c>.
    /// </remarks>
    private static void EnsureUnlitShaderIsIncludedInBuilds()
    {
        Shader shader = Shader.Find("Unlit/Color");
        if (shader == null)
        {
            Debug.LogWarning("BUILD: 'Unlit/Color' not found in the editor either; skipping");
            return;
        }

        var graphicsSettings = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(
            "ProjectSettings/GraphicsSettings.asset");
        var serialized = new SerializedObject(graphicsSettings);
        SerializedProperty included = serialized.FindProperty("m_AlwaysIncludedShaders");

        for (int i = 0; i < included.arraySize; i++)
        {
            if (included.GetArrayElementAtIndex(i).objectReferenceValue == shader)
            {
                return;
            }
        }

        included.InsertArrayElementAtIndex(included.arraySize);
        included.GetArrayElementAtIndex(included.arraySize - 1).objectReferenceValue = shader;
        serialized.ApplyModifiedProperties();
        AssetDatabase.SaveAssets();
        Debug.Log("BUILD: added 'Unlit/Color' to Always Included Shaders");
    }

    private static string ArgumentOr(string name, string fallback)
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
