# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pylogi import cli  # noqa: E402
from pylogi.core import actions as A, desktop, removal  # noqa: E402
from pylogi.core.config import Config, export_config, import_config  # noqa: E402
from pylogi.core.demo import DemoManager  # noqa: E402
from pylogi.core.history import BatteryHistory  # noqa: E402
from pylogi.core.settings import DeviceSettings  # noqa: E402
from pylogi.hidpp.protocol import Channel  # noqa: E402
from pylogi.hidpp.receiver import Receiver  # noqa: E402
from pylogi.hidpp.transport import Transport  # noqa: E402

app = QApplication.instance() or QApplication([])


def cfg():
    return Config(Path(tempfile.mkdtemp()) / "c.json")


class Recorder(Transport):
    def __init__(self): self.w = []
    def write(self, d): self.w.append(bytes(d))
    def read(self, t): return None


class RemovalTests(unittest.TestCase):
    def test_unpair_frames_per_receiver_family(self):
        for pid, expect in ((0xC52B, bytes([0x10, 0xFF, 0x80, 0xB2, 0x03, 0x02, 0x00])),
                            (0xC548, bytes([0x11, 0xFF, 0x82, 0xC1, 0x03, 0x02]) + bytes(14))):
            t = Recorder()
            rx = Receiver(Channel(t), pid)

            def fake(self, dev, hdr, params=b"", long=False, timeout=1.5, _t=t):
                _t.w.append(self._frame(dev, hdr, bytes(params), long))
                return b""
            with mock.patch.object(Channel, "request", fake):
                rx.unpair(2)
            self.assertEqual(t.w[-1], expect, hex(pid))

    def _paired_dev(self):
        dev = DemoManager().devices[0]
        calls = []
        dev.slot, dev.receiver = 3, type("R", (), {"unpair": lambda s, slot: calls.append(slot)})()
        return dev, calls

    def test_options_depend_on_how_the_device_is_connected(self):
        dev, _ = self._paired_dev()
        self.assertEqual([o.key for o in removal.options(dev)], [removal.UNPAIR, removal.FORGET, removal.HIDE, removal.DELETE])
        self.assertTrue(removal.options(dev)[0].destructive)
        bt = DemoManager().devices[2]
        bt.uniq = "aa:bb:cc:dd:ee:ff"
        self.assertEqual([o.key for o in removal.options(bt)], [removal.BLUETOOTH, removal.FORGET, removal.HIDE, removal.DELETE])
        cam = DemoManager().devices[4]
        self.assertEqual([o.key for o in removal.options(cam)], [removal.FORGET, removal.HIDE, removal.DELETE])

    def test_unpair_backs_up_first_and_calls_the_receiver(self):
        dev, calls = self._paired_dev()
        c = cfg()
        with mock.patch("pylogi.core.removal.backup.save") as save:
            removal.apply(dev, removal.UNPAIR, c)
        save.assert_called_once()
        self.assertEqual(save.call_args.kwargs["tag"], "pre-unpair")
        self.assertEqual(calls, [3])

    def test_bluetooth_removal_uses_bluez(self):
        bt = DemoManager().devices[2]
        bt.uniq = "aa:bb:cc:dd:ee:ff"
        with mock.patch("pylogi.core.removal.shutil.which", return_value="/usr/bin/bluetoothctl"), \
                mock.patch("pylogi.core.removal.subprocess.run") as run, mock.patch("pylogi.core.removal.backup.save"):
            run.return_value = mock.Mock(returncode=0, stdout="Device has been removed", stderr="")
            removal.apply(bt, removal.BLUETOOTH, cfg())
        self.assertEqual(run.call_args.args[0], ["/usr/bin/bluetoothctl", "remove", "AA:BB:CC:DD:EE:FF"])
        with mock.patch("pylogi.core.removal.shutil.which", return_value=None), self.assertRaises(RuntimeError):
            removal.bluetooth_remove("aa:bb:cc:dd:ee:ff")

    def test_forget_and_hide(self):
        dev = DemoManager().devices[0]
        c = cfg()
        st = DeviceSettings(c)
        st.set_binding(dev, "", 0x56, {"type": "key", "mods": [], "key": "F5"})
        st.save(dev, "dpi", 1600)
        self.assertTrue(c.get("dev:" + dev.device_key()))
        removal.apply(dev, removal.FORGET, c)
        self.assertIsNone(c.get("dev:" + dev.device_key()))
        self.assertFalse(removal.is_hidden(c, dev))
        removal.apply(dev, removal.HIDE, c)
        removal.apply(dev, removal.HIDE, c)                      # idempotent
        self.assertTrue(removal.is_hidden(c, dev))
        self.assertEqual(len(removal.hidden_keys(c)), 1)

    def test_hidden_devices_are_not_listed_and_can_be_restored(self):
        from pylogi.ui.home import HomePage
        c = cfg()
        m = DemoManager()
        page = HomePage(None, c)
        page.populate(m.devices, [])
        self.assertIn("5", page.subtitle.text())
        removal.apply(m.devices[0], removal.HIDE, c)
        page.populate(m.devices, [])
        self.assertIn("4", page.subtitle.text())
        removal.unhide_all(c)
        page.populate(m.devices, [])
        self.assertIn("5", page.subtitle.text())

    def test_remove_dialog_requires_confirmation_for_destructive_choices(self):
        from pylogi.ui.dialogs import RemoveDialog
        dev, calls = self._paired_dev()
        from pylogi.ui.main_window import MainWindow
        main = MainWindow(DemoManager(), cfg(), None)
        with mock.patch("pylogi.core.removal.backup.save"):
            dlg = RemoveDialog(main, dev)
            dlg.group.button(0).setChecked(True)                  # unpair
            dlg._update()
            self.assertFalse(dlg.ok.isEnabled())
            dlg.confirm.setChecked(True)
            self.assertTrue(dlg.ok.isEnabled())
            dlg._go()
        self.assertEqual(calls, [3])


class DeleteTests(unittest.TestCase):
    def _paired(self):
        dev = DemoManager().devices[0]
        dev.slot, dev.model_ids, dev.connection = 2, ["b014"], "Bolt"
        return dev

    def test_delete_wipes_everything_and_leaves_no_trace_in_hidden(self):
        dev, c = self._paired(), cfg()
        st = DeviceSettings(c)
        key = dev.device_key()
        st.set_binding(dev, "", 0x56, {"type": "key", "mods": [], "key": "F5"})
        c.set(f"keys:{dev.uid}", {"4": [1, 2, 3]})
        pic = Path(tempfile.mkdtemp()) / "mine.png"
        pic.write_bytes(b"x")
        c.set("pictures", {key: str(pic)})
        removal.apply(dev, removal.HIDE, c)                       # it was hidden before
        self.assertEqual(removal.hidden_keys(c), [key])
        manager = mock.Mock()
        manager.known = Config(Path(tempfile.mkdtemp()) / "k.json")
        manager.known.set("Bolt:B014", ["MX", "mouse"])
        manager.history = BatteryHistory(Path(tempfile.mkdtemp()) / "b.json")
        manager.history.record(key, 50, False, 1000.0)
        removal.apply(dev, removal.DELETE, c, manager, st)
        self.assertIsNone(c.get("dev:" + key))
        self.assertIsNone(c.get(f"keys:{dev.uid}"))
        self.assertFalse(pic.exists())
        self.assertEqual(c.get("pictures"), {})
        self.assertIsNone(manager.known.get("Bolt:B014"))
        self.assertEqual(manager.history.series(key, 1e6, 1001.0), [])
        self.assertEqual(removal.hidden_keys(c), [])              # not listed among the hidden devices
        self.assertEqual(c.get("hidden"), [])
        self.assertTrue(removal.is_deleted(c, dev) and removal.is_hidden(c, dev))
        self.assertEqual(c.get("deleted"), [key])
        removal.apply(dev, removal.DELETE, c, manager, st)        # idempotent
        self.assertEqual(c.get("deleted"), [key])

    def test_a_deleted_device_is_never_published_or_recorded(self):
        from pylogi.core.manager import DeviceManager
        c = cfg()
        m = DeviceManager(c, interval=30)
        m.history = BatteryHistory(Path(tempfile.mkdtemp()) / "b.json")
        demo = DemoManager().devices
        keys = [d.device_key() for d in demo[:2]]
        c.set("deleted", [keys[0]])
        m._chans = {"p": (type("C", (), {"close": lambda s: None})(), None, list(demo[:2]))}
        with mock.patch("pylogi.core.webcam.find_logitech_webcams", return_value=[]):
            m._publish()
        self.assertEqual([d.device_key() for d in m.devices], [keys[1]])
        self.assertNotIn(keys[0], m.history.data)

    def test_deleting_an_online_device_gives_its_buttons_back(self):
        from test_actions import make
        t, dev, st, eng, inj = make()
        t.state[0x53] = [False, 0x56]
        st.set_binding(dev, "", 0x56, {"type": "key", "mods": [], "key": "F5"})
        st.set_binding(dev, "", 0x53, {"type": "mouse", "button": "back"})
        eng.configure(dev)
        self.assertEqual((t.state[0x56][0], t.state[0x53]), (True, [True, 0]))
        removal.apply(dev, removal.DELETE, st.cfg, None, st)
        self.assertEqual(t.state[0x56], [False, 0])                # diversion undone
        self.assertEqual(t.state[0x53], [False, 0])
        self.assertIsNone(st.cfg.get("dev:" + dev.device_key()))

    def test_restore_deleted_lists_them_again(self):
        dev, c = self._paired(), cfg()
        removal.apply(dev, removal.DELETE, c)
        self.assertTrue(removal.is_deleted(c, dev))
        self.assertEqual(removal.restore_deleted(c), 1)
        self.assertFalse(removal.is_deleted(c, dev))

    def test_offline_slots_have_a_stable_key(self):
        a, b = DemoManager().devices[0], DemoManager().devices[0]
        for d, uid in ((a, "/dev/hidraw3:2"), (b, "/dev/hidraw9:2")):      # the node number changes between runs
            d.slot, d.model_ids, d.connection, d.uid = 2, ["b014"], "Bolt", uid
            d.info.serial = d.info.unit = ""
        self.assertEqual(a.device_key(), b.device_key())
        self.assertEqual(a.device_key(), "wpid:bolt:b014")

    def test_dialog_asks_for_the_right_confirmation_text(self):
        from pylogi.ui.dialogs import RemoveDialog
        from pylogi.ui.main_window import MainWindow
        dev = self._paired()
        dev.receiver = type("R", (), {"unpair": lambda s, slot: None})()
        w = MainWindow(DemoManager(), cfg(), None)
        dlg = RemoveDialog(w, dev)
        for i, o in enumerate(dlg.options):
            dlg.group.button(i).setChecked(True)
            dlg._update()
            self.assertEqual(not dlg.confirm.isHidden(), o.destructive, o.key)
            self.assertEqual(dlg.ok.isEnabled(), not o.destructive, o.key)
            if o.key == removal.DELETE:
                self.assertIn("deleted for good", dlg.confirm.text())
                self.assertNotIn("paired again", dlg.confirm.text())
            if o.key == removal.UNPAIR:
                self.assertIn("paired again", dlg.confirm.text())


class CliTests(unittest.TestCase):
    def test_describe_find_and_set(self):
        m = DemoManager()
        st = DeviceSettings(cfg())
        mouse = cli.find(m.devices, "master")
        self.assertEqual(mouse.name, "MX Master 3S")
        out = cli.set_values(mouse, ["dpi=1600", "wheel-invert=on", "wheel-hires=true", "smartshift=free"], st)
        self.assertEqual(out, ["dpi=1600", "wheel-invert=on", "wheel-hires=true", "smartshift=free"])
        self.assertEqual(mouse.dpi().current, 1600)
        self.assertEqual(mouse.wheel(), (True, True))
        self.assertFalse(mouse.smartshift()[0])
        self.assertEqual(st.dpi(mouse), 1600)                       # remembered for the next reconnect
        with self.assertRaises(SystemExit):
            cli.set_values(mouse, ["bogus=1"], st)
        with self.assertRaises(SystemExit):
            cli.set_values(mouse, ["dpi"], st)
        with self.assertRaises(SystemExit):
            cli.find(m.devices, "nothing-like-this")
        rows = [cli.describe(d) for d in m.devices]
        self.assertTrue(json.dumps(rows))

    def test_camera_control_by_name(self):
        cam = DemoManager().devices[4]
        cli.set_values(cam, ["camera.brightness=200"], DeviceSettings(cfg()))
        self.assertEqual(next(c.value for c in cam.cam_controls() if c.name == "Brightness"), 200)


class HistoryTests(unittest.TestCase):
    def test_sampling_pruning_and_series(self):
        h = BatteryHistory(Path(tempfile.mkdtemp()) / "b.json")
        t = 1_000_000.0
        self.assertTrue(h.record("k", 80, False, t))
        self.assertFalse(h.record("k", 79, False, t + 60))          # too soon, small change
        self.assertTrue(h.record("k", 70, False, t + 61))           # 9 points lower: kept
        self.assertTrue(h.record("k", 69, False, t + 700))          # >10 minutes
        self.assertEqual([p for _, p, _ in h.series("k", 7, t + 700)], [80, 70, 69])
        self.assertEqual(len(h.series("k", 0.001, t + 700)), 1)     # only the newest within the window
        h.record("k", 60, False, t + 40 * 86400)                    # old samples pruned
        self.assertEqual(len(h.data["k"]), 1)
        again = BatteryHistory(h.path)                              # persisted
        self.assertEqual(again.data, h.data)
        again.forget("k")
        self.assertEqual(again.series("k", 30, t + 20 * 86400), [])

    def test_chart_builds(self):
        from pylogi.ui.details import BatteryChart
        now = time.time()
        w = BatteryChart([(now - 3600 * i, 100 - 5 * i, i == 3) for i in range(6, -1, -1)])
        w.resize(480, 200)
        w.grab()


class ConfigIoTests(unittest.TestCase):
    def test_export_import_roundtrip(self):
        a, b = cfg(), cfg()
        a.set("theme", "midnight")
        a.set("dev:serial:x", {"dpi": 1200})
        a.set("photos_asked", True)
        path = Path(tempfile.mkdtemp()) / "x.json"
        export_config(a, path)
        self.assertNotIn("photos_asked", json.loads(path.read_text())["data"])
        self.assertEqual(import_config(b, path), 2)
        self.assertEqual((b.get("theme"), b.get("dev:serial:x")), ("midnight", {"dpi": 1200}))
        path.write_text('{"hello": 1}')
        with self.assertRaises(ValueError):
            import_config(b, path)


class DesktopTests(unittest.TestCase):
    def test_launcher_autostart_and_extension(self):
        home = tempfile.mkdtemp()
        env = {"XDG_DATA_HOME": f"{home}/data", "XDG_CONFIG_HOME": f"{home}/config"}
        with mock.patch.dict(os.environ, env):
            files = desktop.install_launcher()
            self.assertTrue(all(f.exists() for f in files))
            text = files[0].read_text()
            self.assertIn("Name=pyLogi", text)
            self.assertIn("Icon=pylogi", text)
            self.assertFalse(desktop.autostart_enabled())
            desktop.set_autostart(True)
            self.assertTrue(desktop.autostart_enabled())
            self.assertIn("--minimized", desktop.autostart_path().read_text())
            desktop.set_autostart(False)
            self.assertFalse(desktop.autostart_enabled())
            ext = desktop.install_gnome_extension()
            meta = json.loads((ext / "metadata.json").read_text())
            self.assertEqual(meta["uuid"], desktop.EXT_UUID)
            self.assertIn("51", meta["shell-version"])
            self.assertIn("org.pylogi.ActiveWindow", (ext / "extension.js").read_text())

    def test_gnome_detector_parses_gdbus_output(self):
        d = A.GnomeDetector()
        out = mock.Mock(returncode=0, stdout="('Firefox',)\n", stderr="")
        with mock.patch("pylogi.core.actions.shutil.which", return_value="/usr/bin/gdbus"), \
                mock.patch("pylogi.core.actions.subprocess.run", return_value=out):
            self.assertEqual(d.current(), "firefox")
            self.assertTrue(d.available)
        bad = mock.Mock(returncode=1, stdout="", stderr="no such object")
        with mock.patch("pylogi.core.actions.shutil.which", return_value="/usr/bin/gdbus"), \
                mock.patch("pylogi.core.actions.subprocess.run", return_value=bad):
            self.assertIsNone(d.current())

    def test_auto_detector_prefers_a_working_backend(self):
        class B:
            def __init__(s, ok, app): s.available, s.reason, s._app = ok, "r", app
            def current(s): return s._app
        auto = A.AutoDetector()
        auto.backends = [B(False, "x"), B(True, "gedit")]
        self.assertEqual((auto.available, auto.current()), (True, "gedit"))
        auto.backends = [B(False, None)]
        self.assertFalse(auto.available)


class UiExtrasTests(unittest.TestCase):
    def test_photo_consent_is_asked_once_and_nothing_downloads_before(self):
        from PySide6.QtWidgets import QMessageBox
        from pylogi.core.images import ImageProvider
        from pylogi.ui.main_window import MainWindow
        c = cfg()
        img = ImageProvider(enabled=False, directory=Path(tempfile.mkdtemp()))
        w = MainWindow(DemoManager(), c, img)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes) as q:
            w._ask_photos()
        self.assertTrue(q.called)
        self.assertTrue(c.get("device_images") and img.enabled)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            w._ask_photos()
        self.assertFalse(img.enabled)
        # an existing answer suppresses the question
        with mock.patch("pylogi.ui.main_window.QTimer.singleShot") as shot:
            w.first_run()
        shot.assert_not_called()

    def test_custom_picture_overrides_the_catalog_photo(self):
        from PySide6.QtGui import QColor, QImage
        from pylogi.core.images import ImageProvider
        d = Path(tempfile.mkdtemp())
        pic = d / "mine.png"
        img = QImage(40, 20, QImage.Format_RGB32)
        img.fill(QColor("red"))
        img.save(str(pic))
        dev = DemoManager().devices[0]
        provider = ImageProvider(enabled=False, directory=d)     # offline and photos disabled
        self.assertIsNone(provider.photo(dev))
        provider.custom = {dev.device_key(): str(pic)}
        self.assertEqual(provider.photo(dev).width(), 40)

    def test_cards_are_keyboard_accessible(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication
        from pylogi.ui.home import DeviceCard
        dev = DemoManager().devices[0]
        card = DeviceCard(dev, None)
        got = []
        card.clicked.connect(got.append)
        self.assertEqual(card.focusPolicy(), Qt.StrongFocus)
        self.assertIn(dev.name, card.accessibleName())
        for key in (Qt.Key_Return, Qt.Key_Space):
            card.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))
        self.assertEqual(got, [dev, dev])
        card.deleteLater()                     # an undeleted top-level widget crashes the interpreter at exit
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_settings_dialog_builds_with_new_controls(self):
        from pylogi.ui.main_window import MainWindow
        from pylogi.ui.dialogs import AddDialog, SettingsDialog
        w = MainWindow(DemoManager(), cfg(), None)
        with mock.patch("pylogi.core.desktop.autostart_enabled", return_value=False):
            SettingsDialog(w)
        AddDialog(w)


class ConflictTests(unittest.TestCase):
    def _proc(self, names):
        root = Path(tempfile.mkdtemp())
        for i, n in enumerate(names, 100):
            (root / str(i)).mkdir()
            (root / str(i) / "comm").write_text(n + "\n")
        (root / "self").mkdir()
        return root

    @unittest.skipUnless(sys.platform.startswith("linux"), "process detection reads /proc (Linux only)")
    def test_known_managers_are_found(self):
        from pylogi.core.conflicts import running_managers
        root = self._proc(["bash", "openlogi-agent", "python3", "solaar", "openlogi-overla", "solaar"])
        self.assertEqual(running_managers(root), ["OpenLogi", "Solaar"])
        self.assertEqual(running_managers(self._proc(["bash", "firefox"])), [])

    def test_banner_shows_and_can_be_dismissed(self):
        from pylogi.ui.home import HomePage
        c = cfg()
        page = HomePage(None, c)
        with mock.patch("pylogi.ui.home.running_managers", return_value=["OpenLogi"]):
            page.populate(DemoManager().devices, [])
            self.assertFalse(page.banner.isHidden())
            self.assertIn("OpenLogi", page.banner_text.text())
            page._dismiss_banner()
            self.assertTrue(page.banner.isHidden())
            page.populate(DemoManager().devices, [])
            self.assertTrue(page.banner.isHidden())          # stays dismissed
        with mock.patch("pylogi.ui.home.running_managers", return_value=["OpenLogi", "Solaar"]):
            page.populate(DemoManager().devices, [])
            self.assertFalse(page.banner.isHidden())        # a new manager shows it again


if __name__ == "__main__":
    unittest.main()


class PerDeviceFileTests(unittest.TestCase):
    def test_each_device_has_its_own_file_named_after_it(self):
        with tempfile.TemporaryDirectory() as td:
            c = Config(Path(td) / "config.json")
            c.set("theme", "midnight")
            c.set("dev:serial:abc", {"dpi": 1600})
            c.name_device("serial:abc", "MX Master 3S")
            c.set("dev:serial:def", {"dpi": 800})
            c.name_device("serial:def", "G915 TKL")
            files = sorted(p.name for p in (Path(td) / "devices").glob("*.json"))
            self.assertEqual(files, ["G915_TKL__serial_def.json", "MX_Master_3S__serial_abc.json"])
            self.assertNotIn("dev:serial:abc", Config(Path(td) / "config.json").data)       # not in config.json
            b = Config(Path(td) / "config.json")                                           # survives a restart
            self.assertEqual((b.get("theme"), b.get("dev:serial:abc"), b.get("dev:serial:def")),
                             ("midnight", {"dpi": 1600}, {"dpi": 800}))

    def test_old_config_json_entries_are_migrated_and_removed_with_the_device(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text('{"theme": "x", "dev:serial:old": {"dpi": 1000}, "cam:cam:1": {"A": {}}, "campreset:cam:1": "A"}')
            c = Config(path)
            self.assertEqual(c.get("dev:serial:old"), {"dpi": 1000})
            self.assertEqual((c.get("cam:cam:1"), c.get("campreset:cam:1")), ({"A": {}}, "A"))
            self.assertEqual(json.loads(path.read_text()), {"theme": "x"})
            self.assertEqual(len(list((Path(td) / "devices").glob("*.json"))), 2)
            c.pop("dev:serial:old")
            c.pop("cam:cam:1")
            c.pop("campreset:cam:1")
            self.assertEqual(list((Path(td) / "devices").glob("*.json")), [])

    def test_export_and_import_keep_the_device_settings(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "a").mkdir()
            a = Config(Path(td) / "a" / "config.json")
            a.set("dev:serial:x", {"dpi": 1200})
            out = Path(td) / "export.json"
            export_config(a, out)
            (Path(td) / "b").mkdir()
            b = Config(Path(td) / "b" / "config.json")
            import_config(b, out)
            self.assertEqual(b.get("dev:serial:x"), {"dpi": 1200})


class RestoreOrderTests(unittest.TestCase):
    class Dev:
        def __init__(self):
            self.calls = []
            self.ob = self

        def set_report_rate(self, v):
            self.calls.append(("rate", v))

        def _ob(self):
            return self

        def set_host_mode(self, host):
            self.calls.append(("host", host))

        def read_sector(self, n):
            return b""

        def write_sector(self, n, raw):
            self.calls.append(("sector", n))

        def set_current_sector(self, n):
            self.calls.append(("current", n))

    def test_report_rate_is_skipped_when_the_snapshot_used_onboard_profiles(self):
        from pylogi.core import backup
        d = self.Dev()
        out = backup.restore(d, {"report_rate": {"current": 4},
                                 "onboard": {"host_mode": False, "sectors": {}}})
        self.assertNotIn(("rate", 4), d.calls)
        self.assertTrue(any(x.startswith("skipped: report rate") for x in out))

    def test_report_rate_is_written_after_switching_to_host_mode(self):
        from pylogi.core import backup
        d = self.Dev()
        backup.restore(d, {"report_rate": {"current": 4}, "onboard": {"host_mode": True, "sectors": {}}})
        self.assertLess(d.calls.index(("host", True)), d.calls.index(("rate", 4)))


class AutoBackupTests(unittest.TestCase):
    class Dev:
        name, kind, connection, online, caps = "Kbd", "keyboard", "USB", True, set()
        battery = None

        class info:
            serial, unit, extra = "S1", "", {}

        def device_key(self):
            return "serial:s1"

        def has(self, cap):
            return False

    def setUp(self):
        from pylogi.core import backup
        self.backup = backup
        self.td = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(backup, "backup_dir", lambda: Path(self.td.name))
        self.patch.start()
        backup._auto_last.clear()

    def tearDown(self):
        self.patch.stop()
        self.td.cleanup()

    def test_one_snapshot_per_burst_of_writes(self):
        d = self.Dev()
        a = self.backup.auto(d, "profile")
        b = self.backup.auto(d, "profile")
        self.assertEqual(a, b)
        self.assertEqual(len(list(Path(self.td.name).glob("auto-*.json"))), 1)
        self.assertEqual(json.loads(a.read_text())["key"], "serial:s1")
        c = self.backup.auto(d, "profile", min_interval=0)               # forced: a new snapshot is written
        self.assertTrue(c.exists())

    def test_only_the_newest_automatic_backups_are_kept(self):
        d = self.Dev()
        for i in range(self.backup._AUTO_KEEP + 5):
            p = Path(self.td.name) / f"auto-profile-serial_s1-20260101-0000{i:02d}.json"
            p.write_text("{}")
        self.backup.auto(d, "profile", min_interval=0)
        self.assertLessEqual(len(list(Path(self.td.name).glob("auto-*.json"))), self.backup._AUTO_KEEP)

    def test_device_memory_writes_take_a_snapshot_first(self):
        from pylogi.core.device import HidppDevice
        calls = []

        class Fake(HidppDevice):
            def __init__(self):
                self._onboard = mock.Mock()
                self._onboard.read_profiles.return_value = [mock.Mock(sector=3)]

            def _ob(self):
                return self._onboard

            def _backup_first(self, reason):
                calls.append(("backup", reason))
        f = Fake()
        f._onboard.write_profile.side_effect = lambda p: calls.append(("write", None))
        f._onboard.set_profile_color.side_effect = lambda p, c: calls.append(("colour", c))
        f.write_profile(mock.Mock())
        f.set_profile_color(3, (1, 2, 3))
        self.assertEqual(calls, [("backup", "profile"), ("write", None), ("backup", "profile-colour"), ("colour", (1, 2, 3))])


class ResumeTests(unittest.TestCase):
    def _manager(self, applied, fail=False):
        from pylogi.core.manager import DeviceManager

        class Settings:
            def apply(self, dev):
                if fail:
                    raise RuntimeError("boom")
                applied.append(dev.name)
        return DeviceManager(settings=Settings(), interval=1)

    class D:
        def __init__(self, name, online=True, kind="keyboard"):
            self.name, self.online, self.kind = name, online, kind

    def test_resume_writes_the_saved_settings_to_every_online_device_again(self):
        applied = []
        m = self._manager(applied)
        m.devices = [self.D("kbd"), self.D("mouse"), self.D("off", online=False), self.D("rx", kind="receiver")]
        self.assertEqual(m.reapply_all(), 2)
        self.assertEqual(applied, ["kbd", "mouse"])

    def test_a_failing_restore_is_reported_not_swallowed(self):
        m = self._manager([], fail=True)
        m._restore(self.D("kbd"))
        self.assertTrue(any("restore kbd" in e and "boom" in e for e in m.errors))

    def test_resume_rescans_then_reapplies(self):
        applied = []
        m = self._manager(applied)
        m.devices = [self.D("kbd")]
        with mock.patch.object(m, "scan_once") as scan:
            m.rescan_after_resume()
        scan.assert_called_once_with(True)
        self.assertEqual(applied, ["kbd"])


class TrayMenuTests(unittest.TestCase):
    def setUp(self):
        from pylogi.ui import tray_menu
        tray_menu.forget_cache()

    def test_profiles_and_hosts_can_be_switched_from_the_tray(self):
        from PySide6.QtWidgets import QMenu
        from pylogi.ui import tray_menu
        devs = [d for d in DemoManager().devices if d.online and d.has("profiles")]
        self.assertTrue(devs)
        dev = devs[0]
        menu = QMenu()
        self.assertGreaterEqual(tray_menu.fill(menu, [dev]), 1)
        sub = next(a.menu() for a in menu.actions() if a.menu() and a.text().replace("\n", " ").endswith(dev.name))
        acts = [a for a in sub.actions() if a.isCheckable()]
        profile_acts = [a for a in acts if a.text()[:2] in {f"{i}." for i in range(1, 10)}]
        self.assertGreaterEqual(len(profile_acts), 2)
        self.assertEqual(sum(a.isChecked() for a in profile_acts[:len(dev.profiles())]) >= 1, True)
        target = next(a for a in profile_acts if not a.isChecked())
        target.trigger()
        self.assertEqual(dev.active_profile(), [p for p in dev.profiles() if f"{p.index + 1}. {p.name}" == target.text()][0].sector)

    def test_a_failing_device_is_skipped_and_reported(self):
        from PySide6.QtWidgets import QMenu
        from pylogi.ui import tray_menu

        class Bad:
            name, kind, online = "Bad", "keyboard", True
            def has(self, cap): return cap == "profiles"
            def device_key(self): return "bad"
            def profiles(self): raise RuntimeError("no answer")
        errors = []
        menu = QMenu()
        self.assertEqual(tray_menu.fill(menu, [Bad()], errors.append), 0)
        self.assertEqual(menu.actions(), [])
        self.assertIn("no answer", errors[0])

    def test_offline_devices_and_receivers_are_not_listed(self):
        from PySide6.QtWidgets import QMenu
        from pylogi.ui import tray_menu

        class Off:
            name, kind, online = "Off", "keyboard", False
            def has(self, cap): return True
        menu = QMenu()
        self.assertEqual(tray_menu.fill(menu, [Off()]), 0)


class ReportTests(unittest.TestCase):
    def test_report_lists_capabilities_and_leaves_serials_out_by_default(self):
        from pylogi.core import report
        devs = DemoManager().devices
        r = report.build(devs)
        self.assertTrue(r["devices"])
        self.assertTrue(all("serial" not in d and "unit" not in d for d in r["devices"]))
        self.assertTrue(all("capabilities" in d and "hidpp_features" in d for d in r["devices"]))
        self.assertNotIn(os.path.expanduser("~"), report.to_text(r))              # no home folder / user name
        self.assertTrue(all("serial" in d for d in report.build(devs, serials=True)["devices"]))

    def test_cli_options_are_known(self):
        from pylogi import app
        ns = app.build_parser().parse_args(["--report", "out.json", "--with-serials"])
        self.assertEqual((ns.report, ns.with_serials), ("out.json", True))
        self.assertEqual(app.build_parser().parse_args(["--report"]).report, "-")      # no file: print it
