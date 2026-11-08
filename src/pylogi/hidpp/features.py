# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""HID++ 2.0 feature helpers: battery, Easy-Switch, DPI, SmartShift, rates, lighting."""
from __future__ import annotations

import struct

from ..core.buttons import CID_NAMES
from ..core.types import (RGB, Battery, Control, DpiInfo, HostInfo, RgbZone,
                          Unsupported)
from .constants import F
from .protocol import Hidpp20Device, HidppError


def _need(d: Hidpp20Device, fid: int) -> None:
    if not d.has(fid):
        raise Unsupported(f"{fid:#06x}")


# ---------------------------------------------------------------- battery
_VOLT_TABLE = [(4186, 100), (4067, 90), (3989, 80), (3922, 70), (3859, 60),
               (3811, 50), (3778, 40), (3751, 30), (3717, 20), (3671, 10),
               (3646, 5), (3579, 2), (3500, 0)]


def _volt_to_pct(mv: int) -> int:
    for v, p in _VOLT_TABLE:
        if mv >= v:
            return p
    return 0


def read_battery(d: Hidpp20Device) -> Battery:
    if d.has(F.UNIFIED_BATTERY):
        r = d.call(F.UNIFIED_BATTERY, 1)
        pct, level, status = r[0], r[1], r[2]
        if pct == 0:  # devices reporting only coarse levels
            pct = {8: 90, 4: 55, 2: 20, 1: 5}.get(level, 0)
        return Battery(pct, status in (1, 2, 3), "")
    if d.has(F.BATTERY_STATUS):
        r = d.call(F.BATTERY_STATUS, 0)
        return Battery(r[0], r[2] in (1, 2, 3, 4), "")
    if d.has(F.BATTERY_VOLTAGE):
        r = d.call(F.BATTERY_VOLTAGE, 0)
        mv, flags = struct.unpack(">HB", r[:3])
        return Battery(_volt_to_pct(mv), bool(flags & 0x80), f"{mv} mV")
    raise Unsupported("battery")


# ------------------------------------------------------------ Easy-Switch
# bus codes of HOSTS_INFO (4 = Bluetooth LE and 5 = Bolt confirmed on a MX Anywhere 3S;
# 1 on a G915 paired through Lightspeed).  Others are best guesses.
_BUS = {0: "", 1: "lightspeed", 2: "unifying", 3: "bluetooth", 4: "bluetooth", 5: "bolt"}


def read_hosts(d: Hidpp20Device) -> tuple[int, list[HostInfo]]:
    """Return (current host index, hosts)."""
    if d.has(F.HOSTS_INFO):
        r = d.call(F.HOSTS_INFO, 0)
        num, cur = r[2], r[3]
        hosts = []
        for i in range(num):
            h = HostInfo(i, current=(i == cur))
            try:
                info = d.call(F.HOSTS_INFO, 1, bytes([i]))
                h.paired = info[1] != 0
                h.bus = _BUS.get(info[2], "")
                n = info[4]
                h.max_name = info[5]
                name = b""
                while h.paired and len(name) < n:
                    chunk = d.call(F.HOSTS_INFO, 3, bytes([i, len(name)]))
                    piece = chunk[2:].split(b"\0")[0]
                    if not piece:
                        break
                    name += piece
                h.name = name[:n].decode("utf-8", "replace")
            except HidppError:
                pass
            hosts.append(h)
        return cur, hosts
    _need(d, F.CHANGE_HOST)
    r = d.call(F.CHANGE_HOST, 0)
    num, cur = r[0], r[1]
    return cur, [HostInfo(i, paired=True, current=(i == cur)) for i in range(num)]


def switch_host(d: Hidpp20Device, host: int) -> None:
    """Move the device to another host. The device disconnects from us."""
    _need(d, F.CHANGE_HOST)
    try:
        d.call(F.CHANGE_HOST, 1, bytes([host]), timeout=0.5)
    except HidppError:
        pass  # the device often drops the link before replying


def rename_host(d: Hidpp20Device, host: int, name: str) -> None:
    """Write the friendly name of an Easy-Switch channel (HOSTS_INFO fn 4, 14 byte chunks)."""
    _need(d, F.HOSTS_INFO)
    data = name.encode("utf-8")
    limit = d.call(F.HOSTS_INFO, 1, bytes([host]))[5] or 24
    data = data[:limit]
    # keep UTF-8 valid after truncation
    data = data.decode("utf-8", "ignore").encode("utf-8")
    padded = data + b"\0"
    for off in range(0, len(padded), 14):
        d.call(F.HOSTS_INFO, 4, bytes([host, off]) + padded[off:off + 14].ljust(14, b"\0"), long=True)


def read_identity(d: Hidpp20Device) -> tuple[str, str]:
    """(unit id hex, serial number) from DEVICE_FW_VERSION; either may be empty."""
    unit = serial = ""
    if d.has(F.FW_VERSION):
        unit = d.call(F.FW_VERSION, 0)[1:5].hex()
        try:
            raw = d.call(F.FW_VERSION, 2)[:12].split(b"\0")[0].decode("ascii", "ignore")
            serial = raw if raw.isalnum() else ""
        except HidppError:
            pass
    return unit, serial


_FW_KINDS = {0: "", 1: "bootloader ", 2: "hardware "}


def read_firmware(d: Hidpp20Device) -> str:
    """Firmware versions from DEVICE_FW_VERSION, e.g. "RQR 24.01.B0012, bootloader BOT 01.00"; "" if unknown."""
    if not d.has(F.FW_VERSION):
        return ""
    parts = []
    try:
        count = d.call(F.FW_VERSION, 0)[0]
        for entity in range(min(count, 8)):
            r = d.call(F.FW_VERSION, 1, bytes([entity]))
            kind = r[0] & 0x0F
            if kind > 2:
                continue
            prefix = r[1:4].decode("ascii", "ignore").strip("\0 ")
            text = f"{prefix} {r[4]:02X}.{r[5]:02X}".strip()
            if kind == 0 and (r[6] or r[7]):
                text += f".B{r[6]:02X}{r[7]:02X}"
            parts.append(_FW_KINDS[kind] + text if kind else text)
    except (HidppError, IndexError):
        pass
    return ", ".join(parts)


def has_fn_lock(d: Hidpp20Device) -> bool:
    return d.has(F.FN_INVERSION) or d.has(F.NEW_FN_INVERSION)


def read_fn_lock(d: Hidpp20Device) -> bool:
    fid = F.FN_INVERSION if d.has(F.FN_INVERSION) else F.NEW_FN_INVERSION
    _need(d, fid)
    return bool(d.call(fid, 0)[0] & 1)


def write_fn_lock(d: Hidpp20Device, on: bool) -> None:
    fid = F.FN_INVERSION if d.has(F.FN_INVERSION) else F.NEW_FN_INVERSION
    _need(d, fid)
    d.call(fid, 1, bytes([1 if on else 0]))


# -------------------------------------------------------------------- DPI
def read_dpi(d: Hidpp20Device, sensor: int = 0) -> DpiInfo:
    _need(d, F.ADJUSTABLE_DPI)
    lst = d.call(F.ADJUSTABLE_DPI, 1, bytes([sensor]))
    words = struct.unpack(">7H", lst[1:15])
    values: list[int] = []
    step = 0
    for w in words:
        if w == 0:
            break
        if w >= 0xE000:
            step = w - 0xE000
        else:
            values.append(w)
    if step and len(values) >= 2:
        values = list(range(values[0], values[1] + 1, step))
    cur = d.call(F.ADJUSTABLE_DPI, 2, bytes([sensor]))
    dpi, default = struct.unpack(">HH", cur[1:5])
    return DpiInfo(values, dpi, default)


def write_dpi(d: Hidpp20Device, dpi: int, sensor: int = 0) -> None:
    _need(d, F.ADJUSTABLE_DPI)
    d.call(F.ADJUSTABLE_DPI, 3, bytes([sensor]) + struct.pack(">H", dpi))


# ------------------------------------------------------------- SmartShift
def read_smartshift(d: Hidpp20Device) -> tuple[bool, int]:
    """Return (ratchet mode, auto-disengage threshold; 255 = always ratchet)."""
    if d.has(F.SMART_SHIFT_ENH):
        r = d.call(F.SMART_SHIFT_ENH, 1)
    else:
        _need(d, F.SMART_SHIFT)
        r = d.call(F.SMART_SHIFT, 0)
    return r[0] == 2 if d.has(F.SMART_SHIFT_ENH) else r[0] == 1, r[1]


def write_smartshift(d: Hidpp20Device, ratchet: bool, threshold: int) -> None:
    if d.has(F.SMART_SHIFT_ENH):
        # mode 2 = ratchet, 1 = free spin on the enhanced feature
        d.call(F.SMART_SHIFT_ENH, 2, bytes([2 if ratchet else 1, threshold & 0xFF]))
        return
    _need(d, F.SMART_SHIFT)
    d.call(F.SMART_SHIFT, 1, bytes([1 if ratchet else 2, threshold & 0xFF]))


# ------------------------------------------------------------------ wheel
def read_wheel(d: Hidpp20Device) -> tuple[bool, bool]:
    """(high-resolution scrolling, inverted direction) of the main wheel (HIRES_WHEEL mode)."""
    _need(d, F.HIRES_WHEEL)
    mode = d.call(F.HIRES_WHEEL, 1)[0]
    return bool(mode & 0x02), bool(mode & 0x04)


def write_wheel(d: Hidpp20Device, hires: bool | None = None, invert: bool | None = None) -> None:
    """Change only the given bits; bit0 (events diverted to software) is preserved."""
    _need(d, F.HIRES_WHEEL)
    mode = d.call(F.HIRES_WHEEL, 1)[0]
    for bit, val in ((0x02, hires), (0x04, invert)):
        if val is not None:
            mode = (mode | bit) if val else (mode & ~bit)
    d.call(F.HIRES_WHEEL, 2, bytes([mode & 0xFF]))


# ------------------------------------------------------------ report rate
_EXT_RATES = ["8 ms", "4 ms", "2 ms", "1 ms", "500 µs", "250 µs", "125 µs"]


def read_report_rate(d: Hidpp20Device) -> tuple[str, list[str]]:
    if d.has(F.EXT_REPORT_RATE):
        flags = struct.unpack(">H", d.call(F.EXT_REPORT_RATE, 1)[:2])[0]
        opts = [n for i, n in enumerate(_EXT_RATES) if flags & (1 << i)]
        cur = _EXT_RATES[d.call(F.EXT_REPORT_RATE, 2)[0]]
        return cur, opts
    _need(d, F.REPORT_RATE)
    flags = d.call(F.REPORT_RATE, 0)[0]
    opts = [f"{i + 1} ms" for i in range(8) if flags & (1 << i)]
    return f"{d.call(F.REPORT_RATE, 1)[0]} ms", opts


def write_report_rate(d: Hidpp20Device, label: str) -> None:
    if d.has(F.EXT_REPORT_RATE):
        d.call(F.EXT_REPORT_RATE, 3, bytes([_EXT_RATES.index(label)]))
        return
    _need(d, F.REPORT_RATE)
    d.call(F.REPORT_RATE, 2, bytes([int(label.split()[0])]))


# ----------------------------------------------------- reprogrammable keys
def read_controls(d: Hidpp20Device) -> list[Control]:
    _need(d, F.REPROG_CONTROLS_V4)
    n = d.call(F.REPROG_CONTROLS_V4, 0)[0]
    out = []
    for i in range(n):
        r = d.call(F.REPROG_CONTROLS_V4, 1, bytes([i]))
        cid, task = struct.unpack(">HH", r[:4])
        flags = r[4] | (r[8] << 8 if len(r) > 8 else 0)
        c = Control(cid, task, CID_NAMES.get(cid, f"Control {cid:#06x}"), flags)
        try:
            rep = d.call(F.REPROG_CONTROLS_V4, 2, struct.pack(">H", cid))
            c.divert = bool(rep[2] & 0x01)
            c.remap = struct.unpack(">H", rep[3:5])[0]
            if c.remap == cid:
                c.remap = 0
        except HidppError:
            pass
        out.append(c)
    return out


def write_control(d: Hidpp20Device, cid: int, remap: int) -> None:
    """Remap `cid` to another control id (0 = restore)."""
    set_control_reporting(d, cid, None, remap)


def get_control_reporting(d: Hidpp20Device, cid: int) -> tuple[bool, int]:
    """(diverted to software, remap target or 0) of one control."""
    _need(d, F.REPROG_CONTROLS_V4)
    rep = d.call(F.REPROG_CONTROLS_V4, 2, struct.pack(">H", cid))
    remap = struct.unpack(">H", rep[3:5])[0]
    return bool(rep[2] & 0x01), (0 if remap == cid else remap)


RAW_XY, RAW_XY_VALID = 0x10, 0x20


def set_control_reporting(d: Hidpp20Device, cid: int, divert: bool | None, remap: int | None,
                          raw_xy: bool | None = None) -> None:
    """Divert a control to software and/or remap it; None keeps the current value.

    flags: bit0 = divert, bit1 = 'divert valid' (the device only changes flagged fields),
    bit4 = raw XY (while the control is held the mouse reports its movement as notifications
    instead of moving the pointer: used by gestures), bit5 = 'raw XY valid'.
    A diverted control stops producing its normal HID event and reports its
    presses as notifications instead (see pylogi.core.actions).
    """
    _need(d, F.REPROG_CONTROLS_V4)
    cur_div, cur_remap = get_control_reporting(d, cid)
    div = cur_div if divert is None else divert
    target = (cur_remap if remap is None else remap) or cid
    flags = (0x01 if div else 0x00) | 0x02
    if raw_xy is not None:
        flags |= (RAW_XY if raw_xy else 0) | RAW_XY_VALID
    d.call(F.REPROG_CONTROLS_V4, 3, struct.pack(">HBH", cid, flags, target))


def parse_diverted(report: bytes, feature_index: int) -> list[int] | None:
    """Control ids currently held down, from a diverted-buttons notification (None if not one)."""
    if len(report) < 12 or report[0] != 0x11 or report[2] != feature_index or report[3] >> 4 != 0:
        return None
    return [c for c in struct.unpack(">4H", report[4:12]) if c]


def parse_raw_xy(report: bytes, feature_index: int) -> tuple[int, int] | None:
    """(dx, dy) from a raw-XY notification of a held, raw-XY-diverted control (None if not one)."""
    if len(report) < 8 or report[0] != 0x11 or report[2] != feature_index or report[3] >> 4 != 1:
        return None
    return struct.unpack(">hh", report[4:8])


# ------------------------------------------------------------- thumb wheel
def read_thumb(d: Hidpp20Device) -> tuple[bool, bool]:
    """(diverted to software, direction inverted) of the thumb wheel (THUMB_WHEEL, function 1)."""
    _need(d, F.THUMB_WHEEL)
    r = d.call(F.THUMB_WHEEL, 1)
    return bool(r[0] & 0x01), bool(r[1] & 0x01)


def write_thumb(d: Hidpp20Device, diverted: bool | None = None, invert: bool | None = None) -> None:
    """Change only the given fields (function 2: reporting mode, inversion)."""
    _need(d, F.THUMB_WHEEL)
    cur_div, cur_inv = read_thumb(d)
    div = cur_div if diverted is None else diverted
    inv = cur_inv if invert is None else invert
    d.call(F.THUMB_WHEEL, 2, bytes([1 if div else 0, 1 if inv else 0]))


def parse_thumb(report: bytes, feature_index: int) -> int | None:
    """Signed rotation from a thumb-wheel notification while it is diverted (None if not one)."""
    if len(report) < 6 or report[0] != 0x11 or report[2] != feature_index or report[3] >> 4 != 0:
        return None
    return struct.unpack(">h", report[4:6])[0]


# --------------------------------------------------------------- backlight
def read_backlight(d: Hidpp20Device) -> tuple[bool, int, int]:
    """Return (enabled, level, max level)."""
    _need(d, F.BACKLIGHT2)
    r = d.call(F.BACKLIGHT2, 0)
    enabled, _opts, supported, _effects, level = struct.unpack(">BBBHB", r[:6])
    levels = max(1, bin(supported).count("1")) if supported else 5
    return bool(enabled), level, max(levels, level)


def write_backlight(d: Hidpp20Device, enabled: bool, level: int) -> None:
    _need(d, F.BACKLIGHT2)
    cur = d.call(F.BACKLIGHT2, 0)
    opts = cur[1]
    dho, dhi, dpow = struct.unpack(">HHH", cur[6:12])
    d.call(F.BACKLIGHT2, 1, struct.pack(">BBBBHHH", 1 if enabled else 0, opts,
                                        0xFF, level, dho, dhi, dpow))


# ---------------------------------------------------------------- lighting
def key_id(usage: int) -> int:
    """HID usage code -> HID++ per-key lighting id."""
    return usage - 120 if usage >= 0xE0 else usage - 3


def has_per_key(d: Hidpp20Device) -> bool:
    return d.has(F.PER_KEY_LIGHTING_V2) or d.has(F.PER_KEY_LIGHTING)


def take_lighting_control(d: Hidpp20Device) -> None:
    """Switch the LEDs from the device's own effect to software control."""
    if d.has(F.RGB_EFFECTS):
        try:
            d.call(F.RGB_EFFECTS, 5, bytes([1, 3, 5]))
        except HidppError:
            pass


def write_key_colors(d: Hidpp20Device, colors: dict[int, RGB]) -> None:
    """Set individual keys (keys given as HID++ key ids)."""
    fid = F.PER_KEY_LIGHTING_V2 if d.has(F.PER_KEY_LIGHTING_V2) else F.PER_KEY_LIGHTING
    _need(d, fid)
    take_lighting_control(d)
    items = sorted(colors.items())
    for i in range(0, len(items), 4):
        payload = b"".join(bytes([k, *rgb]) for k, rgb in items[i:i + 4])
        d.call(fid, 1, payload, long=True)
    d.call(fid, 7, b"", long=True)  # commit the frame


def read_key_presence(d: Hidpp20Device) -> set[int] | None:
    """HID++ key ids the keyboard says it has (bitmap in the per-key lighting 'info' reply), or None.

    Verified on a G915 TKL: the numpad ids (80-96) are absent and the ISO key (97) is present.
    """
    if not d.has(F.PER_KEY_LIGHTING_V2):
        return None
    bits = d.call(F.PER_KEY_LIGHTING_V2, 0, b"\x00")[2:]
    ids = {i * 8 + b for i, byte in enumerate(bits) for b in range(8) if byte >> b & 1}
    return ids if set(range(1, 27)) <= ids else None          # not a key bitmap: ignore (A-Z must exist)


def read_zones(d: Hidpp20Device) -> list[RgbZone]:
    if d.has(F.RGB_EFFECTS):
        fid = F.RGB_EFFECTS
    elif d.has(F.COLOR_LED_EFFECTS):
        fid = F.COLOR_LED_EFFECTS
    else:
        raise Unsupported("zones")
    try:
        n = d.call(fid, 0, bytes([0xFF, 0xFF, 0x00]))[3]
    except (HidppError, IndexError):
        n = 1
    return [RgbZone(i, f"Zone {i + 1}") for i in range(max(1, min(n, 8)))]


def write_zone_color(d: Hidpp20Device, zone: int, rgb: RGB) -> None:
    """Fixed colour effect on an RGB zone."""
    fid = F.RGB_EFFECTS if d.has(F.RGB_EFFECTS) else F.COLOR_LED_EFFECTS
    _need(d, fid)
    d.call(fid, 1, bytes([zone, 0x01, *rgb]) + bytes(10) + bytes([0x02]), long=True)
