# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
import os
import struct
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pylogi.ui import branding as B  # noqa: E402

app = QApplication.instance() or QApplication([])


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class IconTests(unittest.TestCase):
    def test_logo_renders_at_every_size_with_transparent_corners(self):
        for s in B.ICON_SIZES:
            img = B.render_logo(s)
            self.assertEqual((img.width(), img.height()), (s, s))
            self.assertEqual(img.pixelColor(0, 0).alpha(), 0, s)                  # rounded square: corner is empty
            self.assertGreater(img.pixelColor(s // 2, s // 2).alpha(), 200, s)     # the logo itself is opaque
            self.assertNotEqual(img.pixelColor(s // 2, s // 2).rgb(), img.pixelColor(s // 8 + 1, s // 2).rgb())

    def test_prerendered_pngs_match_the_svg_sizes(self):
        for s in B.ICON_SIZES:
            f = B.DATA / "icons" / f"pylogi-{s}.png"
            self.assertTrue(f.exists(), f)
            img = QImage(str(f))
            self.assertEqual((img.width(), img.height()), (s, s), f.name)

    def test_ico_container_is_valid(self):
        data = (B.DATA / "pylogi.ico").read_bytes()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind), (0, 1))
        self.assertGreaterEqual(count, 6)
        sizes = []
        for i in range(count):
            w, h, _c, _r, planes, bpp, length, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
            self.assertEqual(data[offset:offset + 8], b"\x89PNG\r\n\x1a\n")        # PNG-compressed entry
            self.assertEqual(offset + length <= len(data), True)
            sizes.append(w or 256)
        self.assertEqual(sizes, sorted(sizes))
        self.assertIn(256, sizes)

    def test_app_icon_is_usable_and_falls_back_to_pngs(self):
        icon = B.app_icon()
        self.assertFalse(icon.isNull())
        self.assertFalse(icon.pixmap(48, 48).isNull())
        real = B.QIcon
        with mock.patch.object(B, "QIcon", lambda *a: real()):      # the SVG icon comes out null -> PNG set
            fallback = B.app_icon()
        self.assertFalse(fallback.isNull())


class SplashTests(unittest.TestCase):
    def test_background_is_a_rounded_card_with_the_logo(self):
        pm = B.SplashScreen.background("0.2.0", 2.0)
        self.assertEqual((pm.width(), pm.height()), (1120, 640))
        self.assertEqual(pm.devicePixelRatio(), 2.0)
        img = pm.toImage()
        self.assertEqual(img.pixelColor(2, 2).alpha(), 0)                           # rounded corner
        self.assertGreater(img.pixelColor(560, 320).alpha(), 250)                   # card body
        self.assertNotEqual(img.pixelColor(200, 240).rgb(), img.pixelColor(900, 500).rgb())   # logo vs. gradient

    def test_status_line_and_progress_bar_animate(self):
        sp = B.SplashScreen("0.2.0", "Starting…")
        sp._timer.stop()
        frames = []
        for phase in (0.1, 0.5, 0.9):
            sp.phase = phase
            img = QImage(560, 320, QImage.Format_ARGB32)
            img.fill(0xFF0B0E1A)
            from PySide6.QtGui import QPainter
            p = QPainter(img)
            sp.drawContents(p)
            p.end()
            row = [img.pixelColor(x, 320 - 26).blue() for x in range(44, 516)]
            frames.append(max(range(len(row)), key=row.__getitem__))
        self.assertEqual(frames, sorted(frames))                                    # moves to the right
        self.assertGreater(frames[2] - frames[0], 200)
        sp.set_message("Detecting devices…")
        self.assertEqual(sp.message, "Detecting devices…")
        sp.close()

    def test_window_appears_once_after_the_minimum_time_and_scan_result(self):
        sp = B.SplashScreen("0.2.0", "x")
        shown, finished = [], []
        with mock.patch.object(B.SplashScreen, "finish", lambda self, w: finished.append(w)):
            ready, finish = sp.finish_when_ready("WIN", lambda: shown.append(time.monotonic()), min_ms=300, max_ms=5000)
            t0 = time.monotonic()
            ready()                                                   # scan done immediately
            wait(120)
            self.assertEqual(shown, [])                               # not before the minimum display time
            wait(400)
            self.assertEqual(len(shown), 1)
            self.assertGreaterEqual(shown[0] - t0, 0.28)
            ready()
            finish()
            wait(100)
            self.assertEqual((len(shown), finished), (1, ["WIN"]))   # a second trigger changes nothing
        sp._timer.stop()

    def test_timeout_shows_the_window_even_if_no_scan_finishes(self):
        sp = B.SplashScreen("0.2.0", "x")
        shown = []
        with mock.patch.object(B.SplashScreen, "finish", lambda self, w: None):
            sp.finish_when_ready("WIN", lambda: shown.append(1), min_ms=100, max_ms=250)
            wait(450)
        self.assertEqual(shown, [1])
        sp._timer.stop()


class ScanSignalTests(unittest.TestCase):
    def test_scanned_fires_even_when_nothing_is_found(self):
        from pylogi.core.manager import DeviceManager
        m = DeviceManager()
        got = []
        m.scanned.connect(lambda: got.append(1))
        changed = []
        m.changed.connect(lambda: changed.append(1))
        with mock.patch("pylogi.core.manager.enumerate_hidpp", return_value=[]), \
                mock.patch("pylogi.core.webcam.find_logitech_webcams", return_value=[]):
            m.scan_once()
        self.assertEqual((got, changed), ([1], []))                  # no devices: no "changed", but the scan is done

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux only: .desktop entries")

    def test_launcher_installs_a_png_too(self):
        import tempfile
        from pylogi.core import desktop
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tempfile.mkdtemp()}):
            names = [f.name for f in desktop.install_launcher()]
        self.assertEqual(names, ["pylogi.desktop", "pylogi.svg", "pylogi.png"])


if __name__ == "__main__":
    unittest.main()
