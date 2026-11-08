# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Webcam control logic that does not depend on the UI: roles, auto modes, presets, write plans."""
from __future__ import annotations

import re

from ..i18n import N_
from .webcam import CamControl

_PATTERNS = {
    "zoom": ("zoom absolute", "zoom"), "pan": ("pan absolute",), "tilt": ("tilt absolute",),
    "focus": ("focus absolute",), "focus_auto": ("focus automatic continuous", "focus auto"),
    "exposure": ("exposure time absolute", "exposure absolute"),
    "exposure_auto": ("auto exposure", "exposure auto"),
    "flicker": ("power line frequency",), "backlight": ("backlight compensation",),
    "brightness": ("brightness",), "contrast": ("contrast",), "saturation": ("saturation",),
    "hue": ("hue",), "sharpness": ("sharpness",), "gamma": ("gamma",), "gain": ("gain",),
    "wb": ("white balance temperature",), "wb_auto": ("white balance automatic",),
}
LABELS = {
    "zoom": N_("Zoom"), "pan": N_("Pan"), "tilt": N_("Tilt"), "focus": N_("Focus"),
    "exposure": N_("Exposure"), "flicker": N_("Anti-flicker"),
    "backlight": N_("Backlight compensation"), "brightness": N_("Brightness"),
    "contrast": N_("Contrast"), "saturation": N_("Saturation"), "hue": N_("Hue"),
    "sharpness": N_("Sharpness"), "gamma": N_("Gamma"), "gain": N_("Gain"),
    "wb": N_("White balance"),
}
LENS = ["zoom", "pan", "tilt", "focus", "exposure"]
IMAGE = ["flicker", "backlight", "brightness", "contrast", "saturation", "hue",
         "sharpness", "gamma", "gain", "wb"]
AUTO_OF = {"focus": "focus_auto", "exposure": "exposure_auto", "wb": "wb_auto"}
BUILTIN = [N_("Default"), N_("Streaming"), N_("Video call")]


def norm(name: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", name.lower()).split())


def find_roles(controls: list[CamControl]) -> dict[str, CamControl]:
    roles: dict[str, CamControl] = {}
    for c in controls:
        n = norm(c.name)
        for role, pats in _PATTERNS.items():
            if role not in roles and n in pats:
                roles[role] = c
                break
    return roles


def auto_is_on(c: CamControl, value: int) -> bool:
    """Bool controls: 1 = auto.  Auto-exposure menu: anything except manual (1)."""
    return bool(value) if c.type == 2 else value != 1


def auto_value(c: CamControl, on: bool) -> int:
    if c.type == 2:
        return 1 if on else 0
    keys = sorted(c.menu) or [c.minimum, c.maximum]
    if on:
        return 3 if 3 in keys else (0 if 0 in keys else keys[-1])
    return 1 if 1 in keys else keys[0]


def _frac(c: CamControl, f: float) -> int:
    v = c.minimum + (c.maximum - c.minimum) * f
    return int(round((v - c.minimum) / c.step)) * c.step + c.minimum


def builtin_preset(name: str, controls: list[CamControl]) -> dict[str, int]:
    """Values by control name; automatic modes come first so manual values are accepted."""
    roles = find_roles(controls)
    if name == "Default":
        autos = {roles[r].id for r in AUTO_OF.values() if r in roles}
        ordered = [c for c in controls if c.id in autos] + [c for c in controls if c.id not in autos]
        return {c.name: c.default for c in ordered if c.writable and not c.is_button}

    out: dict[str, int] = {}
    for r in AUTO_OF.values():
        if r in roles:
            out[roles[r].name] = auto_value(roles[r], True)

    def put(role: str, value: int | None = None, frac: float | None = None) -> None:
        c = roles.get(role)
        if c is None or not c.writable:
            return
        out[c.name] = value if value is not None else _frac(c, frac or 0.0)

    def default(role: str) -> None:
        if role in roles:
            put(role, value=roles[role].default)

    default("pan"); default("tilt")
    if name == "Streaming":          # punchy picture, widest field of view
        put("contrast", frac=.55); put("saturation", frac=.55); put("sharpness", frac=.6)
        put("gain", frac=0); put("backlight", frac=0)
        if "zoom" in roles:
            put("zoom", value=roles["zoom"].minimum)
    elif name == "Video call":       # brighter face, backlight compensation on
        put("brightness", frac=.55); put("contrast", frac=.5); put("saturation", frac=.5)
        put("sharpness", frac=.5); put("backlight", frac=1)
        default("zoom")
    return out


def plan_writes(values: dict[str, int], controls: list[CamControl]) -> list[tuple[CamControl, int]]:
    """Ordered (control, value) writes for a preset.

    Controls that stay inactive because their automatic mode ends up on
    (exposure time, focus, white-balance temperature) are skipped: the driver
    answers them with EACCES.  Auto modes go first, then menus/toggles, then sliders.
    """
    roles = find_roles(controls)
    by_name = {c.name: c for c in controls}
    inactive: set[int] = set()
    for manual, auto in AUTO_OF.items():
        if manual in roles and auto in roles:
            a = roles[auto]
            if auto_is_on(a, values.get(a.name, a.value)):
                inactive.add(roles[manual].id)
    autos = {roles[a].id for a in AUTO_OF.values() if a in roles}
    plan = [(by_name[n], v) for n, v in values.items()
            if n in by_name and by_name[n].writable and not by_name[n].is_button and by_name[n].id not in inactive]
    plan.sort(key=lambda cv: (cv[0].id not in autos, cv[0].type == 1))
    return plan


# ----------------------------------------------------------------- stored state
def snapshot(controls: list[CamControl]) -> dict[str, int]:
    """Current value of every control that can be written (buttons are actions, not values)."""
    return {c.name: c.value for c in controls if c.writable and not c.is_button}


def resolve_preset(cfg, uid: str, name: str, controls: list[CamControl]) -> dict[str, int]:
    """Values of a preset: the user's own version of a built-in one, a saved preset, or the built-in recipe."""
    over = cfg.get(f"camover:{uid}", {})
    if name in over:
        return dict(over[name])
    if name in BUILTIN:
        return builtin_preset(name, controls)
    return dict(cfg.get(f"cam:{uid}", {}).get(name, {}))


def preset_names(cfg, uid: str) -> list[str]:
    return [*(str(b) for b in BUILTIN), *cfg.get(f"cam:{uid}", {})]


def apply_values(dev, values: dict[str, int]) -> list[str]:
    """Write a set of control values in a safe order; returns the errors (empty when everything was written)."""
    failed = []
    for c, v in plan_writes(values, dev.cam_controls()):
        try:
            dev.set_cam_control(c.id, int(v))
        except OSError as e:
            failed.append(f"{c.name}: {e.strerror or e}")
    return failed


def apply_preset(dev, cfg, name: str) -> list[str]:
    errors = apply_values(dev, resolve_preset(cfg, dev.uid, name, dev.cam_controls()))
    cfg.set(f"campreset:{dev.uid}", name)
    return errors


def mode_text(mode: tuple[str, int, int, int]) -> str:
    fmt, w, h, fps = mode
    return f"{fmt} {w}x{h}" + (f" @{fps}" if fps else "")


def parse_mode(text: str) -> tuple[str, int, int, int]:
    """'MJPG:1280x720@30' (also 'MJPG 1280x720 @30') -> ('MJPG', 1280, 720, 30)."""
    m = re.fullmatch(r"\s*([A-Za-z0-9]{3,4})[\s:]+(\d+)x(\d+)\s*(?:@\s*(\d+))?\s*", text)
    if not m:
        raise ValueError(f"expected FORMAT:WIDTHxHEIGHT@FPS (for example MJPG:1280x720@30), got '{text}'")
    return m.group(1).upper(), int(m.group(2)), int(m.group(3)), int(m.group(4) or 0)
