# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Product photos of the connected devices.

The photos are Logitech material, so they are NOT bundled with pyLogi: the
catalog (index.json) and the needed pictures are downloaded on demand from the
public OpenLogi asset mirror and cached in the user's cache directory.  Only
the catalog and the picture file of a detected model are requested; nothing
about the user or the machine is sent.  Disable with ``--offline``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPixmap

BASES = ["https://assets.openlogi.org", "https://v0-1.openlogi-assets.pages.dev"]
INDEX_MAX_AGE = 7 * 24 * 3600
PREFERRED = ("front_core.png", "front.png")
SIZE_TOKENS = ("s", "m", "l", "xl")      # trailing size letter of some catalog names
TYPES = {"mouse": "MOUSE", "trackball": "MOUSE", "keyboard": "KEYBOARD",
         "numpad": "KEYBOARD", "webcam": "CAMERA"}


def cache_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    d = base / "pylogi" / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _contains(hay: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def match_device(devices: dict, name: str, kind: str, ids: list[str]) -> Optional[str]:
    """Pick the catalog key for a device.

    The product name is the primary signal.  Hex ids (usb/bluetooth pid or
    wireless pid) only break ties, because wireless pids of different products
    can collide with catalog model ids.  With no name match at all, an id is
    accepted only when it identifies exactly one catalog entry.
    """
    want = TYPES.get(kind)
    ids4 = {i.lower()[-4:] for i in ids if i}
    hay = _words(name)
    best, best_score = None, 0
    id_only: list[str] = []
    for key, v in devices.items():
        if want and v.get("type") not in (want, None):
            continue
        id_hit = bool(ids4 & {m.lower()[-4:] for m in v.get("modelIds", [])})
        name_score = 0
        for label in (v.get("displayName", ""), v.get("extendedDisplayName", "")):
            w = _words(label)
            if _contains(hay, w):
                name_score = max(name_score, 10 * len(w) + len("".join(w)))
            elif len(w) > 2 and w[-1] in SIZE_TOKENS and _contains(hay, w[:-1]):
                # the device reports "Signature M650", the catalog says "Signature M650 L"
                name_score = max(name_score, 10 * (len(w) - 1) + len("".join(w[:-1])) - 1)
        if name_score:
            kw = _words(key)
            score = name_score * 10 + (len(kw) * 3 if _contains(hay, kw) else 0) + (5 if id_hit else 0)
            if score > best_score:
                best, best_score = key, score
        elif id_hit:
            id_only.append(key)
    if best is None and len(id_only) == 1:
        return id_only[0]
    return best


def parse_button_slots(meta: dict) -> list[dict]:
    """Button hotspots from a catalog ``core_metadata.json``.

    Each slot: cid (HID++ control id), x/y (marker position, % of the image) and
    side (-1 label left of the marker, 1 right).
    """
    out: list[dict] = []
    for img in meta.get("images", []):
        if img.get("key") != "device_buttons_image":
            continue
        for a in img.get("assignments", []):
            m = re.search(r"_c(\d+)$", a.get("slotId", ""))
            if not m or "marker" not in a:
                continue
            mk, lb = a["marker"], a.get("label", {})
            side = lb.get("x") or (-1 if mk.get("x", 50) < 50 else 1)
            out.append({"cid": int(m.group(1)), "x": mk.get("x", 50), "y": mk.get("y", 50),
                        "side": -1 if side < 0 else 1})
    return out


MAX_DOWNLOAD = 8 * 1024 * 1024          # no catalog file is anywhere near this; refuse anything bigger
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _http_get(path: str, timeout: float = 20, limit: int = MAX_DOWNLOAD) -> bytes:
    last: Exception = OSError("no mirror")
    for base in BASES:
        try:
            req = urllib.request.Request(f"{base}/{path}", headers={"User-Agent": "pyLogi"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read(limit + 1)
            if len(data) > limit:
                raise OSError(f"response larger than {limit} bytes")
            return data
        except Exception as e:  # network errors: try the next mirror
            last = e
    raise OSError(str(last))


class ImageProvider(QObject):
    """Resolves device -> pixmap; downloads in the background and emits `ready`."""
    ready = Signal()

    def __init__(self, enabled: bool = True, directory: Path | None = None):
        super().__init__()
        self.enabled = enabled
        self.dir = directory or cache_dir()
        self._lock = threading.Lock()
        self._index: Optional[dict] = None
        self._busy: set[str] = set()
        self._failed: set[str] = set()
        self._pix: dict[str, QPixmap] = {}
        self._keys: dict[str, Optional[str]] = {}
        self._layouts: dict[str, object] = {}
        self.custom: dict[str, str] = {}      # device key -> picture file chosen by the user

    # -- public
    def pixmap(self, dev, width: int, height: int) -> Optional[QPixmap]:
        """Scaled photo of `dev`, or None (and a background fetch is started)."""
        pm = self.photo(dev)
        if pm is None:
            return None
        return pm.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def photo(self, dev) -> Optional[QPixmap]:
        """Full-size photo of `dev`, or None (and a background fetch is started)."""
        own = self.custom.get(dev.device_key())
        if own and Path(own).exists():                 # a picture the user picked works offline too
            pm = self._pix.get(own)
            if pm is None:
                pm = QPixmap(own)
                if not pm.isNull():
                    self._pix[own] = pm
            if not pm.isNull():
                return pm
        if not self.enabled:
            return None
        sig = f"{dev.uid}|{dev.name}"
        key = self._keys.get(sig)
        if key is None:
            self._start(sig, dev)
            return None
        path = self.dir / f"{key[0]}__{key[1]}"
        if not path.exists():
            self._start(sig, dev)
            return None
        pm = self._pix.get(path.name)
        if pm is None:
            pm = QPixmap(str(path))
            if pm.isNull():
                path.unlink(missing_ok=True)
                return None
            self._pix[path.name] = pm
        return pm

    # -- background work
    def _start(self, sig: str, dev) -> None:
        with self._lock:
            if sig in self._busy or sig in self._failed:
                return
            self._busy.add(sig)
        args = (sig, dev.name, dev.kind, list(getattr(dev, "model_ids", [])))
        threading.Thread(target=self._work, args=args, daemon=True).start()

    def _load_index(self) -> dict:
        p = self.dir / "index.json"
        fresh = p.exists() and time.time() - p.stat().st_mtime < INDEX_MAX_AGE
        if not fresh:
            try:
                data = _http_get("index.json", 60)
                json.loads(data)
                p.write_bytes(data)
            except (OSError, ValueError):
                if not p.exists():
                    raise
        return json.loads(p.read_text())["devices"]

    def _resolve(self, name: str, kind: str, ids: list[str]) -> tuple[str, dict]:
        if self._index is None:
            self._index = self._load_index()
        key = match_device(self._index, name, kind, ids)
        entry = self._index.get(key) if key else None
        if not entry:
            raise LookupError(name)
        return key, entry

    def _fetch_file(self, key: str, entry: dict, fname: str) -> Path:
        """Download (once) a file of a catalog entry, verifying its sha256."""
        dest = self.dir / f"{key}__{fname}"
        if dest.exists():
            return dest
        files = {f["name"]: f for f in entry.get("files", [])}
        if fname not in files:
            raise LookupError(fname)
        data = _http_get(entry["asset_path"] + fname, 60)
        want = files[fname].get("sha256")
        if want and hashlib.sha256(data).hexdigest() != want:
            raise ValueError("checksum mismatch")
        if not want and fname.endswith(".png") and not data.startswith(PNG_MAGIC):
            raise ValueError("not a PNG image")
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return dest

    def _work(self, sig: str, name: str, kind: str, ids: list[str]) -> None:
        try:
            key, entry = self._resolve(name, kind, ids)
            names = {f["name"] for f in entry.get("files", [])}
            fname = next((n for n in PREFERRED if n in names), None) or next(
                (n for n in sorted(names) if n.startswith("front") and n.endswith(".png")), None)
            if not fname:
                raise LookupError(name)
            self._fetch_file(key, entry, fname)
            self._keys[sig] = (key, fname)
            self.ready.emit()
        except Exception:
            self._failed.add(sig)  # keep the drawn icon; retried next run
        finally:
            self._busy.discard(sig)

    # -- button map (photo used by the buttons page + hotspot metadata)
    def buttons_layout(self, dev):
        """(pixmap, slots) for the buttons page, or None; fetched in the background."""
        if not self.enabled:
            return None
        sig = f"{dev.uid}|{dev.name}"
        got = self._layouts.get(sig, False)
        if got is False:
            with self._lock:
                if sig + "#L" in self._busy:
                    return None
                self._busy.add(sig + "#L")
            args = (sig, dev.name, dev.kind, list(getattr(dev, "model_ids", [])))
            threading.Thread(target=self._work_layout, args=args, daemon=True).start()
            return None
        if got is None:
            return None
        path, slots = got
        pm = self._pix.get(path)
        if pm is None:
            pm = QPixmap(path)
            if pm.isNull():
                return None
            self._pix[path] = pm
        return pm, slots

    def _work_layout(self, sig: str, name: str, kind: str, ids: list[str]) -> None:
        try:
            key, entry = self._resolve(name, kind, ids)
            manifest = json.loads(self._fetch_file(key, entry, "manifest.json").read_text())
            res = {r["key"]: r["src"] for r in manifest["devices"][0]["resources"]}
            png = self._fetch_file(key, entry, res["device_buttons_image"])
            meta = json.loads(self._fetch_file(key, entry, res["image_metadata"]).read_text())
            slots = parse_button_slots(meta)
            self._layouts[sig] = (str(png), slots) if slots else None
        except Exception:
            self._layouts[sig] = None
        finally:
            self._busy.discard(sig + "#L")
            self.ready.emit()
