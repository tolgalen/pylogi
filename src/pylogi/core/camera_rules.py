# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Webcam presets that follow the focused application (e.g. "Video call" while Zoom has the focus)."""
from __future__ import annotations

from typing import Optional

from . import debuglog as _debuglog
from .camera import apply_preset


def rules(cfg, dev) -> dict[str, str]:
    """application id (window class, lower case) -> preset name"""
    return dict(cfg.get(f"camrules:{dev.uid}", {}))


def set_rule(cfg, dev, app: str, preset: Optional[str]) -> None:
    r = rules(cfg, dev)
    if preset:
        r[app.strip().lower()] = preset
    else:
        r.pop(app.strip().lower(), None)
    cfg.set(f"camrules:{dev.uid}", r)


class CameraAutomation:
    """Listens to the active-window watcher of the ActionEngine and switches the presets of the cameras."""

    def __init__(self, manager, cfg, settings=None):
        self.manager, self.cfg, self.settings = manager, cfg, settings
        self._applied: dict[str, str] = {}              # camera uid -> rule preset in force

    def _cameras(self) -> list:
        return [d for d in list(self.manager.devices) if getattr(d, "kind", "") == "webcam" and getattr(d, "online", True)]

    def wants_app(self) -> bool:
        """True while some camera has rules, so the watcher keeps following the focus."""
        return any(rules(self.cfg, d) for d in self._cameras())

    def __call__(self, app: Optional[str]) -> None:
        for cam in self._cameras():
            wanted = rules(self.cfg, cam).get((app or "").lower())
            current = self._applied.get(cam.uid)
            try:
                if wanted and wanted != current:
                    self._apply(cam, wanted)
                    self._applied[cam.uid] = wanted
                elif not wanted and current:                     # the application lost the focus: back to the chosen preset
                    self._applied.pop(cam.uid)
                    self._apply(cam, self.cfg.get(f"campreset:{cam.uid}", "Default"), remember_choice=False)
            except Exception as e:
                _debuglog.log("error", f"camera preset for {app}: {e!r}")

    def _apply(self, cam, preset: str, remember_choice: bool = True) -> None:
        chosen = self.cfg.get(f"campreset:{cam.uid}", "Default")
        apply_preset(cam, self.cfg, preset)
        self.cfg.set(f"campreset:{cam.uid}", chosen)            # a rule is temporary: it does not change the selected preset
        if self.settings is not None:
            self.settings.remember_camera(cam)
