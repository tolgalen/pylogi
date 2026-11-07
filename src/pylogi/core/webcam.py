# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Logitech UVC webcam control through the V4L2 ioctl interface (Linux).

Only the standard V4L2 controls are used (zoom, pan, tilt, focus, exposure,
white balance, brightness...), no external tool is required.
"""
from __future__ import annotations

import ctypes
import errno
import fcntl
import glob
import os
from dataclasses import dataclass

from ..i18n import N_
from .device import WEBCAM, Device

V4L2_CTRL_FLAG_DISABLED = 0x0001
V4L2_CTRL_FLAG_READ_ONLY = 0x0004
V4L2_CTRL_FLAG_INACTIVE = 0x0010
V4L2_CTRL_FLAG_NEXT_CTRL = 0x80000000
T_INT, T_BOOL, T_MENU, T_BUTTON, T_CLASS, T_INTMENU = 1, 2, 3, 4, 6, 9


class _QueryCtrl(ctypes.Structure):
    _fields_ = [("id", ctypes.c_uint32), ("type", ctypes.c_uint32),
                ("name", ctypes.c_char * 32), ("minimum", ctypes.c_int32),
                ("maximum", ctypes.c_int32), ("step", ctypes.c_int32),
                ("default_value", ctypes.c_int32), ("flags", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32 * 2)]


class _Control(ctypes.Structure):
    _fields_ = [("id", ctypes.c_uint32), ("value", ctypes.c_int32)]


class _QueryMenu(ctypes.Structure):
    _pack_ = 1
    _layout_ = "ms"
    _fields_ = [("id", ctypes.c_uint32), ("index", ctypes.c_uint32),
                ("name", ctypes.c_char * 32), ("reserved", ctypes.c_uint32)]


def _iowr(nr: int, size: int) -> int:
    return (3 << 30) | (size << 16) | (ord("V") << 8) | nr


VIDIOC_G_CTRL = _iowr(27, ctypes.sizeof(_Control))
VIDIOC_S_CTRL = _iowr(28, ctypes.sizeof(_Control))
VIDIOC_QUERYCTRL = _iowr(36, ctypes.sizeof(_QueryCtrl))
VIDIOC_QUERYMENU = _iowr(37, ctypes.sizeof(_QueryMenu))


@dataclass
class CamControl:
    id: int
    name: str
    type: int
    minimum: int
    maximum: int
    step: int
    default: int
    value: int
    flags: int
    menu: dict[int, str]

    @property
    def writable(self) -> bool:
        return not self.flags & (V4L2_CTRL_FLAG_READ_ONLY | V4L2_CTRL_FLAG_DISABLED)


class V4L2Camera:
    def __init__(self, path: str):
        self.path = path

    def _open(self) -> int:
        return os.open(self.path, os.O_RDWR | os.O_NONBLOCK)

    def controls(self) -> list[CamControl]:
        fd = self._open()
        out: list[CamControl] = []
        try:
            q = _QueryCtrl()
            q.id = V4L2_CTRL_FLAG_NEXT_CTRL
            while True:
                try:
                    fcntl.ioctl(fd, VIDIOC_QUERYCTRL, q)
                except OSError as e:
                    if e.errno in (errno.EINVAL, errno.ENOTTY):
                        break
                    raise
                nxt = q.id | V4L2_CTRL_FLAG_NEXT_CTRL
                if q.type in (T_INT, T_BOOL, T_MENU) and not q.flags & V4L2_CTRL_FLAG_DISABLED:
                    c = _Control()
                    c.id = q.id
                    try:
                        fcntl.ioctl(fd, VIDIOC_G_CTRL, c)
                        val = c.value
                    except OSError:
                        val = q.default_value
                    menu: dict[int, str] = {}
                    if q.type == T_MENU:
                        for i in range(q.minimum, q.maximum + 1):
                            m = _QueryMenu()
                            m.id, m.index = q.id, i
                            try:
                                fcntl.ioctl(fd, VIDIOC_QUERYMENU, m)
                                menu[i] = m.name.decode("utf-8", "replace")
                            except OSError:
                                pass
                    out.append(CamControl(q.id, q.name.decode("utf-8", "replace"),
                                          q.type, q.minimum, q.maximum,
                                          max(q.step, 1), q.default_value, val,
                                          q.flags, menu))
                q = _QueryCtrl()
                q.id = nxt
        finally:
            os.close(fd)
        return out

    def set(self, cid: int, value: int) -> None:
        fd = self._open()
        try:
            c = _Control()
            c.id, c.value = cid, int(value)
            fcntl.ioctl(fd, VIDIOC_S_CTRL, c)
        finally:
            os.close(fd)


class WebcamDevice(Device):
    kind = "webcam"
    connection = "USB"

    def __init__(self, path: str, name: str, uid: str):
        super().__init__()
        self.cam = V4L2Camera(path)
        self.path = path
        self.name = name
        self.uid = uid
        self.caps.add(WEBCAM)
        self.model_ids = []
        self.info.extra[N_("Node")] = path
        self.info.serial = ""

    vid_pid = "046d:0000"

    def device_key(self) -> str:
        if self.info.serial:
            return f"camera:{self.vid_pid}:serial:{self.info.serial.lower()}"
        return f"camera:{self.vid_pid}:{self.path}"

    def cam_controls(self) -> list[CamControl]:
        return self.cam.controls()

    def set_cam_control(self, cid: int, value: int) -> None:
        self.cam.set(cid, value)


def find_logitech_webcams() -> list[WebcamDevice]:
    cams = []
    for d in sorted(glob.glob("/sys/class/video4linux/video*")):
        try:
            with open(os.path.join(d, "index")) as f:
                if int(f.read().strip() or 0) != 0:
                    continue
            real = os.path.realpath(os.path.join(d, "device"))
            vid = _read(os.path.join(real, "..", "idVendor"))
            if vid.lower() != "046d":
                continue
            name = _read(os.path.join(d, "name")) or "Logitech webcam"
        except (OSError, ValueError):
            continue
        node = "/dev/" + os.path.basename(d)
        cam = WebcamDevice(node, name.split(":")[0].strip(),
                           f"cam:{_read(os.path.join(real, '..', 'serial')) or node}")
        cam.model_ids = [_read(os.path.join(real, '..', 'idProduct'))]
        cam.info.serial = _read(os.path.join(real, '..', 'serial')).upper()
        cam.vid_pid = f"046d:{cam.model_ids[0]}"
        cams.append(cam)
    return cams


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


# Names reported by the V4L2 driver (English); listed so they can be translated.
KNOWN_CONTROL_NAMES = [
    N_("Brightness"), N_("Contrast"), N_("Saturation"), N_("Hue"), N_("Gain"),
    N_("Sharpness"), N_("Gamma"), N_("Power Line Frequency"),
    N_("Backlight Compensation"), N_("White Balance, Automatic"),
    N_("White Balance Automatic"), N_("White Balance Temperature"),
    N_("Auto Exposure"), N_("Exposure Time, Absolute"), N_("Exposure (Absolute)"),
    N_("Exposure, Dynamic Framerate"), N_("Pan, Absolute"), N_("Pan (absolute)"),
    N_("Tilt, Absolute"), N_("Tilt (absolute)"), N_("Zoom, Absolute"),
    N_("Zoom (absolute)"), N_("Focus, Absolute"), N_("Focus (absolute)"),
    N_("Focus, Automatic Continuous"), N_("Focus, Auto"),
    N_("Manual Mode"), N_("Aperture Priority Mode"), N_("Disabled"),
    N_("50 Hz"), N_("60 Hz"), N_("Auto"),
]
