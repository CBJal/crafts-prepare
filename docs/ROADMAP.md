# Roadmap

Current behavior is listed in `docs/CAPABILITIES.md`. This file is the gap list.

## Done in this version

Local library, probe, trim, flags, notes, markers, a sequential export queue, H.264 and ProRes presets, explicit bit depth, visual vidstab/deshake, a Gyroflow CLI adapter, and a `.gcsv` reader.

## Next, still local

- A file dialog that still sends a real path (Windows folder picker) instead of a typed path only.
- Waveform and vectorscope. Not started.
- Audio channel mapping and loudness. Only on/off and one encoder per preset.
- Batch apply of one adjustment recipe to many clips. The queue already accepts one job per clip.
- Stronger preview: a short proxy that includes `eq` and `lut3d`, generated with the same filter builder. Stills already do. The HTML5 player does not.
- Drop-frame timecode display. The current frame label is a convenience counter from the rational rate.
- Per-job parallelism as an explicit opt-in. The default stays sequential.

## Gyro and metadata, blocked on real inputs

- A real Gyroflow render. Needs the user's Gyroflow install and a motion-bearing file. The adapter will not invent success.
- Sony embedded gyro via a current telemetry-parser. crates.io 0.2.6 is what builds here. Git master needs Rust edition 2024. No synthetic file can prove camera support.
- Lens profiles. The `.gcsv` reader keeps `lensprofile`, `lens_info`, and readout fields. It does not apply them.

## Explicitly out of scope

- Sony installers, binaries, color science, accounts, and license checks.
- Copying Gyroflow's GPL code into this app.
- Cloud hosting, accounts, or payment gates for Crafts Prepare features.
