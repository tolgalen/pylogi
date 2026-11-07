# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Full snapshot / restore of everything pyLogi can read and write on a device.

A snapshot is plain JSON (raw onboard-memory sectors are hex), so it can be
inspected, archived and restored later.  It is taken before any write test.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import backup_dir
from .device import Device


def _try(fn, *a):
    try:
        return fn(*a)
    except Exception as e:           # unsupported / failed reads are recorded, not fatal
        return {"error": f"{type(e).__name__}: {e}"}


def snapshot(dev: Device) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "when": time.strftime("%Y-%m-%d %H:%M:%S"), "name": dev.name, "kind": dev.kind,
        "key": dev.device_key(), "connection": dev.connection, "online": dev.online,
        "caps": sorted(dev.caps), "serial": dev.info.serial, "unit": dev.info.unit,
        "features": dev.info.extra.get("Features", ""),
    }
    if dev.battery:
        snap["battery"] = dev.battery.percent
    if dev.has("easyswitch"):
        r = _try(dev.hosts)
        snap["hosts"] = r if isinstance(r, dict) else {
            "current": r[0], "list": [{"index": h.index, "paired": h.paired, "name": h.name, "bus": h.bus}
                                      for h in r[1]]}
    if dev.has("dpi"):
        r = _try(dev.dpi)
        snap["dpi"] = r if isinstance(r, dict) else {"current": r.current, "default": r.default}
    if dev.has("smartshift"):
        r = _try(dev.smartshift)
        snap["smartshift"] = r if isinstance(r, dict) else {"ratchet": r[0], "threshold": r[1]}
    if dev.has("report_rate"):
        r = _try(dev.report_rate)
        snap["report_rate"] = r if isinstance(r, dict) else {"current": r[0], "options": r[1]}
    if dev.has("buttons"):
        r = _try(dev.controls)
        snap["controls"] = r if isinstance(r, dict) else [
            {"cid": c.cid, "name": c.name, "flags": c.flags, "remap": c.remap, "divert": c.divert} for c in r]
    if dev.has("backlight"):
        r = _try(dev.backlight)
        snap["backlight"] = r if isinstance(r, dict) else {"enabled": r[0], "level": r[1], "max": r[2]}
    if dev.has("fnlock"):
        snap["fn_lock"] = _try(dev.fn_lock)
    if dev.has("profiles"):
        snap["onboard"] = _try(_onboard, dev)
    if dev.has("webcam"):
        r = _try(dev.cam_controls)
        snap["camera"] = r if isinstance(r, dict) else {c.name: c.value for c in r if c.writable}
    return snap


def _onboard(dev: Device) -> dict[str, Any]:
    ob = dev._ob()                                       # type: ignore[attr-defined]
    d = ob.desc
    return {"host_mode": ob.host_mode(), "current_profile": ob.current_sector(),
            "desc": {"memory_model": d.memory_model, "profile_format": d.profile_format,
                     "profile_count": d.profile_count, "button_count": d.button_count,
                     "sector_count": d.sector_count, "sector_size": d.sector_size},
            "sectors": {str(n): ob.read_sector(n).hex() for n in range(d.sector_count)}}


def save(dev: Device, snap: dict[str, Any] | None = None, tag: str = "full") -> Path:
    snap = snap or snapshot(dev)
    safe = "".join(c if c.isalnum() else "_" for c in dev.device_key())
    p = backup_dir() / f"{tag}-{safe}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    p.write_text(json.dumps(snap, indent=1), encoding="utf-8")
    return p


_AUTO_KEEP = 20                                       # automatic backups kept per device
_auto_last: dict[str, tuple[float, Path]] = {}


def auto(dev: Device, reason: str = "write", min_interval: float = 600.0) -> Path | None:
    """Full snapshot taken automatically before a write to the device's memory.

    One snapshot per device is enough for a burst of writes, so a new one is only taken when
    the previous one is older than ``min_interval`` seconds.  Old automatic backups are pruned."""
    key = dev.device_key()
    last = _auto_last.get(key)
    if last and time.time() - last[0] < min_interval and last[1].exists():
        return last[1]
    path = save(dev, tag=f"auto-{''.join(c if c.isalnum() else '_' for c in reason)}")
    _auto_last[key] = (time.time(), path)
    safe = "".join(c if c.isalnum() else "_" for c in key)
    for old in sorted(backup_dir().glob(f"auto-*-{safe}-*.json"))[:-_AUTO_KEEP]:
        old.unlink(missing_ok=True)
    return path


def restore(dev: Device, snap: dict[str, Any]) -> list[str]:
    """Write a snapshot back; returns the list of things that were restored (or failed)."""
    done: list[str] = []

    def step(label: str, fn, *a):
        try:
            fn(*a)
            done.append(f"ok: {label}")
        except Exception as e:
            done.append(f"FAILED: {label}: {type(e).__name__}: {e}")

    if "dpi" in snap and "current" in snap["dpi"]:
        step("dpi", dev.set_dpi, snap["dpi"]["current"])
    if "smartshift" in snap and "ratchet" in snap["smartshift"]:
        step("smartshift", dev.set_smartshift, snap["smartshift"]["ratchet"], snap["smartshift"]["threshold"])
    ob = snap.get("onboard")
    onboard_mode = isinstance(ob, dict) and ob.get("host_mode") is False
    if "report_rate" in snap and "current" in snap["report_rate"]:
        if onboard_mode:           # the rate can only be changed in host mode; the onboard profile holds it
            done.append("skipped: report rate (the device was using its onboard profiles)")
        else:
            if isinstance(ob, dict) and ob.get("host_mode") is True:
                step("host mode", lambda: dev._ob().set_host_mode(True))      # type: ignore[attr-defined]
            step("report rate", dev.set_report_rate, snap["report_rate"]["current"])
    for c in snap.get("controls", []) if isinstance(snap.get("controls"), list) else []:
        step(f"remap {c['name']}", dev.remap_control, c["cid"], c["remap"])
    if "backlight" in snap and "enabled" in snap["backlight"]:
        step("backlight", dev.set_backlight, snap["backlight"]["enabled"], snap["backlight"]["level"])
    if isinstance(snap.get("fn_lock"), bool):
        step("fn lock", dev.set_fn_lock, snap["fn_lock"])
    hosts = snap.get("hosts", {})
    if "list" in hosts:
        now = {h.index: h for h in dev.hosts()[1]}
        for h in hosts["list"]:
            if h["paired"] and h["index"] in now and now[h["index"]].name != h["name"]:
                step(f"host name {h['index']}", dev.rename_host, h["index"], h["name"])
    if isinstance(ob, dict) and "sectors" in ob:
        def write_sectors():
            o = dev._ob()                                # type: ignore[attr-defined]
            for n, hexs in ob["sectors"].items():
                raw = bytes.fromhex(hexs)
                if int(n) != 0 and o.read_sector(int(n)) != raw:
                    o.write_sector(int(n), raw)
            o.set_host_mode(ob["host_mode"])
            if "current_profile" in ob and isinstance(ob["current_profile"], int):
                o.set_current_sector(ob["current_profile"])
        step("onboard profiles", write_sectors)
    if isinstance(snap.get("camera"), dict):
        from .camera import plan_writes
        controls = dev.cam_controls()
        for c, v in plan_writes(snap["camera"], controls):
            step(f"camera {c.name}", dev.set_cam_control, c.id, v)
    return done
