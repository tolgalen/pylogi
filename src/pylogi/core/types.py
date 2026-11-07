# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Plain data types shared by the backends and the UI."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

RGB = tuple[int, int, int]


class Unsupported(Exception):
    """The device does not provide the requested capability."""


@dataclass
class Battery:
    percent: Optional[int]
    charging: bool = False
    text: str = ""


@dataclass
class HostInfo:
    index: int
    paired: bool = False
    name: str = ""
    bus: str = ""        # "bluetooth", "bolt", "unifying", ...
    current: bool = False
    max_name: int = 0    # longest name the device accepts (bytes)


@dataclass
class DpiInfo:
    values: list[int]    # selectable values (already expanded if stepped)
    current: int
    default: int = 0


@dataclass
class Control:
    cid: int
    task: int
    name: str
    flags: int = 0
    remap: int = 0       # cid currently remapped to (0 = none)
    divert: bool = False

    @property
    def reprogrammable(self) -> bool:
        return bool(self.flags & 0x0010)

    @property
    def divertable(self) -> bool:
        return bool(self.flags & 0x0020)


@dataclass
class ButtonBinding:
    raw: bytes           # 4 raw bytes as stored in the onboard profile

    def describe(self) -> str:
        from .buttons import describe_binding
        return describe_binding(self.raw)


@dataclass
class Profile:
    index: int
    sector: int
    enabled: bool
    name: str
    report_rate_ms: int
    dpis: list[int]
    dpi_default: int
    dpi_shift: int
    color: RGB
    buttons: list[ButtonBinding] = field(default_factory=list)
    raw: bytes = b""     # original sector, patched on write


@dataclass
class RgbZone:
    index: int
    name: str = ""


@dataclass
class DeviceInfo:
    serial: str = ""
    unit: str = ""
    firmware: str = ""
    protocol: str = ""
    extra: dict[str, str] = field(default_factory=dict)
