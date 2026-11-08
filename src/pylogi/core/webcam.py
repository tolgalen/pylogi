# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Logitech UVC webcam control through the V4L2 ioctl interface (Linux).

Only the standard V4L2 controls are used (zoom, pan, tilt, focus, exposure,
white balance, brightness...), no external tool is required.
"""
from __future__ import annotations

import ctypes
import errno
import glob
import os
from dataclasses import dataclass

from ..i18n import N_
from .device import WEBCAM, Device

try:
    import fcntl          # Linux only (V4L2); elsewhere there are no /dev/video* nodes to open
except ImportError:       # pragma: no cover
    fcntl = None  # type: ignore[assignment]

V4L2_CTRL_FLAG_DISABLED = 0x0001
V4L2_CTRL_FLAG_READ_ONLY = 0x0004
V4L2_CTRL_FLAG_INACTIVE = 0x0010
V4L2_CTRL_FLAG_WRITE_ONLY = 0x0040
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


class _FmtDesc(ctypes.Structure):
    _fields_ = [("index", ctypes.c_uint32), ("type", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                ("description", ctypes.c_char * 32), ("pixelformat", ctypes.c_uint32),
                ("mbus_code", ctypes.c_uint32), ("reserved", ctypes.c_uint32 * 3)]


class _FrmSizeEnum(ctypes.Structure):
    _fields_ = [("index", ctypes.c_uint32), ("pixel_format", ctypes.c_uint32), ("type", ctypes.c_uint32),
                ("width", ctypes.c_uint32), ("height", ctypes.c_uint32), ("stepwise", ctypes.c_uint32 * 4),
                ("reserved", ctypes.c_uint32 * 2)]


class _FrmIvalEnum(ctypes.Structure):
    _fields_ = [("index", ctypes.c_uint32), ("pixel_format", ctypes.c_uint32), ("width", ctypes.c_uint32),
                ("height", ctypes.c_uint32), ("type", ctypes.c_uint32), ("numerator", ctypes.c_uint32),
                ("denominator", ctypes.c_uint32), ("stepwise", ctypes.c_uint32 * 4),
                ("reserved", ctypes.c_uint32 * 2)]


class _PixFormat(ctypes.Structure):
    _fields_ = [("width", ctypes.c_uint32), ("height", ctypes.c_uint32), ("pixelformat", ctypes.c_uint32),
                ("field", ctypes.c_uint32), ("bytesperline", ctypes.c_uint32), ("sizeimage", ctypes.c_uint32),
                ("colorspace", ctypes.c_uint32), ("priv", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                ("ycbcr_enc", ctypes.c_uint32), ("quantization", ctypes.c_uint32), ("xfer_func", ctypes.c_uint32)]


class _FormatUnion(ctypes.Union):
    _fields_ = [("pix", _PixFormat), ("raw", ctypes.c_char * 200), ("_align", ctypes.c_void_p)]


class _Format(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("fmt", _FormatUnion)]


class _CaptureParm(ctypes.Structure):
    _fields_ = [("capability", ctypes.c_uint32), ("capturemode", ctypes.c_uint32),
                ("numerator", ctypes.c_uint32), ("denominator", ctypes.c_uint32),
                ("extendedmode", ctypes.c_uint32), ("readbuffers", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32 * 4)]


class _ParmUnion(ctypes.Union):
    _fields_ = [("capture", _CaptureParm), ("raw", ctypes.c_char * 200)]


class _StreamParm(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("parm", _ParmUnion)]


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
VIDIOC_ENUM_FMT = _iowr(2, ctypes.sizeof(_FmtDesc))
VIDIOC_G_FMT = _iowr(4, ctypes.sizeof(_Format))
VIDIOC_S_FMT = _iowr(5, ctypes.sizeof(_Format))
VIDIOC_G_PARM = _iowr(21, ctypes.sizeof(_StreamParm))
VIDIOC_S_PARM = _iowr(22, ctypes.sizeof(_StreamParm))
VIDIOC_ENUM_FRAMESIZES = _iowr(74, ctypes.sizeof(_FrmSizeEnum))
VIDIOC_ENUM_FRAMEINTERVALS = _iowr(75, ctypes.sizeof(_FrmIvalEnum))
BUF_TYPE_VIDEO_CAPTURE = 1
FRMSIZE_DISCRETE = 1


def fourcc(code: int) -> str:
    return code.to_bytes(4, "little").decode("ascii", "replace").strip()


def fourcc_code(text: str) -> int:
    return int.from_bytes(text.ljust(4)[:4].encode("ascii"), "little")


@dataclass
class VideoMode:
    """One capture mode: pixel format (FourCC such as MJPG / YUYV / H264), size and the frame rates it supports."""
    fmt: str
    width: int
    height: int
    fps: list[int]

    @property
    def size(self) -> str:
        return f"{self.width}x{self.height}"


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

    @property
    def is_button(self) -> bool:
        return self.type == T_BUTTON

    @property
    def inactive(self) -> bool:
        return bool(self.flags & V4L2_CTRL_FLAG_INACTIVE)


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
                if q.type in (T_INT, T_BOOL, T_MENU, T_INTMENU, T_BUTTON) and not q.flags & V4L2_CTRL_FLAG_DISABLED:
                    c = _Control()
                    c.id = q.id
                    val = q.default_value
                    if q.type != T_BUTTON:
                        try:
                            fcntl.ioctl(fd, VIDIOC_G_CTRL, c)
                            val = c.value
                        except OSError:
                            pass
                    menu: dict[int, str] = {}
                    if q.type == T_INTMENU:             # the entries are 64-bit numbers
                        for i in range(q.minimum, q.maximum + 1):
                            m = _QueryMenu()
                            m.id, m.index = q.id, i
                            try:
                                fcntl.ioctl(fd, VIDIOC_QUERYMENU, m)
                                menu[i] = str(int.from_bytes(bytes(m.name)[:8], "little", signed=True))
                            except OSError:
                                pass
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

    # -- video formats (pixel format, size, frame rate)
    def modes(self) -> list[VideoMode]:
        fd = self._open()
        out: list[VideoMode] = []
        try:
            idx = 0
            while idx < 64:
                d = _FmtDesc()
                d.index, d.type = idx, BUF_TYPE_VIDEO_CAPTURE
                try:
                    fcntl.ioctl(fd, VIDIOC_ENUM_FMT, d)
                except OSError:
                    break
                idx += 1
                s = 0
                while s < 128:
                    fs = _FrmSizeEnum()
                    fs.index, fs.pixel_format = s, d.pixelformat
                    try:
                        fcntl.ioctl(fd, VIDIOC_ENUM_FRAMESIZES, fs)
                    except OSError:
                        break
                    s += 1
                    if fs.type != FRMSIZE_DISCRETE:
                        break
                    out.append(VideoMode(fourcc(d.pixelformat), fs.width, fs.height,
                                         self._rates(fd, d.pixelformat, fs.width, fs.height)))
        finally:
            os.close(fd)
        return out

    @staticmethod
    def _rates(fd: int, pixfmt: int, w: int, h: int) -> list[int]:
        rates: set[int] = set()
        for i in range(64):
            iv = _FrmIvalEnum()
            iv.index, iv.pixel_format, iv.width, iv.height = i, pixfmt, w, h
            try:
                fcntl.ioctl(fd, VIDIOC_ENUM_FRAMEINTERVALS, iv)
            except OSError:
                break
            if iv.type != 1 or not iv.numerator:
                break
            rates.add(round(iv.denominator / iv.numerator))
        return sorted(rates, reverse=True)

    def mode(self) -> tuple[str, int, int, int] | None:
        """(fourcc, width, height, fps) in force, or None if the driver does not say."""
        fd = self._open()
        try:
            f = _Format()
            f.type = BUF_TYPE_VIDEO_CAPTURE
            p = _StreamParm()
            p.type = BUF_TYPE_VIDEO_CAPTURE
            try:
                fcntl.ioctl(fd, VIDIOC_G_FMT, f)
                fcntl.ioctl(fd, VIDIOC_G_PARM, p)
            except OSError:
                return None
            cap = p.parm.capture
            fps = round(cap.denominator / cap.numerator) if cap.numerator else 0
            return fourcc(f.fmt.pix.pixelformat), f.fmt.pix.width, f.fmt.pix.height, fps
        finally:
            os.close(fd)

    def set_mode(self, fmt: str, width: int, height: int, fps: int) -> None:
        fd = self._open()
        try:
            f = _Format()
            f.type = BUF_TYPE_VIDEO_CAPTURE
            f.fmt.pix.width, f.fmt.pix.height = width, height
            f.fmt.pix.pixelformat = fourcc_code(fmt)
            f.fmt.pix.field = 1                       # V4L2_FIELD_NONE
            fcntl.ioctl(fd, VIDIOC_S_FMT, f)
            if fps:
                p = _StreamParm()
                p.type = BUF_TYPE_VIDEO_CAPTURE
                p.parm.capture.numerator, p.parm.capture.denominator = 1, fps
                fcntl.ioctl(fd, VIDIOC_S_PARM, p)
        finally:
            os.close(fd)

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
    usb_path = ""             # where the camera is plugged in (e.g. "1-2.3"); stable, unlike /dev/videoN
    legacy_uid = ""           # identifiers older versions used (the /dev node), to carry settings over
    legacy_key = ""

    def device_key(self) -> str:
        if self.info.serial:
            return f"camera:{self.vid_pid}:serial:{self.info.serial.lower()}"
        return f"camera:{self.vid_pid}:{self.usb_path or self.path}"

    def cam_controls(self) -> list[CamControl]:
        return self.cam.controls()

    def set_cam_control(self, cid: int, value: int) -> None:
        self.cam.set(cid, value)

    def video_modes(self) -> list[VideoMode]:
        return self.cam.modes()

    def video_mode(self) -> tuple[str, int, int, int] | None:
        return self.cam.mode()

    def set_video_mode(self, fmt: str, width: int, height: int, fps: int) -> None:
        self.cam.set_mode(fmt, width, height, fps)


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
        serial = _read(os.path.join(real, '..', 'serial'))
        usb_path = os.path.basename(os.path.realpath(os.path.join(real, "..")))
        cam = WebcamDevice(node, name.split(":")[0].strip(), f"cam:{serial or 'usb-' + usb_path}")
        cam.usb_path = usb_path
        cam.legacy_uid = f"cam:{serial or node}"
        cam.model_ids = [_read(os.path.join(real, '..', 'idProduct'))]
        cam.info.serial = serial.upper()
        cam.vid_pid = f"046d:{cam.model_ids[0]}"
        cam.legacy_key = f"camera:{cam.vid_pid}:{node}"
        cam.info.extra[N_("USB port")] = usb_path
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
