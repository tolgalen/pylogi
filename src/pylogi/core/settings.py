# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Per-device settings that pyLogi re-applies when a device (re)connects.

Remaps, diversions and DPI are volatile on most devices (lost at power-off), so they
are stored here and written again when the device comes online.  Turning "manage
this device" off restores the defaults and stops applying anything.

Button bindings live in *profiles*: "" is the default profile and every other name is
an application id (the active window's class) whose bindings replace the default ones
while that application has focus.  See core.actions for the binding format.
"""
from __future__ import annotations

from typing import Any

from . import debuglog as _debuglog
from .config import Config
from .device import Device


class DeviceSettings:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.engine = None            # core.actions.ActionEngine, set when software actions are on

    # -- storage
    def _key(self, dev: Device) -> str:
        return "dev:" + dev.device_key()

    def _rec(self, dev: Device) -> dict[str, Any]:
        self.cfg.name_device(dev.device_key(), dev.name)       # the device's file is named after it
        rec = self.cfg.get(self._key(dev), {})
        old_key = getattr(dev, "legacy_key", "")            # a camera used to be identified by its /dev/videoN node
        if old_key and not rec and old_key != dev.device_key():
            old = self.cfg.pop("dev:" + old_key)
            if old:
                rec = old
                self._put(dev, rec)
        old_colors = self.cfg.pop(f"keys:{dev.uid}")           # colours stored by older versions, by uid
        if old_colors:
            rec = dict(rec)
            rec.setdefault("key_colors", old_colors)
            self._put(dev, rec)
        legacy = rec.get("remaps")
        if legacy:                                    # old versions stored plain cid -> cid remaps
            actions = rec.setdefault("actions", {})
            default = actions.setdefault("", {})
            for cid, target in legacy.items():
                default.setdefault(str(cid), {"type": "remap", "cid": int(target)})
            rec.pop("remaps")
        return rec

    def _put(self, dev: Device, rec: dict[str, Any]) -> None:
        self.cfg.set(self._key(dev), rec)

    # -- state
    def managed(self, dev: Device) -> bool:
        return bool(self._rec(dev).get("managed", True))

    def dpi(self, dev: Device) -> int | None:
        return self._rec(dev).get("dpi")

    def thumb(self, dev: Device) -> tuple[str, bool]:
        """(mode, inverted) saved for the thumb wheel; mode is one of actions.THUMB_MODES."""
        t = self._rec(dev).get("thumb") or {}
        return t.get("mode", "native"), bool(t.get("invert", False))

    def save_thumb(self, dev: Device, mode: str | None = None, invert: bool | None = None) -> None:
        cur_mode, cur_inv = self.thumb(dev)
        self.save(dev, "thumb", {"mode": cur_mode if mode is None else mode,
                                 "invert": cur_inv if invert is None else invert})

    # -- webcam: control values and video mode, written again when the camera is plugged in
    def camera(self, dev: Device) -> dict[str, int]:
        return dict(self._rec(dev).get("camera") or {})

    def camera_mode(self, dev: Device) -> list | None:
        return self._rec(dev).get("camera_mode")

    def remember_camera(self, dev: Device) -> None:
        """Store what the camera is set to now (control values only; the video mode has its own entry)."""
        from .camera import snapshot
        self.save(dev, "camera", snapshot(dev.cam_controls()))

    def remember_camera_mode(self, dev: Device, mode: tuple[str, int, int, int] | None) -> None:
        self.save(dev, "camera_mode", list(mode) if mode else None)

    # -- profiles and bindings
    def profiles(self, dev: Device) -> list[str]:
        names = [p for p in self._rec(dev).get("actions", {}) if p]
        return [""] + sorted(names)

    def bindings(self, dev: Device, profile: str = "") -> dict[int, dict]:
        acts = self._rec(dev).get("actions", {}).get(profile, {})
        return {int(c): b for c, b in acts.items() if b and b.get("type") != "default"}

    def raw(self, dev: Device, profile: str) -> dict[int, dict]:
        """Entries stored in exactly this profile (an explicit {"type": "default"} is kept)."""
        acts = self._rec(dev).get("actions", {}).get(profile, {})
        return {int(c): b for c, b in acts.items() if b}

    def effective(self, dev: Device, profile: str = "") -> dict[int, dict]:
        """Bindings in force for a profile: the Default ones overridden by the profile's own."""
        out = dict(self.bindings(dev, ""))
        if profile:
            for cid, b in self.raw(dev, profile).items():
                if b.get("type") == "default":
                    out.pop(cid, None)          # explicitly back to the original function
                else:
                    out[cid] = b
        return out

    def all_cids(self, dev: Device) -> set[int]:
        return {int(c) for acts in self._rec(dev).get("actions", {}).values() for c in acts}

    def add_profile(self, dev: Device, app: str) -> None:
        rec = self._rec(dev)
        rec.setdefault("actions", {}).setdefault(app, {})
        self._put(dev, rec)

    def remove_profile(self, dev: Device, app: str) -> None:
        rec = self._rec(dev)
        rec.get("actions", {}).pop(app, None)
        self._put(dev, rec)

    def set_binding(self, dev: Device, profile: str, cid: int, binding: dict | None) -> None:
        if not self.managed(dev):
            return
        rec = self._rec(dev)
        acts = rec.setdefault("actions", {}).setdefault(profile, {})
        if binding and (binding.get("type") != "default" or profile):
            acts[str(cid)] = binding            # in an app profile "default" overrides the Default profile
        else:
            acts.pop(str(cid), None)
        self._put(dev, rec)

    # -- legacy helpers (button -> button remaps of the default profile)
    def remaps(self, dev: Device) -> dict[int, int]:
        return {c: b["cid"] for c, b in self.bindings(dev, "").items() if b["type"] == "remap"}

    def save_remap(self, dev: Device, cid: int, target: int) -> None:
        self.set_binding(dev, "", cid, {"type": "remap", "cid": target} if target else None)

    def save(self, dev: Device, name: str, value: Any) -> None:
        if not self.managed(dev):
            return
        rec = self._rec(dev)
        rec[name] = value
        self._put(dev, rec)

    # -- actions
    def apply(self, dev: Device) -> None:
        """Write the saved settings to a device that just came online."""
        if not self.managed(dev):
            return
        rec = self._rec(dev)
        self.apply_buttons(dev)
        if rec.get("dpi") and dev.has("dpi"):
            dev.set_dpi(int(rec["dpi"]))
        if rec.get("smartshift") and dev.has("smartshift"):
            dev.set_smartshift(bool(rec["smartshift"][0]), int(rec["smartshift"][1]))
        if rec.get("wheel") and dev.has("wheel"):
            dev.set_wheel(bool(rec["wheel"][0]), bool(rec["wheel"][1]))
        if rec.get("thumb") and dev.has("thumbwheel"):
            if self.engine is not None:
                self.engine.configure_thumb(dev)
            else:                                          # no software actions: only the direction can be set
                dev.set_thumb(diverted=False, invert=self.thumb(dev)[1])
        if dev.has("webcam"):
            self._apply_camera(dev, rec)
        if rec.get("key_colors") and dev.has("keylight"):
            dev.set_key_colors({int(u): tuple(c) for u, c in rec["key_colors"].items()})
        fx = rec.get("fx")
        if isinstance(fx, dict) and fx.get("effect") and dev.has("keylight"):
            from . import lighting_fx
            if fx["effect"] in lighting_fx.EFFECTS:
                lighting_fx.HUB.start(dev, fx["effect"], lighting_fx.positions_for(dev, rec),
                                      tuple(fx.get("color", (0, 200, 255))), int(fx.get("speed", 5)))

    def _apply_camera(self, dev: Device, rec: dict[str, Any]) -> None:
        from .camera import apply_values
        mode = rec.get("camera_mode")
        if mode and len(mode) == 4:                      # first: a new video mode can reset the controls
            try:
                dev.set_video_mode(str(mode[0]), int(mode[1]), int(mode[2]), int(mode[3]))
            except (OSError, ValueError) as e:
                _debuglog.log("error", f"camera video mode: {e!r}")
        if rec.get("camera"):
            for err in apply_values(dev, rec["camera"]):
                _debuglog.log("error", f"camera: {err}")

    def apply_buttons(self, dev: Device) -> None:
        """Write the button bindings (diversions + remaps) of the profile in force."""
        if not dev.has("buttons") or not self.managed(dev):
            return
        if self.engine is not None:
            self.engine.configure(dev)
        else:                                            # no software actions: plain device remaps only
            remaps = self.remaps(dev)
            for cid in self.all_cids(dev) | set(remaps):
                dev.remap_control(cid, remaps.get(cid, 0))

    def release(self, dev: Device) -> None:
        """Undo the diversions / remaps pyLogi applied to a device's buttons (no camera changes)."""
        if not dev.online or not dev.has("buttons"):
            return
        if self.engine is not None:
            self.engine.release(dev)
        for cid in self.all_cids(dev):
            try:
                dev.remap_control(cid, 0)
            except Exception as e:
                _debuglog.log("error", f"clearing a remap: {e!r}")

    def set_managed(self, dev: Device, on: bool) -> None:
        """Turning management off restores default functions and forgets the saved values."""
        rec = self._rec(dev)
        if on:
            rec["managed"] = True
            self._put(dev, rec)
            if dev.online and self.engine is not None:
                self.engine.configure(dev)
            return
        if dev.online:
            self.release(dev)
            if dev.has("webcam"):
                from .camera import plan_writes
                controls = dev.cam_controls()
                for c, v in plan_writes({c.name: c.default for c in controls}, controls):
                    try:
                        dev.set_cam_control(c.id, v)
                    except OSError:
                        pass
        self._put(dev, {"managed": False})
