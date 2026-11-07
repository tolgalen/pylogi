# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Logitech receivers (Unifying, Bolt, Lightspeed, Nano): slot enumeration and pairing."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .constants import (RECEIVER_NUMBER, RECEIVERS, REG_CONNECTION,
                        REG_BOLT_DISCOVERY, REG_BOLT_PAIRING, REG_NOTIFICATIONS, REG_PAIRING,
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


class BoltPairing:
    """State of a Bolt pairing run, fed with the receiver's unsolicited reports.

    Sequence: discovery is opened (register 0xC0); a device in pairing mode is announced (0x4F: kind, address,
    authentication, then its name); once known, pairing is requested (register 0x2C1); the receiver then asks for a
    passkey (0x4D) that the user enters on the device; 0x54 reports the lock state, the slot of the new device or an error.
    """
    SUB_PASSKEY, SUB_PASSKEY_KEY, SUB_DISCOVERY, SUB_DISCOVERY_STATUS, SUB_STATUS = 0x4D, 0x4E, 0x4F, 0x53, 0x54
    ERRORS = {1: "the device did not answer in time", 2: "pairing failed"}

    def __init__(self):
        self.discovering = False
        self.lock_open = False
        self.counter: Optional[int] = None
        self.kind = 0
        self.address: Optional[bytes] = None
        self.authentication = 0
        self.name = ""
        self.passkey: Optional[str] = None
        self.new_slot: Optional[int] = None
        self.error: Optional[str] = None
        self.requested = False            # pairing with the announced device has been asked for
        self.finished = False             # the worker has stopped

    @property
    def device_ready(self) -> bool:
        return bool(self.discovering and self.address and self.name and not self.requested)

    @property
    def keyboard(self) -> bool:
        return self.kind == 1

    def feed(self, m: bytes) -> bool:
        """Process one report [id, devnum, sub_id, address, data...]; True if it was a pairing report."""
        if len(m) < 5:
            return False
        sub, addr, data = m[2], m[3], bytes(m[4:])
        if sub == self.SUB_DISCOVERY_STATUS:
            self.discovering = addr == 0x00
            self.error = None
            if self.discovering:
                self.counter = self.address = None
                self.name = ""
                self.requested = False
            self.passkey = None
            if data[:1] and data[0]:
                self.error = self.ERRORS.get(data[0], f"error {data[0]}")
        elif sub == self.SUB_DISCOVERY and len(data) > 14:
            counter = addr + data[0] * 256
            if self.counter is None:
                self.counter = counter
            elif self.counter != counter:           # another device announcing itself: stay with the first
                return True
            if data[1] == 0:
                self.kind, self.address, self.authentication = data[3], data[6:12], data[14]
            elif data[1] == 1:
                self.name = data[3:3 + data[2]].decode("utf-8", "replace")
        elif sub == self.SUB_STATUS:
            self.passkey = None
            self.lock_open = addr == 0x00
            self.error = None
            if not self.lock_open:
                self.counter = self.address = None
                self.name = ""
            if data[:1] and data[0]:
                self.error = self.ERRORS.get(data[0], f"error {data[0]}")
                self.new_slot = None
            elif addr == 0x02 and len(data) > 7:
                self.new_slot = data[7]
        elif sub == self.SUB_PASSKEY and len(data) >= 6:
            self.passkey = data[:6].decode("ascii", "replace")
        elif sub != self.SUB_PASSKEY_KEY:
            return False
        return True

    def passkey_steps(self) -> list[str]:
        """Mouse-style devices: the passkey is entered as a series of left / right clicks."""
        if not self.passkey or not self.passkey.isdigit():
            return []
        return ["right" if b == "1" else "left" for b in f"{int(self.passkey):010b}"]

    @property
    def type_passkey(self) -> bool:
        return bool(self.authentication & 0x01)


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
        return self.family in ("Unifying", "Lightspeed", "Nano", "Bolt")

    # -- Bolt pairing (discovery, then pairing with the chosen device)
    def bolt_discover(self, timeout: int = 30, cancel: bool = False) -> None:
        self.ch.write_register(RECEIVER_NUMBER, REG_BOLT_DISCOVERY, bytes([timeout, 0x02 if cancel else 0x01]))

    def bolt_pair(self, address: bytes, authentication: int, entropy: int, slot: int = 0) -> None:
        self.ch.write_register(RECEIVER_NUMBER, REG_BOLT_PAIRING,
                               bytes([0x01, slot]) + bytes(address) + bytes([authentication, entropy]))

    def bolt_cancel(self) -> None:
        self.ch.write_register(RECEIVER_NUMBER, REG_BOLT_PAIRING,
                               bytes([0x02, 0]) + bytes(6) + bytes([0, 20]))

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
