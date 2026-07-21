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

$iconPath = switch ($App) {
    "capture" { Join-Path $repoRoot "capture_express\assets\nb_capture.ico" }
    default { Join-Path $repoRoot "capture_express\assets\nb_subtitles.ico" }
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
    $buildDir
)

if (Test-Path $iconPath) {
    $pyinstallerArgs += @("--icon", $iconPath)
}

$assetsDir = Join-Path $repoRoot "capture_express\assets"
if (Test-Path $assetsDir) {
    $pyinstallerArgs += @("--add-data", "$assetsDir;capture_express/assets")
}

$pyinstallerArgs += $entrypoint

$bundledFfmpeg = Join-Path $repoRoot "bin\ffmpeg\ffmpeg.exe"
$ffmpegPath = if (Test-Path -LiteralPath $bundledFfmpeg) {
    $bundledFfmpeg
} else {
    $ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($ffmpeg) { $ffmpeg.Source } else { $null }
}

if ($ffmpegPath) {
    $pyinstallerArgs += @("--add-binary", "$ffmpegPath;.")
    Write-Host "Bundling ffmpeg.exe inside the executable."
} elseif ($App -eq "capture") {
    throw "FFmpeg est introuvable. Le build NBCapture est annule pour eviter une version sans capture video."
}

python -m PyInstaller @pyinstallerArgs

$outputExe = Join-Path $releaseDir "$Name.exe"
if (!(Test-Path $outputExe)) {
    throw "Expected build output was not created: $outputExe"
}

Write-Host "Windows build ready for testing:"
Write-Host $outputExe
