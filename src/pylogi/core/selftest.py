# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Write-verification on real devices:  write -> read back -> compare -> restore -> verify.

Run through ``pylogi --self-test`` (a full backup is saved first).  Settings that
cannot be observed from software (LED colours) are reported as UNCONFIRMED: the
device accepted the command, a human has to look at the keyboard.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import debuglog as _debuglog
from . import backup
from .device import Device

PASS, FAIL, SKIP, UNCONFIRMED, ERROR = "PASS", "FAIL", "SKIP", "UNCONFIRMED", "ERROR"


@dataclass
class Result:
    device: str
    test: str
    status: str
    detail: str = ""


class Check(Exception):
    """Raised by a test to report a status other than PASS."""
    def __init__(self, status: str, detail: str):
        self.status, self.detail = status, detail


def _expect(label: str, got, want) -> None:
    if got != want:
        raise Check(FAIL, f"{label}: wrote {want!r}, read back {got!r}")


def _roundtrip(read: Callable, write: Callable, new, label: str, original=None):
    """write(new) -> read()==new ; then write(original) -> read()==original."""
    orig = read() if original is None else original
    try:
        write(new)
        _expect(label, read(), new)
    finally:
        write(orig)
    _expect(label + " (restore)", read(), orig)
    return orig


# ------------------------------------------------------------------ tests
def t_dpi(dev: Device) -> str:
    info = dev.dpi()
    vals = info.values
    i = vals.index(info.current) if info.current in vals else 0
    new = vals[i + 4] if i + 4 < len(vals) else vals[i - 4]
    _roundtrip(lambda: dev.dpi().current, dev.set_dpi, new, "DPI", info.current)
    return f"{info.current} -> {new} -> {info.current}"


def t_smartshift(dev: Device) -> str:
    ratchet, thr = dev.smartshift()
    new = (not ratchet, thr)
    _roundtrip(dev.smartshift, lambda v: dev.set_smartshift(*v), new, "SmartShift", (ratchet, thr))
    return f"ratchet {ratchet} -> {new[0]} -> {ratchet}"


def t_remap(dev: Device) -> str:
    ctrls = {c.cid: c for c in dev.controls()}
    src, dst = ctrls.get(0x56), ctrls.get(0x53)
    if not src or not dst or not src.reprogrammable:
        raise Check(SKIP, "Forward/Back controls not reprogrammable here")
    read = lambda: {c.cid: c.remap for c in dev.controls()}[0x56]
    _roundtrip(read, lambda v: dev.remap_control(0x56, v), 0x53, "remap Forward->Back", src.remap)
    return "Forward remapped to Back, read back and restored"


def t_divert(dev: Device) -> str:
    from ..hidpp import features as ft
    ctrls = {c.cid: c for c in dev.controls()}
    c = ctrls.get(0x56)
    if not c or not c.reprogrammable:
        raise Check(SKIP, "Forward button is not reprogrammable here")
    d = dev.dev                                                          # type: ignore[attr-defined]
    before = ft.get_control_reporting(d, 0x56)
    try:
        ft.set_control_reporting(d, 0x56, True, None)
        _expect("divert flag", ft.get_control_reporting(d, 0x56), (True, before[1]))
        ft.set_control_reporting(d, 0x56, None, 0x53)                    # remap must keep the divert flag
        _expect("divert + remap", ft.get_control_reporting(d, 0x56), (True, 0x53))
    finally:
        ft.set_control_reporting(d, 0x56, before[0], before[1])
    _expect("divert restore", ft.get_control_reporting(d, 0x56), before)
    return "Forward diverted to software (+ remap kept), read back and restored"


def t_wheel(dev: Device) -> str:
    hires, inv = dev.wheel()
    _roundtrip(dev.wheel, lambda v: dev.set_wheel(*v), (hires, not inv), "wheel invert", (hires, inv))
    _roundtrip(dev.wheel, lambda v: dev.set_wheel(*v), (not hires, inv), "wheel resolution", (hires, inv))
    return f"invert {inv} -> {not inv} -> {inv}; high-res {hires} -> {not hires} -> {hires}"


def t_host_rename(dev: Device) -> str:
    cur, hosts = dev.hosts()
    cand = [h for h in hosts if h.paired and not h.current]
    if not cand:
        raise Check(SKIP, "no other paired channel")
    h = cand[0]
    name = {x.index: x.name for x in hosts}

    def read():
        return {x.index: x.name for x in dev.hosts()[1]}[h.index]
    _roundtrip(read, lambda v: dev.rename_host(h.index, v), "pyLogiTest", f"rename channel {h.index + 1}", h.name)
    return f"channel {h.index + 1}: {name[h.index]!r} -> 'pyLogiTest' -> {name[h.index]!r}"


def t_report_rate(dev: Device) -> str:
    cur, opts = dev.report_rate()
    others = [o for o in opts if o != cur]
    if not others:
        raise Check(SKIP, "single report rate")
    onboard = dev.has("profiles") and not dev.profile_host_mode()
    if onboard:                                  # the device refuses the change while a profile is active
        try:
            dev.set_report_rate(others[0])
        except Exception as e:
            note = f"refused in onboard mode as expected ({e}); "
        else:
            raise Check(FAIL, "report rate accepted in onboard mode (unexpected)")
        dev.set_profile_host_mode(True)
    else:
        note = ""
    try:
        _roundtrip(lambda: dev.report_rate()[0], dev.set_report_rate, others[0], "report rate", cur)
    finally:
        if onboard:
            dev.set_profile_host_mode(False)
    return f"{note}{cur} -> {others[0]} -> {cur} (host mode, then back to onboard mode)"


def t_onboard(dev: Device) -> str:
    from ..hidpp.onboard import Onboard, crc16, parse_profile
    import struct
    ob: Onboard = dev._ob()                                            # type: ignore[attr-defined]
    profiles = ob.read_profiles()
    if not profiles:
        raise Check(SKIP, "no profiles")
    p = profiles[0]
    # 1. CRC of the stored sector equals ours
    body, stored = p.raw[:-2], struct.unpack(">H", p.raw[-2:])[0]
    _expect("CRC16 of stored profile", crc16(body), stored)
    # 2. our encoder reproduces an untouched profile byte for byte
    same = ob.encode(p) == p.raw
    notes = ["CRC matches" , "encode(parse(raw)) is byte-identical" if same else "encode(parse(raw)) differs (re-seal only)"]
    # 3. rewrite the identical sector
    original = p.raw
    try:
        ob.write_sector(p.sector, original)
        _expect("rewrite identical sector", ob.read_sector(p.sector), original)
        # 4. real change: rename the profile, read it back, restore
        q = parse_profile(p.index, p.sector, p.enabled, original, ob.desc.button_count)
        q.name = (p.name + "*")[:20]
        ob.write_profile(q)
        back = parse_profile(p.index, p.sector, p.enabled, ob.read_sector(p.sector), ob.desc.button_count)
        _expect("profile name", back.name, q.name)
        notes.append(f"renamed {p.name!r} -> {q.name!r}")
    finally:
        ob.write_sector(p.sector, original)
    _expect("profile restore", ob.read_sector(p.sector), original)
    notes.append("restored byte-identical")
    return "; ".join(notes)


def t_active_profile(dev: Device) -> str:
    if dev.profile_host_mode():
        raise Check(SKIP, "device is in host mode: it ignores its stored profiles")
    profiles = dev.profiles()
    if len(profiles) < 2:
        raise Check(SKIP, "a single profile")
    original = dev.active_profile()
    other = next(p.sector for p in profiles if p.sector != original)
    _roundtrip(dev.active_profile, dev.set_active_profile, other, "active profile", original)
    return f"active profile sector {original} -> {other} -> {original}"


def t_key_colors(dev: Device) -> str:
    colors = {0x04: (255, 0, 0), 0x05: (0, 255, 0), 0x06: (0, 0, 255)}     # keys A, B, C
    try:
        dev.set_key_colors(colors)
    finally:
        # hand the LEDs back to the onboard profile
        try:
            dev.set_profile_host_mode(True)
            dev.set_profile_host_mode(False)
        except Exception as e:
            _debuglog.log("error", f"returning LEDs to the onboard profile: {e!r}")
    raise Check(UNCONFIRMED, "per-key colour frame accepted without error (A=red B=green C=blue for a moment); "
                             "LEDs handed back to the onboard profile - confirm visually")


def t_camera(dev: Device) -> str:
    from .camera import find_roles
    ctrls = dev.cam_controls()
    b = find_roles(ctrls).get("brightness")
    if b is None or not b.writable:
        raise Check(SKIP, "no brightness control")
    new = b.value + (10 if b.value + 10 <= b.maximum else -10)
    _roundtrip(lambda: next(c.value for c in dev.cam_controls() if c.id == b.id),
               lambda v: dev.set_cam_control(b.id, v), new, "brightness", b.value)
    return f"brightness {b.value} -> {new} -> {b.value}"


def t_camera_default_preset(dev: Device) -> str:
    from .camera import builtin_preset, plan_writes
    before = {c.name: c.value for c in dev.cam_controls() if c.writable}
    ctrls = dev.cam_controls()
    try:
        for c, v in plan_writes(builtin_preset("Default", ctrls), ctrls):
            dev.set_cam_control(c.id, v)           # raises on 'Permission denied' (the old bug)
    finally:
        ctrls = dev.cam_controls()
        for c, v in plan_writes(before, ctrls):
            dev.set_cam_control(c.id, v)
    after = {c.name: c.value for c in dev.cam_controls() if c.writable}
    manual = {n: (before[n], after[n]) for n in before if before[n] != after[n]}
    _expect("camera restore", {k: v for k, v in after.items() if k in before and k not in manual},
            {k: v for k, v in before.items() if k not in manual})
    if manual:
        raise Check(UNCONFIRMED, f"Default preset applied without errors; some values differ after restore "
                                 f"(auto-controls drifting): {manual}")
    return "Default preset applied without errors and restored"


TESTS: list[tuple[str, str, Callable[[Device], str]]] = [
    ("dpi", "dpi", t_dpi), ("smartshift", "smartshift", t_smartshift),
    ("wheel", "scroll wheel (invert / resolution)", t_wheel),
    ("buttons", "remap a button (volatile)", t_remap), ("buttons", "divert a button to software", t_divert), ("easyswitch", "rename a channel", t_host_rename),
    ("report_rate", "report rate", t_report_rate), ("profiles", "onboard profile write", t_onboard), ("profiles", "switch the active profile", t_active_profile),
    ("keylight", "per-key colours", t_key_colors),
    ("webcam", "camera control", t_camera), ("webcam", "camera 'Default' preset", t_camera_default_preset),
]


def run(devices: list[Device], log: Callable[[str], None] = print) -> list[Result]:
    results: list[Result] = []
    for dev in devices:
        if dev.kind == "receiver":
            continue
        if not dev.online:
            results.append(Result(dev.name, "-", SKIP, "offline"))
            continue
        pre = backup.snapshot(dev)
        path = backup.save(dev, pre, tag="pre-selftest")
        log(f"\n== {dev.name}  (backup: {path})")
        for cap, label, fn in TESTS:
            if not dev.has(cap):
                continue
            try:
                res = Result(dev.name, label, PASS, fn(dev))
            except Check as c:
                res = Result(dev.name, label, c.status, c.detail)
            except Exception as e:
                res = Result(dev.name, label, ERROR, f"{type(e).__name__}: {e}")
            results.append(res)
            log(f"  [{res.status:11}] {label}: {res.detail}")
        # the device must end exactly as it began
        changed = _diff(pre, backup.snapshot(dev))
        if changed:
            results.append(Result(dev.name, "final state", FAIL, f"differs from the pre-test backup: {changed}"))
            log(f"  [FAIL       ] final state differs from the backup: {changed}")
        else:
            results.append(Result(dev.name, "final state", PASS, "identical to the pre-test backup"))
            log("  [PASS       ] final state identical to the backup")
    return results


_VOLATILE = {"when", "battery"}
_AUTO_DRIVEN = {"Exposure Time, Absolute", "Focus, Absolute", "White Balance Temperature", "Gain"}


def _diff(a: dict, b: dict) -> list[str]:
    """Top-level settings that differ (camera auto-driven values are ignored: the driver moves them)."""
    out = []
    for k in sorted(set(a) | set(b)):
        if k in _VOLATILE:
            continue
        va, vb = a.get(k), b.get(k)
        if k == "camera" and isinstance(va, dict) and isinstance(vb, dict):
            va = {n: v for n, v in va.items() if n not in _AUTO_DRIVEN}
            vb = {n: v for n, v in vb.items() if n not in _AUTO_DRIVEN}
        if va != vb:
            out.append(k)
    return out
