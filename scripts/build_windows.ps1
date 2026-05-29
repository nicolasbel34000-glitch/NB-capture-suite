param(
    [string]$Name = "NBCapture",
    [ValidateSet("capture", "subtitles-cloud", "subtitles-github")]
    [string]$App = "capture"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

python -m pip install -r requirements-dev.txt

$releaseDir = Join-Path $repoRoot "release"
$buildDir = Join-Path $repoRoot "build"
if (!(Test-Path $releaseDir)) {
    New-Item -ItemType Directory -Path $releaseDir | Out-Null
}

$entrypoint = switch ($App) {
    "capture" { "launch.py" }
    "subtitles-cloud" { "launch_subtitles_express_cloud.py" }
    "subtitles-github" { "launch_subtitles_express.py" }
}

if ($Name -eq "NBCapture" -and $App -ne "capture") {
    $Name = if ($App -eq "subtitles-cloud") { "SousTitresExpress" } else { "SousTitresExpress-GitHub" }
}

$pyinstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--windowed",
    "--onefile",
    "--name",
    $Name,
    "--distpath",
    $releaseDir,
    "--workpath",
    $buildDir,
    "--specpath",
    $buildDir,
    $entrypoint
)

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ffmpeg) {
    $pyinstallerArgs += @("--add-binary", "$($ffmpeg.Source);.")
    Write-Host "Bundling ffmpeg.exe inside the executable."
} else {
    Write-Warning "ffmpeg.exe was not found on PATH. Video, audio, and webcam capture require ffmpeg."
}

python -m PyInstaller @pyinstallerArgs

$outputExe = Join-Path $releaseDir "$Name.exe"
if (!(Test-Path $outputExe)) {
    throw "Expected build output was not created: $outputExe"
}

Write-Host "Windows build ready for testing:"
Write-Host $outputExe
