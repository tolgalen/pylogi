# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pylogi.ui import theme  # noqa: E402

app = QApplication.instance() or QApplication([])


class ThemeTests(unittest.TestCase):
    def tearDown(self):
        theme.apply(app, "dark", "default")

    def test_shipped_themes_are_listed_and_readable(self):
        themes = theme.available_themes()
        for tid in ("default", "midnight", "graphite"):
            self.assertIn(tid, themes)
        for tid in themes:
            for mode in ("dark", "light"):
                pal = theme.palette(tid, mode)
                self.assertEqual(set(pal), set(theme.TOKENS))
                # text must stay readable on every surface of every theme
                for surface in ("BG", "PANEL", "CARD"):
                    self.assertGreaterEqual(theme.contrast(pal["TEXT"], pal[surface]), 7.0, (tid, mode, surface))
                    self.assertGreaterEqual(theme.contrast(pal["MUTED"], pal[surface]), 3.6, (tid, mode, surface))

    def test_accent_is_the_same_in_every_appearance(self):
        # OpenLogi: "the brand colour, identical in both appearances"
        for tid in theme.available_themes():
            self.assertEqual(theme.palette(tid, "dark")["ACCENT"], theme.palette(tid, "light")["ACCENT"])
        self.assertEqual(theme.DARK["ACCENT"].lower(), "#3b82f6")

    def test_apply_switches_tokens_and_stylesheet(self):
        self.assertEqual(theme.apply(app, "light", "default"), "light")
        self.assertEqual((theme.MODE, theme.BG), ("light", theme.LIGHT["BG"]))
        self.assertIn(theme.LIGHT["BG"], theme.QSS)
        self.assertEqual(theme.apply(app, "dark", "midnight"), "dark")
        self.assertEqual(theme.BG, theme.palette("midnight", "dark")["BG"])
        theme.apply(app, "dark", "does-not-exist")           # unknown theme -> default
        self.assertEqual(theme.THEME, "default")

    def test_system_mode_resolves_to_dark_or_light(self):
        self.assertIn(theme.resolve_mode("system"), ("dark", "light"))
        self.assertEqual(theme.resolve_mode("light"), "light")

    def test_user_theme_file_overrides_and_ignores_bad_colours(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "mine.json").write_text(json.dumps({
                "_meta": {"id": "mine", "name": "Mine"},
                "dark": {"BG": "#101010", "TEXT": "not-a-colour", "NOT_A_TOKEN": "#fff"}}))
            orig = theme._user_dir
            theme._user_dir = lambda: Path(d)
            try:
                self.assertEqual(theme.available_themes()["mine"], "Mine")
                pal = theme.palette("mine", "dark")
                self.assertEqual(pal["BG"], "#101010")
                self.assertEqual(pal["TEXT"], theme.DARK["TEXT"])      # invalid value ignored
                self.assertNotIn("NOT_A_TOKEN", pal)
                self.assertEqual(theme.palette("mine", "light")["BG"], theme.LIGHT["BG"])   # no light overrides
            finally:
                theme._user_dir = orig

    def test_live_change_rebuilds_the_whole_ui(self):
        from PySide6.QtTest import QTest
        from pylogi.core.config import Config
        from pylogi.core.demo import DemoManager
        from pylogi.ui.main_window import MainWindow
        theme.apply(app, "dark", "default")
        m = DemoManager()
        w = MainWindow(m, Config(Path(tempfile.mkdtemp()) / "c.json"))
        w._open(m.devices[0])
        for mode, tid in (("light", "default"), ("dark", "graphite"), ("light", "midnight")):
            w.set_appearance(mode, tid)
            QTest.qWait(80)
            self.assertEqual((theme.MODE, theme.THEME), (mode, tid))
            self.assertIs(w.stack.currentWidget(), w.page)          # still on the open device
            for dev in m.devices:                                    # every page builds with the palette
                w._open(dev)
                for key in list(w.page.navs):
                    w.page.ensure(key)
        self.assertEqual(w.cfg.get("appearance"), "light")


if __name__ == "__main__":
    unittest.main()
