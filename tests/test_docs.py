# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Project hygiene: licence, language of comments, documentation and help completeness."""
import ast
import io
import os
import re
import sys
import tokenize
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

PY_FILES = sorted([*(ROOT / "src").rglob("*.py"), *(ROOT / "tests").glob("*.py"),
                   *(ROOT / "scripts").glob("*.py"), ROOT / "run.py"])
SKIP_DIRS = {"__pycache__", ".git", "build", "dist", ".venv", "venv", "site-packages"}
# the words are assembled so that this very file does not contain them
FORBIDDEN = re.compile("|".join(["cla" + "ude", "anthro" + "pic", "co-authored" + "-by"]), re.I)
TEXT_SUFFIXES = (".py", ".md", ".json", ".html", ".txt", ".toml", ".yml", ".yaml", ".rules", ".sh", ".svg", ".js", ".in", "")


def project_text_files():
    for p in ROOT.rglob("*"):
        if p.is_file() and not (set(p.parts) & SKIP_DIRS) and p.suffix.lower() in TEXT_SUFFIXES:
            yield p


class LicenceTests(unittest.TestCase):
    def test_every_python_file_has_the_spdx_header(self):
        for p in PY_FILES:
            head = "\n".join(p.read_text(encoding="utf-8").split("\n")[:3])
            self.assertIn("SPDX-License-Identifier: GPL-3.0-or-later", head, p.relative_to(ROOT))
            self.assertIn("Copyright (C)", head, p.relative_to(ROOT))

    def test_licence_file_is_the_complete_gpl_v3_and_is_packaged(self):
        text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("GNU GENERAL PUBLIC LICENSE", text.split("\n")[0])
        self.assertIn("Version 3, 29 June 2007", text)
        self.assertEqual(len(text.splitlines()), 674)                  # the unmodified FSF text
        self.assertEqual(text, (ROOT / "src/pylogi/data/LICENSE").read_text(encoding="utf-8"))
        meta = (ROOT / "pyproject.toml").read_text()
        self.assertIn('license = {text = "GPL-3.0-or-later"}', meta)
        self.assertIn("data/LICENSE", meta)
        self.assertIn("data/help/*.html", meta)
        import pylogi
        self.assertEqual(pylogi.__license__, "GPL-3.0-or-later")

    def test_generated_documents_are_up_to_date(self):
        import gen_docs
        for path, text in gen_docs.build().items():
            self.assertEqual(path.read_text(encoding="utf-8"), text, f"run scripts/gen_docs.py ({path.name})")

    def test_third_party_components_are_listed_everywhere(self):
        from pylogi.core import credits
        third = (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for c in credits.COMPONENTS:
            self.assertIn(c.name, third)
            self.assertIn(c.name, readme)
        self.assertIn("GPL-3.0-or-later", readme)
        self.assertIn("without any warranty", readme.lower())
        self.assertIn("WITHOUT ANY WARRANTY", third)


class HygieneTests(unittest.TestCase):
    def test_no_references_to_ai_assistants_anywhere_in_the_project(self):
        checked = 0
        for p in project_text_files():
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            checked += 1
            self.assertIsNone(FORBIDDEN.search(text), p.relative_to(ROOT))
            self.assertIsNone(FORBIDDEN.search(p.name), p.name)
        self.assertGreater(checked, 50)

    def test_comments_and_docstrings_are_english(self):
        es = set("el la los las del que para con por una unos unas es se su sus como pero más muy este esta estos estas "
                 "cuando donde también todo todos sin sobre entre desde hasta solo cada otro otra puede pueden hay está "
                 "están son fue han tiene tienen nos".split())
        en = set("the of and to is in for with that this it are be as on at by or from not can if when then so".split())

        def spanish(text: str) -> bool:
            words = re.findall(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]+", text.lower())
            return len(words) >= 3 and sum(w in es for w in words) >= 2 and \
                sum(w in es for w in words) > sum(w in en for w in words)
        for p in PY_FILES:
            src = p.read_text(encoding="utf-8")
            for tok in tokenize.generate_tokens(io.StringIO(src).readline):
                if tok.type == tokenize.COMMENT:
                    self.assertFalse(spanish(tok.string), f"{p.name}:{tok.start[0]} {tok.string}")
            for n in ast.walk(ast.parse(src)):
                if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    d = ast.get_docstring(n)
                    self.assertFalse(d and spanish(d), f"{p.name}: docstring {d!r}"[:120])


class HelpTests(unittest.TestCase):
    def setUp(self):
        from pylogi.ui import help as H
        self.H = H

    def test_every_command_line_option_is_documented_in_every_manual(self):
        from pylogi.app import build_parser
        options = [o for a in build_parser()._actions for o in a.option_strings if o.startswith("--")]
        self.assertGreater(len(options), 20)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for lang in ("en", "es", "fr"):                           # fr has no manual of its own -> English
            html = self.H.help_html(lang)
            for o in options:
                self.assertIn(f"<code>{o}</code>", html, f"{o} missing from the {lang} help")
        for o in options:
            self.assertIn(f"`{o}`", readme, f"{o} missing from the README")

    def test_manuals_cover_the_same_sections_and_all_links_resolve(self):
        en = self.H.sections(self.H.help_html("en"))
        es = self.H.sections(self.H.help_html("es"))
        self.assertEqual([a for a, _ in en], [a for a, _ in es])
        anchors = {a for a, _ in en}
        for need in ("getting-started", "devices", "buttons", "pointer", "profiles", "lighting", "camera", "device-section",
                     "add-remove", "settings", "cli", "files", "safety", "troubleshooting", "shortcuts", "license"):
            self.assertIn(need, anchors)
        for lang in ("en", "es"):
            html = self.H.help_html(lang)
            for target in re.findall(r'href="#([\w-]+)"', html):
                self.assertIn(target, anchors, f"{lang}: dead link #{target}")
            self.assertNotIn(self.H.CLI_MARKER, html)

    def test_help_documents_every_ui_option(self):
        text = self.H.help_html("en")
        for phrase in ("Grid / List / By category", "Keyboard shortcut", "Media key", "Type text", "Run command",
                       "Change DPI", "Profiles per application", "SmartShift", "Invert scrolling direction",
                       "Use onboard profiles", "Apply to selected", "Rainbow", "Auto", "Streaming", "Video call",
                       "Switch host", "Rename", "Fn lock", "Manage this device", "Pair new device",
                       "Unpair from the receiver", "Remove the Bluetooth pairing", "Forget pyLogi settings",
                       "Hide from the list", "Delete from pyLogi", "Hidden devices", "Export settings",
                       "Start pyLogi with the desktop session", "Keep running in the tray", "F1", "Save as PDF", "Use this profile"):
            self.assertIn(phrase, text, phrase)

    def test_dialog_opens_on_a_section_and_searches(self):
        import tempfile
        from pylogi.core.config import Config
        from pylogi.core.demo import DemoManager
        from pylogi.ui.main_window import MainWindow
        w = MainWindow(DemoManager(), Config(Path(tempfile.mkdtemp()) / "c.json"), None)
        d = self.H.HelpDialog(w, "buttons")
        self.assertEqual(d.toc.currentItem().data(0x0100), "buttons")
        self.assertGreaterEqual(d.toc.count(), 16)
        d.search.setText("uinput")
        self.assertTrue(d.view.textCursor().hasSelection())


class HelpPdfTests(unittest.TestCase):
    def setUp(self):
        import shutil
        import subprocess
        import tempfile
        from pylogi.ui import help as H
        self.H, self.tmp = H, Path(tempfile.mkdtemp())
        self.pdftotext = shutil.which("pdftotext")
        self.subprocess = subprocess

    def test_export_writes_a_valid_multipage_a4_pdf_in_the_chosen_language(self):
        from pylogi.i18n import set_language
        out = {}
        for lang in ("en", "es"):
            set_language(lang)
            f = self.tmp / f"help-{lang}.pdf"
            pages = self.H.export_pdf(f, lang)
            out[lang] = (f, pages)
            data = f.read_bytes()
            self.assertTrue(data.startswith(b"%PDF-"), lang)
            self.assertGreater(len(data), 20_000)
            self.assertGreaterEqual(pages, 4, lang)                            # a whole manual, not a stub
            self.assertEqual(len(re.findall(rb"/Type\s*/Page(?![s\w])", data)), pages)
        set_language("en")
        self.assertGreater(out["en"][0].stat().st_size, 0)
        if self.pdftotext:                                                      # the text must be real, selectable text
            def text(f):
                return self.subprocess.run([self.pdftotext, str(f), "-"], capture_output=True, text=True).stdout
            en, es = text(out["en"][0]), text(out["es"][0])
            self.assertIn("pyLogi Help", en)
            self.assertIn("Ayuda de pyLogi", es)
            self.assertIn("Atajo de teclado", es)
            for phrase in ("--export-help", "GPL-3.0-or-later", "Delete from pyLogi", "Troubleshooting"):
                self.assertIn(phrase, en)
            info = self.subprocess.run(["pdfinfo", str(out["en"][0])], capture_output=True, text=True).stdout
            self.assertIn("A4", info)
            self.assertRegex(info, rf"Pages:\s+{out['en'][1]}\b")

    def test_print_document_is_black_on_white_regardless_of_the_theme(self):
        from pylogi.ui import theme
        theme.apply(app, "dark", "default")
        try:
            doc = self.H.print_document("en")
            html = doc.defaultStyleSheet()
            self.assertIn("color: #111", html)
            self.assertNotIn(theme.BG, html)
            self.assertIn("pyLogi 0.2.0", doc.toPlainText()[:200])
        finally:
            theme.apply(app, "dark", "default")

    def test_dialog_button_saves_through_the_file_chooser(self):
        import tempfile
        from unittest import mock
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from pylogi.core.config import Config
        from pylogi.core.demo import DemoManager
        from pylogi.ui.main_window import MainWindow
        w = MainWindow(DemoManager(), Config(Path(tempfile.mkdtemp()) / "c.json"), None)
        d = self.H.HelpDialog(w)
        target = self.tmp / "from-dialog"                                       # no extension: ".pdf" is added
        with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "")), \
                mock.patch.object(QMessageBox, "information") as info:
            d._save_pdf()
        self.assertTrue((self.tmp / "from-dialog.pdf").read_bytes().startswith(b"%PDF-"))
        self.assertIn("from-dialog.pdf", info.call_args.args[2])
        with mock.patch.object(QFileDialog, "getSaveFileName", return_value=("", "")), \
                mock.patch.object(QMessageBox, "information") as info:
            d._save_pdf()                                                       # cancelled: nothing happens
        info.assert_not_called()

    def test_command_line_export(self):
        import subprocess
        target = self.tmp / "cli.pdf"
        r = subprocess.run([sys.executable, str(ROOT / "run.py"), "--export-help", str(target), "--lang", "es"],
                           capture_output=True, text=True, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(target.read_bytes().startswith(b"%PDF-"))
        self.assertRegex(r.stdout, r"cli\.pdf: [4-9]\d* pages|cli\.pdf: \d{2,} pages")


class AboutTests(unittest.TestCase):
    def test_about_dialog_shows_version_licence_components_and_system_report(self):
        import tempfile
        from pylogi import __version__
        from pylogi.core import credits
        from pylogi.core.config import Config
        from pylogi.core.demo import DemoManager
        from pylogi.ui.about import TABS, AboutDialog
        from pylogi.ui.main_window import MainWindow
        w = MainWindow(DemoManager(), Config(Path(tempfile.mkdtemp()) / "c.json"), None)
        d = AboutDialog(w, "license")
        self.assertEqual(d.tabs.count(), len(TABS))
        self.assertEqual(d.tabs.currentIndex(), TABS.index("license"))
        lic = d.tabs.widget(1).toPlainText()
        self.assertIn("GNU GENERAL PUBLIC LICENSE", lic)
        self.assertIn("WITHOUT ANY WARRANTY", lic)
        comps = d.tabs.widget(2).toPlainText()
        for c in credits.COMPONENTS:
            self.assertIn(c.name, comps)
        report = d.report.toPlainText()
        self.assertIn(f"pyLogi {__version__}", report)
        self.assertIn("MX Master 3S", report)
        self.assertNotRegex(report, r"serial:|SN01")                 # no serial numbers in the report

    def test_licence_notice_on_the_command_line(self):
        import subprocess
        out = subprocess.run([sys.executable, str(ROOT / "run.py"), "--license"], capture_output=True, text=True,
                             env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}).stdout
        self.assertIn("GNU General Public License", out)
        self.assertIn("WITHOUT ANY WARRANTY", out)
        self.assertIn("not affiliated", out)


if __name__ == "__main__":
    unittest.main()
