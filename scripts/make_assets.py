#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Render the logo (src/pylogi/data/pylogi.svg) into PNG sizes, a Windows .ico and a splash preview."""
import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
from pylogi import __version__  # noqa: E402
from pylogi.ui.branding import DATA, ICON_SIZES, SplashScreen, render_logo  # noqa: E402


def png_bytes(img) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def write_ico(path: Path, sizes=(16, 24, 32, 48, 64, 128, 256)) -> None:
    """ICO container with PNG-compressed entries (Windows Vista and later)."""
    blobs = [png_bytes(render_logo(s)) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, blob in zip(sizes, blobs):
        entries += struct.pack("<BBBBHHII", 0 if s >= 256 else s, 0 if s >= 256 else s, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    path.write_bytes(head + entries + b"".join(blobs))


def main() -> None:
    out = DATA / "icons"
    out.mkdir(exist_ok=True)
    for s in ICON_SIZES:
        render_logo(s).save(str(out / f"pylogi-{s}.png"))
    write_ico(DATA / "pylogi.ico")
    SplashScreen.background(__version__, 2.0).save(str(DATA / "splash.png"))
    print("icons:", ", ".join(str(s) for s in ICON_SIZES), "| pylogi.ico | splash.png")


if __name__ == "__main__":
    main()
