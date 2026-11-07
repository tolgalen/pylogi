# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Persistent user configuration (JSON in the XDG config dir)."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any


def config_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "pylogi"
    d.mkdir(parents=True, exist_ok=True)
    return d


# Settings of one device are kept in their own file, devices/<name>__<id>.json, not in config.json.
# A key "<prefix>:<id>" with one of these prefixes is routed there; the id is the device key or uid.
DEVICE_PREFIXES = ("dev", "keys", "campreset", "cam")


def _split(key: str) -> tuple[str, str] | None:
    for pre in DEVICE_PREFIXES:
        if key.startswith(pre + ":"):
            return pre, key[len(pre) + 1:]
    return None


def _slug(text: str, limit: int = 60) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")[:limit] or "device"


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or config_dir() / "config.json"
        self.devdir = self.path.with_name("devices")
        self.data: dict[str, Any] = {}
        self._dev: dict[str, dict[str, Any]] = {}      # id -> {"name": .., "id": .., "settings": {prefix: value}}
        self._files: dict[str, Path] = {}              # id -> file
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        self._scan()
        legacy = [k for k in self.data if _split(k)]
        for k in legacy:                               # older versions kept everything in config.json
            pre, ident = _split(k)                     # type: ignore[misc]
            self._entry(ident)["settings"].setdefault(pre, self.data.pop(k))
            self._write_dev(ident)
        if legacy:
            self.save()

    # -- per-device files
    def _scan(self) -> None:
        try:
            files = sorted(self.devdir.glob("*.json"))
        except OSError:
            return
        for f in files:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                ident = d["id"]
                if isinstance(d.get("settings"), dict):
                    self._dev[ident], self._files[ident] = d, f
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def _entry(self, ident: str) -> dict[str, Any]:
        return self._dev.setdefault(ident, {"pylogi_device": 1, "name": "", "id": ident, "settings": {}})

    def _write_dev(self, ident: str) -> None:
        e = self._dev[ident]
        old = self._files.get(ident)
        if not e["settings"]:                          # nothing left: remove the file
            self._dev.pop(ident, None)
            self._files.pop(ident, None)
            if old:
                old.unlink(missing_ok=True)
            return
        target = self.devdir / f"{_slug(e.get('name') or 'device')}__{_slug(ident, 80)}.json"
        try:
            self.devdir.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(e, indent=1, ensure_ascii=False), encoding="utf-8")
            if old and old != target:                  # the device got its name (or a new one)
                old.unlink(missing_ok=True)
            self._files[ident] = target
        except OSError:
            pass

    def name_device(self, ident: str, name: str) -> None:
        """Tell which device an id belongs to, so its file is named after it."""
        e = self._dev.get(ident)
        if e is not None and name and e.get("name") != name:
            e["name"] = name
            self._write_dev(ident)

    def device_ids(self) -> list[str]:
        return sorted(self._dev)

    # -- key/value API (per-device keys are routed to their own file)
    def get(self, key: str, default: Any = None) -> Any:
        sp = _split(key)
        if sp:
            return self._dev.get(sp[1], {}).get("settings", {}).get(sp[0], default)
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        sp = _split(key)
        if sp:
            self._entry(sp[1])["settings"][sp[0]] = value
            self._write_dev(sp[1])
            return
        self.data[key] = value
        self.save()

    def pop(self, key: str) -> Any:
        sp = _split(key)
        if sp:
            e = self._dev.get(sp[1])
            val = e["settings"].pop(sp[0], None) if e else None
            if e is not None:
                self._write_dev(sp[1])
            return val
        val = self.data.pop(key, None)
        self.save()
        return val

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        except OSError:
            pass


def backup_dir() -> Path:
    d = config_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_backup(device_uid: str, text: str) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in device_uid)
    p = backup_dir() / f"{safe}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    p.write_text(text, encoding="utf-8")
    return p


# keys that describe the machine or a session, not user choices
_NOT_EXPORTED = {"photos_asked"}


def export_config(cfg: "Config", path: Path) -> None:
    data = {k: v for k, v in cfg.data.items() if k not in _NOT_EXPORTED}
    for ident, e in cfg._dev.items():                  # device files are exported as "<prefix>:<id>" keys
        for pre, value in e["settings"].items():
            data[f"{pre}:{ident}"] = value
    path.write_text(json.dumps({"pylogi_config": 1, "data": data}, indent=1), encoding="utf-8")


def import_config(cfg: "Config", path: Path) -> int:
    """Merge an exported file into the configuration; returns the number of keys imported."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("pylogi_config") != 1 or not isinstance(raw.get("data"), dict):
        raise ValueError("not a pyLogi configuration file")
    for k, v in raw["data"].items():
        if k in _NOT_EXPORTED:
            continue
        if _split(k):
            cfg.set(k, v)
        else:
            cfg.data[k] = v
    cfg.save()
    return len(raw["data"])
