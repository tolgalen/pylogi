# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Removing a device: unpair it from its receiver / Bluetooth, forget its settings, or hide it.

Unpairing is destructive (the device must be paired again), so a backup of everything
pyLogi can read is saved first and the caller is expected to ask for confirmation.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import backup
from ..i18n import N_
from .config import Config
from .device import Device

UNPAIR, BLUETOOTH, FORGET, HIDE, DELETE = "unpair", "bluetooth", "forget", "hide", "delete"


@dataclass
class Option:
    key: str
    label: str            # English, translated by the UI
    detail: str
    destructive: bool = False
    confirm: str = N_("I understand that the device has to be paired again")


def options(dev: Device) -> list[Option]:
    out: list[Option] = []
    if getattr(dev, "slot", 0) and getattr(dev, "receiver", None) is not None:
        out.append(Option(UNPAIR, N_("Unpair from the receiver"),
                          N_("The device is removed from the receiver; it must be paired again to be used."), True))
    if dev.connection == "Bluetooth" and getattr(dev, "uniq", ""):
        out.append(Option(BLUETOOTH, N_("Remove the Bluetooth pairing"),
                          N_("The device is removed from this computer's Bluetooth devices."), True))
    out.append(Option(FORGET, N_("Forget pyLogi settings"),
                      N_("Deletes the saved buttons, profiles, colours and other settings of this device.")))
    out.append(Option(HIDE, N_("Hide from the list"),
                      N_("Only hides it here (Settings → Hidden devices brings it back).")))
    out.append(Option(DELETE, N_("Delete from pyLogi"),
                      N_("Deletes everything pyLogi stored about it and never shows it again, not even among the hidden ones."),
                      True, N_("I understand that its saved settings are deleted for good")))
    return out


def bluetooth_remove(mac: str) -> None:
    """Remove a paired Bluetooth device through BlueZ (bluetoothctl)."""
    tool = shutil.which("bluetoothctl")
    if not tool:
        raise RuntimeError("bluetoothctl is not installed")
    r = subprocess.run([tool, "remove", mac.upper()], capture_output=True, text=True, timeout=15)
    if r.returncode != 0 or "not available" in (r.stdout + r.stderr).lower():
        raise RuntimeError((r.stdout + r.stderr).strip() or "bluetoothctl failed")


def is_hidden(cfg: Config, dev: Device) -> bool:
    return dev.device_key() in cfg.get("hidden", []) or is_deleted(cfg, dev)


def is_deleted(cfg: Config, dev: Device) -> bool:
    return dev.device_key() in cfg.get("deleted", [])


def hidden_keys(cfg: Config) -> list[str]:
    return [k for k in cfg.get("hidden", []) if k not in cfg.get("deleted", [])]


def unhide_all(cfg: Config) -> None:
    cfg.set("hidden", [])


def restore_deleted(cfg: Config) -> int:
    """Forget the 'deleted' list: those devices are listed again (their settings stay gone)."""
    n = len(cfg.get("deleted", []))
    cfg.set("deleted", [])
    return n


def _wipe(dev: Device, cfg: Config, manager, settings) -> None:
    """Remove everything pyLogi keeps about a device (backups on disk are left alone)."""
    key = dev.device_key()
    if dev.online:
        (settings or _plain_settings(cfg)).release(dev)       # leave the buttons as they were found
    for name in (f"dev:{key}", f"keys:{dev.uid}", f"cam:{dev.uid}", f"campreset:{dev.uid}"):
        cfg.pop(name)
    pictures = dict(cfg.get("pictures", {}))
    pic = pictures.pop(key, None)
    if pic:
        try:
            Path(pic).unlink(missing_ok=True)
        except OSError:
            pass
        cfg.data["pictures"] = pictures
    cfg.data["hidden"] = [k for k in cfg.get("hidden", []) if k != key]
    cfg.save()
    if manager is not None:
        if getattr(dev, "slot", 0) and dev.model_ids and getattr(manager, "known", None) is not None:
            manager.known.data.pop(f"{dev.connection}:{dev.model_ids[0].upper()}", None)
            manager.known.save()
        if getattr(manager, "history", None) is not None:
            manager.history.forget(key)


def _plain_settings(cfg: Config):
    from .settings import DeviceSettings
    return DeviceSettings(cfg)


def apply(dev: Device, key: str, cfg: Config, manager=None, settings=None) -> str:
    """Perform one removal option; returns a short English description of what was done."""
    if key == UNPAIR:
        if dev.online:                                    # snapshot only what can be read
            backup.save(dev, tag="pre-unpair")
        rdev = dev.receiver
        rdev.unpair(dev.slot)                             # type: ignore[attr-defined]
        return "unpaired from the receiver"
    if key == BLUETOOTH:
        if dev.online:
            backup.save(dev, tag="pre-unpair")
        bluetooth_remove(dev.uniq)                        # type: ignore[attr-defined]
        return "Bluetooth pairing removed"
    if key == FORGET:
        for name in (f"dev:{dev.device_key()}", f"keys:{dev.uid}", f"cam:{dev.uid}", f"campreset:{dev.uid}"):
            cfg.pop(name)
        return "settings forgotten"
    if key == DELETE:
        _wipe(dev, cfg, manager, settings)
        deleted = list(cfg.get("deleted", []))
        if dev.device_key() not in deleted:
            cfg.set("deleted", deleted + [dev.device_key()])
        return "deleted"
    if key == HIDE:
        hidden = hidden_keys(cfg)
        if dev.device_key() not in hidden:
            cfg.set("hidden", hidden + [dev.device_key()])
        return "hidden"
    raise ValueError(key)
