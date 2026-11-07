# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Optional HID++ frame trace (``--trace-hid FILE``): every report sent and received."""
from __future__ import annotations

import threading
import time

_file = None
_lock = threading.Lock()
_t0 = 0.0


def enable(path: str) -> None:
    global _file, _t0
    _file = open(path, "a", buffering=1, encoding="utf-8")
    _t0 = time.monotonic()
    _file.write(f"# pyLogi HID trace {time.strftime('%Y-%m-%d %H:%M:%S')}\n")


def disable() -> None:
    global _file
    with _lock:
        if _file is not None:
            _file.close()
            _file = None


def log(direction: str, data: bytes, note: str = "") -> None:
    """direction: TX (to device) / RX (from device)."""
    if _file is None:
        return
    with _lock:
        _file.write(f"{time.monotonic() - _t0:10.4f} {direction} {bytes(data).hex(' ')} {note}\n")
