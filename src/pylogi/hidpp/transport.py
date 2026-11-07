# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Raw HID transports and enumeration of HID++ capable interfaces.

On Linux the kernel ``hidraw`` interface is used directly (no extra
dependency).  On other platforms the optional ``hidapi`` module is used.
"""
from __future__ import annotations

import glob
import os
import select
import sys
import time
from dataclasses import dataclass
from typing import Optional

from .constants import VENDOR_LOGITECH


@dataclass(frozen=True)
class HidInfo:
    path: str
    vid: int
    pid: int
    bus: str          # "usb" | "bluetooth"
    name: str
    phys: str
    has_short: bool   # interface exposes the 7 byte HID++ report (0x10)
    uniq: str = ""    # Bluetooth address of a BLE device (HID_UNIQ)


class Transport:
    def wait(self, timeout: float) -> bool:
        """Block until a report may be readable (True) or the timeout passes (False)."""
        time.sleep(min(timeout, 0.02))
        return True

    def write(self, data: bytes) -> None: raise NotImplementedError
    def read(self, timeout: float) -> Optional[bytes]: raise NotImplementedError
    def close(self) -> None: pass


class HidrawTransport(Transport):
    def __init__(self, path: str):
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    def wait(self, timeout: float) -> bool:
        r, _, _ = select.select([self.fd], [], [], max(timeout, 0))
        return bool(r)

    def write(self, data: bytes) -> None:
        os.write(self.fd, data)

    def read(self, timeout: float) -> Optional[bytes]:
        r, _, _ = select.select([self.fd], [], [], max(timeout, 0))
        if not r:
            return None
        try:
            return os.read(self.fd, 64)
        except BlockingIOError:
            return None

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


class HidapiTransport(Transport):
    def __init__(self, path: str):
        import hid  # type: ignore
        self.dev = hid.device()
        self.dev.open_path(path.encode())
        self.dev.set_nonblocking(False)

    def write(self, data: bytes) -> None:
        self.dev.write(data)

    def read(self, timeout: float) -> Optional[bytes]:
        d = self.dev.read(64, int(timeout * 1000))
        return bytes(d) if d else None

    def close(self) -> None:
        try:
            self.dev.close()
        except Exception:
            pass


def open_transport(info: HidInfo) -> Transport:
    if sys.platform.startswith("linux"):
        return HidrawTransport(info.path)
    return HidapiTransport(info.path)


def _read_uevent(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        with open(path) as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    out[k] = v
    except OSError:
        pass
    return out


def _enum_linux() -> list[HidInfo]:
    found = []
    for d in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        dev = os.path.join(d, "device")
        ue = _read_uevent(os.path.join(dev, "uevent"))
        try:
            bus, vid, pid = (int(x, 16) for x in ue["HID_ID"].split(":"))
        except (KeyError, ValueError):
            continue
        if vid != VENDOR_LOGITECH:
            continue
        try:
            with open(os.path.join(dev, "report_descriptor"), "rb") as f:
                desc = f.read()
        except OSError:
            continue
        # The HID++ interface declares report ids 0x10 (short) and/or 0x11 (long)
        has_long = b"\x85\x11" in desc
        has_short = b"\x85\x10" in desc
        if not has_long:
            continue
        found.append(HidInfo("/dev/" + os.path.basename(d), vid, pid,
                             "bluetooth" if bus == 5 else "usb",
                             ue.get("HID_NAME", ""), ue.get("HID_PHYS", ""),
                             has_short, ue.get("HID_UNIQ", "")))
    return found


def _enum_hidapi() -> list[HidInfo]:
    try:
        import hid  # type: ignore
    except ImportError:
        return []
    found = []
    for e in hid.enumerate(VENDOR_LOGITECH, 0):
        if e.get("usage_page", 0) not in (0xFF00, 0xFF43):
            continue
        if e.get("usage", 0) not in (1, 2):
            continue
        bt = e.get("bus_type", 1) == 2
        path = e["path"].decode() if isinstance(e["path"], bytes) else e["path"]
        found.append(HidInfo(path, e["vendor_id"], e["product_id"],
                             "bluetooth" if bt else "usb",
                             e.get("product_string", ""), "", not bt))
    return found


def enumerate_hidpp() -> list[HidInfo]:
    return _enum_linux() if sys.platform.startswith("linux") else _enum_hidapi()
