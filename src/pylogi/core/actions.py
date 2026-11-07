# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Software actions for mouse buttons: shortcuts, media keys, clicks, text, commands, DPI.

A button bound to an action is *diverted* (HID++ REPROG_CONTROLS_V4): the mouse stops
sending its normal event and reports presses as notifications instead.  The
ActionEngine listens for them on the receiver's HID channel and performs the action
through an Injector (Linux: a virtual /dev/uinput keyboard + mouse).

Binding = JSON dict, keyed by button (control id) inside a profile:
  {"type": "remap",   "cid": 83}                       another button's function (done in the device)
  {"type": "key",     "mods": ["ctrl"], "key": "C"}    keyboard shortcut
  {"type": "media",   "name": "Play/Pause"}            media / browser key
  {"type": "mouse",   "button": "left"|"right"|"middle"|"back"|"forward"}
  {"type": "scroll",  "dir": "up"|"down"|"left"|"right"}
  {"type": "text",    "text": "hello"}                  typed with a US-layout key map
  {"type": "command", "cmd": "firefox --new-window"}   run a program (no shell)
  {"type": "dpi",     "mode": "next"|"prev"}           cycle the DPI value
  {"type": "macro",   "script": "key ctrl+c\nwait 100"} a sequence of the actions above (see core.macros)
  {"type": "gesture", "threshold": 50, "click": "<script>", "up": ..., "down": ..., "left": ..., "right": ...}
                                                        hold the button and move the mouse; each direction runs a macro script
  {"type": "none"}                                      button disabled
"""
from __future__ import annotations

import os
import shlex
import shutil
import struct
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

from ..hidpp import features as ft
from ..hidpp.constants import F
from ..i18n import N_
from . import debuglog as _debuglog

try:
    import fcntl          # Linux only (/dev/uinput); on other systems the uinput device simply cannot be opened
except ImportError:       # pragma: no cover
    fcntl = None  # type: ignore[assignment]

# --------------------------------------------------------------- key tables (evdev codes)
_LETTERS = dict(zip("QWERTYUIOP", range(16, 26))) | dict(zip("ASDFGHJKL", range(30, 39))) | \
    dict(zip("ZXCVBNM", range(44, 51)))
_DIGITS = dict(zip("1234567890", range(2, 12)))
KEYCODES: dict[str, int] = {
    **_LETTERS, **_DIGITS,
    **{f"F{i}": 58 + i for i in range(1, 11)}, "F11": 87, "F12": 88,
    "Esc": 1, "Backspace": 14, "Tab": 15, "Enter": 28, "Space": 57, "Insert": 110, "Delete": 111,
    "Home": 102, "End": 107, "Page Up": 104, "Page Down": 109, "Up": 103, "Down": 108,
    "Left": 105, "Right": 106, "Print Screen": 99,
}
MODIFIERS = {"ctrl": 29, "shift": 42, "alt": 56, "meta": 125}
MEDIA = {"Play/Pause": 164, "Next track": 163, "Previous track": 165, "Stop": 166, "Mute": 113,
         "Volume up": 115, "Volume down": 114, "Calculator": 140, "Mail": 155, "Browser home": 172,
         "Browser back": 158, "Browser forward": 159, "Browser search": 217, "My computer": 144}
MOUSE = {"left": 0x110, "right": 0x111, "middle": 0x112, "back": 0x113, "forward": 0x114}
_US_PUNCT = {".": (52, 0), ",": (51, 0), ";": (39, 0), "'": (40, 0), "/": (53, 0), "-": (12, 0),
             "=": (13, 0), "[": (26, 0), "]": (27, 0), "\\": (43, 0), "`": (41, 0),
             ":": (39, 1), '"': (40, 1), "?": (53, 1), "_": (12, 1), "+": (13, 1), "!": (2, 1),
             "@": (3, 1), "#": (4, 1), "$": (5, 1), "%": (6, 1), "^": (7, 1), "&": (8, 1),
             "*": (9, 1), "(": (10, 1), ")": (11, 1), "<": (51, 1), ">": (52, 1), "{": (26, 1),
             "}": (27, 1), "|": (43, 1), "~": (41, 1)}

ACTION_TYPES = [  # key, label (translated by the UI)
    ("default", N_("Default")), ("remap", N_("Another button")), ("key", N_("Keyboard shortcut")),
    ("media", N_("Media key")), ("mouse", N_("Mouse click")), ("scroll", N_("Scroll")),
    ("text", N_("Type text")), ("command", N_("Run command")), ("dpi", N_("Change DPI")),
    ("macro", N_("Macro")), ("gesture", N_("Gesture")), ("none", N_("Disabled")),
]
NEEDS_DIVERT = {"key", "media", "mouse", "scroll", "text", "command", "dpi", "macro", "gesture", "none"}
GESTURE_THRESHOLD = 50                  # mouse counts of movement that make a direction (not a click)
GESTURE_DIRECTIONS = ("up", "down", "left", "right")

# thumb wheel modes: how a diverted thumb wheel is used ("native" leaves it to the operating system)
THUMB_MODES = [("native", N_("Horizontal scroll (system)")), ("hscroll", N_("Horizontal scroll (pyLogi)")),
               ("zoom", N_("Zoom (Ctrl + scroll)")), ("volume", N_("Volume")), ("tabs", N_("Switch tabs"))]
THUMB_STEP = 2                          # rotation counts per step (to be tuned per model)


def char_keys(ch: str) -> tuple[int, int] | None:
    """(evdev code, shift) to type a character on a US layout, or None."""
    if ch.isalpha() and ch.isascii():
        return _LETTERS[ch.upper()], int(ch.isupper())
    if ch in _DIGITS:
        return _DIGITS[ch], 0
    if ch == " ":
        return 57, 0
    if ch in ("\n", "\r"):
        return 28, 0
    if ch == "\t":
        return 15, 0
    return _US_PUNCT.get(ch)


def describe(b: dict | None) -> str:
    """Short English summary of a binding (the UI translates the type names)."""
    if not b or b.get("type") == "default":
        return ""
    t = b["type"]
    if t == "key":
        return "+".join([m.capitalize() for m in b.get("mods", [])] + [b.get("key", "?")])
    if t == "media":
        return b.get("name", "")
    if t == "mouse":
        return b.get("button", "")
    if t == "scroll":
        return b.get("dir", "")
    if t == "text":
        return b.get("text", "")[:24]
    if t == "command":
        return b.get("cmd", "")[:24]
    if t == "dpi":
        return b.get("mode", "")
    if t == "macro":
        from . import macros
        return macros.describe(b.get("script", ""))
    if t == "gesture":
        return "↑↓←→"
    return ""


# ------------------------------------------------------------------- injectors
class Injector:
    available = False
    reason = "no input injector for this platform"

    def key(self, code: int, down: bool) -> None: ...
    def rel(self, code: int, value: int) -> None: ...
    def close(self) -> None: ...

    # helpers shared by every backend
    def tap(self, code: int) -> None:
        self.key(code, True)
        self.key(code, False)


class UInputInjector(Injector):
    """Virtual keyboard + mouse through /dev/uinput (Linux; needs write access to the node)."""
    UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_RELBIT = 0x40045564, 0x40045565, 0x40045566
    UI_DEV_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY = 0x405C5503, 0x5501, 0x5502
    EV_SYN, EV_KEY, EV_REL = 0, 1, 2

    def __init__(self, name: str = "pyLogi virtual input"):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        try:
            for ev in (self.EV_KEY, self.EV_REL, self.EV_SYN):
                fcntl.ioctl(self.fd, self.UI_SET_EVBIT, ev)
            for code in list(range(1, 249)) + list(range(0x110, 0x118)):
                fcntl.ioctl(self.fd, self.UI_SET_KEYBIT, code)
            for rel in (0, 1, 6, 8):                 # X, Y, HWHEEL, WHEEL
                fcntl.ioctl(self.fd, self.UI_SET_RELBIT, rel)
            setup = struct.pack("HHHH80sI", 0x03, 0x046D, 0x0, 1, name.encode(), 0)
            fcntl.ioctl(self.fd, self.UI_DEV_SETUP, setup)
            fcntl.ioctl(self.fd, self.UI_DEV_CREATE)
        except OSError:
            os.close(self.fd)
            raise
        self.name = name
        self.available, self.reason = True, ""
        time.sleep(0.15)                              # let the compositor register the device

    def _emit(self, etype: int, code: int, value: int) -> None:
        os.write(self.fd, struct.pack("llHHi", 0, 0, etype, code, value))

    def key(self, code: int, down: bool) -> None:
        self._emit(self.EV_KEY, code, 1 if down else 0)
        self._emit(self.EV_SYN, 0, 0)

    def rel(self, code: int, value: int) -> None:
        self._emit(self.EV_REL, code, value)
        self._emit(self.EV_SYN, 0, 0)

    def close(self) -> None:
        try:
            fcntl.ioctl(self.fd, self.UI_DEV_DESTROY)
        finally:
            os.close(self.fd)


def default_injector() -> Injector:
    if not sys.platform.startswith("linux"):
        from . import injectors                 # SendInput / Quartz; imported here: it needs this module's Injector
        inj = injectors.platform_injector()
        if inj is not None:
            return inj
    try:
        return UInputInjector()
    except (OSError, AttributeError) as e:
        inj = Injector()
        inj.reason = f"/dev/uinput is not available ({e}); install packaging/42-pylogi.rules"
        return inj


# ------------------------------------------------------- active application detection
class X11Detector:
    """Active window class through xprop (X11 and XWayland windows only)."""

    def __init__(self):
        self.available = bool(shutil.which("xprop") and os.environ.get("DISPLAY"))
        self.reason = "" if self.available else "needs xprop and an X11 / XWayland display"

    def current(self) -> Optional[str]:
        try:
            out = subprocess.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"], capture_output=True,
                                 text=True, timeout=1).stdout
            wid = out.split("#")[-1].strip()
            if not wid.startswith("0x") or int(wid, 16) == 0:
                return None
            cls = subprocess.run(["xprop", "-id", wid, "WM_CLASS"], capture_output=True,
                                 text=True, timeout=1).stdout
            names = [p.strip().strip('"').lower() for p in cls.split("=", 1)[-1].split(",")]
            return names[-1] if names and names[-1] else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None


class GnomeDetector:
    """Focused window class from the helper GNOME Shell extension (works on Wayland)."""

    def __init__(self):
        self.reason = "install the helper extension (pylogi --install-gnome-extension) and log in again"
        self._ok_until = 0.0
        self._ok = False

    @property
    def available(self) -> bool:
        now = time.monotonic()
        if now > self._ok_until:               # re-probe now and then: the extension may be enabled later
            self._ok = self.current() is not None
            self._ok_until = now + 5
        return self._ok

    def current(self) -> Optional[str]:
        gdbus = shutil.which("gdbus")
        if not gdbus:
            return None
        try:
            out = subprocess.run([gdbus, "call", "--session", "--dest", "org.gnome.Shell",
                                  "--object-path", "/org/pylogi/ActiveWindow",
                                  "--method", "org.pylogi.ActiveWindow.Get"],
                                 capture_output=True, text=True, timeout=1)
            if out.returncode != 0:
                return None
            return out.stdout.strip().strip("()").strip(",").strip("'").lower()   # "('Firefox',)"
        except (OSError, subprocess.TimeoutExpired):
            return None


class AutoDetector:
    """First backend that works: the GNOME extension, else X11 / XWayland."""

    def __init__(self):
        self.backends = [GnomeDetector(), X11Detector()]

    @property
    def available(self) -> bool:
        return any(b.available for b in self.backends)

    @property
    def reason(self) -> str:
        return " / ".join(b.reason for b in self.backends if b.reason)

    def current(self) -> Optional[str]:
        for b in self.backends:
            if b.available:
                app = b.current()
                if app:
                    return app
        return None


# ------------------------------------------------------------------------ engine
class ActionEngine:
    def __init__(self, settings, injector: Injector | None = None, detector=None):
        self.settings = settings
        settings.engine = self
        self.injector = injector if injector is not None else default_injector()
        self.detector = detector if detector is not None else AutoDetector()
        self._devs: dict[tuple[int, int], object] = {}
        self._held: dict[str, set[int]] = {}
        self._orig: dict[tuple[str, int], tuple[bool, int]] = {}   # how each button was before we touched it
        self._lock = threading.RLock()
        self.active_app: Optional[str] = None
        self._stop = threading.Event()
        self._watcher: Optional[threading.Thread] = None
        self.on_profile_change: Callable[[Optional[str]], None] | None = None
        self._gesture: dict[str, dict] = {}                         # uid -> {"cid", "dx", "dy"} while a gesture button is held
        self._rawxy: set[tuple[str, int]] = set()                   # (uid, cid) whose raw-XY reporting we switched on
        self._macros: set[int] = set()                              # ids of the macro scripts running (no overlapping runs)
        self._thumb_acc: dict[str, int] = {}

    # -- profile watching ----------------------------------------------------------
    def start_watching(self) -> None:
        if self.detector.available and self._watcher is None:
            self._watcher = threading.Thread(target=self._watch, daemon=True, name="pylogi-activewin")
            self._watcher.start()

    def stop(self) -> None:
        self._stop.set()                  # also ends macros that are running
        self.injector.close()

    def _watch(self) -> None:
        while not self._stop.wait(1.0):
            if not any(len(self.settings.profiles(d)) > 1 for d in list(self._devs.values())):
                continue
            app = self.detector.current()
            if app != self.active_app:
                self.set_active_app(app)

    def set_active_app(self, app: Optional[str]) -> None:
        self.active_app = app
        for dev in list(self._devs.values()):
            try:
                self.configure(dev)
            except Exception as e:
                _debuglog.log("error", f"re-applying a profile: {e!r}")
        if self.on_profile_change:
            self.on_profile_change(app)

    def profile_for(self, dev) -> str:
        app = self.active_app
        return app if app and app in self.settings.profiles(dev) else ""

    # -- device configuration ---------------------------------------------------------
    def configure(self, dev, profile: str | None = None) -> None:
        """Write remaps / diversions for `dev` according to the (active) profile's bindings."""
        if not getattr(dev, "online", False) or not dev.has("buttons"):
            return
        d = dev.dev
        if not self.settings.managed(dev):
            return
        prof = self.profile_for(dev) if profile is None else profile
        bindings = self.settings.effective(dev, prof)
        need_listener = False
        for cid in self.settings.all_cids(dev):
            b = bindings.get(cid) or {"type": "default"}
            t = b["type"]
            divert = t in NEEDS_DIVERT
            remap = b.get("cid", 0) if t == "remap" else 0
            if (dev.uid, cid) not in self._orig:       # remember the state we found (another manager may own it)
                self._orig[(dev.uid, cid)] = ft.get_control_reporting(d, cid)
            raw_xy: bool | None = None
            if t == "gesture":
                raw_xy = True
                self._rawxy.add((dev.uid, cid))
            elif (dev.uid, cid) in self._rawxy:           # no longer a gesture button: stop the movement reports
                raw_xy = False
                self._rawxy.discard((dev.uid, cid))
            if raw_xy is None:
                ft.set_control_reporting(d, cid, divert, remap)
            else:
                ft.set_control_reporting(d, cid, divert, remap, raw_xy)
            need_listener |= divert
        if need_listener:
            self.register(dev)

    def release(self, dev) -> None:
        """Undo every diversion / remap pyLogi made (used when management is switched off)."""
        if not getattr(dev, "online", False) or not dev.has("buttons"):
            return
        for cid in self.settings.all_cids(dev):
            divert, remap = self._orig.pop((dev.uid, cid), (False, 0))     # back to what was there before
            try:
                if (dev.uid, cid) in self._rawxy:
                    self._rawxy.discard((dev.uid, cid))
                    ft.set_control_reporting(dev.dev, cid, divert, remap, False)
                else:
                    ft.set_control_reporting(dev.dev, cid, divert, remap)
            except Exception as e:
                _debuglog.log("error", f"restoring a control: {e!r}")
        self.unregister(dev)

    def register(self, dev) -> None:
        ch = dev.dev.ch
        with self._lock:
            self._devs[(id(ch), dev.dev.devnum)] = dev
            self._held.setdefault(dev.uid, set())
        ch.add_listener(self._on_report)

    def unregister(self, dev) -> None:
        with self._lock:
            self._devs.pop((id(dev.dev.ch), dev.dev.devnum), None)
            self._held.pop(dev.uid, None)
            self._gesture.pop(dev.uid, None)

    def configure_thumb(self, dev) -> None:
        """Put the thumb wheel in the mode saved for the device (diverted when pyLogi has to act on it)."""
        if not getattr(dev, "online", False) or not dev.has("thumbwheel") or not self.settings.managed(dev):
            return
        mode, invert = self.settings.thumb(dev)
        dev.set_thumb(diverted=mode != "native", invert=invert)
        if mode != "native":
            self.register(dev)

    # -- events -------------------------------------------------------------------------
    def _on_report(self, ch, report: bytes) -> None:
        if len(report) < 12 or report[0] != 0x11:
            return
        with self._lock:
            dev = self._devs.get((id(ch), report[1]))
        if dev is None:
            return
        idx = dev.dev.feature_index(F.REPROG_CONTROLS_V4)
        if idx:
            xy = ft.parse_raw_xy(report, idx)
            if xy is not None:
                self.on_raw_xy(dev, *xy)
                return
            cids = ft.parse_diverted(report, idx)
            if cids is not None:
                self.handle_buttons(dev, set(cids))
                return
        if dev.has("thumbwheel"):
            tidx = dev.dev.feature_index(F.THUMB_WHEEL)
            rot = ft.parse_thumb(report, tidx) if tidx else None
            if rot is not None:
                self.on_thumb(dev, rot)

    def on_raw_xy(self, dev, dx: int, dy: int) -> None:
        g = self._gesture.get(dev.uid)
        if g is not None:
            g["dx"] += dx
            g["dy"] += dy

    @staticmethod
    def gesture_direction(dx: int, dy: int, threshold: int = GESTURE_THRESHOLD) -> str:
        """'click' when the movement stayed under the threshold, else up / down / left / right."""
        if max(abs(dx), abs(dy)) < max(1, threshold):
            return "click"
        if abs(dx) >= abs(dy):
            return "right" if dx > 0 else "left"
        return "down" if dy > 0 else "up"

    def on_thumb(self, dev, rotation: int) -> None:
        mode, _inv = self.settings.thumb(dev)
        if mode == "native" or not rotation:
            return
        acc = self._thumb_acc.get(dev.uid, 0)
        if acc and (acc > 0) != (rotation > 0):
            acc = 0                                         # direction changed: start counting again
        acc += rotation
        steps, acc = divmod(abs(acc), THUMB_STEP)
        sign = 1 if rotation > 0 else -1
        self._thumb_acc[dev.uid] = sign * acc
        inj = self.injector
        for _ in range(min(steps, 20)):
            if mode == "hscroll":
                inj.rel(6, sign)
            elif mode == "zoom":
                inj.key(MODIFIERS["ctrl"], True)
                inj.rel(8, sign)
                inj.key(MODIFIERS["ctrl"], False)
            elif mode == "volume":
                inj.tap(MEDIA["Volume up" if sign > 0 else "Volume down"])
            elif mode == "tabs":
                inj.key(MODIFIERS["ctrl"], True)
                inj.tap(KEYCODES["Page Down" if sign > 0 else "Page Up"])
                inj.key(MODIFIERS["ctrl"], False)

    def handle_buttons(self, dev, now: set[int]) -> None:
        with self._lock:
            held = self._held.setdefault(dev.uid, set())
            pressed, released = now - held, held - now
            self._held[dev.uid] = set(now)
        bindings = self.settings.effective(dev, self.profile_for(dev))
        for cid in sorted(released):
            b = bindings.get(cid)
            if b and b.get("type") == "gesture":
                self._finish_gesture(dev, cid, b)
            elif b:
                self.perform(dev, b, False)
        for cid in sorted(pressed):
            b = bindings.get(cid)
            if b and b.get("type") == "gesture":
                self._gesture[dev.uid] = {"cid": cid, "dx": 0, "dy": 0}
            elif b:
                self.perform(dev, b, True)

    def _finish_gesture(self, dev, cid: int, b: dict) -> None:
        g = self._gesture.pop(dev.uid, None)
        if g is None or g["cid"] != cid:
            return
        direction = self.gesture_direction(g["dx"], g["dy"], int(b.get("threshold", GESTURE_THRESHOLD)))
        script = b.get(direction, "")
        if script:
            self.run_macro(dev, script)

    # -- macros
    def run_macro(self, dev, script: str) -> bool:
        """Start a macro in its own thread (the HID reader must not wait for pauses). False if refused."""
        from . import macros
        try:
            steps = macros.parse(script)
        except macros.MacroError as e:
            _debuglog.log("error", f"macro not run: {e}")
            return False
        key = hash(script)
        with self._lock:
            if not steps or key in self._macros:           # nothing to do / the same macro is still running
                return False
            self._macros.add(key)
        threading.Thread(target=self._run_steps, args=(dev, steps, key), daemon=True, name="pylogi-macro").start()
        return True

    def _run_steps(self, dev, steps: list[dict], key: int) -> None:
        try:
            for s in steps:
                for _ in range(s.get("times", 1)):
                    if self._stop.is_set():
                        return
                    self._step(dev, s)
        except Exception as e:
            _debuglog.log("error", f"macro step failed: {e!r}")
        finally:
            with self._lock:
                self._macros.discard(key)

    def _step(self, dev, s: dict) -> None:
        op = s["op"]
        if op == "wait":
            self._stop.wait(s["ms"] / 1000)
        elif op == "key":
            b = {"type": "key", "mods": s.get("mods", []), "key": s["key"]}
            self.perform(dev, b, True)
            self.perform(dev, b, False)
        elif op == "click":
            b = {"type": "mouse", "button": s["button"]}
            self.perform(dev, b, True)
            self.perform(dev, b, False)
        elif op == "text":
            self.perform(dev, {"type": "text", "text": s["text"]}, True)
        elif op == "media":
            self.perform(dev, {"type": "media", "name": s["name"]}, True)
        elif op == "scroll":
            self.perform(dev, {"type": "scroll", "dir": s["dir"]}, True)
        elif op == "run":
            self.perform(dev, {"type": "command", "cmd": s["cmd"]}, True)

    # -- actions ----------------------------------------------------------------------------
    def perform(self, dev, b: dict, down: bool) -> None:
        t, inj = b.get("type"), self.injector
        if t == "key":
            codes = [MODIFIERS[m] for m in b.get("mods", []) if m in MODIFIERS]
            key = KEYCODES.get(b.get("key", ""))
            if key is None:
                return
            seq = codes + [key]
            for c in (seq if down else reversed(seq)):
                inj.key(c, down)
        elif t == "mouse":
            code = MOUSE.get(b.get("button", ""))
            if code:
                inj.key(code, down)
        elif not down:
            return                                 # the remaining actions fire once, on press
        elif t == "media":
            code = MEDIA.get(b.get("name", ""))
            if code:
                inj.tap(code)
        elif t == "scroll":
            d = b.get("dir", "down")
            axis, val = {"up": (8, 1), "down": (8, -1), "left": (6, -1), "right": (6, 1)}.get(d, (8, -1))
            inj.rel(axis, val)
        elif t == "text":
            for ch in b.get("text", ""):
                k = char_keys(ch)
                if k:
                    if k[1]:
                        inj.key(MODIFIERS["shift"], True)
                    inj.tap(k[0])
                    if k[1]:
                        inj.key(MODIFIERS["shift"], False)
        elif t == "command":
            try:
                subprocess.Popen(shlex.split(b.get("cmd", "")), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            except (OSError, ValueError):
                pass
        elif t == "dpi":
            self._step_dpi(dev, +1 if b.get("mode", "next") == "next" else -1)
        elif t == "macro":
            self.run_macro(dev, b.get("script", ""))

    def _step_dpi(self, dev, direction: int) -> None:
        try:
            info = dev.dpi()
            vals = info.values
            presets = [v for v in (400, 800, 1200, 1600, 2400, 3200, 4000, 6400, 8000) if v in vals] or vals[::8]
            nxt = [v for v in presets if v > info.current] if direction > 0 else [v for v in presets if v < info.current]
            new = (min(nxt) if direction > 0 else max(nxt)) if nxt else (presets[0] if direction > 0 else presets[-1])
            dev.set_dpi(new)
        except Exception as e:
            _debuglog.log("error", f"stepping DPI: {e!r}")
