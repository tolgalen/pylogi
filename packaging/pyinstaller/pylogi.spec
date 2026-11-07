# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
# PyInstaller recipe: a self-contained folder (no Python needed on the target computer).
#   python scripts/build_release.py            # runs this and packs the result
#   pyinstaller packaging/pyinstaller/pylogi.spec --noconfirm
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]
SRC = ROOT / "src"
PKG = SRC / "pylogi"

datas = [(str(PKG / sub), f"pylogi/{sub}") for sub in ("data", "locales", "themes")]
datas += [(str(ROOT / "LICENSE"), "."), (str(ROOT / "README.md"), "."),
          (str(ROOT / "packaging" / "42-pylogi.rules"), "packaging")]

hidden = ["pylogi.app", "pylogi.ui.main_window"]
if sys.platform == "darwin" or sys.platform.startswith("win"):
    hidden.append("hid")                      # hidapi: no /dev/hidraw on these systems

a = Analysis([str(ROOT / "run.py")], pathex=[str(SRC)], datas=datas, hiddenimports=hidden,
             excludes=["tkinter", "unittest", "pydoc_data"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="pylogi", console=False,
          icon=str(PKG / "data" / "pylogi.ico") if sys.platform.startswith("win") else None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="pylogi")
