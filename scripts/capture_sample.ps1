<#
.SYNOPSIS
    Renders the BasicMap sample to a PNG, start to finish.

.DESCRIPTION
    Starts uvicorn against the published artifacts, copies the package sample into the dev
    harness the way Package Manager's "Import Sample" would, runs Unity in batchmode to open the
    scene and render it, then shuts the server down again.

    Unity runs *without* -nographics, because the point is to render, and *without* -quit,
    because the load is asynchronous and Assets/Editor/CaptureSample.cs decides when it is done.

    The build and publish steps are not run here -- they are slow, they need Node, and they are
    documented in Samples~/BasicMap/README.md. This assumes a published revision already exists.

.EXAMPLE
    pwsh scripts/capture_sample.ps1 -Output docs/phases/faz-4-ornek-sahne.png
#>
[CmdletBinding()]
param(
    [string]$Output = "docs/phases/faz-4-ornek-sahne.png",
    [string]$Lod = "high",
    [string]$DatasetDir = "services/geometry-api/data",
    [string]$UnityExe = "C:/Program Files/Unity/Hub/Editor/6000.1.1f1/Editor/Unity.exe",
    [int]$Port = 8000,
    [int]$Width = 1600,
    [int]$Height = 1200
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$projectPath = Join-Path $repoRoot "unity/TerritoryKitDev"
$logPath = Join-Path $repoRoot "unity/TerritoryKitDev/Logs/capture.log"
# 127.0.0.1 rather than localhost: on Windows "localhost" resolves to ::1 first, and uvicorn
# binds IPv4 only, so the health poll would time out against a server that is already up.
$baseUrl = "http://127.0.0.1:$Port"

function Resolve-Python {
    $venv = Join-Path $repoRoot "services/geometry-api/.venv/Scripts/python.exe"
    if (Test-Path $venv) { return $venv }
    return "python"
}

# The sample lives in Samples~, which Unity deliberately does not import. Copying it in is
# exactly what Package Manager's "Import Sample" button does; the copy is gitignored.
$sampleSource = Join-Path $repoRoot "packages/com.oguzhanonur.territorykit-unity/Samples~/BasicMap"
$sampleTarget = Join-Path $projectPath "Assets/Samples/BasicMap"
if (Test-Path $sampleTarget) { Remove-Item -Recurse -Force $sampleTarget }
New-Item -ItemType Directory -Force -Path $sampleTarget | Out-Null
Copy-Item -Path (Join-Path $sampleSource "*") -Destination $sampleTarget -Recurse -Force

$python = Resolve-Python
$artifacts = Join-Path $repoRoot "$DatasetDir/artifacts"
if (-not (Test-Path $artifacts)) {
    throw "no published artifacts at $artifacts; run build_lod.py and publish_dataset.py first (see Samples~/BasicMap/README.md)"
}

$env:GEOMETRY_API_ARTIFACTS_DIR = $artifacts
$env:GEOMETRY_API_CACHE_DIR = Join-Path $repoRoot "$DatasetDir/cache"
$env:GEOMETRY_API_DATASET_DIR = Join-Path $repoRoot "$DatasetDir/datasets"

Write-Host "starting uvicorn on port $Port"
$server = Start-Process -FilePath $python `
    -ArgumentList @("-m", "uvicorn", "geometry_api.main:app", "--host", "127.0.0.1", "--port", "$Port",
                    "--app-dir", (Join-Path $repoRoot "services/geometry-api/src")) `
    -PassThru -NoNewWindow

try {
    $ready = $false
    foreach ($attempt in 1..40) {
        Start-Sleep -Milliseconds 500
        try {
            $health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 2
            if ($health.status -eq "ok") { $ready = $true; break }
        } catch {
            # Not up yet; keep polling until the attempt budget runs out.
        }
    }

    if (-not $ready) { throw "uvicorn did not become healthy on $baseUrl" }
    Write-Host "server healthy; rendering with Unity"

    $outputFull = Join-Path $repoRoot $Output
    # Start-Process -Wait rather than the call operator: Unity.exe is a GUI-subsystem binary, so
    # "& $UnityExe" returns immediately and leaves $LASTEXITCODE empty. Without the wait the
    # finally block below would stop uvicorn while Unity was still opening the project.
    $unity = Start-Process -FilePath $UnityExe -Wait -PassThru -NoNewWindow -ArgumentList @(
        "-batchmode",
        "-projectPath", $projectPath,
        "-executeMethod", "CaptureSample.Run",
        "-captureOutput", $outputFull,
        "-captureBaseUrl", $baseUrl,
        "-captureLod", $Lod,
        "-captureWidth", "$Width",
        "-captureHeight", "$Height",
        "-logFile", $logPath
    )
    $unityExit = $unity.ExitCode

    if (Test-Path $logPath) {
        Select-String -Path $logPath -Pattern "CAPTURE_OK|CAPTURE_FAILED" | ForEach-Object {
            Write-Host $_.Line
        }
    }

    if ($unityExit -ne 0) { throw "Unity exited $unityExit; see $logPath" }
    Write-Host "wrote $outputFull"
}
finally {
    if ($server -and -not $server.HasExited) {
        Write-Host "stopping uvicorn"
        Stop-Process -Id $server.Id -Force -Confirm:$false
    }
}
