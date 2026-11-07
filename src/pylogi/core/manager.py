# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Device discovery and background refresh."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from PySide6.QtCore import QObject, Signal

from ..hidpp.constants import RECEIVERS
from ..hidpp.protocol import Channel, Hidpp20Device, HidppError
from ..hidpp.receiver import Receiver
from ..hidpp.transport import HidInfo, enumerate_hidpp, open_transport
from .device import Device, HidppDevice
from .config import Config, config_dir
from . import debuglog
from .history import BatteryHistory
from .settings import DeviceSettings
from .types import DeviceInfo


class ReceiverDevice(Device):
    kind = "receiver"

    def __init__(self, rx: Receiver, uid: str):
        super().__init__()
        self.rx = rx
        self.uid = uid
        self.connection = rx.family
        self.name = f"{rx.family} receiver"
        self.caps.add("receiver")
        self.info = DeviceInfo(serial=rx.serial())
        self.slot_names: list[str] = []
        self._slots_t = 0.0
        self._slot_ids: set | None = None
        self.slots_changed = False

    SLOTS_EVERY = 30.0        # paired devices change rarely; no need to reread them every scan

    def set_slots(self, slots) -> None:
        self.slot_names = [f"{s.number}: {s.name or s.wpid} ({s.kind})" for s in slots]
        self._slots_t = time.monotonic()
        ids = {(s.number, s.wpid) for s in slots}
        if self._slot_ids is not None and ids != self._slot_ids:
            self.slots_changed = True          # a device was paired / unpaired
        self._slot_ids = ids

    def refresh(self, force: bool = False) -> None:
        if not force and time.monotonic() - self._slots_t < self.SLOTS_EVERY:
            return
        try:
            self.set_slots(self.rx.slots())
        except HidppError:
            pass

    def open_pairing(self, timeout: int = 30) -> None:
        self.rx.open_pairing(timeout)
        self._slots_t = 0.0

    # -- Bolt: the receiver announces devices in pairing mode; see hidpp.receiver.BoltPairing
    def bolt_begin(self, timeout: int = 30):
        from ..hidpp.receiver import BoltPairing
        self.bolt = BoltPairing()
        self._bolt_listener = lambda _ch, m: self.bolt.feed(m)
        self.rx.ch.add_listener(self._bolt_listener)
        self.rx.bolt_discover(timeout)
        return self.bolt

    def bolt_step(self) -> None:
        """Called regularly while pairing: asks for pairing as soon as a device has announced itself."""
        b = self.bolt
        if b.device_ready:
            b.requested = True
            self.rx.bolt_pair(b.address, b.authentication, 20 if b.keyboard else 10)

    def bolt_end(self) -> None:
        listener = getattr(self, "_bolt_listener", None)
        if listener:
            self.rx.ch.remove_listener(listener)
            self._bolt_listener = None
        for fn in (lambda: self.rx.bolt_cancel(), lambda: self.rx.bolt_discover(cancel=True)):
            try:
                fn()
            except HidppError:
                pass
        self._slots_t = 0.0

    def unpair(self, slot: int) -> None:
        self.rx.unpair(slot)
        self._slots_t = 0.0        # next scan rereads the slots; the changed list reopens the channel


class DeviceManager(QObject):
    changed = Signal()
    scanned = Signal()          # a scan finished (also when nothing changed or nothing was found)

    ACTIVE_INTERVAL = 8.0      # seconds between scans while the window is in use
    IDLE_INTERVAL = 30.0       # ... and while it is in the background

    def __init__(self, cfg: Config | None = None, interval: float | None = None,
                 settings: DeviceSettings | None = None):
        super().__init__()
        self.cfg = cfg
        self.settings = settings or (DeviceSettings(cfg) if cfg is not None else None)
        self.devices: list[Device] = []
        self.interval = interval if interval is not None else self.ACTIVE_INTERVAL
        self._fixed_interval = interval is not None
        self._wake = threading.Event()
        self._force = False
        self._scan_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pylogi-scan")
        self._chans: dict[str, tuple[Channel, HidInfo, list[Device]]] = {}
        self._known: dict[str, Device] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sig: tuple = ()
        self.errors: list[str] = []
        self.known = Config(config_dir() / "known_devices.json")   # identities seen before
        self.history = BatteryHistory()

    def _err(self, msg: str) -> None:
        """Keep the last few distinct errors (a persistent fault must not grow the list)."""
        if not self.errors or self.errors[-1] != msg:
            debuglog.log("error", msg)
            self.errors.append(msg)
            del self.errors[:-50]

    def _restore(self, dev) -> None:
        if self.settings is not None:
            try:
                self.settings.apply(dev)
            except Exception as e:                 # shown in the error list instead of being lost
                self._err(f"restore {getattr(dev, 'name', '?')}: {e}")

    RESUME_GAP = 20.0          # seconds of wall-clock time that "disappeared" between two loop turns = suspend

    def reapply_all(self) -> int:
        """Write the saved settings again to every online device (after the computer woke up)."""
        n = 0
        for d in list(self.devices):
            if getattr(d, "online", False) and getattr(d, "kind", "") != "receiver":
                self._restore(d)
                n += 1
        return n

    def _remember(self, key: str):
        def save(name: str, kind: str) -> None:
            if self.known.get(key) != [name, kind]:
                self.known.set(key, [name, kind])
        return save

    # -- lifecycle
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)      # let a running scan finish before closing the devices
        self._pool.shutdown(wait=False, cancel_futures=True)
        for ch, _, _ in self._chans.values():
            ch.close()

    def set_active(self, active: bool) -> None:
        """Scan less often while the window is in the background."""
        if not self._fixed_interval:
            self.interval = self.ACTIVE_INTERVAL if active else self.IDLE_INTERVAL
        if active:
            self._wake.set()         # catch up right away when the user comes back

    def rescan(self) -> None:
        """Scan now (forces a battery / slot re-read)."""
        self._force = True
        self._wake.set()

    def rescan_after_resume(self) -> None:
        try:
            self.scan_once(True)
            self.reapply_all()
        except Exception as e:
            self._err(f"resume: {e}")

    RECHECK_AFTER = 1.2        # seconds until the second ping after an unanswered one
    EVENT_MIN_GAP = 2.0        # seconds between event-driven refreshes (devices can flap)

    def _loop(self) -> None:
        pending: set[str] = set()
        last_event = 0.0
        last_wall = time.time()
        while not self._stop.is_set():
            now_wall = time.time()
            if now_wall - last_wall > self.interval + self.RESUME_GAP:
                # the loop was frozen: the computer slept.  Devices reset on wake-up without ever looking
                # offline, so their volatile settings (diversions, DPI, colours) are written again.
                self.rescan_after_resume()
            last_wall = now_wall
            try:
                if pending:
                    self.refresh_channels(pending)
                else:
                    self.scan_once(self._force)
            except Exception as e:             # the scan thread must never die
                self._err(f"scan: {e}")
            self._force = False
            pending = set()
            queued: set[str] = set()
            quick = False
            for d in list(self.devices):                       # an unanswered ping: look again soon, do not wait
                if getattr(d, "recheck", False):
                    d.recheck, quick = False, True
            deadline = time.monotonic() + (self.RECHECK_AFTER if quick else self.interval)
            # wait in short slices so devices waking up / sleeping are noticed quickly
            while not self._stop.is_set() and time.monotonic() < deadline:
                if self._wake.wait(0.5):
                    self._wake.clear()
                    break
                try:
                    queued |= {p for p, (ch, _, _) in list(self._chans.items()) if ch.connection_event()}
                except Exception as e:
                    self._err(f"poll: {e}")
                    queued = set(self._chans)
                if queued and time.monotonic() - last_event >= self.EVENT_MIN_GAP:
                    pending, last_event = queued, time.monotonic()
                    break

    # -- scan
    def scan_once(self, force: bool = False) -> None:
        with self._scan_lock:          # one scan at a time, whoever asks
            self._scan(force)

    def _scan(self, force: bool) -> None:
        if self._stop.is_set():
            return
        try:
            infos = {i.path: i for i in enumerate_hidpp()}
        except Exception as e:  # pragma: no cover - platform specific
            self._err(str(e))
            infos = {}
        for path in list(self._chans):
            if path not in infos:
                ch, _, devs = self._chans.pop(path)
                ch.close()
                for d in devs:
                    self._known.pop(d.uid, None)
        new = [(p, i) for p, i in infos.items() if p not in self._chans]
        if len(new) > 1:               # receivers enumerate independently: do it in parallel
            list(self._pool.map(lambda pi: self._open(*pi), new))
        else:
            for p, i in new:
                self._open(p, i)

        self._refresh_groups(list(self._chans), force)
        self._publish()

    def refresh_channels(self, paths) -> None:
        """Event-driven refresh of only the channels that reported something."""
        with self._scan_lock:
            if not self._stop.is_set():
                self._refresh_groups([p for p in paths if p in self._chans], False)
                self._publish()

    def _refresh_groups(self, paths: list[str], force: bool) -> None:
        broken: list[str] = []

        def refresh_channel(path: str) -> None:
            devs = self._chans[path][2]
            for d in list(devs):
                try:
                    d.refresh(force)
                except OSError as e:           # the interface is gone: drop it, reopen next scan
                    self._err(f"{d.name}: {e}")
                    broken.append(path)
                    return
                except Exception as e:
                    self._err(f"{d.name}: {e}")
                if getattr(d, "slots_changed", False):     # paired / unpaired: rebuild the device list
                    d.slots_changed = False
                    broken.append(path)
        if len(paths) > 1:
            list(self._pool.map(refresh_channel, paths))
        else:
            for p in paths:
                refresh_channel(p)
        for path in set(broken):
            ch, _, devs = self._chans.pop(path, (None, None, []))
            if ch is not None:
                ch.close()
            for d in devs:
                self._known.pop(d.uid, None)

    def _open(self, path: str, info: HidInfo) -> None:
        try:
            ch = Channel(open_transport(info), info.has_short)
        except OSError as e:
            self._err(f"{path}: {e} (check udev rules / permissions)")
            return
        try:
            self._enumerate(path, info, ch)
        except (OSError, HidppError) as e:        # e.g. the device vanished mid-conversation
            self._err(f"{path}: {e}")
            ch.close()
            self._chans.pop(path, None)

    def _enumerate(self, path: str, info: HidInfo, ch: Channel) -> None:
        devs: list[Device] = []
        if info.pid in RECEIVERS:
            rx = Receiver(ch, info.pid)
            rx.enable_notifications()
            rdev = ReceiverDevice(rx, f"{path}:rx")
            rdev.model_ids = [f"{info.pid:04x}"]
            devs.append(rdev)
            self._known[rdev.uid] = rdev
            try:
                slots = rx.slots()
                rdev.set_slots(slots)
                for s in slots:
                    key = f"{rx.family}:{s.wpid}"
                    old = self.known.get(key) or [s.name, s.kind]
                    hd = HidppDevice(Hidpp20Device(ch, s.number), f"{path}:{s.number}",
                                     rx.family, rdev.name, s.name or old[0],
                                     old[1] if s.kind == "unknown" else s.kind)
                    hd.remember = self._remember(key)
                    hd.on_online = self._restore
                    hd.model_ids = [s.wpid.lower()]
                    hd.slot = s.number
                    hd.receiver = rdev
                    devs.append(hd)
                    self._known[hd.uid] = hd
            except HidppError as e:
                self._err(f"{path}: {e}")
        else:
            conn = "Bluetooth" if info.bus == "bluetooth" else "USB"
            for num in ((0xFF, 0x00) if info.bus == "usb" else (0x00, 0xFF)):
                h = Hidpp20Device(ch, num)
                if h.ping():
                    hd = HidppDevice(h, path, conn, "", info.name)
                    hd.model_ids = [f"{info.pid:04x}"]
                    hd.on_online = self._restore
                    hd.uniq = info.uniq
                    devs.append(hd)
                    self._known[hd.uid] = hd
                    break
            if not devs:
                ch.close()
                return
        self._chans[path] = (ch, info, devs)

    @staticmethod
    def dedupe(devs: list) -> list:
        """One entry per physical device.

        A wireless mouse can be reached twice: through its receiver (a slot) and as a node of its own that
        the kernel creates for it. Both answer, so they would be listed twice:
          * two online entries with the same identity (serial / unit id): the one behind the receiver is kept;
          * a paired slot that is offline while the same model is online some other way: the placeholder goes.
        """
        def identity(d) -> str | None:
            if getattr(d, "kind", "") == "receiver" or not getattr(d, "online", False):
                return None
            key = d.device_key()
            return key if key.startswith(("serial:", "unit:")) else None

        best: dict[str, object] = {}
        for d in devs:
            k = identity(d)
            if k is None:
                continue
            cur = best.get(k)
            if cur is None or (getattr(d, "slot", None) and not getattr(cur, "slot", None)):
                best[k] = d
        out = [d for d in devs if identity(d) is None or best[identity(d)] is d]

        def model(d) -> str:
            ids = getattr(d, "model_ids", None) or [""]
            return str(ids[0]).lower()
        # mice that expose no serial / unit id (a G305): the same model reachable both through its receiver
        # and as a node of its own is one mouse, and the receiver entry is the one kept
        via_receiver = {model(d) for d in out if getattr(d, "online", False) and getattr(d, "slot", None)
                        and getattr(d, "kind", "") != "receiver"}
        out = [d for d in out if getattr(d, "slot", None) or not getattr(d, "online", False)
               or getattr(d, "kind", "") == "receiver" or model(d) not in via_receiver]
        online_models = {str(m).lower() for d in out if getattr(d, "online", False)
                         and getattr(d, "kind", "") != "receiver" for m in (getattr(d, "model_ids", None) or [])[:1]}
        return [d for d in out
                if getattr(d, "online", True) or not getattr(d, "slot", None)
                or not (getattr(d, "model_ids", None) and str(d.model_ids[0]).lower() in online_models)]

    def _publish(self) -> None:
        devs = [d for _, _, ds in self._chans.values() for d in ds]
        from .webcam import find_logitech_webcams
        try:
            for cam in find_logitech_webcams():
                if cam.uid in self._known:
                    cam = self._known[cam.uid]  # type: ignore[assignment]
                else:
                    self._known[cam.uid] = cam
                devs.append(cam)
        except Exception as e:  # pragma: no cover
            self._err(f"webcam: {e}")
        merged = self.dedupe(devs)
        if debuglog.LOG.enabled:
            for d in devs:
                if d not in merged:
                    debuglog.log("merge", f"{d.name} ({d.uid}) hidden: the same device is listed through another path")
        devs = merged
        if self.cfg is not None:                       # "Delete from pyLogi": never shown, never recorded
            gone = set(self.cfg.get("deleted", []))
            devs = [d for d in devs if d.device_key() not in gone]
        for d in devs:
            if d.online and d.battery is not None and d.battery.percent is not None:
                self.history.record(d.device_key(), d.battery.percent, d.battery.charging)
        sig = tuple((d.uid, d.name, d.online, d.battery.percent if d.battery else None,
                     d.battery.charging if d.battery else None, tuple(sorted(d.caps)))
                    for d in devs)
        if sig != self._sig:
            self._sig = sig
            self.devices = devs
            debuglog.LOG.devices(devs)
            self.changed.emit()
        self.scanned.emit()
