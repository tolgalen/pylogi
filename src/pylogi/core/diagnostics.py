# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""A plain-text system report for bug reports (no serial numbers, no personal data)."""
from __future__ import annotations

import os
import platform
import sys

from .. import __license__, __version__
from . import conflicts
from .config import config_dir


def collect(manager=None) -> str:
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import qVersion
    lines = [f"pyLogi {__version__} ({__license__})",
             f"Python {platform.python_version()} on {platform.system()} {platform.release()} ({platform.machine()})",
             f"PySide6 {pyside_version}, Qt {qVersion()}",
             f"Session: {os.environ.get('XDG_SESSION_TYPE', '?')} / {os.environ.get('XDG_CURRENT_DESKTOP', '?')}",
             f"Config folder: {config_dir()}"]
    if sys.platform.startswith("linux"):
        lines.append("HID backend: /dev/hidraw (kernel)")
        lines.append(f"/dev/uinput writable: {os.access('/dev/uinput', os.W_OK)}")
    else:
        try:
            import hid  # noqa: F401
            lines.append("HID backend: hidapi")
        except ImportError:
            lines.append("HID backend: hidapi is NOT installed")
    managers = conflicts.running_managers()
    lines.append("Other Logitech managers running: " + (", ".join(managers) if managers else "none"))
    if manager is not None:
        lines.append("Devices:")
        for d in getattr(manager, "devices", []):
            bat = f"{d.battery.percent}%" if d.battery and d.battery.percent is not None else "-"
            lines.append(f"  - {d.name} [{d.kind}, {d.connection}, {'online' if d.online else 'offline'}, "
                         f"battery {bat}] features: {', '.join(sorted(d.caps)) or '-'}")
        errors = getattr(manager, "errors", [])
        if errors:
            lines.append("Recent errors:")
            lines += [f"  - {e}" for e in errors[-10:]]
    return "\n".join(lines)
