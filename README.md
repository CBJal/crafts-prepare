# Crafts Prepare

Crafts Prepare is a local video-preparation bench for inspecting, trimming, adjusting, visually stabilizing, and batch-exporting footage. It runs on your machine. It does not upload media, create an account, or charge for its own features.

It is an independent app. Sony Catalyst Prepare is a separate product. Public Catalyst help pages were used as a feature checklist only. This repository does not contain Sony code, Sony artwork, or camera footage.

## What you need

- Windows 10 or later, or another OS with Python 3.12, 3.13, or 3.14
- Python 3.12, 3.13, or 3.14. Python 3.14 is supported with the pinned wheels in `backend/requirements.lock` (`pydantic` 2.14.1 / `pydantic-core` 2.50.1). Do not install another system Python just to run this app.
- Node.js 20 or newer, the first time, to build the web UI
- ffmpeg and ffprobe on `PATH`, installed by you

Optional:

- Gyroflow, installed by you, if you want gyro stabilization through its public CLI
- A build of `tools/telemetry-inspect` if you want a telemetry-parser 0.2.6 inspection

Crafts Prepare does not download or redistribute those tools. A missing tool is reported as unavailable. That is not a subscription lock.

## Windows installer

Version 0.2.1 is a native Qt desktop application. The approved silver C mark is `assets/brand/app-icon.png` and `assets/brand/app-icon.ico`. `packaging/make_icon.py` copies that ICO into `packaging/icon.ico`. It does not draw a substitute. See `docs/BRANDING.md`. It does not open a browser and it does not start the loopback web server. `python -m app` is still the optional development web UI and still needs Node the first time you build `frontend/dist`.

On a Windows x64 build machine that already has Python 3.12–3.14 and Inno Setup 6:

```
powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
```

The script does not download Python, Inno Setup, FFmpeg, or Gyroflow, and it does not need Node. It does not delete earlier build folders. Each run writes a new directory:

- `packaging\output\<yyyyMMdd-HHmmss>\dist\CraftsPrepare\CraftsPrepare.exe`
- `packaging\output\<yyyyMMdd-HHmmss>\work\`
- `packaging\output\<yyyyMMdd-HHmmss>\licenses\` — Python, Qt/LGPL, PySide6, and PyInstaller license texts
- `packaging\output\<yyyyMMdd-HHmmss>\installer\CraftsPrepare-Setup-0.2.1.exe`
- `packaging\output\<yyyyMMdd-HHmmss>\installer\SHA256SUMS.txt`
- `packaging\output\<yyyyMMdd-HHmmss>\installer\build-info.json`

Pass `-OutputRoot` to choose that directory. If it already exists, the script stops and leaves it in place. `CraftsPrepare.exe` carries FileVersion and ProductVersion `0.2.1`. The build is unsigned. Windows may show a SmartScreen prompt until you sign the installer yourself.

The installer uses the same AppId as 0.1.0, so it upgrades `%LOCALAPPDATA%\Programs\Crafts Prepare` and leaves `%LOCALAPPDATA%\CraftsPrepare` settings in place. Uninstall removes program files only. Saved projects stay where you put them.

The installed app ignores `CRAFTS_HOST`, `CRAFTS_PORT`, `CRAFTS_SETTINGS_PATH`, `CRAFTS_DATA_DIR`, `CRAFTS_LOG_PATH`, and `CRAFTS_DISABLE_WORKER`. Development still honors those. `--profile qa` uses `%LOCALAPPDATA%\CraftsPrepare-qa`. A second launch focuses the existing window.

FFmpeg, ffprobe, and Gyroflow stay separate programs. Qt may include its own playback libraries. Those are not the export tools. If the export tools are missing, the window still opens and says so.

An installed smoke test is off unless you pass `--smoke`. It drives the real window. Repeat `--sample` for each clip, or pass `--config` with a JSON file of samples, export, and gyro settings. A required export that does not succeed fails the run. Set `"gyro": {"expect": "failed"}` only when that failure is the control you want.

```
& "$env:LOCALAPPDATA\Programs\Crafts Prepare\CraftsPrepare.exe" --smoke --sample C:\clips\a.mp4 --sample C:\clips\b.mp4 --output C:\clips\out --report C:\clips\out\smoke.json --screenshots C:\clips\out\shots --config C:\clips\smoke.json
```

Example `smoke.json` for 10-bit sources that need an acknowledged ProRes export:

```json
{
  "export": {
    "preset": "prores_422",
    "audio": false,
    "fit": "resize",
    "width": 640,
    "height": 360,
    "acknowledge": true,
    "suffix": "_export",
    "trim_in": 0.2,
    "trim_out": 1.2
  },
  "gyro": {
    "format": "prores_422",
    "audio": false,
    "width": 640,
    "height": 360,
    "suffix": "_gyro",
    "expect": "succeeded"
  },
  "expect": {"min_export_succeeded": 2, "min_gyro_succeeded": 2}
}
```

`trim_in` and `trim_out` are written through the trim controls on every selected clip, then checked against the stored values, before the ordinary export is queued. That export uses 0.2–1.2 seconds. Gyro jobs do not read those trim fields; each one renders the whole clip. `expect.min_export_succeeded` and `expect.min_gyro_succeeded` default to the number of samples. With `"expect": "succeeded"` every required gyro job must publish a file. `"expect": "failed"` is only a negative control. The report records import, theme, project round-trip, viewer `playback.frames` and `playback.position_ms` from the production player, queue rows, `source_sha256_before` and `source_sha256_after`, screenshots, and close. The process exits nonzero when any of those steps is not ok, a required job is missing or failed, or a source hash changes. A second launch of `CraftsPrepare.exe` with no arguments focuses the window already open. It does not start a browser or an HTTP server.

A normal double-click does not show that diagnostic mode. A second launch focuses the window that is already open. `--smoke` exits with the report's status, including 2 when a sample, tool, or required export is missing. If the project file cannot be replaced, the previous file stays in place and the queue pauses instead of dropping a job. Save As to another folder, or free the file, and the queued jobs continue.

## Windows development

1. Install Python 3.12, 3.13, or 3.14 and make sure `python` works in PowerShell.
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
pip install -r backend/requirements.lock
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

Gyro stabilization is a separate queue item. A manual job still accepts a gyro log, a `.gyroflow` project, an optional preset, and an output path. A batch can also send each selected clip's own file to the installed Gyroflow CLI. That path exports parsed metadata into the job's staging directory, continues only when `raw_imu` is a progressing sequence of timestamped gyro vectors, exports a project into that same directory, and renders it there. Parsed metadata larger than 256 MiB is rejected before it is decoded. It does not treat a data stream or another clip's project as motion. It does not apply export trim, exposure, LUT, resize, or vidstab settings. The command includes `--stdout-progress` and `--out-params`. It does not include `--overwrite`, `--watch`, or `--when-done`. A project or preset is copied first and, when it has an output section, that copy's folder and filename are pointed at a private staging file. Gyroflow 1.6.3 uses those project fields instead of `--out-params`. Relative media and gyro paths are resolved from the original project directory. Crafts Prepare probes the staging file and only then publishes it. If Gyroflow or the motion for that clip is missing, that job fails and later jobs still run.

Checkboxes in the library choose the batch. The highlighted clip is only the preview. Batch export uses each clip's saved trim and the shared preset, audio, LUT, geometry, and visual stabilization. Batch gyro offers H.264 MP4 8-bit or ProRes 422 MOV 10-bit, source or explicit size, audio, and an optional preset. Those output choices replace the output section on both the staged project and the staged preset. A preset can still change stabilization. The render is published only when ffprobe matches that choice. Manual `.gyroflow` jobs still change only the output location. If any selected clip is invalid or would collide, nothing in that batch is queued. Cancel batch stops queued and running jobs in that batch. Retry failed requeues only failed jobs.

The theme control is Light, Dark, or System. The choice is stored in this browser. System follows the operating system.

Opening a saved project does not start its queue. Use Retry on a paused job. The app refuses to switch projects while a job is queued or running.

Filter graphs are one `-vf` argument. User paths are not pasted into that graph: a LUT is copied to `grade.cube` in the job directory first. That keeps Windows drive letters, spaces, and apostrophes out of the filter syntax.

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
- Outputs are written to a staging file, probed, then published. Publication is a hard link when the filesystem allows it. Otherwise Windows `os.rename` or Linux `renameat2` with `RENAME_NOREPLACE` is used, both of which leave an existing destination untouched. POSIX `os.rename` is not used, and an empty placeholder is never created at the final name. If neither hard links nor a no-replace rename are available, nothing is published and that attempt's staging file is removed. The source file stays unchanged. A later cleanup deletes a published file only when it is still the same inode this job created.
