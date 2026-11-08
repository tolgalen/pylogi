# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Command-line control: `pylogi --list --json`, `--get DEVICE [name ...]`, `--set DEVICE name=value ...`."""
from __future__ import annotations

import json

from .core import backup
from .core.device import Device
from .core.settings import DeviceSettings

TRUE = ("1", "true", "on", "yes")


def find(devices: list[Device], query: str) -> Device:
    q = query.lower()
    hits = [d for d in devices if d.kind != "receiver" and (q in d.name.lower() or q in d.device_key().lower())]
    if not hits:
        raise SystemExit(f"no device matches '{query}'")
    online = [d for d in hits if d.online]
    if len(online) > 1 or (not online and len(hits) > 1):
        raise SystemExit("ambiguous: " + ", ".join(d.name for d in hits))
    return (online or hits)[0]


def describe(d: Device) -> dict:
    return {"name": d.name, "kind": d.kind, "connection": d.connection, "online": d.online,
            "battery": d.battery.percent if d.battery else None,
            "charging": bool(d.battery and d.battery.charging), "key": d.device_key(),
            "capabilities": sorted(d.caps)}


def list_devices(devices: list[Device], as_json: bool) -> None:
    rows = [describe(d) for d in devices if d.kind != "receiver"]
    if as_json:
        print(json.dumps(rows, indent=1))
        return
    for r in rows:
        bat = f"{r['battery']}%" if r["battery"] is not None else "-"
        print(f"{r['name']:34} {r['kind']:10} {r['connection']:11} "
              f"{'online' if r['online'] else 'offline':8} battery={bat}")


def read_value(dev: Device, name: str, settings: DeviceSettings | None = None):
    """Current value of one setting, in the form `--set` accepts it."""
    name = name.lower().replace("_", "-")
    if name == "dpi":
        return dev.dpi().current
    if name == "smartshift":
        return "ratchet" if dev.smartshift()[0] else "free"
    if name == "wheel-hires":
        return dev.wheel()[0]
    if name == "wheel-invert":
        return dev.wheel()[1]
    if name == "report-rate":
        return dev.report_rate()[0]
    if name == "fn-lock":
        return dev.fn_lock()
    if name == "backlight":
        on, level, _ = dev.backlight()
        return level if on else 0
    if name == "thumb-invert":
        return dev.thumb()[1]
    if name == "battery":
        return dev.battery.percent if dev.battery else None
    if name == "firmware":
        return dev.info.firmware
    if name == "preset" and settings is not None:
        return settings.cfg.get(f"campreset:{dev.uid}", "Default")
    if name == "video-mode":
        from .core.camera import mode_text
        mode = dev.video_mode()
        return mode_text(mode) if mode else None
    if name.startswith("camera."):
        want = name.split(".", 1)[1]
        from .core.camera import norm
        for c in dev.cam_controls():
            if norm(c.name).replace(" ", "-").startswith(want):
                return c.value
    raise SystemExit(f"unknown setting '{name}' (dpi, smartshift, wheel-invert, wheel-hires, report-rate, fn-lock, "
                     "backlight, thumb-invert, battery, firmware, preset, video-mode, camera.<control>)")


def get(dev: Device, names: list[str] | None = None, settings: DeviceSettings | None = None) -> dict:
    """Everything the device reports, or only the named settings."""
    if names:
        out = {}
        for n in names:
            try:
                out[n] = read_value(dev, n, settings)
            except SystemExit:
                raise
            except Exception as e:
                out[n] = {"error": f"{type(e).__name__}: {e}"}
        return out
    snap = backup.snapshot(dev)
    if isinstance(snap.get("onboard"), dict):
        snap["onboard"] = {k: v for k, v in snap["onboard"].items() if k != "sectors"}   # raw memory is for --backup
    return snap


def _bool(v: str, current: bool | None = None) -> bool:
    """on/off words; `toggle` (or `~`) flips the current value."""
    if v.lower() in ("toggle", "~"):
        if current is None:
            raise SystemExit("this setting cannot be toggled")
        return not current
    return v.lower() in TRUE


def set_values(dev: Device, pairs: list[str], settings: DeviceSettings) -> list[str]:
    """Apply name=value settings; returns what was done.  Raises SystemExit on a bad name/value."""
    done = []
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"expected name=value, got '{pair}'")
        name, value = pair.split("=", 1)
        name = name.lower().replace("_", "-")
        if name == "dpi":
            dev.set_dpi(int(value))
            settings.save(dev, "dpi", int(value))
        elif name == "smartshift":
            cur, thr = dev.smartshift()
            ratchet = (not cur) if value.lower() in ("toggle", "~") else value.lower() in ("ratchet", *TRUE)
            dev.set_smartshift(ratchet, thr)
            settings.save(dev, "smartshift", [ratchet, thr])
        elif name in ("wheel-invert", "wheel-hires"):
            hires, inv = dev.wheel()
            if name == "wheel-invert":
                inv = _bool(value, inv)
            else:
                hires = _bool(value, hires)
            dev.set_wheel(hires, inv)
            settings.save(dev, "wheel", [hires, inv])
        elif name == "report-rate":
            dev.set_report_rate(value)
        elif name == "fn-lock":
            dev.set_fn_lock(_bool(value, dev.fn_lock()))
        elif name == "thumb-invert":
            inv = _bool(value, dev.thumb()[1])
            dev.set_thumb(invert=inv)
            settings.save_thumb(dev, invert=inv)
        elif name == "backlight":
            dev.set_backlight(int(value) > 0, int(value))
        elif name.startswith("camera."):
            want = name.split(".", 1)[1]
            from .core.camera import norm, plan_writes
            ctrls = dev.cam_controls()
            c = next((c for c in ctrls if norm(c.name).replace(" ", "-") == want
                      or norm(c.name).replace(" ", "-").startswith(want)), None)
            if c is None:
                raise SystemExit(f"unknown camera control '{want}': " +
                                 ", ".join(norm(c.name).replace(" ", "-") for c in ctrls))
            for cc, v in plan_writes({c.name: int(value)}, ctrls):
                dev.set_cam_control(cc.id, v)
            settings.remember_camera(dev)
        elif name == "preset":
            from .core.camera import apply_preset, preset_names
            if value not in preset_names(settings.cfg, dev.uid):
                raise SystemExit(f"unknown preset '{value}': " + ", ".join(preset_names(settings.cfg, dev.uid)))
            errors = apply_preset(dev, settings.cfg, value)
            settings.remember_camera(dev)
            if errors:
                raise SystemExit("; ".join(errors))
        elif name in ("video-mode", "format"):
            from .core.camera import parse_mode
            try:
                mode = parse_mode(value)
            except ValueError as e:
                raise SystemExit(str(e))
            dev.set_video_mode(*mode)
            settings.remember_camera_mode(dev, mode)
        else:
            raise SystemExit(f"unknown setting '{name}' (dpi, smartshift, wheel-invert, wheel-hires, "
                             "report-rate, fn-lock, backlight, thumb-invert, preset, video-mode, camera.<control>)")
        done.append(f"{name}={value}")
    return done
