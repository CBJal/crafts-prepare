# Build a per-user Crafts Prepare installer on Windows x64.
# Does not download Python, Inno Setup, FFmpeg, or Gyroflow.
# The native app does not need Node, and this script does not build the optional web UI.
# Does not delete previous build folders. Each run writes a new output directory.
param(
    [string]$OutputRoot
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Assert-Native($Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

function Find-Iscc {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    throw "Inno Setup 6 (ISCC.exe) was not found. Install it yourself from https://jrsoftware.org/isinfo.php and rerun. This script does not download it."
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.12, 3.13, or 3.14 is required on the build machine. This script does not install Python."
}
$versionText = & python -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
Assert-Native "Reading the Python version"
& python -c "import sys; raise SystemExit(0 if (3, 12) <= sys.version_info[:2] <= (3, 14) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python $versionText is outside 3.12-3.14. Crafts Prepare does not install another interpreter."
}
$appVersion = (& python -c @'
import pathlib, re, sys
text = pathlib.Path("backend/app/version.py").read_text(encoding="utf-8")
match = re.search(r'APP_VERSION = "([^"]+)"', text)
if not match:
    sys.exit(1)
print(match.group(1))
'@).Trim()
Assert-Native "Reading APP_VERSION"
if (-not $appVersion) { throw "APP_VERSION was not found." }

$sha = "unknown"
if (Get-Command git -ErrorAction SilentlyContinue) {
    $sha = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { $sha = "unknown" }
}

$venv = Join-Path $Root "packaging\.build-venv"
$python = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $python)) {
    & python -m venv $venv
    Assert-Native "python -m venv packaging\.build-venv"
}
& $python -m pip install -r backend\requirements.lock -r packaging\requirements-build.txt
Assert-Native "pip install build dependencies"

& $python packaging\make_icon.py
Assert-Native "make_icon.py"

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
if ($OutputRoot) {
    $outRoot = $OutputRoot
} else {
    $outRoot = Join-Path $Root "packaging\output\$stamp"
}
if (Test-Path $outRoot) {
    throw "Refusing to overwrite $outRoot. Previous build attempts are kept, and this script does not delete them."
}
New-Item -ItemType Directory -Path $outRoot | Out-Null
$dist = Join-Path $outRoot "dist"
$work = Join-Path $outRoot "work"
$licenseDir = Join-Path $outRoot "licenses"
$installerOut = Join-Path $outRoot "installer"
New-Item -ItemType Directory -Path $dist, $work, $installerOut | Out-Null

& $python packaging\collect_licenses.py --output $licenseDir
Assert-Native "collect_licenses.py"
$versionFile = Join-Path $outRoot "file_version_info.txt"
& $python packaging\write_version_info.py --output $versionFile
Assert-Native "write_version_info.py"
$env:CRAFTS_LICENSE_DIR = $licenseDir
$env:CRAFTS_VERSION_FILE = $versionFile

& $python -m PyInstaller packaging\crafts-prepare.spec --noconfirm --distpath $dist --workpath $work
Assert-Native "PyInstaller"
$bundle = Join-Path $dist "CraftsPrepare"
$exePath = Join-Path $bundle "CraftsPrepare.exe"
if (-not (Test-Path $exePath)) {
    throw "PyInstaller did not write $exePath."
}

$builtAt = [DateTime]::UtcNow.ToString("o")
$info = @{
    name = "Crafts Prepare"
    version = $appVersion
    source_sha = $sha
    built_at = $builtAt
    python = $versionText
    executable = $exePath
    output_root = $outRoot
} | ConvertTo-Json
$infoPath = Join-Path $bundle "build-info.json"
Set-Content -Path $infoPath -Value $info -Encoding utf8

$iscc = Find-Iscc
& $iscc @(
    "/DAppVersion=$appVersion",
    "/DSourceDir=$bundle",
    "/DOutputDir=$installerOut",
    (Join-Path $Root "packaging\crafts-prepare.iss")
)
Assert-Native "ISCC"

$setup = Join-Path $installerOut "CraftsPrepare-Setup-$appVersion.exe"
if (-not (Test-Path $setup)) {
    throw "Installer was not written to $setup."
}
Copy-Item $infoPath (Join-Path $installerOut "build-info.json")
$setupHash = (Get-FileHash -Algorithm SHA256 $setup).Hash.ToLower()
$exeHash = (Get-FileHash -Algorithm SHA256 $exePath).Hash.ToLower()
@(
    "$setupHash  CraftsPrepare-Setup-$appVersion.exe",
    "$exeHash  CraftsPrepare.exe"
) | Set-Content -Path (Join-Path $installerOut "SHA256SUMS.txt") -Encoding ascii

Write-Host "Output:    $outRoot"
Write-Host "Executable: $exePath"
Write-Host "Installer: $setup"
Write-Host "SHA256:    $setupHash"
Write-Host "Source:    $sha"
