# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Simulated devices so the UI can be explored without hardware (--demo)."""
from __future__ import annotations

import struct

from PySide6.QtCore import QObject, Signal

from ..hidpp.onboard import O_BUTTONS, O_DPIS, O_NAME, seal
from ..i18n import N_
from . import buttons as B
from .device import (
    BACKLIGHT,
    BATTERY,
    BUTTONS,
    DPI,
    EASYSWITCH,
    FNLOCK,
    KEYLIGHT,
    PROFILES,
    RATE,
    SMARTSHIFT,
    ZONES,
    Device
)
from .types import (Battery, Control, DpiInfo, HostInfo, Profile, RgbZone,
                    ButtonBinding)
from .webcam import (CamControl, T_BOOL, T_INT, T_MENU, WebcamDevice)


class DemoDevice(Device):
    def __init__(self, uid, name, kind, connection, caps, parent="", battery=None):
        super().__init__()
        self.uid, self.name, self.kind = uid, name, kind
        self.connection, self.parent = connection, parent
        self.caps = set(caps)
        self.battery = battery
        self._host = 0
        self._hosts = [HostInfo(0, True, "Workstation", "bolt", max_name=24),
                       HostInfo(1, True, "Laptop", "bluetooth", max_name=24),
                       HostInfo(2, False, "", "", max_name=24)]
        self.info.serial = uid.split(":")[-1].upper() + "SN01"
        self._dpi = 1600
        self._ratchet, self._thr = True, 20
        self._rate = "1 ms"
        self._bl = (True, 3)
        self._profiles: list[Profile] = []
        self._host_mode = False
        self.key_colors: dict[int, tuple] = {}
        if PROFILES in caps:
            for i in range(3):
                raw = bytearray(255)
                raw[0] = 1
                raw[1], raw[2] = 1, 3
                raw[O_DPIS:O_DPIS + 10] = struct.pack("<5H", 400, 800, 1600, 3200, 6400)
                raw[13:16] = bytes([[0, 200, 255], [255, 80, 0], [0, 255, 100]][i])
                defaults = [B.mouse_binding(m) for m in (1, 2, 4, 8, 16)] + \
                    [B.special_binding(3)] + [B.DISABLED] * 10
                for j, d in enumerate(defaults):
                    raw[O_BUTTONS + 4 * j:O_BUTTONS + 4 * j + 4] = d
                nm = f"Profile {i + 1}".encode("utf-16-le")
                raw[O_NAME:O_NAME + len(nm)] = nm
                raw = bytearray(seal(bytes(raw)))
                self._profiles.append(Profile(
                    i, i + 1, True, f"Profile {i + 1}", 1, [400, 800, 1600, 3200, 6400],
                    1, 3, tuple(raw[13:16]),
                    [ButtonBinding(bytes(raw[O_BUTTONS + 4 * j:O_BUTTONS + 4 * j + 4]))
                     for j in range(16)], bytes(raw)))

    def hosts(self):
        return self._host, [HostInfo(h.index, h.paired, h.name, h.bus,
                                     h.index == self._host) for h in self._hosts]
    def switch_host(self, index): self._host = index
    def rename_host(self, index, name): self._hosts[index].name = name
    def key_presence(self):
        if self.name.startswith("G915"):               # real answer of a G915 TKL (ISO): no numpad, ISO key present
            bits = bytes.fromhex("ffffffffffffffffffff0000feff")
            return {i * 8 + b for i, byte in enumerate(bits) for b in range(8) if byte >> b & 1}
        return None
    def wheel(self): return getattr(self, "_wheel", (False, False))
    def set_wheel(self, hires=None, invert=None):
        h, i = self.wheel()
        self._wheel = (h if hires is None else hires, i if invert is None else invert)
    def thumb(self): return getattr(self, "_thumb", (False, False))
    def set_thumb(self, diverted=None, invert=None):
        d, i = self.thumb()
        self._thumb = (d if diverted is None else diverted, i if invert is None else invert)
    def fn_lock(self): return getattr(self, "_fn", False)
    def set_fn_lock(self, on): self._fn = on
    def dpi(self): return DpiInfo(list(range(200, 8001, 50)), self._dpi, 800)
    def set_dpi(self, value): self._dpi = value
    def smartshift(self): return self._ratchet, self._thr
    def set_smartshift(self, ratchet, threshold): self._ratchet, self._thr = ratchet, threshold
    def report_rate(self): return self._rate, ["8 ms", "4 ms", "2 ms", "1 ms", "500 µs", "250 µs"]
    def set_report_rate(self, label): self._rate = label
    def controls(self):
        base = [(0x50, "Left click", 0x401), (0x51, "Right click", 0x401), (0x52, "Middle click", 0x530),
                (0x53, "Back", 0x530), (0x56, "Forward", 0x530), (0xC3, "Gesture button", 0x530),
                (0xC4, "SmartShift", 0x530)]
        remaps = getattr(self, "_remaps", {})
        return [Control(cid, 0, name, flags, remaps.get(cid, 0)) for cid, name, flags in base]
    def remap_control(self, cid, target):
        self.__dict__.setdefault("_remaps", {})[cid] = target
    def profiles(self): return list(self._profiles)
    def write_profile(self, p): self._profiles[p.index] = p
    def active_profile(self): return getattr(self, "_active", self._profiles[0].sector)
    def set_active_profile(self, sector): self._active = sector
    def profile_host_mode(self): return self._host_mode
    def set_profile_host_mode(self, host): self._host_mode = host
    def backlight(self): return self._bl[0], self._bl[1], 5
    def set_backlight(self, enabled, level): self._bl = (enabled, level)
    def zones(self): return [RgbZone(0, N_("Logo")), RgbZone(1, N_("Side light"))]
    def set_zone_color(self, zone, rgb): pass
    def set_key_colors(self, colors): self.key_colors.update(colors)


class DemoWebcam(WebcamDevice):
    def __init__(self):
        super().__init__("/dev/null", "BRIO Ultra HD Webcam (demo)", "demo:cam")
        self.previewable = False
        self._c = [
            CamControl(1, "Brightness", T_INT, 0, 255, 1, 128, 128, 0, {}),
            CamControl(2, "Contrast", T_INT, 0, 255, 1, 128, 128, 0, {}),
            CamControl(3, "Saturation", T_INT, 0, 255, 1, 128, 128, 0, {}),
            CamControl(4, "White Balance, Automatic", T_BOOL, 0, 1, 1, 1, 1, 0, {}),
            CamControl(5, "White Balance Temperature", T_INT, 2000, 7500, 10, 4000, 4000, 0, {}),
            CamControl(6, "Auto Exposure", T_MENU, 0, 3, 1, 3, 3, 0,
                       {1: "Manual Mode", 3: "Aperture Priority Mode"}),
            CamControl(7, "Focus, Automatic Continuous", T_BOOL, 0, 1, 1, 1, 1, 0, {}),
            CamControl(8, "Focus (absolute)", T_INT, 0, 250, 5, 0, 0, 0, {}),
            CamControl(9, "Zoom, Absolute", T_INT, 100, 500, 1, 100, 100, 0, {}),
            CamControl(10, "Pan (absolute)", T_INT, -36000, 36000, 3600, 0, 0, 0, {}),
            CamControl(11, "Tilt (absolute)", T_INT, -36000, 36000, 3600, 0, 0, 0, {}),
        ]

    def cam_controls(self): return list(self._c)
    def set_cam_control(self, cid, value):
        for c in self._c:
            if c.id == cid:
                c.value = int(value)


class DemoManager(QObject):
    changed = Signal()
    scanned = Signal()

    def __init__(self):
        super().__init__()
        mouse = DemoDevice("demo:mx", "MX Master 3S", "mouse", "Bolt",
                           [BATTERY, EASYSWITCH, DPI, SMARTSHIFT, BUTTONS, "wheel", "thumbwheel"],
                           "Bolt receiver", Battery(78, False))
        gmouse = DemoDevice("demo:g502", "G502 X LIGHTSPEED", "mouse", "Lightspeed",
                            [BATTERY, DPI, RATE, PROFILES, BUTTONS, ZONES],
                            "Lightspeed receiver", Battery(34, True))
        kb = DemoDevice("demo:mxkeys", "MX Keys S", "keyboard", "Bluetooth",
                        [BATTERY, EASYSWITCH, BACKLIGHT, FNLOCK], "", Battery(91, False))
        g915 = DemoDevice("demo:g915", "G915 TKL", "keyboard", "USB",
                          [KEYLIGHT, ZONES, BACKLIGHT, PROFILES], "", None)
        self.devices = [mouse, gmouse, kb, g915, DemoWebcam()]
        self.errors: list[str] = []

    def start(self):
        self.changed.emit()
        self.scanned.emit()
    def stop(self): pass
    def rescan(self): self.changed.emit()
    def set_active(self, active): pass
