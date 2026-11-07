# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Decoding/encoding of onboard-profile button bindings and key tables."""
from __future__ import annotations

from ..i18n import N_, tr

MOUSE_BUTTONS = {0x0001: N_("Left click"), 0x0002: N_("Right click"), 0x0004: N_("Middle click"),
                 0x0008: N_("Back"), 0x0010: N_("Forward"), 0x0020: N_("Button 6"),
                 0x0040: N_("Button 7"), 0x0080: N_("Button 8"), 0x0100: N_("Button 9"),
                 0x0200: N_("Button 10")}

CONSUMER = {0x00CD: N_("Play/Pause"), 0x00B5: N_("Next track"), 0x00B6: N_("Previous track"),
            0x00B7: N_("Stop"), 0x00E2: N_("Mute"), 0x00E9: N_("Volume up"),
            0x00EA: N_("Volume down"), 0x0192: N_("Calculator"), 0x018A: N_("Mail"),
            0x0223: N_("Browser home"), 0x0224: N_("Browser back"), 0x0225: N_("Browser forward"),
            0x0221: N_("Browser search"), 0x0194: N_("My computer")}

MODIFIERS = [("Ctrl", 0x01), ("Shift", 0x02), ("Alt", 0x04), ("Win", 0x08)]


def _build_keys() -> dict[int, str]:
    keys: dict[int, str] = {}
    for i in range(26):
        keys[0x04 + i] = chr(ord("A") + i)
    for i in range(9):
        keys[0x1E + i] = str(i + 1)
    keys[0x27] = "0"
    for i in range(12):
        keys[0x3A + i] = f"F{i + 1}"
    keys.update({0x28: "Enter", 0x29: "Esc", 0x2A: "Backspace", 0x2B: "Tab",
                 0x2C: "Space", 0x4C: "Delete", 0x49: "Insert", 0x4A: "Home",
                 0x4D: "End", 0x4B: "Page Up", 0x4E: "Page Down", 0x4F: "Right",
                 0x50: "Left", 0x51: "Down", 0x52: "Up", 0x46: "Print Screen"})
    return keys


KEYS = _build_keys()

SPECIAL = {0x01: N_("Next DPI"), 0x02: N_("Previous DPI"), 0x03: N_("Cycle DPI"),
           0x04: N_("Default DPI"), 0x05: N_("DPI shift"), 0x06: N_("Next profile"),
           0x07: N_("Previous profile"), 0x08: N_("Cycle profile"), 0x09: N_("G-shift"),
           0x0A: N_("Battery status"), 0x0B: N_("Profile select")}


def describe_binding(raw: bytes) -> str:
    if len(raw) < 4 or raw[0] == 0xFF:
        return tr("Disabled")
    t = raw[0]
    if t == 0x80:
        sub = raw[1]
        if sub == 0x01:
            mask = (raw[2] << 8) | raw[3]
            return tr(MOUSE_BUTTONS.get(mask, f"Mouse buttons {mask:#06x}"))
        if sub == 0x02:
            mods = "+".join(n for n, b in MODIFIERS if raw[2] & b)
            key = KEYS.get(raw[3], f"key {raw[3]:#04x}")
            return f"{mods}+{key}" if mods else key
        if sub == 0x03:
            u = (raw[2] << 8) | raw[3]
            return tr(CONSUMER.get(u, f"Media {u:#06x}"))
    if t == 0x90:
        return tr(SPECIAL.get(raw[1], f"Special {raw[1]:#04x}"))
    if t == 0x00:
        return tr("Macro")
    return "Raw " + raw.hex(" ")


def mouse_binding(mask: int) -> bytes:
    return bytes([0x80, 0x01, mask >> 8, mask & 0xFF])


def key_binding(modifiers: int, usage: int) -> bytes:
    return bytes([0x80, 0x02, modifiers, usage])


def consumer_binding(usage: int) -> bytes:
    return bytes([0x80, 0x03, usage >> 8, usage & 0xFF])


def special_binding(code: int) -> bytes:
    return bytes([0x90, code, 0, 0])


DISABLED = bytes([0xFF, 0xFF, 0xFF, 0xFF])

# Control ids (REPROG_CONTROLS_V4) -> friendly names; unknown ones are shown in hex
CID_NAMES = {0x0050: N_("Left click"), 0x0051: N_("Right click"), 0x0052: N_("Middle click"),
             0x0053: N_("Back"), 0x0056: N_("Forward"), 0x005B: N_("Left scroll"),
             0x005D: N_("Right scroll"), 0x00C3: N_("Gesture button"), 0x00C4: N_("SmartShift"),
             0x00D7: N_("DPI switch"), 0x00FD: N_("Battery LED"), 0x00E8: N_("Thumb wheel"),
             0x00A0: N_("Host switch 1"), 0x00A1: N_("Host switch 2"), 0x00A2: N_("Host switch 3"),
             0x0083: N_("Back (alt)"), 0x0086: N_("Forward (alt)"), 0x00E4: N_("Haptic panel"),
             0x00D0: N_("Horizontal scroll")}
