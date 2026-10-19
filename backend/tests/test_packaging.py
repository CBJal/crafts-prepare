"""Packaging files. These tests do not run PyInstaller or Inno Setup."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_inno_script_has_no_pascal_comment_and_keeps_user_data():
    text = (ROOT / "packaging" / "crafts-prepare.iss").read_text(encoding="utf-8")
    assert "[Code]" not in text
    assert "{%" not in text
    assert "UninstallDelete" not in text
    assert "PrivilegesRequired=lowest" in text


def test_build_script_does_not_delete_previous_outputs():
    text = (ROOT / "packaging" / "build-windows.ps1").read_text(encoding="utf-8")
    assert "Remove-Item" not in text
    assert "Refusing to overwrite" in text
    assert '/DSourceDir=`"$bundle`"' in text
    assert '/DOutputDir=`"$installerOut`"' in text
    assert "pyinstaller==6.22.3" in (ROOT / "packaging" / "requirements-build.txt").read_text(encoding="utf-8")
    pins = (ROOT / "packaging" / "requirements-build.txt").read_text(encoding="utf-8")
    for pin in (
        "pyinstaller-hooks-contrib==2026.8",
        "altgraph==0.17.5",
        "packaging==24.0",
        "pefile==2024.8.26",
        "pywin32-ctypes==0.2.3",
        "setuptools==80.9.0",
    ):
        assert pin in pins


def test_version_resource_matches_the_app_version(tmp_path: Path):
    module = _load("write_version_info", "packaging/write_version_info.py")
    destination = tmp_path / "file_version_info.txt"
    assert module.write_version_info(destination) == "0.1.0"
    text = destination.read_text(encoding="utf-8")
    assert "filevers=(0, 1, 0, 0)" in text
    assert "prodvers=(0, 1, 0, 0)" in text
    assert "FileVersion" in text and "0.1.0" in text
    assert "ProductVersion" in text
    assert "InternalName" in text and "CraftsPrepare" in text
    committed = (ROOT / "packaging" / "file_version_info.txt").read_text(encoding="utf-8")
    assert "FileVersion" in committed and "0.1.0" in committed


def test_runtime_license_texts_are_copied(tmp_path: Path):
    module = _load("collect_licenses", "packaging/collect_licenses.py")
    output = tmp_path / "licenses"
    copied = module.collect_runtime_licenses(output)
    python_license = (output / "Python" / "LICENSE.txt").read_text(encoding="utf-8")
    assert "PYTHON SOFTWARE FOUNDATION" in python_license.upper()
    fastapi_files = list((output / "fastapi").rglob("*"))
    assert any(path.is_file() and path.stat().st_size > 0 for path in fastapi_files)
    assert any(item.startswith("Python/") for item in copied)
    assert "FFmpeg" not in " ".join(path.name for path in output.rglob("*"))
