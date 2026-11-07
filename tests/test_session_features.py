# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Tray toggle, single instance, battery / connection alerts, window memory, two-column cards, autonomy."""
import os
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pylogi.core.config import Config  # noqa: E402
from pylogi.core.demo import DemoManager  # noqa: E402
from pylogi.core.history import BatteryHistory  # noqa: E402
from pylogi.core.types import Battery  # noqa: E402

app = QApplication.instance() or QApplication([])


def cfg() -> Config:
    return Config(Path(tempfile.mkdtemp()) / "c.json")


def fake_window(c: Config):
    """Just what MainWindow's alert methods use."""
    from pylogi.ui.main_window import MainWindow
    shown = []
    tray = SimpleNamespace(showMessage=lambda title, text, *a: shown.append((title, text)))
    w = SimpleNamespace(cfg=c, tray=tray, _warned=set(), _critical=set(), _online=None)
    w.alerts = lambda devs: MainWindow._battery_alerts(w, devs)
    w.connections = lambda devs: MainWindow._connection_alerts(w, devs)
    return w, shown


def dev(uid="a", percent=50, charging=False, online=True, kind="mouse"):
    return SimpleNamespace(uid=uid, name=f"dev-{uid}", battery=Battery(percent, charging), online=online, kind=kind)


class BatteryAlertTests(unittest.TestCase):
    def test_low_then_critical_then_rearm(self):
        w, shown = fake_window(cfg())
        w.alerts([dev(percent=40)])
        self.assertEqual(shown, [])
        w.alerts([dev(percent=15)])
        self.assertEqual([t for t, _ in shown], ["Low battery"])
        w.alerts([dev(percent=14)])                                    # no repeat
        self.assertEqual(len(shown), 1)
        w.alerts([dev(percent=5)])
        self.assertEqual(shown[-1][0], "Battery critically low")
        w.alerts([dev(percent=4)])
        self.assertEqual(len(shown), 2)
        w.alerts([dev(percent=60)])                                    # recovered: both re-armed
        w.alerts([dev(percent=15)])
        self.assertEqual(len(shown), 3)

    def test_disabled_and_charging_are_silent(self):
        c = cfg()
        c.set("battery_alert", False)
        w, shown = fake_window(c)
        w.alerts([dev(percent=3)])
        self.assertEqual(shown, [])
        c.set("battery_alert", True)
        w.alerts([dev(percent=3, charging=True)])
        self.assertEqual(shown, [])

    def test_custom_levels(self):
        c = cfg()
        c.set("battery_alert_pct", 30)
        c.set("battery_alert_critical_pct", 10)
        w, shown = fake_window(c)
        w.alerts([dev(percent=30)])
        self.assertEqual(len(shown), 1)
        w.alerts([dev(percent=9)])
        self.assertEqual(shown[-1][0], "Battery critically low")


class ConnectionAlertTests(unittest.TestCase):
    def test_off_by_default_and_first_scan_is_silent(self):
        w, shown = fake_window(cfg())
        w.connections([dev(online=True)])
        w.connections([dev(online=False)])
        self.assertEqual(shown, [])

    def test_reports_changes_when_enabled(self):
        c = cfg()
        c.set("notify_connection", True)
        w, shown = fake_window(c)
        w.connections([dev(online=True), dev("r", kind="receiver", online=True)])
        self.assertEqual(shown, [])
        w.connections([dev(online=False), dev("r", kind="receiver", online=False)])
        self.assertEqual(shown, [("Device disconnected", "dev-a")])     # receivers are ignored
        w.connections([dev(online=True)])
        self.assertEqual(shown[-1], ("Device connected", "dev-a"))


class AutonomyTests(unittest.TestCase):
    def history(self):
        return BatteryHistory(Path(tempfile.mkdtemp()) / "b.json")

    def test_steady_discharge(self):
        h, now = self.history(), 10_000_000.0
        for i in range(0, 5):                                          # 10 points per day for four days
            h.record("k", 100 - 10 * i, False, now - (4 - i) * 86400)
        days = h.estimate_days("k", 60, now)
        self.assertAlmostEqual(days, 6.0, delta=0.1)

    def test_not_enough_data(self):
        h, now = self.history(), 10_000_000.0
        h.record("k", 80, False, now - 3600)
        h.record("k", 79, False, now)
        self.assertIsNone(h.estimate_days("k", 79, now))
        self.assertIsNone(h.estimate_days("k", None, now))

    def test_charging_periods_are_ignored(self):
        h, now = self.history(), 10_000_000.0
        h.record("k", 90, False, now - 3 * 86400)
        h.record("k", 80, False, now - 2 * 86400)
        h.record("k", 20, True, now - 2 * 86400 + 700)                 # plugged in: not a discharge
        h.record("k", 100, True, now - 86400)
        h.record("k", 90, False, now - 86400 + 700)
        h.record("k", 80, False, now)
        days = h.estimate_days("k", 80, now)
        self.assertIsNotNone(days)
        self.assertGreater(days, 5)


class InstanceTests(unittest.TestCase):
    def setUp(self):
        from pylogi.ui import single_instance as si
        self.si = si
        self._name = si._name
        unique = f"pylogi-test-{uuid.uuid4().hex}"
        si._name = lambda demo=False: unique

    def tearDown(self):
        self.si._name = self._name

    def pump(self, got, n):
        end = time.time() + 3
        while len(got) < n and time.time() < end:
            app.processEvents()
            time.sleep(0.01)

    def test_nobody_listening(self):
        self.assertFalse(self.si.notify_running())

    def test_second_launch_reaches_the_first(self):
        got = []
        server = self.si.InstanceServer(got.append)
        self.assertTrue(server.ok)
        import threading
        res = []
        t = threading.Thread(target=lambda: res.append(self.si.notify_running()))
        t.start()
        self.pump(got, 1)
        t.join(2)
        self.assertEqual(res, [True])
        self.assertEqual(got, ["show"])
        t = threading.Thread(target=lambda: res.append(self.si.notify_running(message=b"toggle")))
        t.start()
        self.pump(got, 2)
        t.join(2)
        self.assertEqual(got, ["show", "toggle"])

    def test_cli_has_toggle(self):
        from pylogi.app import build_parser
        self.assertTrue(build_parser().parse_args(["--toggle"]).toggle)


class WindowTests(unittest.TestCase):
    def window(self, c=None):
        from pylogi.ui.main_window import MainWindow
        return MainWindow(DemoManager(), c or cfg(), None)

    def test_opens_at_1200_by_800(self):
        w = self.window()
        self.assertEqual((w.width(), w.height()), (1200, 800))

    def test_remember_geometry_is_opt_in(self):
        c = cfg()
        w = self.window(c)
        w.show()
        w.resize(720, 480)
        app.processEvents()
        w._save_geometry()
        self.assertEqual(c.get("window_geometry", ""), "")             # off: nothing stored
        c.set("remember_window", True)
        w._save_geometry()
        self.assertTrue(c.get("window_geometry"))
        again = self.window(c)
        self.assertEqual((again.width(), again.height()), (720, 480))

    def test_toggle_and_remote_messages(self):
        w = self.window()
        w.show()
        w.remote("toggle")
        self.assertFalse(w.isVisible())
        w.remote("toggle")
        self.assertTrue(w.isVisible())
        w.hide()
        w.remote("show")
        self.assertTrue(w.isVisible())

    def test_tray_menu_entry_follows_visibility(self):
        w = self.window()
        w.show()
        w._fill_tray()
        self.assertEqual(w._tray_menu.actions()[-4].text(), "Hide")
        w.hide()
        w._fill_tray()
        self.assertEqual(w._tray_menu.actions()[-4].text(), "Show")

    def test_monochrome_tray_icon(self):
        c = cfg()
        w = self.window(c)
        normal = w._tray_icon().cacheKey()
        c.set("tray_mono", True)
        self.assertFalse(w._tray_icon().isNull())
        self.assertNotEqual(w._tray_icon().cacheKey(), normal)

    def test_settings_dialog_stores_the_new_options(self):
        from pylogi.ui.dialogs import SettingsDialog
        c = cfg()
        w = self.window(c)
        d = SettingsDialog(w)
        d.hidden.setChecked(True)
        d.remember.setChecked(True)
        d.conn.setChecked(True)
        d.mono.setChecked(True)
        d.splash.setChecked(False)
        d.batt_pct.setValue(25)
        d.batt_crit.setValue(8)
        self.assertEqual([c.get(k) for k in ("start_hidden", "remember_window", "notify_connection", "tray_mono",
                                             "battery_alert_pct", "battery_alert_critical_pct", "show_splash")],
                         [True, True, True, True, 25, 8, False])
        self.assertTrue(self.window(cfg()).cfg.get("show_splash", True))      # on by default
        d.batt.setChecked(False)
        self.assertFalse(d.batt_pct.isEnabled() or d.batt_crit.isEnabled())


class TrayTitleTests(unittest.TestCase):
    def test_battery_level_leads_the_entry(self):
        from pylogi.ui import tray_menu
        self.assertEqual(tray_menu._title(dev(percent=85)), "🔋 85%  dev-a")
        self.assertTrue(tray_menu._title(dev(percent=10)).startswith("🪫 10%"))
        self.assertTrue(tray_menu._title(dev(percent=50, charging=True)).startswith("🔋 50%⚡"))
        self.assertNotRegex(tray_menu._title(dev(percent=30)), "[🟢🟡🔴🔵]")      # no coloured dot any more
        d = dev()
        d.battery = None
        self.assertEqual(tray_menu._title(d), "dev-a")

    def test_long_names_use_two_lines(self):
        from pylogi.ui import tray_menu
        name = "G915 TKL LIGHTSPEED Wireless RGB Mechanical Gaming Keyboard"
        d = dev(percent=40)
        d.name = name
        title = tray_menu._title(d)
        self.assertEqual(title.count("\n"), 1)
        self.assertEqual(title.replace("\n", " ").split("  ", 1)[1], name)        # nothing lost
        self.assertTrue(all(len(line) <= tray_menu.MAX_LINE + 10 for line in title.split("\n")))
        d.name = "MX Anywhere 3S"
        self.assertNotIn("\n", tray_menu._title(d))


class DetailsLayoutTests(unittest.TestCase):
    def test_two_columns_when_wide_one_when_narrow(self):
        from pylogi.ui.details import DetailsPage
        m = DemoManager()
        d = next(x for x in m.devices if x.online and x.has("easyswitch"))
        page = DetailsPage(d, cfg())
        page.resize(1400, 700)
        page.show()
        app.processEvents()
        self.assertEqual(page._ncols, 2)
        self.assertTrue(all(c.isVisible() for c in page.cards))
        page.resize(800, 700)
        app.processEvents()
        self.assertEqual(page._ncols, 1)
        self.assertTrue(all(c.isVisible() for c in page.cards))

    def test_chart_hover_reads_the_nearest_sample(self):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from pylogi.ui.details import BatteryChart
        now = time.time()
        w = BatteryChart([(now - 86400 * i, 100 - 10 * i, False) for i in range(5, -1, -1)])
        w.resize(500, 200)
        x = w._point(now - 3 * 86400, 0).x()
        w.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(x, 50), QPointF(x, 50),
                                     Qt.NoButton, Qt.NoButton, Qt.NoModifier))
        self.assertEqual(w.samples[w.hover][1], 70)
        w.grab()
        self.assertNotIn(":", w._stamp(now))                          # a date, never a time of day


if __name__ == "__main__":
    unittest.main()


class DedupeTests(unittest.TestCase):
    """The same mouse reached through its receiver and as a node of its own is listed once."""

    @staticmethod
    def d(uid, key, online=True, slot=None, model="4074", kind="mouse"):
        return SimpleNamespace(uid=uid, device_key=lambda: key, online=online, slot=slot, kind=kind,
                               model_ids=[model])

    def test_same_identity_keeps_the_receiver_entry(self):
        from pylogi.core.manager import DeviceManager
        direct = self.d("/dev/hidraw10", "unit:85f9c662")
        via_rx = self.d("/dev/hidraw7:1", "unit:85f9c662", slot=1)
        for order in ([direct, via_rx], [via_rx, direct]):
            self.assertEqual(DeviceManager.dedupe(order), [via_rx])

    def test_offline_slot_of_an_online_model_is_hidden(self):
        from pylogi.core.manager import DeviceManager
        direct = self.d("/dev/hidraw10", "unit:85f9c662")
        ghost = self.d("/dev/hidraw7:1", "wpid:lightspeed:4074", online=False, slot=1)
        other = self.d("/dev/hidraw7:2", "wpid:lightspeed:408e", online=False, slot=2, model="408e")
        out = DeviceManager.dedupe([ghost, direct, other])
        self.assertEqual(out, [direct, other])                       # a different offline model stays

    def test_mouse_without_serial_seen_twice_is_listed_once(self):
        """The G305 case: no serial / unit id, so only the model tells the two paths apart."""
        from pylogi.core.manager import DeviceManager
        direct = self.d("/dev/hidraw10", "uid:/dev/hidraw10", slot=0)
        via_rx = self.d("/dev/hidraw9:1", "wpid:lightspeed:4074", slot=1)
        rx = self.d("/dev/hidraw9:rx", "serial:9dc1987b", kind="receiver", model="c53f")
        self.assertEqual(DeviceManager.dedupe([rx, via_rx, direct]), [rx, via_rx])
        self.assertEqual(DeviceManager.dedupe([direct, rx, via_rx]), [rx, via_rx])
        alone = self.d("/dev/hidraw10", "uid:/dev/hidraw10", slot=0)         # no receiver entry: stays
        self.assertEqual(DeviceManager.dedupe([alone]), [alone])

    def test_different_devices_and_receivers_are_untouched(self):
        from pylogi.core.manager import DeviceManager
        a, b = self.d("a", "serial:aaa"), self.d("b", "serial:bbb", model="b014")
        rx = self.d("rx", "uid:rx", kind="receiver", model="c53f")
        self.assertEqual(DeviceManager.dedupe([rx, a, b]), [rx, a, b])
        off = self.d("o", "wpid:bolt:b014", online=False, slot=3, model="b014")
        self.assertEqual(DeviceManager.dedupe([off]), [off])           # nothing online: kept
