#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Build the Fedora RPM (noarch) and SRPM into ./dist.

    python scripts/build_rpm.py

Needs rpmbuild plus the packages the spec asks for (python3-devel, pyproject-rpm-macros, python3-setuptools,
desktop-file-utils, systemd-rpm-macros; `sudo dnf builddep packaging/rpm/pylogi.spec` installs them).  The
sources come from `git archive HEAD`, so commit first.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from pylogi import __version__  # noqa: E402


def main() -> int:
    if not shutil.which("rpmbuild"):
        print("rpmbuild not found: sudo dnf install rpm-build", file=sys.stderr)
        return 1
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "src", "packaging", "scripts", "pyproject.toml"],
                           cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if dirty:
        print("warning: uncommitted changes are NOT in the RPM (it is built from git HEAD):\n" + dirty, file=sys.stderr)
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as top:
        top_dir = Path(top)
        for d in ("SOURCES", "SPECS", "BUILD", "RPMS", "SRPMS", "BUILDROOT"):
            (top_dir / d).mkdir()
        src = top_dir / "SOURCES" / f"pylogi-{__version__}.tar.gz"
        subprocess.run(["git", "archive", f"--prefix=pylogi-{__version__}/", "-o", str(src), "HEAD"],
                       cwd=ROOT, check=True)
        spec = top_dir / "SPECS" / "pylogi.spec"
        shutil.copyfile(ROOT / "packaging/rpm/pylogi.spec", spec)
        subprocess.run(["rpmbuild", "-ba", "--define", f"_topdir {top_dir}",
                        "--define", f"pylogi_version {__version__}", str(spec)], check=True)
        for rpm in (*top_dir.glob("RPMS/*/*.rpm"), *top_dir.glob("SRPMS/*.rpm")):
            shutil.copyfile(rpm, dist / rpm.name)
            print("built", dist / rpm.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
