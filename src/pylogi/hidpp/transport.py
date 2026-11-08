# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Raw HID transports and enumeration of HID++ capable interfaces.

On Linux the kernel ``hidraw`` interface is used directly (no extra
dependency).  On other platforms the optional ``hidapi`` module is used.
"""
from __future__ import annotations

import glob
import os
import re
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
    short_path: str = ""   # hidapi only: separate collection carrying the short (0x10) reports


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
    """hidapi transport.

    Windows exposes every top-level HID collection as its own device, so the short (report 0x10) and
    the long (0x11) HID++ reports of one receiver live on two different handles: writes are routed by
    report id and both handles are read.
    """

    def __init__(self, path: str, short_path: str = ""):
        import hid  # type: ignore
        self.long = self._open(hid, path)
        self.short = self._open(hid, short_path) if short_path and short_path != path else None
        self._devs = [d for d in (self.long, self.short) if d is not None]

    @staticmethod
    def _open(hid, path: str):
        d = hid.device()
        d.open_path(path.encode())
        d.set_nonblocking(True)
        return d

    def _poll(self) -> Optional[bytes]:
        for d in self._devs:
            data = d.read(64)
            if data:
                return bytes(data)
        return None

    def wait(self, timeout: float) -> bool:
        end = time.monotonic() + max(timeout, 0)
        while True:
            for d in self._devs:                 # peek is not possible: keep what we read for read()
                data = d.read(64)
                if data:
                    self._pending = bytes(data)
                    return True
            if time.monotonic() >= end:
                return False
            time.sleep(0.002)

    _pending: Optional[bytes] = None

    def write(self, data: bytes) -> None:
        dev = self.short if (data[:1] == b"\x10" and self.short is not None) else self.long
        if dev.write(data) < 0:
            raise OSError("hidapi write failed: %s" % (dev.error() if hasattr(dev, "error") else ""))

    def read(self, timeout: float) -> Optional[bytes]:
        if self._pending is not None:
            data, self._pending = self._pending, None
            return data
        end = time.monotonic() + max(timeout, 0)
        while True:
            data = self._poll()
            if data is not None:
                return data
            if time.monotonic() >= end:
                return None
            time.sleep(0.002)

    def close(self) -> None:
        for d in self._devs:
            try:
                d.close()
            except Exception:  # noqa: S110 - closing a handle that may already be gone
                pass


def open_transport(info: HidInfo) -> Transport:
    if sys.platform.startswith("linux"):
        return HidrawTransport(info.path)
    return HidapiTransport(info.path, info.short_path)


def _read_uevent(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
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


_HID_PAGES = {0xFF00, 0xFF43}       # Logitech vendor pages: receivers / USB devices, Bluetooth LE devices
_USAGE_SHORT = {0x0001}             # report 0x10
_USAGE_LONG = {0x0002, 0x0202}      # report 0x11 (0x0202 on Bluetooth LE)


def _hidapi_group_key(e: dict) -> str:
    """Collections of one interface differ only by '&colNN' / the trailing collection index."""
    p = e["path"].decode(errors="replace") if isinstance(e["path"], bytes) else e["path"]
    p = re.sub(r"&col\d+", "", p.lower())
    return re.sub(r"&\d{4}#", "#", p)


def _enum_hidapi() -> list[HidInfo]:
    try:
        import hid  # type: ignore
    except ImportError:
        return []
    groups: dict[str, dict] = {}
    for e in hid.enumerate(VENDOR_LOGITECH, 0):
        page, usage = e.get("usage_page", 0), e.get("usage", 0)
        if page not in _HID_PAGES:
            continue
        kind = "long" if usage in _USAGE_LONG else "short" if usage in _USAGE_SHORT else ""
        if not kind:
            continue
        g = groups.setdefault(_hidapi_group_key(e), {"e": e})
        g[kind] = e["path"].decode() if isinstance(e["path"], bytes) else e["path"]
        if kind == "long":
            g["e"] = e
    found = []
    for g in groups.values():
        if "long" not in g:
            continue
        e = g["e"]
        bt = e.get("bus_type", 0) == 2 or e.get("usage_page") == 0xFF43
        found.append(HidInfo(g["long"], e["vendor_id"], e["product_id"],
                             "bluetooth" if bt else "usb",
                             e.get("product_string") or "", "", "short" in g and not bt, "",
                             g.get("short", "") if not bt else ""))
    return found


def enumerate_hidpp() -> list[HidInfo]:
    return _enum_linux() if sys.platform.startswith("linux") else _enum_hidapi()
