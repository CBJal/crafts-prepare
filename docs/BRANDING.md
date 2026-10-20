# Branding

The app icon is the approved artwork in `assets/brand/app-icon.png` and `assets/brand/app-icon.ico`. Those files are the source. Do not redraw the mark.

`python packaging/make_icon.py` copies `app-icon.ico` to `packaging/icon.ico`. The Windows build runs that copy before PyInstaller. The PyInstaller spec also packs `assets/brand` into the frozen app, and the window loads it from that folder or from the source tree. The web header uses a 64px PNG scaled from the same artwork at `frontend/public/favicon.png`.

Action pictograms in the desktop app (`backend/app/desktop/icons.py`) and the development web UI (`frontend/src/icons.tsx`) are original 24px stroke drawings made for Crafts Prepare. They are not an icon font, a downloaded set, or CapCut artwork.

The installer id is unchanged. Version 0.3.0 is the interface that carries this icon.

Synthetic workspace screenshots, made from a generated test pattern and not from camera footage, are in `docs/ui/`.
