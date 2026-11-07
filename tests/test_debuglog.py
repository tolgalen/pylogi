# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pylogi.core import debuglog  # noqa: E402
from pylogi.core.types import Battery  # noqa: E402

app = QApplication.instance() or QApplication([])


def dev(uid="a", name="G305", online=True, percent=50, key="serial:ABCDEF123456", slot=1, model="4074"):
    return SimpleNamespace(uid=uid, name=name, kind="mouse", connection="Lightspeed", online=online,
                           battery=Battery(percent), device_key=lambda: key, model_ids=[model],
                           caps={"battery", "dpi"}, slot=slot)


class DebugLogTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.log = debuglog.DebugLog()
        self.path = self.dir / "debug.log"

    def tearDown(self):
        self.log.disable()

    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def test_off_by_default_and_a_no_op(self):
        self.assertFalse(self.log.enabled)
        self.log.log("x", "nothing")
        self.log.devices([dev()])
        self.assertFalse(self.path.exists())

    def test_header_has_system_and_hid_interfaces(self):
        self.log.enable(path=self.path)
        t = self.text()
        for needle in ("debug log", "[system", "Python ", "PySide6", "HID backend", "HID++ interface"):
            self.assertIn(needle, t)

    def test_device_table_and_changes(self):
        self.log.enable(path=self.path)
        self.log.devices([dev(online=False, percent=80)])
        self.log.devices([dev(online=False, percent=80)])                      # identical: not repeated
        self.assertEqual(self.text().count("1 entry"), 1)
        self.log.devices([dev(online=True, percent=80)])
        self.log.devices([dev(online=True, percent=75)])
        self.log.devices([])
        t = self.text()
        self.assertIn("OFFLINE", t)
        self.assertIn("offline -> online", t)
        self.assertIn("battery 80% -> 75%", t)
        self.assertIn("removed from the list", t)

    def test_serials_and_home_folder_are_masked(self):
        self.log.enable(path=self.path)
        self.log.devices([dev(key="serial:ABCDEF123456")])
        self.log.log("note", f"opened {Path.home()}/x and unit:0011223344")
        t = self.text()
        self.assertNotIn("ABCDEF123456", t)
        self.assertIn("serial:…3456", t)
        self.assertNotIn("0011223344", t)
        self.assertNotIn(str(Path.home()), t)
        self.assertIn("~/x", t)

    def test_exceptions_are_recorded_and_hooks_restored(self):
        old_main, old_thread = sys.excepthook, threading.excepthook
        seen = []
        threading.excepthook = lambda args: seen.append(args.exc_value)       # keep the test output quiet
        old_thread = threading.excepthook
        self.log.enable(path=self.path)
        t = threading.Thread(target=lambda: 1 / 0, name="boom")
        t.start()
        t.join()
        self.assertIn("ZeroDivisionError", self.text())
        self.assertIn("in thread boom", self.text())
        self.assertEqual(len(seen), 1)                                         # the original hook still runs
        self.log.disable()
        self.assertIs(sys.excepthook, old_main)
        self.assertIs(threading.excepthook, old_thread)

    def test_stops_writing_when_disabled(self):
        self.log.enable(path=self.path)
        self.log.disable()
        size = self.path.stat().st_size
        self.log.log("x", "late")
        self.assertEqual(self.path.stat().st_size, size)
        self.assertIn("debug log off", self.text())

    def test_rotation_and_size_cap(self):
        self.path.write_text("x" * (debuglog.MAX_BYTES + 10))
        self.log.enable(path=self.path)
        self.assertTrue((self.dir / "debug.log.1").exists())
        self.assertLess(self.path.stat().st_size, 100_000)
        old = debuglog.MAX_BYTES
        debuglog.MAX_BYTES = 2000
        try:
            for i in range(500):
                self.log.log("spam", "y" * 100)
        finally:
            debuglog.MAX_BYTES = old
        self.assertFalse(self.log.enabled)                                     # a runaway log shuts itself off
        self.assertIn("too large", self.text())

    def test_raw_traffic_goes_to_its_own_file(self):
        from pylogi.hidpp import trace
        self.log.enable(raw=True, path=self.path)
        trace.log("TX", b"\x10\x01\x00\x10")
        self.log.disable()
        raw = (self.dir / "debug-hid.log").read_text()
        self.assertIn("TX 10 01 00 10", raw)
        trace.log("TX", b"\x99")                                               # after disable: ignored
        self.assertNotIn("99", (self.dir / "debug-hid.log").read_text().split("TX 10 01 00 10")[-1])


class ManagerHooksTests(unittest.TestCase):
    def test_merged_duplicates_and_errors_are_logged(self):
        from pylogi.core.manager import DeviceManager
        log = debuglog.LOG
        path = Path(tempfile.mkdtemp()) / "debug.log"
        log.enable(path=path)
        try:
            m = DeviceManager(None)
            m._err("something failed")
            direct = SimpleNamespace(uid="/dev/hidraw10", name="G305", kind="mouse", online=True, slot=0,
                                     model_ids=["4074"], device_key=lambda: "uid:/dev/hidraw10",
                                     battery=None, caps=set(), connection="USB")
            via = SimpleNamespace(uid="/dev/hidraw9:1", name="G305", kind="mouse", online=True, slot=1,
                                  model_ids=["4074"], device_key=lambda: "wpid:lightspeed:4074",
                                  battery=None, caps=set(), connection="Lightspeed")
            m._chans = {"a": (None, None, [direct, via])}
            m._publish()
            t = path.read_text()
            self.assertIn("[error", t)
            self.assertIn("something failed", t)
            self.assertIn("[merge", t)
            self.assertIn("/dev/hidraw10", t)
            table = t.split("[devices")[1:]                                    # the table: one G305 row only
            self.assertEqual(sum("uid=/dev/hidraw9:1" in row for row in table), 1)
            self.assertFalse(any("uid=/dev/hidraw10 " in row for row in table))
        finally:
            log.disable()


class SettingsTests(unittest.TestCase):
    def test_switch_starts_and_stops_the_log(self):
        from pylogi.core.config import Config
        from pylogi.core.demo import DemoManager
        from pylogi.ui import theme
        from pylogi.ui.dialogs import SettingsDialog
        from pylogi.ui.main_window import MainWindow
        theme.apply(app, "dark", "default")
        cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        orig = debuglog.config_dir
        debuglog.config_dir = lambda: Path(tempfile.mkdtemp())
        w = MainWindow(DemoManager(), cfg, None)
        d = SettingsDialog(w)
        try:
            self.assertFalse(d.debug.isChecked())
            self.assertFalse(d.debug_raw.isEnabled())
            d.debug.setChecked(True)
            self.assertTrue(debuglog.LOG.enabled)
            self.assertTrue(cfg.get("debug_log"))
            self.assertTrue(d.debug_raw.isEnabled())
            self.assertIn("devices", debuglog.LOG.path.read_text())            # the current devices are written at once
            self.assertIn("debug.log", d.debug_info.text())
            d.debug.setChecked(False)
            self.assertFalse(debuglog.LOG.enabled)
            self.assertFalse(cfg.get("debug_log"))
        finally:
            debuglog.LOG.disable()
            debuglog.config_dir = orig
            d.deleteLater()
            w.deleteLater()
            app.processEvents()

    def test_cli_flag(self):
        from pylogi.app import build_parser
        self.assertTrue(build_parser().parse_args(["--debug-log"]).debug_log)


if __name__ == "__main__":
    unittest.main()
