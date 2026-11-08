#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Build a .deb (Debian 12/13, Ubuntu 22.04 and later) from the PyInstaller bundle into ./dist.

    python scripts/build_deb.py

The package carries its own Python and Qt (Debian 12 and Ubuntu 22.04 have no PySide6 package), so it only needs
the system libraries Qt loads at run time.  Build it on the OLDEST target (Ubuntu 22.04, glibc 2.35): a bundle
built there runs on every newer system.  Needs: pip install pyinstaller, the app's requirements and dpkg-deb.
"""
from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = Path(os.environ.get("PYLOGI_DIST", ROOT / "dist"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from pylogi import __version__  # noqa: E402

# libraries that Qt's xcb / wayland / EGL platform plugins load and that the bundle does not carry
DEPENDS = ["libc6 (>= 2.35)", "libegl1", "libgl1", "libfontconfig1", "libdbus-1-3", "libglib2.0-0",
           "libxkbcommon0", "libxkbcommon-x11-0", "libxcb-cursor0", "libxcb-icccm4", "libxcb-image0",
           "libxcb-keysyms1", "libxcb-render-util0", "libxcb-shape0", "libxcb-xkb1", "libxcb-xinerama0"]
RECOMMENDS = ["x11-utils"]          # xprop: active-window detection on X11 / XWayland

CONTROL = """Package: pylogi
Version: {version}
Section: utils
Priority: optional
Architecture: {arch}
Depends: {depends}
Recommends: {recommends}
Installed-Size: {size}
Maintainer: pyLogi contributors <candion@gmail.com>
Homepage: https://github.com/tolgalen/pylogi
Description: Qt manager for Logitech mice, keyboards and webcams (HID++)
 pyLogi manages Logitech devices over USB, Bluetooth and the Unifying, Bolt
 and LIGHTSPEED receivers without any Logitech software: buttons, macros and
 gestures, pointer and DPI, onboard profiles, per-application profiles,
 lighting, webcam controls, battery, Easy-Switch and receiver pairing.
 .
 This package carries its own Python and Qt. Logitech, Logi, Unifying, Bolt
 and LIGHTSPEED are trademarks of Logitech Europe S.A.; pyLogi is an
 independent project and is not affiliated with Logitech.
"""

DESKTOP = """[Desktop Entry]
Type=Application
Name=pyLogi
Comment=Manage Logitech mice, keyboards and webcams
Exec=pylogi
Icon=pylogi
Terminal=false
Categories=Settings;HardwareSettings;
StartupWMClass=pylogi
"""

POSTINST = """#!/bin/sh
set -e
if [ "$1" = configure ] && command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload >/dev/null 2>&1 || true
    udevadm trigger --subsystem-match=hidraw --subsystem-match=misc >/dev/null 2>&1 || true
fi
exit 0
"""

POSTRM = """#!/bin/sh
set -e
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload >/dev/null 2>&1 || true
fi
exit 0
"""


def put(path: Path, data: str | bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    path.chmod(mode)


def stage(root: Path, bundle: Path) -> int:
    """Lay the package out under `root`; returns its size in KiB."""
    lib = root / "usr/lib/pylogi"
    shutil.copytree(bundle, lib, symlinks=True)
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/bin/pylogi").symlink_to("../lib/pylogi/pylogi")
    put(root / "usr/share/applications/pylogi.desktop", DESKTOP)
    data = ROOT / "src/pylogi/data"
    put(root / "usr/share/icons/hicolor/scalable/apps/pylogi.svg", (data / "pylogi.svg").read_bytes())
    for png in sorted((data / "icons").glob("pylogi-*.png")):
        size = png.stem.split("-")[1]
        put(root / f"usr/share/icons/hicolor/{size}x{size}/apps/pylogi.png", png.read_bytes())
    put(root / "usr/lib/udev/rules.d/42-pylogi.rules", (ROOT / "packaging/42-pylogi.rules").read_bytes())
    from pylogi.core import desktop
    put(root / "usr/lib/systemd/user/pylogi.service", desktop.service_unit("/usr/bin/pylogi"))
    doc = root / "usr/share/doc/pylogi"
    put(doc / "copyright", "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"
        "Upstream-Name: pyLogi\nSource: https://github.com/tolgalen/pylogi\nLicense: GPL-3.0-or-later\n\n"
        "Files: *\nCopyright: 2026 pyLogi contributors\nLicense: GPL-3.0-or-later\n"
        " On Debian systems the full text is in /usr/share/common-licenses/GPL-3.\n"
        " The bundled Qt / PySide6 libraries keep their own licences (LGPL-3.0); see THIRD_PARTY.md.\n")
    put(doc / "changelog.gz", gzip.compress((ROOT / "CHANGELOG.md").read_bytes(), mtime=0))
    shutil.copy(ROOT / "THIRD_PARTY.md", doc / "THIRD_PARTY.md")
    for d in (root, *[p for p in root.rglob("*") if p.is_dir()]):
        d.chmod(0o755)
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) // 1024


def main() -> int:
    if not shutil.which("dpkg-deb"):
        print("dpkg-deb not found (apt install dpkg-dev)", file=sys.stderr)
        return 1
    bundle = DIST / "pylogi"
    if not (bundle / "pylogi").exists():
        import build_release
        bundle = build_release.freeze()
    arch = subprocess.run(["dpkg", "--print-architecture"], capture_output=True, text=True, check=True).stdout.strip()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "pkg"
        size = stage(root, bundle)
        debian = root / "DEBIAN"
        put(debian / "control", CONTROL.format(version=__version__, arch=arch, size=size, depends=", ".join(DEPENDS),
                                               recommends=", ".join(RECOMMENDS)))
        put(debian / "postinst", POSTINST, 0o755)
        put(debian / "postrm", POSTRM, 0o755)
        debian.chmod(0o755)
        out = DIST / f"pylogi_{__version__}_{arch}.deb"
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(root), str(out)], check=True)
    print(f"built {out} ({out.stat().st_size // 1_000_000} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
