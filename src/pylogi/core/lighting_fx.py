# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Software lighting effects for per-key RGB keyboards (wave, rainbow, breathing, radar, ripple, ...).

The effects are computed here and written key by key (the same per-key writes as the colour editor), so
pyLogi has to keep running for them to play; stopping one hands the keys back to the saved colours.
`frame()` is pure (time in, colours out) and is what the tests exercise."""
from __future__ import annotations

import colorsys
import math
import os
import random
import struct
import threading
import time
from collections import deque
from typing import Callable

from ..i18n import N_
from . import debuglog as _debuglog

RGB = tuple[int, int, int]
Positions = dict[int, tuple[float, float]]            # HID usage -> (x, y) of the key centre, in key units

# id -> (label, needs key presses, uses the colour picker)
EFFECTS: dict[str, tuple[str, bool, bool]] = {
    "wave": (N_("Wave"), False, True),
    "rainbow": (N_("Rainbow wave"), False, False),
    "cycle": (N_("Colour cycle"), False, False),
    "breathing": (N_("Breathing"), False, True),
    "radar": (N_("Radar"), False, True),
    "starlight": (N_("Starlight"), False, True),
    "ripple": (N_("Ripple on key press"), True, True),
    "reactive": (N_("Reactive (lights up when pressed)"), True, True),
}
BLACK: RGB = (0, 0, 0)


def _scale(rgb: RGB, k: float) -> RGB:
    k = max(0.0, min(1.0, k))
    return (int(rgb[0] * k), int(rgb[1] * k), int(rgb[2] * k))


def _hsv(h: float, s: float = 1.0, v: float = 1.0) -> RGB:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def positions_from_rows(rows) -> Positions:
    """Key centres from keyboard_widget rows [(label, usage, width[, height]), ...]."""
    out: Positions = {}
    for r, row in enumerate(rows):
        x = 0.0
        for k in row:
            u, w = k[1], k[2]
            span = k[3] if len(k) > 3 else 1
            if u is not None:
                out[u] = (x + w / 2, r + span / 2)
            x += w
    return out


def speed_factor(speed: int) -> float:
    """Speed 1..10 -> cycles per second (0.08 .. 1.0)."""
    return 0.08 * (1.0 + (max(1, min(10, speed)) - 1) * 1.4)


class State:
    """What the animated effects remember between frames."""

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self.stars: dict[int, tuple[float, float]] = {}      # usage -> (birth time, lifetime)
        self.presses: list[tuple[float, int]] = []           # (time, usage)
        self.last_star = 0.0


def frame(effect: str, t: float, keys: Positions, color: RGB = (0, 200, 255), speed: int = 5,
          state: State | None = None, presses: list[int] | None = None) -> dict[int, RGB]:
    """Colour of every key at time t (seconds). `presses` are usages pressed since the last frame."""
    state = state or State()
    f = speed_factor(speed)
    xs = [p[0] for p in keys.values()] or [0.0]
    ys = [p[1] for p in keys.values()] or [0.0]
    width = max(max(xs) - min(xs), 1.0)
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    out: dict[int, RGB] = {}
    if effect == "wave":                                     # a band of light of the chosen colour sweeping across
        for u, (x, _y) in keys.items():
            v = 0.5 + 0.5 * math.sin(2 * math.pi * (x / (width * 0.6) - t * f))
            out[u] = _scale(color, 0.12 + 0.88 * v ** 2)
    elif effect == "rainbow":                                # the spectrum travelling across the keyboard
        for u, (x, y) in keys.items():
            out[u] = _hsv(x / width * 0.9 + y * 0.015 - t * f)
    elif effect == "cycle":                                  # every key the same colour, walking the spectrum
        c = _hsv(t * f)
        out = {u: c for u in keys}
    elif effect == "breathing":
        k = 0.08 + 0.92 * (0.5 - 0.5 * math.cos(2 * math.pi * t * f * 0.6))
        c = _scale(color, k)
        out = {u: c for u in keys}
    elif effect == "radar":                                  # a sweeping arm with a fading tail around the centre
        head = (t * f) % 1.0
        for u, (x, y) in keys.items():
            ang = (math.atan2(y - cy, x - cx) / (2 * math.pi)) % 1.0
            behind = (head - ang) % 1.0
            out[u] = _scale(color, (1 - behind / 0.45) ** 2) if behind < 0.45 else BLACK
    elif effect == "starlight":                              # random keys twinkle and fade
        for u in [u for u, (b, life) in state.stars.items() if t - b > life]:
            del state.stars[u]
        rate = 0.05 / max(f, 0.05)
        while t - state.last_star > rate and keys:
            state.last_star += rate if t - state.last_star < 1 else t - state.last_star
            state.stars[state.rng.choice(list(keys))] = (t, 0.6 + state.rng.random() * 0.9)
        for u in keys:
            b = state.stars.get(u)
            out[u] = _scale(color, 1 - (t - b[0]) / b[1]) if b else BLACK
    elif effect in ("ripple", "reactive"):
        for u in presses or []:
            state.presses.append((t, u))
        life = 1.6 if effect == "ripple" else 0.9
        state.presses = [(b, u) for b, u in state.presses if t - b < life][-24:]
        for u, (x, y) in keys.items():
            level = 0.0
            for b, pu in state.presses:
                age = t - b
                if effect == "reactive":
                    if pu == u:
                        level = max(level, 1 - age / life)
                else:
                    px, py = keys.get(pu, (x, y))
                    ring = age * (5 + 8 * f)                  # the ring grows in key units
                    d = abs(math.hypot(x - px, y - py) - ring)
                    level = max(level, max(0.0, 1 - d / 1.4) * (1 - age / life))
            out[u] = _scale(color, level)
    else:
        raise ValueError(f"unknown effect {effect!r}")
    return out


# ----------------------------------------------------------------------------- key presses (Linux evdev)
_EVDEV_TO_USAGE = {
    1: 0x29, 2: 0x1E, 3: 0x1F, 4: 0x20, 5: 0x21, 6: 0x22, 7: 0x23, 8: 0x24, 9: 0x25, 10: 0x26, 11: 0x27,
    12: 0x2D, 13: 0x2E, 14: 0x2A, 15: 0x2B, 16: 0x14, 17: 0x1A, 18: 0x08, 19: 0x15, 20: 0x17, 21: 0x1C,
    22: 0x18, 23: 0x0C, 24: 0x12, 25: 0x13, 26: 0x2F, 27: 0x30, 28: 0x28, 29: 0xE0, 30: 0x04, 31: 0x16,
    32: 0x07, 33: 0x09, 34: 0x0A, 35: 0x0B, 36: 0x0D, 37: 0x0E, 38: 0x0F, 39: 0x33, 40: 0x34, 41: 0x35,
    42: 0xE1, 43: 0x31, 44: 0x1D, 45: 0x1B, 46: 0x06, 47: 0x19, 48: 0x05, 49: 0x11, 50: 0x10, 51: 0x36,
    52: 0x37, 53: 0x38, 54: 0xE5, 55: 0x55, 56: 0xE2, 57: 0x2C, 58: 0x39, 59: 0x3A, 60: 0x3B, 61: 0x3C,
    62: 0x3D, 63: 0x3E, 64: 0x3F, 65: 0x40, 66: 0x41, 67: 0x42, 68: 0x43, 69: 0x53, 70: 0x47, 71: 0x5F,
    72: 0x60, 73: 0x61, 74: 0x56, 75: 0x5C, 76: 0x5D, 77: 0x5E, 78: 0x57, 79: 0x59, 80: 0x5A, 81: 0x5B,
    82: 0x62, 83: 0x63, 86: 0x64, 87: 0x44, 88: 0x45, 96: 0x58, 97: 0xE4, 98: 0x54, 99: 0x46, 100: 0xE6,
    102: 0x4A, 103: 0x52, 104: 0x4B, 105: 0x50, 106: 0x4F, 107: 0x4D, 108: 0x51, 109: 0x4E, 110: 0x49,
    111: 0x4C, 119: 0x48, 125: 0xE3, 126: 0xE7, 127: 0x65,
}
_EVENT = struct.Struct("llHHi")                        # struct input_event on 64-bit Linux
EV_KEY = 1


def usage_for_keycode(code: int) -> int | None:
    return _EVDEV_TO_USAGE.get(code)


def _event_nodes(device_name: str, sysfs: str = "/sys/class/input") -> list[str]:
    """/dev/input/eventN nodes whose name contains the model token of the device (e.g. 'G915')."""
    words = [w.lower() for w in device_name.replace("-", " ").split() if len(w) >= 3]
    token = next((w for w in words if any(c.isdigit() for c in w)), words[0] if words else "")
    nodes: list[str] = []
    try:
        names = sorted(os.listdir(sysfs))
    except OSError:
        return nodes
    for n in names:
        if not n.startswith("event"):
            continue
        try:
            with open(f"{sysfs}/{n}/device/name", encoding="utf-8", errors="replace") as fh:
                label = fh.read().strip().lower()
        except OSError:
            continue
        if token and token in label and "logitech" in label + device_name.lower():
            nodes.append(f"/dev/{'input/' if os.path.isdir('/dev/input') else ''}{n}")
    return nodes


class KeyPressSource:
    """Key presses of one keyboard, read from its evdev nodes (needs read access to /dev/input/event*).

    Only the key code is used (to light the key); nothing is stored or sent anywhere."""

    def __init__(self, device_name: str, sysfs: str = "/sys/class/input"):
        self.nodes = _event_nodes(device_name, sysfs)
        self.fds: list[int] = []
        self.reason = ""
        self.queue: deque[int] = deque(maxlen=64)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if not self.nodes:
            self.reason = "no input device found for this keyboard"
            return False
        for n in self.nodes:
            try:
                self.fds.append(os.open(n, os.O_RDONLY | os.O_NONBLOCK))
            except OSError as e:
                self.reason = f"cannot read {n} ({e.strerror}); add your user to the 'input' group"
        if not self.fds:
            return False
        self._thread = threading.Thread(target=self._run, daemon=True, name="pylogi-keypress")
        self._thread.start()
        return True

    def _run(self) -> None:
        import select
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select(self.fds, [], [], 0.1)
            except (OSError, ValueError):
                return
            for fd in ready:
                try:
                    data = os.read(fd, _EVENT.size * 32)
                except OSError:
                    continue
                for off in range(0, len(data) - _EVENT.size + 1, _EVENT.size):
                    _s, _us, etype, code, value = _EVENT.unpack_from(data, off)
                    if etype == EV_KEY and value == 1:
                        u = usage_for_keycode(code)
                        if u is not None:
                            self.queue.append(u)

    def pop(self) -> list[int]:
        out = []
        while self.queue:
            out.append(self.queue.popleft())
        return out

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(1)
        for fd in self.fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self.fds.clear()


# ----------------------------------------------------------------------------- running an effect
class Runner:
    """Plays one effect on one keyboard in a background thread, sending only the keys that changed."""

    def __init__(self, dev, effect: str, keys: Positions, color: RGB = (0, 200, 255), speed: int = 5,
                 fps: float = 20.0, on_stop: Callable[["Runner"], None] | None = None):
        self.dev, self.effect, self.keys = dev, effect, keys
        self.color, self.speed, self.fps = tuple(color), speed, fps
        self.on_stop = on_stop
        self.error = ""
        self.source: KeyPressSource | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.sent: dict[int, RGB] = {}
        self.frames = 0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> bool:
        if EFFECTS[self.effect][1]:
            self.source = KeyPressSource(self.dev.name)
            if not self.source.start():
                self.error = self.source.reason
                return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="pylogi-fx")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(3)
        if self.source:
            self.source.stop()

    def step(self, t: float, state: State) -> dict[int, RGB]:
        """One frame: the keys whose colour changed since the last one (all of them on the first)."""
        presses = self.source.pop() if self.source else None
        full = frame(self.effect, t, self.keys, self.color, self.speed, state, presses)
        changed = {u: c for u, c in ((u, tuple(v)) for u, v in full.items()) if self.sent.get(u) != c}
        return changed

    def _run(self) -> None:
        state = State()
        t0 = time.monotonic()
        state.last_star = 0.0
        period = 1.0 / self.fps
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                changed = self.step(started - t0, state)
                if changed:
                    self.dev.set_key_colors(changed)
                    self.sent.update(changed)
                    self.frames += 1
                # a slow link (receiver) sets the pace: never queue frames faster than they can be written
                spent = time.monotonic() - started
                self._stop.wait(max(0.01, period - spent))
        except Exception as e:                           # device gone / write refused: stop quietly, report
            self.error = str(e) or e.__class__.__name__
        finally:
            if self.on_stop:
                self.on_stop(self)


class Hub:
    """The effects currently playing, one per keyboard (keyed by device uid)."""

    def __init__(self):
        self.runners: dict[str, Runner] = {}
        self._lock = threading.Lock()

    def start(self, dev, effect: str, keys: Positions, color: RGB, speed: int) -> Runner:
        self.stop(dev)
        r = Runner(dev, effect, keys, color, speed, on_stop=self._gone)
        with self._lock:
            self.runners[dev.uid] = r
        if not r.start():
            self._gone(r)
        return r

    def _gone(self, runner: Runner) -> None:
        with self._lock:
            if self.runners.get(runner.dev.uid) is runner:
                del self.runners[runner.dev.uid]

    def get(self, dev) -> Runner | None:
        return self.runners.get(dev.uid)

    def stop(self, dev) -> None:
        r = self.runners.get(dev.uid)
        if r:
            r.stop()
            self._gone(r)

    def stop_all(self) -> None:
        """At exit: stop every effect and give the keys back to the keyboard's own lighting."""
        for r in list(self.runners.values()):
            r.stop()
            try:
                r.dev.reset_lighting()
            except Exception as e:
                _debuglog.log("error", f"resetting lighting: {e!r}")
        self.runners.clear()


def positions_for(dev, rec: dict) -> Positions:
    """Key positions of a keyboard with the layout / size the user chose (or the detected ones)."""
    from ..ui.keyboard_widget import build_rows, detect_form
    try:
        presence = dev.key_presence()
    except Exception:
        presence = None
    auto_layout, auto_numpad = detect_form(presence, dev.name)
    layout, size = rec.get("key_layout", "auto"), rec.get("key_size", "auto")
    manual = layout != "auto" or size != "auto"
    rows = build_rows(auto_layout if layout == "auto" else layout,
                      auto_numpad if size == "auto" else size == "full", None if manual else presence)
    return positions_from_rows(rows)


HUB = Hub()
