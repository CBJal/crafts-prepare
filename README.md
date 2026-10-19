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

The development start script still needs Python and Node. The installer does not. On a Windows x64 build machine that already has Python 3.12–3.14, Node.js with npm, and Inno Setup 6:

```
powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
```

The script does not download those tools, FFmpeg, or Gyroflow. It does not delete earlier build folders. Each run writes a new directory:

- `packaging\output\<yyyyMMdd-HHmmss>\dist\CraftsPrepare\CraftsPrepare.exe` — the windowed app
- `packaging\output\<yyyyMMdd-HHmmss>\work\` — PyInstaller work files
- `packaging\output\<yyyyMMdd-HHmmss>\licenses\` — Python, dependency, PyInstaller, and React license texts copied into the app
- `packaging\output\<yyyyMMdd-HHmmss>\installer\CraftsPrepare-Setup-0.1.0.exe` — the per-user installer
- `packaging\output\<yyyyMMdd-HHmmss>\installer\SHA256SUMS.txt` — SHA-256 of the installer and `CraftsPrepare.exe`
- `packaging\output\<yyyyMMdd-HHmmss>\installer\build-info.json` — version and the git SHA that was built

Pass `-OutputRoot` to choose that directory. If it already exists, the script stops and leaves it in place. `CraftsPrepare.exe` carries FileVersion and ProductVersion `0.1.0`. The build is unsigned. Windows may show a SmartScreen prompt until you sign the installer yourself.

Install for the current user. Shortcuts go on the Start menu, and a desktop shortcut is optional. Uninstall removes the program files only. Settings, cache, logs, and projects stay in `%LOCALAPPDATA%\CraftsPrepare` and wherever you saved projects. The installed app ignores `CRAFTS_HOST`, `CRAFTS_PORT`, `CRAFTS_SETTINGS_PATH`, `CRAFTS_DATA_DIR`, `CRAFTS_LOG_PATH`, and `CRAFTS_DISABLE_WORKER`. Development still honors those. A separate frozen profile for QA is `%LOCALAPPDATA%\CraftsPrepare-<token>` when you start the exe with `--profile <token>` or set `CRAFTS_PROFILE` to a short token such as `qa`.

FFmpeg, ffprobe, and Gyroflow stay separate programs. The app looks on PATH and in common install locations, or uses the paths you set in Capabilities. If they are missing, the window still opens and says so.

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
