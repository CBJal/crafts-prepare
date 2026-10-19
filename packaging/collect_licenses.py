"""Copy the license texts that a Windows build actually bundles.

Run this with the build virtualenv after pip install. It does not download
anything. The external ffmpeg program and Gyroflow are not collected.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import shutil
import sys
from pathlib import Path

RUNTIME_DISTRIBUTIONS = (
    "annotated-types",
    "anyio",
    "click",
    "fastapi",
    "h11",
    "idna",
    "pydantic",
    "pydantic-core",
    "starlette",
    "typing-extensions",
    "typing-inspection",
    "uvicorn",
    "PySide6-Essentials",
    "PySide6-Addons",
    "shiboken6",
)

ROOT = Path(__file__).resolve().parents[1]


def find_distribution(name: str) -> importlib.metadata.Distribution | None:
    for candidate in (name, name.replace("-", "_"), name.replace("_", "-")):
        try:
            return importlib.metadata.distribution(candidate)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


def license_files(dist: importlib.metadata.Distribution) -> list[Path]:
    found: list[Path] = []
    for item in dist.files or []:
        name = Path(str(item)).name.lower()
        if name.startswith(("license", "copying", "notice")):
            located = Path(dist.locate_file(item))
            if located.is_file():
                found.append(located)
    return found


def _write_metadata_license(dist: importlib.metadata.Distribution, destination: Path) -> None:
    meta = dist.metadata
    license_text = (meta.get("License") or "").strip()
    classifiers = [item for item in (meta.get_all("Classifier") or []) if str(item).startswith("License")]
    body = license_text or "\n".join(classifiers)
    if not body.strip():
        raise SystemExit(f"{meta['Name']} has no license file and no license metadata.")
    destination.write_text(body + "\n", encoding="utf-8")


def collect_distribution(name: str, output: Path) -> list[str]:
    dist = find_distribution(name)
    if dist is None:
        raise SystemExit(f"Required distribution {name} is not installed in this interpreter.")
    folder_name = dist.metadata["Name"] or name
    folder = output / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for source in license_files(dist):
        target = folder / source.name
        shutil.copyfile(source, target)
        copied.append(f"{folder_name}/{source.name}")
    if not copied:
        _write_metadata_license(dist, folder / "LICENSE.txt")
        copied.append(f"{folder_name}/LICENSE.txt")
    return copied


def collect_python_license(output: Path) -> str:
    import sysconfig

    stdlib = Path(sysconfig.get_path("stdlib"))
    candidates = [
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE",
        stdlib / "LICENSE.txt",
        Path(sys.executable).resolve().parent / "LICENSE.txt",
    ]
    source = next((item for item in candidates if item.is_file()), None)
    if source is None:
        raise SystemExit(f"Python license was not found under {sys.base_prefix}.")
    folder = output / "Python"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, folder / "LICENSE.txt")
    return "Python/LICENSE.txt"


def collect_pyinstaller_bootloader(output: Path) -> str:
    dist = find_distribution("pyinstaller")
    if dist is None:
        raise SystemExit("PyInstaller is not installed, so its bootloader license cannot be copied.")
    sources = license_files(dist)
    if not sources:
        import PyInstaller

        package = Path(PyInstaller.__file__).resolve().parent
        for name in ("COPYING.txt", "COPYING"):
            for candidate in (package / name, package.parent / name):
                if candidate.is_file():
                    sources.append(candidate)
                    break
    if not sources:
        raise SystemExit("The installed PyInstaller distribution has no COPYING.txt.")
    folder = output / "PyInstaller"
    folder.mkdir(parents=True, exist_ok=True)
    target_name = "COPYING.txt" if sources[0].name.upper().startswith("COPYING") else sources[0].name
    shutil.copyfile(sources[0], folder / target_name)
    return f"PyInstaller/{target_name}"


def collect_react_licenses(output: Path) -> list[str]:
    copied: list[str] = []
    for package in ("react", "react-dom"):
        source = ROOT / "frontend" / "node_modules" / package / "LICENSE"
        if not source.is_file():
            raise SystemExit(f"{source} is missing. Run npm ci in frontend/ before collecting licenses.")
        folder = output / package
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, folder / "LICENSE")
        copied.append(f"{package}/LICENSE")
    return copied


def collect_qt_notices(output: Path) -> list[str]:
    source = ROOT / "packaging" / "third-party-licenses"
    folder = output / "Qt"
    folder.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    notice = ROOT / "packaging" / "QT_NOTICES.txt"
    if not notice.is_file():
        raise SystemExit("packaging/QT_NOTICES.txt is missing.")
    shutil.copyfile(notice, folder / "QT_NOTICES.txt")
    copied.append("Qt/QT_NOTICES.txt")
    for path in sorted(source.glob("*.txt")):
        shutil.copyfile(path, folder / path.name)
        copied.append(f"Qt/{path.name}")
    if not any(name.endswith("LGPL-3.0.txt") for name in copied):
        raise SystemExit("The LGPL-3.0 text was not copied.")
    return copied


def collect_runtime_licenses(output: Path) -> list[str]:
    output.mkdir(parents=True, exist_ok=True)
    copied = [collect_python_license(output)]
    for name in RUNTIME_DISTRIBUTIONS:
        copied.extend(collect_distribution(name, output))
    copied.extend(collect_qt_notices(output))
    return copied


def collect_all(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    copied = collect_runtime_licenses(output)
    copied.append(collect_pyinstaller_bootloader(output))
    notice = ROOT / "THIRD_PARTY_NOTICES.md"
    if notice.is_file():
        shutil.copyfile(notice, output / "THIRD_PARTY_NOTICES.md")
        copied.append("THIRD_PARTY_NOTICES.md")
    lines = [
        "License texts collected for the Crafts Prepare Windows build.",
        "The external ffmpeg, ffprobe, and Gyroflow programs are not bundled.",
        "Qt Multimedia may include its own playback libraries. Those are not the external ffmpeg program.",
        "Qt WebEngine is not bundled. The optional web UI is not bundled.",
        "",
        *copied,
        "",
    ]
    (output / "INDEX.txt").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    collect_all(args.output)


if __name__ == "__main__":
    main()
