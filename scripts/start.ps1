# Start Crafts Prepare on 127.0.0.1. Requires Python 3.11+ and a caller-installed ffmpeg.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.11+ is required."
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    throw "Install ffmpeg and ffprobe yourself. Crafts Prepare does not download them."
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

if (-not (Test-Path "frontend\dist\index.html")) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "npm is required to build the web UI the first time."
    }
    Push-Location frontend
    npm ci
    npm run build
    Pop-Location
}

$env:CRAFTS_HOST = "127.0.0.1"
if (-not $env:CRAFTS_PORT) { $env:CRAFTS_PORT = "8765" }
Set-Location "$Root\backend"
& "$Root\.venv\Scripts\python.exe" -m app
