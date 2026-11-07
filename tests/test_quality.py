# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Self-test, backups, image downloads, uevent parsing and listener-thread shutdown."""
import hashlib
import io
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pylogi.core import backup, images, selftest  # noqa: E402
from pylogi.core.demo import DemoManager  # noqa: E402
from pylogi.hidpp import transport  # noqa: E402
from pylogi.hidpp.protocol import Channel  # noqa: E402
from pylogi.hidpp.transport import Transport  # noqa: E402

app = QApplication.instance() or QApplication([])


class BackupDirMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch("pylogi.core.backup.backup_dir", lambda: Path(self._tmp.name))
        self._patch.start()
        backup._auto_last.clear()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()


class SelfTestOnDemoDevices(BackupDirMixin, unittest.TestCase):
    def test_every_demo_device_ends_in_its_original_state(self):
        mgr = DemoManager()
        # the demo devices fake the settings layer, not raw HID++ (divert / onboard sectors / report rate)
        needs_hid = {"divert a button to software", "report rate", "onboard profile write", "switch the active profile"}
        tests = [x for x in selftest.TESTS if x[1] not in needs_hid]
        with mock.patch.object(selftest, "TESTS", tests):
            results = selftest.run(mgr.devices, log=lambda _m: None)
        self.assertTrue(results)
        bad = [r for r in results if r.status in (selftest.FAIL, selftest.ERROR)]
        self.assertEqual(bad, [], bad)
        finals = [r for r in results if r.test == "final state"]
        self.assertEqual(len(finals), len([d for d in mgr.devices if d.kind != "receiver"]))

    def test_offline_devices_are_skipped(self):
        mgr = DemoManager()
        dev = mgr.devices[0]
        dev.online = False
        out = selftest.run([dev], log=lambda _m: None)
        self.assertEqual([r.status for r in out], [selftest.SKIP])

    def test_diff_ignores_volatile_and_driver_driven_values(self):
        a = {"battery": 1, "when": 1, "camera": {"Gain": 1, "Zoom": 100}, "dpi": 800}
        b = {"battery": 2, "when": 2, "camera": {"Gain": 9, "Zoom": 100}, "dpi": 800}
        self.assertEqual(selftest._diff(a, b), [])
        b["dpi"] = 1600
        self.assertEqual(selftest._diff(a, b), ["dpi"])


class Backups(BackupDirMixin, unittest.TestCase):
    def test_snapshot_save_and_restore_roundtrip(self):
        dev = DemoManager().devices[1]            # G502: dpi, report rate, profiles
        snap = backup.snapshot(dev)
        path = backup.save(dev, snap)
        self.assertTrue(path.exists())
        before = dev.dpi().current
        dev.set_dpi(max(dev.dpi().values))
        backup.restore(dev, snap)
        self.assertEqual(dev.dpi().current, before)

    def test_auto_backup_is_throttled_and_pruned(self):
        dev = DemoManager().devices[0]
        first = backup.auto(dev, "write")
        self.assertEqual(backup.auto(dev, "write"), first)               # inside min_interval: reused
        safe = "".join(c if c.isalnum() else "_" for c in dev.device_key())
        for i in range(backup._AUTO_KEEP + 5):        # old automatic backups of this device
            (Path(self._tmp.name) / f"auto-write-{safe}-2000010{i:02d}-000000.json").write_text("{}")
        backup._auto_last.clear()
        newest = backup.auto(dev, "write", min_interval=0)
        left = list(Path(self._tmp.name).glob(f"auto-*-{safe}-*.json"))
        self.assertEqual(len(left), backup._AUTO_KEEP)
        self.assertIn(newest, left)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ImageDownloads(unittest.TestCase):
    def test_oversized_response_is_refused_on_every_mirror(self):
        big = FakeResponse(b"x" * 100)
        with mock.patch("urllib.request.urlopen", return_value=big):
            with self.assertRaises(OSError):
                images._http_get("a.png", limit=10)

    def test_small_response_is_returned(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(b"hello")):
            self.assertEqual(images._http_get("a.txt", limit=10), b"hello")

    def _provider(self, tmp):
        return images.ImageProvider(enabled=True, directory=Path(tmp))

    def test_png_without_checksum_must_look_like_a_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider(tmp)
            entry = {"asset_path": "x/", "files": [{"name": "front.png"}]}
            with mock.patch("pylogi.core.images._http_get", return_value=b"<html>not an image</html>"):
                with self.assertRaises(ValueError):
                    p._fetch_file("k", entry, "front.png")
            self.assertFalse((Path(tmp) / "k__front.png").exists())
            good = images.PNG_MAGIC + b"data"
            with mock.patch("pylogi.core.images._http_get", return_value=good):
                self.assertTrue(p._fetch_file("k", entry, "front.png").exists())

    def test_checksum_mismatch_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._provider(tmp)
            data = images.PNG_MAGIC + b"data"
            entry = {"asset_path": "x/", "files": [{"name": "front.png", "sha256": hashlib.sha256(b"other").hexdigest()}]}
            with mock.patch("pylogi.core.images._http_get", return_value=data):
                with self.assertRaises(ValueError):
                    p._fetch_file("k", entry, "front.png")


class Uevent(unittest.TestCase):
    def test_parses_key_values_and_survives_bad_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "uevent"
            f.write_bytes(b"HID_ID=0003:0000046D:0000C52B\nHID_NAME=Logi \xff\xfe\nNOEQUALS\n")
            out = transport._read_uevent(str(f))
            self.assertEqual(out["HID_ID"], "0003:0000046D:0000C52B")
            self.assertTrue(out["HID_NAME"].startswith("Logi"))
            self.assertNotIn("NOEQUALS", out)

    def test_missing_file_gives_empty_dict(self):
        self.assertEqual(transport._read_uevent("/nonexistent/uevent"), {})


class ListenerShutdown(unittest.TestCase):
    class Slow(Transport):
        def __init__(self):
            self.closed_while_waiting = False
            self.waiting = False
            self.closed = False

        def write(self, data):
            pass

        def wait(self, timeout):
            self.waiting = True
            time.sleep(0.05)
            self.waiting = False
            return False

        def read(self, timeout):
            return None

        def close(self):
            if self.waiting:
                self.closed_while_waiting = True
            self.closed = True

    def test_close_waits_for_the_reader_thread_before_closing_the_handle(self):
        t = self.Slow()
        ch = Channel(t)
        ch.add_listener(lambda c, m: None)
        time.sleep(0.02)
        ch.close()
        self.assertTrue(t.closed)
        self.assertFalse(t.closed_while_waiting)
        self.assertFalse(ch._listen_thread.is_alive())

    def test_close_from_the_reader_thread_does_not_deadlock(self):
        t = self.Slow()
        ch = Channel(t)
        done = threading.Event()

        def listener(c, m):
            c.close()
            done.set()
        ch.add_listener(listener)
        ch.notifications.append(b"x")
        ch.close()      # also fine when called twice
        self.assertTrue(t.closed)


if __name__ == "__main__":
    unittest.main()
