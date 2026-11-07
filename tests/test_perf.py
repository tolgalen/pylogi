# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from test_hidpp import FakeDevice  # noqa: E402
from pylogi.core import device as devmod  # noqa: E402
from pylogi.core.device import EASYSWITCH, Device, HidppDevice  # noqa: E402
from pylogi.core.types import HostInfo, Unsupported  # noqa: E402
from pylogi.hidpp.constants import F  # noqa: E402
from pylogi.hidpp.protocol import Channel, Hidpp20Device  # noqa: E402
from pylogi.hidpp.transport import Transport  # noqa: E402

app = QApplication.instance() or QApplication([])


class SlowDevice(Device):
    """hosts() is slow and records which thread ran it."""
    kind, name, uid, connection, online = "mouse", "Slow", "slow", "Bolt", True

    def __init__(self):
        super().__init__()
        self.caps = {EASYSWITCH}
        self.calls: list[str] = []

    def hosts(self):
        self.calls.append(threading.current_thread().name)
        time.sleep(0.3)
        return 0, [HostInfo(0, True, "pc", "bolt", True)]

    def dpi(self):
        raise Unsupported


class ReadCacheTests(unittest.TestCase):
    def test_prefetch_is_served_once(self):
        d = SlowDevice()
        d.prefetch(["hosts"])
        t = time.perf_counter()
        d.hosts()
        self.assertLess(time.perf_counter() - t, 0.05)          # from the cache
        self.assertEqual(len(d.calls), 1)
        d.hosts()                                                # one-shot: real read again
        self.assertEqual(len(d.calls), 2)

    def test_expired_entry_is_ignored(self):
        d = SlowDevice()
        d.prefetch(["hosts"])
        d._cache["hosts"] = (time.monotonic() - devmod.CACHE_TTL - 1, d._cache["hosts"][1])
        d.hosts()
        self.assertEqual(len(d.calls), 2)

    def test_errors_are_reraised_and_unsupported_skipped(self):
        d = SlowDevice()
        d.caps.add("dpi")
        d.prefetch(["dpi", "controls", "profiles"])        # controls/profiles: capability missing
        with self.assertRaises(Unsupported):
            d.dpi()
        self.assertNotIn("controls", d._cache)


class ThrottleTests(unittest.TestCase):
    def setUp(self):
        self.t = FakeDevice()
        self.t.FEATS = dict(FakeDevice.FEATS)
        self.t.FEATS[F.BATTERY_STATUS] = 7
        orig = self.t.write
        self.battery_reads = 0

        def write(data):
            if data[2] == 7 and data[3] >> 4 == 0:
                self.battery_reads += 1
                self.t.q.append(bytes([0x11, data[1], data[2], data[3], 80, 70, 0]) + bytes(13))
                return
            orig(data)
        self.t.write = write
        # FEATURE_SET must list the extra feature
        self.hd = HidppDevice(Hidpp20Device(Channel(self.t), 1), "u", "Bolt")

    def test_battery_is_read_once_per_minute(self):
        # F.FEATURE_SET reports len(FEATS) features, the fake maps index 7 -> BATTERY_STATUS
        orig_write = self.t.write

        def write(data):
            if data[2] == 1 and data[3] >> 4 == 1 and data[4] == 7:      # feature id of index 7
                self.t.q.append(bytes([0x11, data[1], data[2], data[3], 0x10, 0x00]) + bytes(14))
                return
            orig_write(data)
        self.t.write = write
        self.hd.refresh()
        self.assertEqual(self.battery_reads, 1)
        for _ in range(5):
            self.hd.refresh()
        self.assertEqual(self.battery_reads, 1)                        # throttled
        self.hd.refresh(force=True)
        self.assertEqual(self.battery_reads, 2)
        self.hd._battery_t -= devmod.BATTERY_EVERY + 1
        self.hd.refresh()
        self.assertEqual(self.battery_reads, 3)
        self.assertEqual(self.hd.battery.percent, 80)

    def test_offline_device_is_probed_with_a_short_timeout(self):
        seen = []
        orig = self.hd.dev.ping
        self.hd.dev.ping = lambda timeout=0.8: (seen.append(timeout), orig(timeout))[1]
        self.hd.refresh()
        self.t.silent = True
        self.hd.refresh()                       # was online -> normal timeout (one miss is tolerated)
        self.hd.refresh()                       # second miss in a row: now offline
        self.assertFalse(self.hd.online)
        self.hd.refresh()                       # was offline -> short timeout
        self.assertEqual(seen[-1], devmod.OFFLINE_PING)
        self.assertLess(devmod.OFFLINE_PING, 0.8)


class ReceiverThrottleTests(unittest.TestCase):
    def test_slots_are_reread_every_30s_unless_forced(self):
        from pylogi.core.manager import ReceiverDevice

        class Rx:
            family, max_devices = "Bolt", 6
            calls = 0
            def serial(self): return "X"
            def slots(self):
                Rx.calls += 1
                return []
        rd = ReceiverDevice(Rx(), "r")
        rd.refresh()
        rd.refresh()
        rd.refresh()
        self.assertEqual(Rx.calls, 1)
        rd.refresh(force=True)
        self.assertEqual(Rx.calls, 2)
        rd._slots_t -= ReceiverDevice.SLOTS_EVERY + 1
        rd.refresh()
        self.assertEqual(Rx.calls, 3)


class NotificationTests(unittest.TestCase):
    def _chan(self, reports):
        class T(Transport):
            def __init__(s): s.q = list(reports)
            def write(s, d): pass
            def read(s, timeout): return s.q.pop(0) if s.q else None
        return Channel(T())

    def test_connection_report_is_detected(self):
        ch = self._chan([bytes([0x10, 1, 0x41, 0x04, 0x40, 0x02, 0x40])])
        self.assertTrue(ch.poll())
        self.assertEqual(len(ch.notifications), 1)

    def test_other_reports_are_kept_but_do_not_trigger(self):
        ch = self._chan([bytes([0x11, 1, 0x05, 0x00]) + bytes(16)])
        self.assertFalse(ch.poll())
        self.assertEqual(len(ch.notifications), 1)

    def test_poll_never_blocks_a_request_in_flight(self):
        ch = self._chan([bytes([0x10, 1, 0x41, 0, 0, 0, 0])])
        done = []
        with ch.lock:                       # simulate a request holding the channel
            t = threading.Thread(target=lambda: done.append(ch.poll()))
            t.start(); t.join(1)
        self.assertEqual(done, [False])     # skipped, report is still queued for later
        self.assertTrue(ch.poll())


class ManagerLoopTests(unittest.TestCase):
    def test_rescan_wakes_the_single_scan_thread_and_scans_never_overlap(self):
        from pylogi.core.manager import DeviceManager
        m = DeviceManager(interval=60)
        running, overlap, count = [0], [False], [0]

        def fake_scan(force):
            running[0] += 1
            overlap[0] |= running[0] > 1
            count[0] += 1
            time.sleep(0.05)
            running[0] -= 1
        m._scan = fake_scan
        m.start()
        deadline = time.time() + 2
        while count[0] < 1 and time.time() < deadline:
            time.sleep(0.01)
        for _ in range(5):
            m.rescan()
            threading.Thread(target=m.scan_once).start()      # concurrent callers
        time.sleep(0.8)
        m.stop()
        self.assertGreaterEqual(count[0], 3)
        self.assertFalse(overlap[0])

    def test_adaptive_interval(self):
        from pylogi.core.manager import DeviceManager
        m = DeviceManager()
        self.assertEqual(m.interval, DeviceManager.ACTIVE_INTERVAL)
        m.set_active(False)
        self.assertEqual(m.interval, DeviceManager.IDLE_INTERVAL)
        m.set_active(True)
        self.assertEqual(m.interval, DeviceManager.ACTIVE_INTERVAL)
        m.stop()


class AsyncPageTests(unittest.TestCase):
    def test_page_reads_happen_off_the_ui_thread(self):
        import tempfile
        from pathlib import Path
        from pylogi.core.config import Config
        from pylogi.ui.device_page import DevicePage
        page = DevicePage(Config(Path(tempfile.mkdtemp()) / "c.json"), None)
        dev = SlowDevice()
        page.show_device(dev)                        # first section = "device" (easy-switch)
        t = time.perf_counter()
        page.show_device(dev)
        self.assertLess(time.perf_counter() - t, 0.15)             # UI is not blocked by the 0.3 s read
        placeholder = page.pages["device"]
        deadline = time.time() + 3
        while page.pages["device"] is placeholder and time.time() < deadline:
            QTest.qWait(20)
        self.assertIsNot(page.pages["device"], placeholder)
        self.assertIs(page.stack.currentWidget(), page.pages["device"])
        self.assertNotIn(threading.main_thread().name, dev.calls[-1:])
        self.assertEqual(dev.calls.count(threading.current_thread().name), 0)   # never on the UI thread

    def test_late_result_of_a_previous_device_is_ignored(self):
        import tempfile
        from pathlib import Path
        from pylogi.core.config import Config
        from pylogi.ui.device_page import DevicePage
        page = DevicePage(Config(Path(tempfile.mkdtemp()) / "c.json"), None)
        a, b = SlowDevice(), SlowDevice()
        b.uid = "other"
        page.show_device(a)
        page.show_device(b)                           # a's read is still running
        QTest.qWait(900)
        self.assertIs(page.dev, b)
        self.assertEqual(page.stack.count(), 1)       # only b's page is in the stack


if __name__ == "__main__":
    unittest.main()


class ManagerRobustnessTests(unittest.TestCase):
    def test_scan_thread_survives_an_exception(self):
        from pylogi.core.manager import DeviceManager
        m = DeviceManager(interval=0.2)
        calls = []

        def flaky(force):
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("boom")
        m._scan = flaky
        m.start()
        deadline = time.time() + 3
        while len(calls) < 3 and time.time() < deadline:
            m.rescan()
            time.sleep(0.05)
        m.stop()
        self.assertGreaterEqual(len(calls), 3)               # kept scanning after the error
        self.assertTrue(any("boom" in e for e in m.errors))

    def test_broken_pipe_while_opening_drops_the_channel(self):
        from pylogi.core import manager as mgr
        from pylogi.hidpp.transport import HidInfo

        class Broken(Transport):
            closed = False
            def write(self, d): raise BrokenPipeError(32, "Broken pipe")
            def read(self, t): return None
            def close(self): Broken.closed = True
        m = mgr.DeviceManager()
        orig = mgr.open_transport
        mgr.open_transport = lambda info: Broken()
        try:
            m._open("/dev/hidrawX", HidInfo("/dev/hidrawX", 0x046D, 0xC548, "usb", "rx", "", True))
        finally:
            mgr.open_transport = orig
        self.assertNotIn("/dev/hidrawX", m._chans)
        self.assertTrue(Broken.closed)
        self.assertTrue(m.errors)

    def test_io_error_on_a_known_channel_triggers_reopen(self):
        from pylogi.core import manager as mgr
        m = mgr.DeviceManager()

        class Dev:
            uid, name, online, kind, battery, caps = "u", "d", True, "mouse", None, set()
            def refresh(self, force=False): raise BrokenPipeError(32, "Broken pipe")
        closed = []
        ch = type("C", (), {"close": lambda s: closed.append(1), "poll": lambda s: False, "connection_event": lambda s: False})()
        m._chans["/dev/hidrawY"] = (ch, None, [Dev()])
        orig = mgr.enumerate_hidpp
        mgr.enumerate_hidpp = lambda: []
        try:
            m._scan(False)
        finally:
            mgr.enumerate_hidpp = orig
        self.assertEqual(m._chans, {})
        m._chans["/dev/hidrawY"] = (ch, None, [Dev()])
        m._scan(False)  # nothing enumerated -> path vanished -> closed (also covered)

    def test_error_log_is_bounded_and_deduplicated(self):
        from pylogi.core.manager import DeviceManager
        m = DeviceManager()
        for _ in range(10):
            m._err("same")
        self.assertEqual(m.errors, ["same"])
        for i in range(200):
            m._err(f"e{i}")
        self.assertEqual(len(m.errors), 50)


class HotplugTests(unittest.TestCase):
    def test_poll_reports_a_vanished_interface_instead_of_raising(self):
        class Gone(Transport):
            def write(self, d): pass
            def read(self, t): raise OSError(5, "Input/output error")
        self.assertTrue(Channel(Gone()).poll())

    def test_loop_keeps_running_when_polling_fails(self):
        from pylogi.core.manager import DeviceManager
        m = DeviceManager(interval=0.2)
        scans = []
        m._scan = lambda force: scans.append(1)
        bad = type("C", (), {"connection_event": lambda s: (_ for _ in ()).throw(RuntimeError("x")), "close": lambda s: None})()
        m._chans["p"] = (bad, None, [])
        m.start()
        time.sleep(1.2)
        m.stop()
        self.assertGreaterEqual(len(scans), 2)


class EventDrivenRefreshTests(unittest.TestCase):
    class Dev:
        online, kind, battery, caps = True, "mouse", None, set()
        def __init__(self, name): self.uid = self.name = name; self.calls = 0
        def refresh(self, force=False): self.calls += 1

    class Ch:
        def __init__(self, hits): self.hits, self.closed = hits, False
        def poll(self): return self.hits
        def connection_event(self): return self.hits
        def close(self): self.closed = True

    def _manager(self, a_hits, b_hits):
        from pylogi.core.manager import DeviceManager
        m = DeviceManager(interval=30)
        a, b = self.Dev("a"), self.Dev("b")
        m._chans = {"A": (self.Ch(a_hits), None, [a]), "B": (self.Ch(b_hits), None, [b])}
        m._scan = lambda force: None              # no real enumeration
        return m, a, b

    def test_only_the_reporting_channel_is_refreshed(self):
        m, a, b = self._manager(True, False)
        m.EVENT_MIN_GAP = 0.2
        m.start()
        time.sleep(1.4)
        m.stop()
        self.assertGreaterEqual(a.calls, 1)
        self.assertEqual(b.calls, 0)

    def test_event_storms_are_rate_limited(self):
        m, a, _ = self._manager(True, False)      # channel A reports an event on every poll
        m.EVENT_MIN_GAP = 1.0
        m.start()
        time.sleep(3.3)
        m.stop()
        self.assertTrue(1 <= a.calls <= 4, a.calls)       # ~1 per second, not 1 per poll (every 0.5 s)

    def test_pairing_a_device_reopens_the_receiver_channel(self):
        from pylogi.core.manager import DeviceManager, ReceiverDevice
        from pylogi.hidpp.receiver import Slot

        class Rx:
            family, max_devices = "Unifying", 6
            current = [Slot(1, "4082", "mouse", "A")]
            def serial(self): return "S"
            def slots(self): return list(Rx.current)
        rd = ReceiverDevice(Rx(), "r")
        rd.refresh(force=True)
        self.assertFalse(rd.slots_changed)
        rd.refresh(force=True)                                  # same slots: nothing to do
        self.assertFalse(rd.slots_changed)
        Rx.current = [Slot(1, "4082", "mouse", "A"), Slot(2, "408A", "keyboard", "B")]
        rd.refresh(force=True)
        self.assertTrue(rd.slots_changed)
        m = DeviceManager()
        ch = self.Ch(False)
        m._chans = {"P": (ch, None, [rd])}
        m._refresh_groups(["P"], False)
        self.assertEqual(m._chans, {})                          # dropped: next scan re-enumerates
        self.assertTrue(ch.closed)
