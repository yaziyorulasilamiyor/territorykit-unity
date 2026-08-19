<#
.SYNOPSIS
    Measures draw calls, batches and frame rate for the viewport streamer, end to end.

.DESCRIPTION
    Starts uvicorn against the published artifacts, builds a Windows Development Player from
    unity/TerritoryKitDev, runs it against the live server, prints the numbers and shuts the
    server down again.

    Why a built player rather than a PlayMode test: the render-statistics counters
    (`ProfilerRecorder(ProfilerCategory.Render, "Draw Calls Count")`, and the memory counters
    too) report `Valid == true` and collect **zero samples** under `-batchmode`. The same is true
    of the legacy `UnityStats.drawCalls` API. A Development Player has a real render loop and
    reports them normally, so this is what makes the phase 5 draw-call figure measurable
    automatically instead of by hand in the editor.

    Two things the player needs that the editor does not:
      * `Unlit/Color` must be in Project Settings > Graphics > Always Included Shaders, or
        `Shader.Find` returns null in the build and nothing draws. BuildBenchmark.Build adds it.
      * `QualitySettings.vSyncCount = 0` *and* `Application.targetFrameRate` — setting only the
        first still pins the result at exactly 60.0 on Windows standalone.

.EXAMPLE
    pwsh scripts/measure_render.ps1
#>
[CmdletBinding()]
param(
    [string]$UnityExe = "C:/Program Files/Unity/Hub/Editor/6000.1.1f1/Editor/Unity.exe",
    [string]$ProjectPath = "unity/TerritoryKitDev",
    [string]$DatasetDir = "services/geometry-api/data",
    [string]$DatasetId = "tr-adm1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$project = Join-Path $repoRoot $ProjectPath
$buildDir = Join-Path $repoRoot "unity/BenchmarkBuild"
$exePath = Join-Path $buildDir "Benchmark.exe"
$resultPath = Join-Path $repoRoot "benchmark-result.txt"
# 127.0.0.1 rather than localhost: on Windows "localhost" resolves to ::1 first and uvicorn
# binds IPv4 only, so the player would time out against a server that is already up.
$baseUrl = "http://127.0.0.1:$Port"

function Resolve-Python {
    $venv = Join-Path $repoRoot "services/geometry-api/.venv/Scripts/python.exe"
    if (Test-Path $venv) { return $venv }
    return "python"
}

$artifacts = Join-Path $repoRoot "$DatasetDir/artifacts"
if (-not (Test-Path $artifacts)) {
    throw "no published artifacts at $artifacts; run build_lod.py and publish_dataset.py first (see Samples~/BasicMap/README.md)"
}

$env:GEOMETRY_API_ARTIFACTS_DIR = $artifacts
$env:GEOMETRY_API_CACHE_DIR = Join-Path $repoRoot "$DatasetDir/cache"
$env:GEOMETRY_API_DATASET_DIR = Join-Path $repoRoot "$DatasetDir/datasets"

$python = Resolve-Python
Write-Host "starting geometry API on $baseUrl"
$server = Start-Process -FilePath $python `
    -ArgumentList @("-m", "uvicorn", "geometry_api.main:app", "--host", "127.0.0.1",
                    "--port", "$Port", "--app-dir", (Join-Path $repoRoot "services/geometry-api/src")) `
    -PassThru -NoNewWindow

try {
    $healthy = $false
    foreach ($attempt in 1..30) {
        Start-Sleep -Seconds 1
        try {
            Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 2 | Out-Null
            $healthy = $true
            break
        } catch { }
    }

    if (-not $healthy) { throw "geometry API did not become healthy on $baseUrl" }
    Write-Host "server is up"

    if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir }
    New-Item -ItemType Directory -Force -Path $buildDir | Out-Null

    Write-Host "building development player"
    & $UnityExe -batchmode -quit -projectPath $project `
        -executeMethod BuildBenchmark.Build `
        -benchmarkOutput $exePath `
        -benchmarkResult $resultPath `
        -benchmarkBaseUrl $baseUrl `
        -benchmarkDataset $DatasetId `
        -logFile (Join-Path $repoRoot "Logs/benchmark-build.log")
    if ($LASTEXITCODE -ne 0) { throw "player build failed; see Logs/benchmark-build.log" }

    if (Test-Path $resultPath) { Remove-Item $resultPath }

    Write-Host "running player"
    # Windowed and a fixed size, so the measurement does not depend on the desktop resolution.
    & $exePath -logFile (Join-Path $repoRoot "Logs/benchmark-player.log") `
        -screen-width 1600 -screen-height 1200 -screen-fullscreen 0 | Out-Null

    if (-not (Test-Path $resultPath)) { throw "player wrote no result; see Logs/benchmark-player.log" }

    Write-Host ""
    Write-Host "=== benchmark result ==="
    Get-Content $resultPath
}
finally {
    if ($server -and -not $server.HasExited) {
        Write-Host "stopping geometry API"
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
}
