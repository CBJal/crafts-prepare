# Crafts Prepare

Crafts Prepare is a local video-preparation bench for inspecting, trimming, adjusting, visually stabilizing, and batch-exporting footage. It runs on your machine. It does not upload media, create an account, or charge for its own features.

It is an independent app. Sony Catalyst Prepare is a separate product. Public Catalyst help pages were used as a feature checklist only. This repository does not contain Sony code, Sony artwork, or camera footage.

## What you need

- Windows 10 or later, or another OS with Python 3.11+
- Python 3.11 or newer
- Node.js 20 or newer, the first time, to build the web UI
- ffmpeg and ffprobe on `PATH`, installed by you

Optional:

- Gyroflow, installed by you, if you want gyro stabilization through its public CLI
- A build of `tools/telemetry-inspect` if you want a telemetry-parser 0.2.6 inspection

Crafts Prepare does not download or redistribute those tools. A missing tool is reported as unavailable. That is not a subscription lock.

## Windows

1. Install Python 3.11+ and make sure `python` works in PowerShell.
2. Install ffmpeg and confirm `ffmpeg -version` and `ffprobe -version`.
3. Install Node.js if `frontend\dist\index.html` is not already built.
4. In this folder, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

5. Open http://127.0.0.1:8765

The server binds to `127.0.0.1` only. `CRAFTS_PORT` can change the port. `CRAFTS_HOST` may only be `127.0.0.1` or `localhost`.

## macOS or Linux

```bash
chmod +x scripts/start.sh
./scripts/start.sh
```

Then open http://127.0.0.1:8765

## Develop

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cd frontend && npm ci && npm run build && cd ..
cd backend && python -m pytest
```

Tests generate short synthetic videos with the local ffmpeg. They skip a preset when that encoder is missing and print the skip reason. They do not use camera files.

## Using the bench

1. Type a file or folder path and choose Import. Supported containers are whatever ffprobe can decode. `.mp4`, `.mov`, `.mxf`, `.mts`, and `.m2ts` are the folder candidates.
2. Read the inspector. Data streams are listed as ffprobe saw them. An empty data list is not a claim that the file has no gyro.
3. Set in and out in seconds. The frame counter is a display derived from the rational frame rate.
4. Set OK, KEEP, or NG, notes, and the adjustment values.
5. Choose a destination folder, a filename suffix, and a preset. Queue export.
6. If the preset's bit depth differs from the source, the queue refuses until you acknowledge the conversion. The preset's pixel format is what ffmpeg is given.
7. Save the project as a `*.crafts.json` file. Source clips are not modified.

Visual stabilization has two modes:

- **vidstab two-pass** sends shakiness and accuracy to `vidstabdetect`, then smoothing, crop, zoom, and optzoom to `vidstabtransform`.
- **deshake** sends `rx` and `ry` to the deshake filter and is labeled as a visual fallback.

Neither mode is gyro stabilization. "Fill vidstab auto values" writes the documented numbers (shakiness 5, accuracy 15, smoothing 10, crop black, zoom 0, optzoom 2) into those fields.

Gyro stabilization is a separate queue item that runs an external Gyroflow executable. You pass the media, a `.gcsv` or other gyro log, a `.gyroflow` project, or a preset file, plus an output path. The command includes `--stdout-progress` and `--out-params`. It does not include `--overwrite`, `--watch`, or `--when-done`. If Gyroflow or the motion file is missing, the job fails and no finished file is published.

## Optional telemetry helper

```bash
cd tools/telemetry-inspect
cargo build --release
```

Point Capabilities at `target/release/crafts-telemetry` (or `crafts-telemetry.exe`). The crate is telemetry-parser 0.2.6, MIT OR Apache-2.0, Adrian Eddy. On rustc 1.83 the helper pins `serde_json` 1.0.120 and `indexmap` 2.2.6 so the build does not pull an edition-2024 indexmap. A parse of a synthetic file that the crate rejects is an unsupported-file result, not evidence about a camera.

## Layout

- `backend/app` — FastAPI service
- `frontend` — React UI, built into `frontend/dist` and served by the API
- `tools/telemetry-inspect` — optional telemetry-parser binary
- `docs/CAPABILITIES.md` — what works
- `docs/ROADMAP.md` — what does not
- `docs/SOURCES.md` — public docs, local observations, inference, unknown
- `THIRD_PARTY_NOTICES.md` — licenses

## Safety

- Requests must use a loopback Host. A foreign Origin is rejected.
- Subprocess commands are argument lists.
- Export names are a restricted character set, so a suffix cannot become an ffmpeg filter.
- Outputs are written to a `.crafts-partial` file, probed, then linked into place. Failures delete the partial. Existing outputs and the source file are not overwritten, including symlink aliases.
