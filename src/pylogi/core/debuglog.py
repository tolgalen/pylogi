# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Optional debug log (Settings -> "Record a debug log", or ``--debug-log``).

Writes ``debug.log`` in the configuration folder: a header with the system, the HID interfaces found, the
devices (a fresh table every time the list changes), connect / disconnect / lost-ping events, duplicate
entries that were merged, errors and uncaught exceptions.  Optionally also every raw HID++ report
(``debug-hid.log``).  Serial numbers, unit ids, Bluetooth addresses and the home folder are masked, so the
file can be attached to a bug report.  Everything is a no-op while the log is off."""
from __future__ import annotations

import re
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from .. import __version__
from .config import config_dir

MAX_BYTES = 2_000_000          # the file is rotated (debug.log.1) beyond this


def _mask(value: str) -> str:
    return f"…{value[-4:]}" if value and len(value) > 4 else ("…" if value else "")


def _scrub(text: str) -> str:
    """No home folder, no user name, no long hex ids that look like serial numbers."""
    home = str(Path.home())
    text = text.replace(home, "~")
    return re.sub(r"(serial:|unit:)([0-9A-Za-z]+)", lambda m: m.group(1) + _mask(m.group(2)), text)


class DebugLog:
    def __init__(self):
        self.path: Path | None = None
        self.raw = False
        self._file = None
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self._last: dict[str, Any] = {}
        self._old_hooks = None

    @property
    def enabled(self) -> bool:
        return self._file is not None

    # -- switching
    def enable(self, raw: bool = False, path: str | Path | None = None) -> Path:
        """Start (or restart) logging; returns the file."""
        self.disable()
        self.path = Path(path) if path else config_dir() / "debug.log"
        self._rotate()
        self._file = open(self.path, "a", buffering=1, encoding="utf-8")
        self._t0 = time.monotonic()
        self._last.clear()
        self.raw = raw
        if raw:
            from ..hidpp import trace
            trace.enable(str(self.path.with_name("debug-hid.log")))
        self._hook_exceptions()
        self.log("start", f"pyLogi {__version__} debug log" + (" (with raw HID++ traffic)" if raw else ""))
        self._header()
        return self.path

    def disable(self) -> None:
        if self._file is None:
            return
        self.log("stop", "debug log off")
        self._unhook_exceptions()
        if self.raw:
            from ..hidpp import trace
            trace.disable()
        with self._lock:
            self._file.close()
            self._file = None

    def _rotate(self) -> None:
        try:
            if self.path and self.path.exists() and self.path.stat().st_size > MAX_BYTES:
                self.path.replace(self.path.with_name(self.path.name + ".1"))
        except OSError:
            pass

    # -- writing
    def log(self, category: str, message: str) -> None:
        f = self._file
        if f is None:
            return
        line = f"{time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d} [{category:<9}] {_scrub(message)}\n"
        with self._lock:
            try:
                f.write(line)
                if f.tell() > MAX_BYTES * 1.5:                  # a runaway log must not fill the disk
                    f.write("... log too large, further lines dropped until restart ...\n")
                    f.close()
                    self._file = None
            except (OSError, ValueError):
                pass

    def _header(self) -> None:
        from . import diagnostics
        try:
            for line in diagnostics.collect().splitlines():
                self.log("system", line)
        except Exception as e:
            self.log("system", f"(system report failed: {e})")
        self.log("system", f"argv: {' '.join(sys.argv[1:]) or '-'}")
        try:
            from ..hidpp.transport import enumerate_hidpp
            infos = enumerate_hidpp()
            self.log("hid", f"{len(infos)} HID++ interface(s)")
            for i in infos:
                self.log("hid", f"{i.path} {i.vid:04x}:{i.pid:04x} bus={i.bus} short={i.has_short} "
                                f"name={i.name!r} uniq={_mask(i.uniq)}")
        except Exception as e:
            self.log("hid", f"(enumeration failed: {e})")

    # -- what the app reports
    def devices(self, devs: list, note: str = "") -> None:
        """The device table, written when it differs from the previous one."""
        if not self.enabled:
            return
        rows = []
        for d in devs:
            bat = f"{d.battery.percent}%{'+' if d.battery.charging else ''}" if d.battery and d.battery.percent is not None else "-"
            rows.append((d.uid, d.name, getattr(d, "kind", "?"), getattr(d, "connection", "?"),
                         bool(d.online), bat, d.device_key(), tuple(getattr(d, "model_ids", None) or ()),
                         tuple(sorted(getattr(d, "caps", ()) or ())), getattr(d, "slot", None)))
        key = repr(rows)
        if self._last.get("devices") == key:
            return
        before = {r[0]: r for r in (self._last.get("rows") or [])}
        self._last["devices"], self._last["rows"] = key, rows
        self.log("devices", f"{len(rows)} entr{'y' if len(rows) == 1 else 'ies'}{' - ' + note if note else ''}")
        for r in rows:
            self.log("devices", f"  {r[1]} | {r[2]} | {r[3]} | {'online' if r[4] else 'OFFLINE'} | battery {r[5]} | "
                                f"uid={r[0]} key={r[6]} model={','.join(r[7]) or '-'} slot={r[9]} | caps: {', '.join(r[8]) or '-'}")
            old = before.get(r[0])
            if old is not None and old[4] != r[4]:
                self.log("change", f"{r[1]} ({r[0]}): {'online' if old[4] else 'offline'} -> {'online' if r[4] else 'offline'}")
            elif old is not None and old[5] != r[5]:
                self.log("change", f"{r[1]}: battery {old[5]} -> {r[5]}")
        for uid, old in before.items():
            if uid not in {r[0] for r in rows}:
                self.log("change", f"{old[1]} ({uid}): removed from the list")

    # -- uncaught exceptions
    def _hook_exceptions(self) -> None:
        self._old_hooks = (sys.excepthook, threading.excepthook)

        def main_hook(tp, val, tb):
            self.log("exception", "".join(traceback.format_exception(tp, val, tb)).rstrip())
            self._old_hooks[0](tp, val, tb)

        def thread_hook(args):
            name = args.thread.name if args.thread else "?"
            self.log("exception", f"in thread {name}: " +
                     "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)).rstrip())
            self._old_hooks[1](args)
        sys.excepthook, threading.excepthook = main_hook, thread_hook

    def _unhook_exceptions(self) -> None:
        if self._old_hooks:
            sys.excepthook, threading.excepthook = self._old_hooks
            self._old_hooks = None


LOG = DebugLog()


def log(category: str, message: str) -> None:
    LOG.log(category, message)
