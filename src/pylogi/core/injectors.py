# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Input injection for Windows (SendInput), through ctypes.

The action engine speaks Linux evdev codes (``actions.KEYCODES`` ...).  These backends translate them to the
platform's own key codes.  NOT verified on a real Windows machine: the translation tables and the
call structure are covered by tests, the effect on a real desktop is not.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import sys

from .actions import Injector

# evdev code -> Windows virtual-key code
_VK: dict[int, int] = {
    1: 0x1B, 2: 0x31, 3: 0x32, 4: 0x33, 5: 0x34, 6: 0x35, 7: 0x36, 8: 0x37, 9: 0x38, 10: 0x39, 11: 0x30,
    12: 0xBD, 13: 0xBB, 14: 0x08, 15: 0x09, 26: 0xDB, 27: 0xDD, 28: 0x0D, 39: 0xBA, 40: 0xDE, 41: 0xC0,
    43: 0xDC, 51: 0xBC, 52: 0xBE, 53: 0xBF, 57: 0x20, 87: 0x7A, 88: 0x7B, 99: 0x2C, 102: 0x24, 103: 0x26,
    104: 0x21, 105: 0x25, 106: 0x27, 107: 0x23, 108: 0x28, 109: 0x22, 110: 0x2D, 111: 0x2E,
    29: 0xA2, 42: 0xA0, 56: 0xA4, 125: 0x5B,                       # ctrl shift alt meta (left)
    113: 0xAD, 114: 0xAE, 115: 0xAF, 140: 0xB7, 155: 0xB4, 158: 0xA6, 159: 0xA7, 163: 0xB0,
    164: 0xB3, 165: 0xB1, 166: 0xB2, 172: 0xAC, 217: 0xAA, 144: 0xB6,
}
_VK |= dict(zip(range(16, 26), map(ord, "QWERTYUIOP"))) | dict(zip(range(30, 39), map(ord, "ASDFGHJKL"))) | \
    dict(zip(range(44, 51), map(ord, "ZXCVBNM"))) | {58 + i: 0x6F + i for i in range(1, 11)}   # F1..F10
_VK_EXTENDED = {102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 125}
_VK_MOUSE = {0x110: (0x0002, 0x0004, 0), 0x111: (0x0008, 0x0010, 0), 0x112: (0x0020, 0x0040, 0),
             0x113: (0x0080, 0x0100, 1), 0x114: (0x0080, 0x0100, 2)}      # (down, up, xbutton)

class WindowsInjector(Injector):
    """SendInput for keyboard, mouse buttons and the wheels."""
    INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
    KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002
    MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000

    def __init__(self, user32=None):
        if user32 is None:
            user32 = ctypes.windll.user32            # type: ignore[attr-defined]
        self.user32 = user32
        self.available, self.reason = True, ""

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                        ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                        ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]

        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", ctypes.c_ulong), ("u", _U)]

        self._INPUT, self._KB, self._MI = INPUT, KEYBDINPUT, MOUSEINPUT

    def _send(self, **kw) -> None:
        inp = self._INPUT()
        if "ki" in kw:
            inp.type, inp.ki = self.INPUT_KEYBOARD, kw["ki"]
        else:
            inp.type, inp.mi = self.INPUT_MOUSE, kw["mi"]
        self.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

    def key(self, code: int, down: bool) -> None:
        if code in _VK_MOUSE:
            d, u, x = _VK_MOUSE[code]
            self._send(mi=self._MI(0, 0, x, d if down else u, 0, 0))
            return
        vk = _VK.get(code)
        if vk is None:
            return
        flags = (self.KEYEVENTF_EXTENDEDKEY if code in _VK_EXTENDED else 0) | (0 if down else self.KEYEVENTF_KEYUP)
        self._send(ki=self._KB(vk, 0, flags, 0, 0))

    def rel(self, code: int, value: int) -> None:
        if code in (8, 6):                      # REL_WHEEL / REL_HWHEEL, one notch = 120
            flag = self.MOUSEEVENTF_WHEEL if code == 8 else self.MOUSEEVENTF_HWHEEL
            self._send(mi=self._MI(0, 0, ctypes.c_ulong(value * 120 & 0xFFFFFFFF).value, flag, 0, 0))

    def close(self) -> None:
        pass


def platform_injector() -> Injector | None:
    """The injector of the running platform, or None (Linux is handled by actions.UInputInjector)."""
    try:
        if sys.platform == "win32":
            return WindowsInjector()
    except (OSError, AttributeError) as e:
        inj = Injector()
        inj.reason = f"input injection is not available ({e})"
        return inj
    return None
