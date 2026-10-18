# Start Crafts Prepare on 127.0.0.1.
# Python 3.12, 3.13, or 3.14. Native command failures stop the script.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Assert-Native($Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.12, 3.13, or 3.14 is required. Crafts Prepare does not install another Python for you."
}
$version = & python -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
Assert-Native "Reading the Python version"
& python -c "import sys; raise SystemExit(0 if (3, 12) <= sys.version_info[:2] <= (3, 14) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Python $version is outside the supported range. Use Python 3.12, 3.13, or 3.14. Crafts Prepare does not install another interpreter."
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    throw "Install ffmpeg and ffprobe yourself. Crafts Prepare does not download them."
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & python -m venv .venv
    Assert-Native "python -m venv .venv"
}
& .\.venv\Scripts\python.exe -m pip install -r backend\requirements.lock
Assert-Native "pip install -r backend\requirements.lock"

if (-not (Test-Path "frontend\dist\index.html")) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "npm is required to build the web UI the first time."
    }
    Push-Location frontend
    & npm ci
    Assert-Native "npm ci"
    & npm run build
    Assert-Native "npm run build"
    Pop-Location
}

$env:CRAFTS_HOST = "127.0.0.1"
if (-not $env:CRAFTS_PORT) { $env:CRAFTS_PORT = "8765" }
Set-Location "$Root\backend"
& "$Root\.venv\Scripts\python.exe" -m app
Assert-Native "Crafts Prepare"
