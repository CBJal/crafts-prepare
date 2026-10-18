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

## Local observation (this implementation session)

- Ubuntu ffmpeg 6.1.1-3ubuntu5, GPL build, with libx264, libx265, prores_ks, dnxhd, vidstab, deshake, lut3d, and eq.
- Synthetic fixtures generated in tests: H.264 8-bit AAC MP4, HEVC 10-bit AAC MP4, ProRes 422 10-bit PCM MOV, DNxHR HQ PCM MXF, a 30000/1001 MP4, and a crop-shake MP4. No camera clip was used.
- `crafts-telemetry` linked telemetry-parser 0.2.6 and reported `Unsupported file format` on the synthetic H.264 MP4.
- rustc 1.83 cannot build telemetry-parser git master (edition 2024) or an unpinned crates.io resolution that pulls indexmap 2.14. The helper pins `telemetry-parser =0.2.6`, `serde_json =1.0.120`, and `indexmap =2.2.6`.

## Local observation supplied by the task brief (not re-checked here)

- A Sony-signed Catalyst Prepare V3 3.0.0.641 installer exists on the user's machine and was not executed.
- A static PE resource inventory was done on that installer. That does not describe the running app.
- Catalyst Browse 2026.1.1.100 is a separate install. Its Qt DLLs do not establish Prepare V3's architecture.
- The user's FFmpeg 9.0.2 is GPL-enabled with libvidstab, libx264, libx265, and AMF. It is not in this environment and is not bundled.

## Inference

- Output location for Gyroflow is the documented `--out-params` JSON keys `output_path`, `output_folder`, and `output_filename`. This session did not run a real Gyroflow binary, so that mapping is unverified against an installed Gyroflow.

## Unknown

- Whether a future Gyroflow release renames those JSON keys.
- Whether telemetry-parser 0.2.6 can read current Sony embedded metadata. It was not given a real camera file.
- Native Windows UI behavior. This session did not drive the Windows desktop.
