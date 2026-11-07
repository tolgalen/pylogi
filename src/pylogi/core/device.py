# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""High level device abstraction used by the UI.

`Device` defines the capability API; `HidppDevice` implements it over HID++ 2.0.
Capabilities are advertised in ``caps`` so the UI only shows what is available.
"""
from __future__ import annotations

import functools
import json
import time
from typing import Optional

from ..hidpp import features as ft
from ..i18n import N_
from ..hidpp.constants import F
from ..hidpp.onboard import Onboard
from ..hidpp.protocol import Hidpp20Device, HidppError
from . import debuglog
from .types import (RGB, Battery, Control, DeviceInfo, DpiInfo, HostInfo,
                    Profile, RgbZone, Unsupported)

# Capability names
BATTERY, EASYSWITCH, DPI, SMARTSHIFT, RATE = ("battery", "easyswitch", "dpi",
                                              "smartshift", "report_rate")
PROFILES, BUTTONS, KEYLIGHT, ZONES, BACKLIGHT = ("profiles", "buttons", "keylight",
                                                 "zones", "backlight")
WEBCAM = "webcam"
FNLOCK = "fnlock"
WHEEL = "wheel"


CACHE_TTL = 5.0          # seconds a prefetched read stays valid
BATTERY_EVERY = 60.0     # seconds between battery reads of an online device
MISSES_BEFORE_OFFLINE = 2     # unanswered pings in a row before an online device is shown as offline
OFFLINE_PING = 0.35      # shorter wait when probing a device that was offline

# read methods that pages call while being built; see Device.prefetch
PREFETCH_CAP = {"hosts": EASYSWITCH, "dpi": DPI, "smartshift": SMARTSHIFT, "report_rate": RATE,
                "controls": BUTTONS, "profiles": PROFILES, "profile_host_mode": PROFILES, "active_profile": PROFILES,
                "backlight": BACKLIGHT, "zones": ZONES, "fn_lock": FNLOCK, "cam_controls": WEBCAM,
                "wheel": WHEEL, "key_presence": KEYLIGHT}


def _cached(fn):
    """One-shot read cache: a value fetched by prefetch() (off the UI thread) is returned once."""
    @functools.wraps(fn)
    def wrapper(self, *a, **k):
        if not a and not k:
            hit = self._cache.pop(fn.__name__, None)
            if hit is not None and time.monotonic() - hit[0] < CACHE_TTL:
                if isinstance(hit[1], BaseException):
                    raise hit[1]
                return hit[1]
        return fn(self, *a, **k)
    return wrapper


class Device:
    uid: str = ""
    name: str = "Logitech device"
    kind: str = "mouse"           # mouse | keyboard | webcam | receiver | ...
    connection: str = "USB"       # Bolt | Unifying | Lightspeed | Bluetooth | USB
    parent: str = ""              # receiver name when paired through one
    slot: int = 0                 # receiver slot (1-based), 0 when not paired through a receiver
    receiver = None               # the ReceiverDevice it is paired to
    uniq: str = ""                # Bluetooth address (BLE devices)
    online: bool = True
    battery: Optional[Battery] = None
    caps: set[str]
    info: DeviceInfo
    model_ids: list[str] = []     # hex pids/wpids used to find the product photo

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        for name in PREFETCH_CAP:
            if name in cls.__dict__:
                setattr(cls, name, _cached(cls.__dict__[name]))

    def __init__(self) -> None:
        self.caps = set()
        self.info = DeviceInfo()
        self.model_ids = []
        self._cache: dict[str, tuple[float, object]] = {}

    def has(self, cap: str) -> bool:
        return cap in self.caps

    def prefetch(self, names) -> None:
        """Run the slow device reads now (call from a worker thread).  The UI thread's next
        call of each read method then returns instantly instead of blocking on HID/V4L2."""
        for name in names:
            if PREFETCH_CAP.get(name) not in self.caps:
                continue
            self._cache.pop(name, None)
            try:
                value: object = getattr(self, name)()
            except Exception as e:           # re-raised where the UI handles it
                value = e
            self._cache[name] = (time.monotonic(), value)

    def refresh(self, force: bool = False) -> None:
        """Re-read volatile state (battery, online)."""

    # capability API; default: unsupported
    def hosts(self) -> tuple[int, list[HostInfo]]: raise Unsupported
    def switch_host(self, index: int) -> None: raise Unsupported
    def dpi(self) -> DpiInfo: raise Unsupported
    def set_dpi(self, value: int) -> None: raise Unsupported
    def smartshift(self) -> tuple[bool, int]: raise Unsupported
    def set_smartshift(self, ratchet: bool, threshold: int) -> None: raise Unsupported
    def report_rate(self) -> tuple[str, list[str]]: raise Unsupported
    def set_report_rate(self, label: str) -> None: raise Unsupported
    def controls(self) -> list[Control]: raise Unsupported
    def remap_control(self, cid: int, target: int) -> None: raise Unsupported
    def profiles(self) -> list[Profile]: raise Unsupported
    def write_profile(self, p: Profile) -> None: raise Unsupported
    def profile_host_mode(self) -> bool: raise Unsupported
    def active_profile(self) -> int: raise Unsupported            # sector of the profile in use
    def set_active_profile(self, sector: int) -> None: raise Unsupported
    def set_profile_host_mode(self, host: bool) -> None: raise Unsupported
    def reset_lighting(self) -> None:
        """Hand the LEDs back to the lighting stored in the active onboard profile."""
        raise Unsupported
    def profile_color(self, sector: int) -> RGB | None:
        """Fixed lighting colour stored in an onboard profile (None: another effect)."""
        raise Unsupported
    def set_profile_color(self, sector: int, rgb: RGB) -> None:
        """Store a fixed lighting colour in an onboard profile (device memory)."""
        raise Unsupported
    def backlight(self) -> tuple[bool, int, int]: raise Unsupported
    def set_backlight(self, enabled: bool, level: int) -> None: raise Unsupported
    def zones(self) -> list[RgbZone]: raise Unsupported
    def set_zone_color(self, zone: int, rgb: RGB) -> None: raise Unsupported
    def set_key_colors(self, colors: dict[int, RGB]) -> None:
        """colors: HID usage code -> RGB."""
        raise Unsupported
    def key_layout(self) -> str: return "ansi"
    def rename_host(self, index: int, name: str) -> None: raise Unsupported
    def key_presence(self) -> set[int] | None: return None
    def wheel(self) -> tuple[bool, bool]: raise Unsupported
    def set_wheel(self, hires: bool | None = None, invert: bool | None = None) -> None: raise Unsupported
    def fn_lock(self) -> bool: raise Unsupported
    def set_fn_lock(self, on: bool) -> None: raise Unsupported

    def device_key(self) -> str:
        """Stable id used to store per-device settings."""
        i = self.info
        if i.serial:
            return f"serial:{i.serial.lower()}"
        if i.unit:
            return f"unit:{i.unit}"
        if self.slot and self.model_ids:                      # paired but never seen online this session
            return f"wpid:{self.connection.lower()}:{self.model_ids[0]}"
        return f"uid:{self.uid}"


class HidppDevice(Device):
    def __init__(self, dev: Hidpp20Device, uid: str, connection: str,
                 parent: str = "", fallback_name: str = "",
                 fallback_kind: str = "mouse"):
        super().__init__()
        self.dev = dev
        self.uid = uid
        self.connection = connection
        self.parent = parent
        self.name = fallback_name or "Logitech device"
        self.kind = fallback_kind
        self.online = False
        self._onboard: Optional[Onboard] = None
        self._identified = False
        self.remember = None      # callback(name, kind) to persist the identity
        self.on_online = None     # callback(device) when it (re)connects
        self._battery_t = 0.0
        self._misses = 0
        self.recheck = False      # a ping went unanswered: the manager scans again soon

    # -- identification / refresh -------------------------------------------
    def refresh(self, force: bool = False) -> None:
        was_online = self.online
        proto = self.dev.ping(0.8 if (was_online or not self._identified) else OFFLINE_PING)
        if proto is None and was_online and self._misses < MISSES_BEFORE_OFFLINE - 1:
            self._misses += 1          # one unanswered ping is not "offline": check again shortly
            self.recheck = True
            debuglog.log("ping", f"{self.name} ({self.uid}): no answer ({self._misses}/{MISSES_BEFORE_OFFLINE - 1} tolerated)")
            return
        if self._misses and proto is not None:
            debuglog.log("ping", f"{self.name} ({self.uid}): answered again")
        self._misses = 0
        self.online = proto is not None
        if self.online != was_online:
            debuglog.log("state", f"{self.name} ({self.uid}): {'online' if self.online else 'OFFLINE'} "
                                  f"(protocol {proto})")
        if not self.online:
            return
        if not self._identified:
            self._identify()
        if not was_online and self.on_online:      # (re)connected: restore saved settings
            try:
                self.on_online(self)
            except Exception:
                pass
        now = time.monotonic()
        if BATTERY in self.caps and (force or not was_online or self.battery is None
                                     or now - self._battery_t >= BATTERY_EVERY):
            self._battery_t = now
            try:
                self.battery = ft.read_battery(self.dev)
            except (HidppError, Unsupported):
                pass

    def _identify(self) -> None:
        d = self.dev
        try:
            self.name = d.name() or self.name
            self.kind = d.kind()
        except HidppError:
            pass
        if self.remember:
            self.remember(self.name, self.kind)
        try:
            table = d.features()
        except HidppError:
            table = {}
        c = self.caps
        if table.keys() & {F.UNIFIED_BATTERY, F.BATTERY_STATUS, F.BATTERY_VOLTAGE}:
            c.add(BATTERY)
        if table.keys() & {F.CHANGE_HOST, F.HOSTS_INFO}:
            c.add(EASYSWITCH)
        if F.ADJUSTABLE_DPI in table:
            c.add(DPI)
        if table.keys() & {F.SMART_SHIFT, F.SMART_SHIFT_ENH}:
            c.add(SMARTSHIFT)
        if table.keys() & {F.REPORT_RATE, F.EXT_REPORT_RATE}:
            c.add(RATE)
        if F.ONBOARD_PROFILES in table:
            c.add(PROFILES)
        if F.REPROG_CONTROLS_V4 in table:
            c.add(BUTTONS)
        if table.keys() & {F.PER_KEY_LIGHTING, F.PER_KEY_LIGHTING_V2}:
            c.add(KEYLIGHT)
        if table.keys() & {F.RGB_EFFECTS, F.COLOR_LED_EFFECTS}:
            c.add(ZONES)
        if F.BACKLIGHT2 in table:
            c.add(BACKLIGHT)
        if F.HIRES_WHEEL in table:
            c.add(WHEEL)
        if table.keys() & {F.FN_INVERSION, F.NEW_FN_INVERSION}:
            c.add(FNLOCK)
        try:
            self.info.unit, self.info.serial = ft.read_identity(d)
        except HidppError:
            pass
        self.info.protocol = "%d.%d" % (self.dev.protocol or (0, 0))
        self.info.extra[N_("Features")] = ", ".join(f"{k:#06x}" for k in sorted(table))
        self._identified = True

    # -- capability API ------------------------------------------------------
    def hosts(self): return ft.read_hosts(self.dev)
    def switch_host(self, index): ft.switch_host(self.dev, index)
    def rename_host(self, index, name): ft.rename_host(self.dev, index, name)
    def key_presence(self): return ft.read_key_presence(self.dev)
    def wheel(self): return ft.read_wheel(self.dev)
    def set_wheel(self, hires=None, invert=None): ft.write_wheel(self.dev, hires, invert)
    def fn_lock(self): return ft.read_fn_lock(self.dev)
    def set_fn_lock(self, on): ft.write_fn_lock(self.dev, on)
    def dpi(self): return ft.read_dpi(self.dev)
    def set_dpi(self, value): ft.write_dpi(self.dev, value)
    def smartshift(self): return ft.read_smartshift(self.dev)
    def set_smartshift(self, ratchet, threshold): ft.write_smartshift(self.dev, ratchet, threshold)
    def report_rate(self): return ft.read_report_rate(self.dev)
    def set_report_rate(self, label):
        if self.has(PROFILES) and not self._ob().host_mode():
            raise HidppError("The report rate can only be changed in host mode "
                             "(Profiles: turn off 'Use onboard profiles').")
        ft.write_report_rate(self.dev, label)
    def controls(self): return ft.read_controls(self.dev)
    def remap_control(self, cid, target): ft.write_control(self.dev, cid, target)
    def backlight(self): return ft.read_backlight(self.dev)
    def set_backlight(self, enabled, level): ft.write_backlight(self.dev, enabled, level)
    def zones(self): return ft.read_zones(self.dev)
    def set_zone_color(self, zone, rgb): ft.write_zone_color(self.dev, zone, rgb)

    def set_key_colors(self, colors):
        ft.write_key_colors(self.dev, {ft.key_id(u): c for u, c in colors.items()})

    def _ob(self) -> Onboard:
        if self._onboard is None:
            if not self.has(PROFILES):
                raise Unsupported
            self._onboard = Onboard(self.dev)
        return self._onboard

    last_backup = None                                    # path of the backup taken before the last memory write

    def profiles(self): return self._ob().read_profiles()

    def _backup_first(self, reason: str) -> None:
        """Every write to the device's memory is preceded by a restorable snapshot."""
        from . import backup
        self.last_backup = backup.auto(self, reason)

    def write_profile(self, p):
        self._backup_first("profile")
        self._ob().write_profile(p)
    def profile_host_mode(self): return self._ob().host_mode()
    def active_profile(self): return self._ob().current_sector()
    def set_active_profile(self, sector): self._ob().set_current_sector(sector)
    def set_profile_host_mode(self, host): self._ob().set_host_mode(host)

    def reset_lighting(self):
        ob = self._ob()
        cur = ob.current_sector()
        others = [sec for sec, enabled in ob.directory() if enabled and sec != cur]
        if others:                     # selecting a profile makes the keyboard reload its stored lighting
            ob.set_current_sector(others[0])
        ob.set_current_sector(cur)

    def profile_color(self, sector):
        from ..hidpp.onboard import fixed_color, lighting_records
        raw = self._ob().read_sector(sector)
        return next((c for c in map(fixed_color, lighting_records(raw)) if c), None)

    def set_profile_color(self, sector, rgb):
        self._backup_first("profile-colour")
        ob = self._ob()
        prof = next(p for p in ob.read_profiles() if p.sector == sector)
        ob.set_profile_color(prof, tuple(rgb))


def profiles_to_json(profiles: list[Profile]) -> str:
    """Serialise raw profile sectors (used for backups / export)."""
    return json.dumps([{"sector": p.sector, "enabled": p.enabled,
                        "raw": p.raw.hex()} for p in profiles], indent=1)
