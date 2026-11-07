# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pylogi import i18n  # noqa: E402


class I18nTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("en")

    def test_languages_discovered_from_files(self):
        langs = i18n.available_languages()
        for code in ("en", "es", "fr", "de", "it", "pt"):
            self.assertIn(code, langs)
        self.assertEqual(langs["de"], "Deutsch")

    def test_switch_and_fallback(self):
        i18n.set_language("fr")
        self.assertEqual(i18n.tr("Devices"), "Appareils")
        self.assertEqual(i18n.tr("Not a known string"), "Not a known string")
        i18n.set_language("xx")            # unknown -> English
        self.assertEqual((i18n.language(), i18n.tr("Devices")), ("en", "Devices"))

    def test_all_locales_complete(self):
        import i18n_tool
        keys = set(i18n_tool.extract())
        for f in i18n_tool.LOC.glob("*.json"):
            data = i18n_tool.load(f)
            if data["_meta"]["code"] == "en":
                continue
            missing = [k for k in keys if not data["strings"].get(k)]
            self.assertEqual(missing, [], f.name)

    def test_user_locale_file_adds_and_overrides(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "tlh.json").write_text(json.dumps(
                {"_meta": {"code": "tlh", "name": "tlhIngan"}, "strings": {"Devices": "Ngoq"}}))
            (Path(d) / "es.json").write_text(json.dumps(
                {"_meta": {"code": "es", "name": "Español"}, "strings": {"Devices": "Equipos"}}))
            orig = i18n.user_locales_dir
            i18n.user_locales_dir = lambda: Path(d)
            try:
                self.assertIn("tlh", i18n.available_languages())
                i18n.set_language("tlh")
                self.assertEqual(i18n.tr("Devices"), "Ngoq")
                i18n.set_language("es")
                self.assertEqual(i18n.tr("Devices"), "Equipos")       # overridden
                self.assertEqual(i18n.tr("Rescan"), "Buscar")         # inherited
            finally:
                i18n.user_locales_dir = orig
                i18n.available_languages()


class UiLanguageTests(unittest.TestCase):
    def test_runtime_language_change(self):
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QPushButton
        app = QApplication.instance() or QApplication([])
        from pylogi.core.config import Config
        from pylogi.core.demo import DemoManager
        from pylogi.ui.main_window import MainWindow
        i18n.set_language("en")
        w = MainWindow(DemoManager(), Config(Path(tempfile.mkdtemp()) / "c.json"))
        texts = lambda: {b.text().strip() for b in w.findChildren(QPushButton)}
        self.assertIn("Settings", texts())
        w.set_ui_language("de")
        QTest.qWait(100)
        self.assertIn("Einstellungen", texts())
        w._open([d for d in w.manager.devices if d.kind != "receiver"][0])
        QTest.qWait(50)
        w.set_ui_language("en")
        QTest.qWait(100)
        self.assertIn("Settings", texts())
        self.assertIs(w.stack.currentWidget(), w.page)      # stays on the open device
        i18n.set_language("en")


if __name__ == "__main__":
    unittest.main()
