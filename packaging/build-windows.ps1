# Build a per-user Crafts Prepare installer on Windows x64.
# Does not download Python, Node, Inno Setup, FFmpeg, or Gyroflow.
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
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm is required to build the web UI. This script does not install Node.js."
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

Push-Location frontend
& npm ci
Assert-Native "npm ci"
& npm run build
Assert-Native "npm run build"
Pop-Location
if (-not (Test-Path "frontend\dist\index.html")) {
    throw "frontend\dist\index.html is missing after npm run build."
}

& $python packaging\make_icon.py
Assert-Native "make_icon.py"

$dist = Join-Path $Root "packaging\dist"
$work = Join-Path $Root "packaging\build"
if (Test-Path $dist) { Remove-Item -Recurse -Force $dist }
& $python -m PyInstaller packaging\crafts-prepare.spec --noconfirm --distpath $dist --workpath $work
Assert-Native "PyInstaller"
$bundle = Join-Path $dist "CraftsPrepare"
if (-not (Test-Path (Join-Path $bundle "CraftsPrepare.exe"))) {
    throw "PyInstaller did not write packaging\dist\CraftsPrepare\CraftsPrepare.exe."
}

$builtAt = [DateTime]::UtcNow.ToString("o")
$info = @{
    name = "Crafts Prepare"
    version = $appVersion
    source_sha = $sha
    built_at = $builtAt
    python = $versionText
} | ConvertTo-Json
$infoPath = Join-Path $bundle "build-info.json"
Set-Content -Path $infoPath -Value $info -Encoding utf8

$iscc = Find-Iscc
$output = Join-Path $Root "packaging\output"
New-Item -ItemType Directory -Force -Path $output | Out-Null
& $iscc "/DAppVersion=$appVersion" "/DSourceDir=$bundle" "/DOutputDir=$output" (Join-Path $Root "packaging\crafts-prepare.iss")
Assert-Native "ISCC"

$setup = Join-Path $output "CraftsPrepare-Setup-$appVersion.exe"
if (-not (Test-Path $setup)) {
    throw "Installer was not written to $setup."
}
Copy-Item $infoPath (Join-Path $output "build-info.json") -Force
$setupHash = (Get-FileHash -Algorithm SHA256 $setup).Hash.ToLower()
$exeHash = (Get-FileHash -Algorithm SHA256 (Join-Path $bundle "CraftsPrepare.exe")).Hash.ToLower()
@(
    "$setupHash  CraftsPrepare-Setup-$appVersion.exe",
    "$exeHash  CraftsPrepare/CraftsPrepare.exe"
) | Set-Content -Path (Join-Path $output "SHA256SUMS.txt") -Encoding ascii

Write-Host "Installer: $setup"
Write-Host "SHA256:    $setupHash"
Write-Host "Source:    $sha"
