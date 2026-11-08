# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Device entries of the system-tray menu: switch onboard profile or Easy-Switch host without opening the window."""
from __future__ import annotations

import time
from typing import Callable

from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu

from ..core.device import Device
from ..i18n import tr

_CACHE_SECONDS = 30.0          # reading profiles takes a moment; the menu may be opened often
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, fn: Callable):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_SECONDS:
        return hit[1]
    value = fn()
    _cache[key] = (time.monotonic(), value)
    return value


def forget_cache(dev: Device | None = None) -> None:
    for k in [k for k in _cache if dev is None or k.endswith(dev.device_key())]:
        _cache.pop(k, None)


def _switchable(dev: Device, cfg=None) -> bool:
    return dev.online and dev.kind != "receiver" and (
        dev.has("profiles") or dev.has("easyswitch") or (cfg is not None and dev.has("webcam")))


MAX_LINE = 34          # a longer device name is split in two lines


def _two_lines(name: str) -> str:
    """Split a long name at the space closest to its middle ('...Wireless RGB\nMechanical Gaming Keyboard')."""
    if len(name) <= MAX_LINE or " " not in name:
        return name
    mid = len(name) / 2
    cut = min((i for i, ch in enumerate(name) if ch == " "), key=lambda i: abs(i - mid))
    return name[:cut] + "\n" + name[cut + 1:]


def _title(dev: Device) -> str:
    """'🔋 85%  Name': battery emoji (empty at 15% or less) and the level; ⚡ while charging.

    Plain text on purpose: some desktops (GNOME's AppIndicator) do not draw icons next to menu entries."""
    b = getattr(dev, "battery", None)
    if b is None or b.percent is None:
        return _two_lines(dev.name)
    icon = "🔋" if b.charging or b.percent > 15 else "🪫"
    return f"{icon} {b.percent}%" + ("⚡" if b.charging else "") + "  " + _two_lines(dev.name)


def fill(menu: QMenu, devices: list[Device], on_error: Callable[[str], None] = lambda m: None, cfg=None,
         settings=None) -> int:
    """Append one submenu per online device that has something to switch; returns how many were added."""
    n = 0
    for dev in devices:
        if not _switchable(dev, cfg):
            continue
        sub = menu.addMenu(_title(dev))
        try:
            added = _profiles(sub, dev, on_error) + _hosts(sub, dev, on_error) + _presets(sub, dev, on_error, cfg, settings)
        except Exception as e:                      # a device that does not answer must not break the menu
            on_error(f"{dev.name}: {e}")
            added = 0
        if added == 0:
            menu.removeAction(sub.menuAction())
        else:
            n += 1
    return n


def _profiles(sub: QMenu, dev: Device, on_error) -> int:
    if not dev.has("profiles"):
        return 0
    profs = [p for p in _cached(f"profiles:{dev.device_key()}", dev.profiles) if p.enabled]
    if not profs or dev.profile_host_mode():           # in host mode the stored profiles are not in use
        return 0
    active = dev.active_profile()
    if len(sub.actions()):
        sub.addSeparator()
    sub.addSection(tr("Profile"))
    group = QActionGroup(sub)
    for p in profs:
        a = QAction(f"{p.index + 1}. {p.name}", sub)
        a.setCheckable(True)
        a.setChecked(p.sector == active)
        a.triggered.connect(lambda _=False, s=p.sector: _run(dev, on_error, dev.set_active_profile, s))
        group.addAction(a)
        sub.addAction(a)
    return len(profs)


def _hosts(sub: QMenu, dev: Device, on_error) -> int:
    if not dev.has("easyswitch"):
        return 0
    current, hosts = dev.hosts()
    paired = [h for h in hosts if h.paired]
    if not paired:
        return 0
    if len(sub.actions()):
        sub.addSeparator()
    sub.addSection(tr("Easy-Switch"))
    group = QActionGroup(sub)
    for h in paired:
        a = QAction(f"{h.index + 1}. {h.name or tr('Host')}", sub)
        a.setCheckable(True)
        a.setChecked(h.index == current)
        a.triggered.connect(lambda _=False, i=h.index: _run(dev, on_error, dev.switch_host, i))
        group.addAction(a)
        sub.addAction(a)
    return len(paired)


def _presets(sub: QMenu, dev: Device, on_error, cfg, settings) -> int:
    """Webcam: one entry per preset (the built-in ones and the user's own)."""
    if cfg is None or not dev.has("webcam"):
        return 0
    from ..core.camera import BUILTIN, apply_preset, preset_names
    current = cfg.get(f"campreset:{dev.uid}", "Default")
    sub.addSection(tr("Preset"))
    group = QActionGroup(sub)
    names = preset_names(cfg, dev.uid)

    def choose(name: str) -> None:
        errors = apply_preset(dev, cfg, name)
        if settings is not None:
            settings.remember_camera(dev)
        if errors:
            on_error("; ".join(errors))

    for name in names:
        a = QAction(tr(name) if name in BUILTIN else name, sub)
        a.setCheckable(True)
        a.setChecked(name == current)
        a.triggered.connect(lambda _=False, n=name: _run(dev, on_error, choose, n))
        group.addAction(a)
        sub.addAction(a)
    return len(names)


def _run(dev: Device, on_error, fn, *args) -> None:
    try:
        fn(*args)
        forget_cache(dev)
    except Exception as e:
        on_error(f"{dev.name}: {e}")
