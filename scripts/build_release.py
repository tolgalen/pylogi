#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Build installable packages of the current platform into ./dist.

    python scripts/build_release.py            # needs: pip install pyinstaller (and the app's requirements)

Always produced: the self-contained folder (PyInstaller) packed as .tar.gz (Linux) or .zip (Windows).
Extra packages, only when their tool is on PATH:
    Linux    pyLogi-<version>-x86_64.AppImage   (appimagetool)
    Windows  pyLogi-<version>-setup.exe         (Inno Setup: iscc)
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = Path(__import__("os").environ.get("PYLOGI_DIST", ROOT / "dist"))
sys.path.insert(0, str(ROOT / "src"))
from pylogi import __version__  # noqa: E402


def run(*cmd: str, **kw) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def freeze() -> Path:
    build = ROOT / "build"
    run(sys.executable, "-m", "PyInstaller", str(ROOT / "packaging/pyinstaller/pylogi.spec"),
        "--noconfirm", "--distpath", str(DIST), "--workpath", str(build))
    shutil.rmtree(build, ignore_errors=True)
    return DIST / "pylogi"


def pack(folder: Path) -> Path:
    arch = platform.machine().lower()
    if sys.platform.startswith("win"):
        out = DIST / f"pyLogi-{__version__}-windows-{arch}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in folder.rglob("*"):
                z.write(f, Path("pyLogi") / f.relative_to(folder))
    else:
        out = DIST / f"pyLogi-{__version__}-{sys.platform}-{arch}.tar.gz"
        with tarfile.open(out, "w:gz") as t:
            t.add(folder, arcname="pyLogi")
    return out


def appimage(folder: Path) -> Path | None:
    tool = shutil.which("appimagetool")
    if not tool:
        print("appimagetool not found: skipping the AppImage (https://github.com/AppImage/appimagetool)")
        return None
    appdir = DIST / "pyLogi.AppDir"
    shutil.rmtree(appdir, ignore_errors=True)
    (appdir / "usr/lib").mkdir(parents=True)
    shutil.copytree(folder, appdir / "usr/lib/pylogi")
    shutil.copy(ROOT / "packaging/appimage/AppRun", appdir / "AppRun")
    shutil.copy(ROOT / "packaging/appimage/pylogi.desktop", appdir / "pylogi.desktop")
    shutil.copy(ROOT / "src/pylogi/data/pylogi.svg", appdir / "pylogi.svg")
    out = DIST / f"pyLogi-{__version__}-{platform.machine()}.AppImage"
    run(tool, str(appdir), str(out), env={**__import__("os").environ, "ARCH": platform.machine()})
    shutil.rmtree(appdir, ignore_errors=True)
    return out


def installer() -> Path | None:
    tool = shutil.which("iscc")
    if not tool:
        print("Inno Setup (iscc) not found: skipping the installer (https://jrsoftware.org/isinfo.php)")
        return None
    run(tool, f"/DAppVersion={__version__}", str(ROOT / "packaging/windows/pylogi.iss"))
    return DIST / f"pyLogi-{__version__}-setup.exe"


def main() -> int:
    DIST.mkdir(exist_ok=True)
    folder = freeze()
    made = [pack(folder)]
    extra = installer() if sys.platform.startswith("win") else appimage(folder) if sys.platform.startswith("linux") else None
    if extra:
        made.append(extra)
    print("\nBuilt:")
    for p in made:
        print(f"  {p}  ({p.stat().st_size // 1_000_000} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
