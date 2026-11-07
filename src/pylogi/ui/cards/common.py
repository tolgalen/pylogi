# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Helpers shared by the device cards."""
from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QSlider
)

from ...core.device import Device
from ...i18n import tr




def _slider(lo, hi, val, step=1) -> QSlider:
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setValue(val)
    return s


def _zone_label(name: str) -> str:
    if name.startswith("Zone "):
        return tr("Zone") + name[4:]
    return tr(name)


# ----------------------------------------------------------------- Pointer
def is_pointing(dev: Device) -> bool:
    """True when the section is about a pointer (DPI, SmartShift, wheel); a keyboard only has the report rate."""
    return dev.has("dpi") or dev.has("smartshift") or dev.has("wheel")
