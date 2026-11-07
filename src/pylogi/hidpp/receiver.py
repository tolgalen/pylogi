# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Logitech receivers (Unifying, Bolt, Lightspeed, Nano): slot enumeration and pairing."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .constants import (RECEIVER_NUMBER, RECEIVERS, REG_CONNECTION,
                        REG_BOLT_PAIRING, REG_NOTIFICATIONS, REG_PAIRING,
                        REG_RECEIVER_INFO)
from .protocol import Channel, HidppError

_PAIRED_KIND = {1: "keyboard", 2: "mouse", 3: "numpad", 4: "presenter",
                7: "trackball", 8: "touchpad"}


@dataclass
class Slot:
    number: int
    wpid: str = ""
    kind: str = "unknown"
    name: str = ""


class Receiver:
    def __init__(self, channel: Channel, pid: int):
        self.ch = channel
        self.pid = pid
        self.family, self.max_devices = RECEIVERS.get(pid, ("Receiver", 6))

    # -- helpers
    def _info(self, sub: int) -> Optional[bytes]:
        try:
            return self.ch.read_register(RECEIVER_NUMBER, REG_RECEIVER_INFO,
                                         bytes([sub]), long=True)
        except HidppError:
            return None

    def enable_notifications(self) -> None:
        """Ask the receiver to report wireless/software-present events."""
        try:
            self.ch.write_register(RECEIVER_NUMBER, REG_NOTIFICATIONS,
                                   b"\x00\x09\x00")
        except HidppError:
            pass

    def serial(self) -> str:
        r = self._info(0x03)
        return r[1:5].hex().upper() if r else ""

    def refresh_max_devices(self) -> int:
        r = self._info(0x03)
        if r and len(r) > 6 and 0 < r[6] <= 15:
            self.max_devices = r[6]
        return self.max_devices

    def paired_count(self) -> int:
        try:
            r = self.ch.read_register(RECEIVER_NUMBER, REG_CONNECTION)
            return r[1]
        except (HidppError, IndexError):
            return 0

    # -- slots
    def slot(self, n: int) -> Optional[Slot]:
        bolt = self.family == "Bolt"
        p = self._info(0x50 + n if bolt else 0x20 + n - 1)
        if not p or not any(p[1:8]):
            return None
        s = Slot(n, wpid=p[3:5].hex().upper())
        s.kind = _PAIRED_KIND.get((p[1] if bolt else p[7]) & 0x0F, "unknown")
        nm = self._info(0x60 + n if bolt else 0x40 + n - 1)
        if nm and len(nm) > 2 and 0 < nm[1] <= len(nm) - 2:
            s.name = nm[2:2 + nm[1]].decode("utf-8", "replace")
        return s

    def slots(self) -> list[Slot]:
        self.refresh_max_devices()
        out = []
        for n in range(1, self.max_devices + 1):
            s = self.slot(n)
            if s:
                out.append(s)
        return out

    # -- pairing (Unifying / Lightspeed / Nano: pairing lock)
    @property
    def can_pair(self) -> bool:
        return self.family in ("Unifying", "Lightspeed", "Nano")

    def open_pairing(self, timeout: int = 30) -> None:
        self.ch.write_register(RECEIVER_NUMBER, REG_PAIRING,
                               bytes([0x01, 0x00, timeout]))

    def close_pairing(self) -> None:
        self.ch.write_register(RECEIVER_NUMBER, REG_PAIRING,
                               bytes([0x02, 0x00, 0x00]))

    def unpair(self, slot: int) -> None:
        if self.family == "Bolt":          # register 0x2C1 (long): action 3 = unpair, then the slot
            self.ch.write_register(RECEIVER_NUMBER, REG_BOLT_PAIRING,
                                   bytes([0x03, slot]).ljust(16, b"\0"))
        else:
            self.ch.write_register(RECEIVER_NUMBER, REG_PAIRING,
                                   bytes([0x03, slot, 0x00]))
