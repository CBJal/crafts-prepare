# Sources and claim labels

## Public documentation (read, not copied into the app)

- Catalyst Prepare help, current version 3: https://helpguide.sony.net/di-app/cpv3/v1/en/index.html
- Interface overview: https://helpguide.sony.net/di-app/cpv3/v1/en/contents/0100_app_screen.html
- Stabilization: https://helpguide.sony.net/di-app/cpv3/v1/en/contents/0401_stablization.html
- Export: https://helpguide.sony.net/di-app/cpv3/v1/en/contents/0601_transcode_clip.html
- Recording-media formats: https://helpguide.sony.net/di-app/cpv3/v1/en/contents/0802_video_device.html
- Metadata compatibility note: https://support.d-imaging.sony.co.jp/www/cscs/function/catalyst.php?area=gb&lang=en
- Gyroflow CLI: https://docs.gyroflow.xyz/app/advanced-usage/command-line-cli
- Gyroflow Sony camera limitations: https://docs.gyroflow.xyz/app/getting-started/supported-cameras/sony
- GCSV format: https://docs.gyroflow.xyz/app/technical-details/gcsv-format
- telemetry-parser: https://github.com/AdrianEddy/telemetry-parser
- FFmpeg legal and filters: https://ffmpeg.org/legal.html , https://ffmpeg.org/ffmpeg-filters.html

The Gyroflow CLI progress lines (`Rendering progress: current/total frames (percent%)`) were confirmed from the public `src/cli.rs` as an interface, so the adapter can read them. That source was not copied.

Public v1.6.3 `src/cli.rs` merges `--out-params` into the default output object, and the comment on those defaults says the project file overrides them. Public `src/rendering/render_queue.rs` then replaces render options from the loaded project's `output` object (`output_folder` as a file URI or absolute path, then `output_filename`). A preset with an `output` object is applied after that. `output_path` remains a backwards-compatible field read before those two. This repository does not copy that implementation.

## Local observation (this implementation session)

- Ubuntu ffmpeg 6.1.1-3ubuntu5, GPL build, with libx264, libx265, prores_ks, dnxhd, vidstab, deshake, lut3d, and eq.
- Official Gyroflow 1.6.3 `Gyroflow-linux64.tar.gz` was downloaded from the GitHub release and was not committed. Its SHA-256 is `98625e3ce318964d49d3c66f2dc302a9e6c4c138233d9aa6cbd986f687c421c3`. The release notes do not attach a separate checksum file. `--version` prints debug lines and then `Gyroflow v1.6.3 (gh2453)`. On a synthetic 320×180 clip, the unmodified exported project queued `project-output/from-project.mp4` even though `--out-params` named a different folder, and began `from-project.mp4.tmp` there. After the adapter staged that project, the same binary queued `stage/staged.mp4` and began `staged.mp4.tmp` there. The original project bytes were unchanged. No finished video file was produced: OpenCL and GPU are unavailable here, and the process was stopped during frame 1 of 10. A relative `videofile` and gyro `filepath` were rewritten to absolute paths; Gyroflow then opened the gyro URL beside the original project. A preset launch exited before writing video. No camera file was used.
- Synthetic fixtures generated in tests: H.264 8-bit AAC MP4, HEVC 10-bit AAC MP4, ProRes 422 10-bit PCM MOV, DNxHR HQ PCM MXF, a 30000/1001 MP4, and a crop-shake MP4. No camera clip was used.
- `crafts-telemetry` linked telemetry-parser 0.2.6 and reported `Unsupported file format` on the synthetic H.264 MP4.
- rustc 1.83 cannot build telemetry-parser git master (edition 2024) or an unpinned crates.io resolution that pulls indexmap 2.14. The helper pins `telemetry-parser =0.2.6`, `serde_json =1.0.120`, and `indexmap =2.2.6`.

## Local observation supplied by the task brief (not re-checked here)

- A Sony-signed Catalyst Prepare V3 3.0.0.641 installer exists on the user's machine and was not executed.
- A static PE resource inventory was done on that installer. That does not describe the running app.
- Catalyst Browse 2026.1.1.100 is a separate install. Its Qt DLLs do not establish Prepare V3's architecture.
- The user's FFmpeg 9.0.2 is GPL-enabled with libvidstab, libx264, libx265, and AMF. It is not in this environment and is not bundled.

## Inference

- The project-output override above was observed. Preset output is applied later in the public CLI, so a preset that contains an output section is staged the same way. That preset path was not confirmed by a finished 1.6.3 render in this environment.

## Unknown

- Whether a future Gyroflow release renames those JSON keys.
- Whether telemetry-parser 0.2.6 can read current Sony embedded metadata. It was not given a real camera file.
- Native Windows UI behavior. This session did not drive the Windows desktop.
