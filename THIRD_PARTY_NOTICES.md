# Third-party notices

Crafts Prepare does not bundle FFmpeg, Gyroflow, Sony software, or camera footage.

The optional Windows installer does bundle a Python runtime and the PyInstaller bootloader so the installed app can start without a developer Python. It still does not bundle FFmpeg, Gyroflow, or camera footage.

**Python.** PSF License. Used only as the runtime inside the Windows build. https://docs.python.org/3/license.html

**PyInstaller.** The application libraries are collected from the pins in `backend/requirements.lock`. The bootloader is GPL-2.0-or-later with the PyInstaller exception that allows using it to freeze and distribute a program that is not itself GPL. https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt Build pin: `packaging/requirements-build.txt`.

**tkinter / Tcl/Tk.** Used for the installed app's Open/Quit window. The Windows python.org builds ship Tcl/Tk under their own license, which PyInstaller collects. Not used by the development server.

## Application dependencies

| Component | License | Role |
| --- | --- | --- |
| FastAPI | MIT | Local HTTP API |
| Uvicorn | BSD-3-Clause | Loopback server |
| Pydantic | MIT | Request validation |
| Starlette | BSD-3-Clause | HTTP toolkit used by FastAPI |
| HTTPX | BSD-3-Clause | Test client |
| pytest | MIT | Tests |
| React | MIT | Web UI |
| Vite | MIT | UI build |
| TypeScript | Apache-2.0 | UI language |
| Fraunces and Outfit via Fontsource | SIL Open Font License | UI fonts |

Lock files: `backend/requirements.txt`, `backend/requirements.lock`, `frontend/package-lock.json`.

## Caller-installed tools

**FFmpeg / ffprobe.** Not distributed. The build on a machine may be GPL, LGPL, or nonfree depending on how the user compiled it. This environment's Ubuntu ffmpeg 6.1.1 reports `--enable-gpl` and includes libx264, libx265, libvidstab, and prores_ks. A different machine (the local observation of FFmpeg 9.0.2 with AMF) is a different binary. See https://ffmpeg.org/legal.html and https://ffmpeg.org/ffmpeg-filters.html.

**libvidstab.** Used only through the installed ffmpeg filters `vidstabdetect` and `vidstabtransform`. GPL-2.0-or-later. Not copied into this repository.

**Gyroflow.** Optional external application. Public CLI: https://docs.gyroflow.xyz/app/advanced-usage/command-line-cli. Gyroflow is GPL-licensed. This repository does not copy Gyroflow source, and it does not pass `--overwrite`, `--watch`, or `--when-done`. Whether you may distribute Gyroflow beside Crafts Prepare is a Gyroflow licensing question, not something this app decides.

**telemetry-parser 0.2.6** by Adrian Eddy, MIT OR Apache-2.0. Optional helper in `tools/telemetry-inspect` links the crates.io crate. It is not a copy of Gyroflow. The published 0.2.6 crate (2022-09-06) is older than the unpublished git master (edition 2024). No camera support is claimed from its output.

## Not included

Sony Catalyst Prepare, Catalyst Browse, Sony installers, Sony DLLs, Sony trademarks, and Sony camera footage are not in this repository.
