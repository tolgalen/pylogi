# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""In-app help: HTML pages per language, with the command-line table generated from the real parser."""
from __future__ import annotations

import re
from html import escape
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QTextBrowser, QVBoxLayout)

from ..i18n import language, tr
from . import branding, theme

HELP_DIR = branding.DATA / "help"
CLI_MARKER = "<!--CLI-->"
_HEADING = re.compile(r'<h2><a name="([\w-]+)"></a>(.*?)</h2>', re.S)


def cli_table_html(parser=None) -> str:
    """HTML table of every command-line option, built from argparse so it can never go stale."""
    if parser is None:
        from ..app import build_parser
        parser = build_parser()
    rows = []
    for a in parser._actions:
        if not a.option_strings:
            continue
        names = ", ".join(f"<code>{escape(o)}</code>" for o in a.option_strings)
        if a.metavar:
            meta = a.metavar if isinstance(a.metavar, str) else " ".join(a.metavar)
            names += f" <code>{escape(meta)}</code>"
        elif a.choices:
            names += " <code>{" + escape("|".join(map(str, a.choices))) + "}</code>"
        rows.append(f"<tr><td>{names}</td><td>{escape(a.help or '')}</td></tr>")
    return "<table>" + "".join(rows) + "</table>"


def help_path(lang: str | None = None):
    """The help file for a language (English when it has no translated manual)."""
    f = HELP_DIR / f"{lang or language()}.html"
    return f if f.exists() else HELP_DIR / "en.html"


def help_html(lang: str | None = None, parser=None) -> str:
    return help_path(lang).read_text(encoding="utf-8").replace(CLI_MARKER, cli_table_html(parser))


PRINT_CSS = ("body { color: #111; font-family: 'Noto Sans', 'DejaVu Sans', sans-serif; font-size: 10pt; } "
             "h1 { font-size: 22pt; color: #1d4ed8; } h2 { font-size: 14pt; margin-top: 16pt; color: #111; "
             "border-bottom: 1px solid #999; } a { color: #1d4ed8; text-decoration: none; } "
             "code, pre { background: #eef0f4; color: #111; font-family: 'DejaVu Sans Mono', monospace; font-size: 9pt; } "
             "kbd { background: #e5e7eb; } td { padding: 3pt 8pt 3pt 0; vertical-align: top; }")


def print_document(lang: str | None = None, parser=None):
    """A QTextDocument of the manual styled for paper: black on white, with a title block."""
    from PySide6.QtGui import QTextDocument

    from .. import __copyright__, __license__, __version__
    doc = QTextDocument()
    doc.setDefaultStyleSheet(PRINT_CSS)
    head = (f"<p style='color:#555'>pyLogi {__version__} &middot; {__license__} &middot; {escape(__copyright__)}</p>")
    html = help_html(lang, parser)
    doc.setHtml(html.replace("</h1>", "</h1>" + head, 1))
    return doc


def export_pdf(path, lang: str | None = None, parser=None) -> int:
    """Write the manual (in the current language, English if there is no translation) as an A4 PDF."""
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.A4))
    writer.setPageMargins(QMarginsF(18, 16, 18, 16), QPageLayout.Millimeter)
    writer.setTitle("pyLogi - " + tr("Help"))
    writer.setCreator("pyLogi")
    doc = print_document(lang, parser)
    doc.print_(writer)
    del writer                                         # flush and close the file before reading it
    return len(re.findall(rb"/Type\s*/Page(?![s\w])", Path(path).read_bytes()))


def sections(html: str) -> list[tuple[str, str]]:
    """(anchor, title) of every section, for the table of contents."""
    return [(a, re.sub(r"<[^>]+>", "", t).strip()) for a, t in _HEADING.findall(html)]


class HelpDialog(QDialog):
    def __init__(self, main, section: str | None = None):
        super().__init__(main)
        self.setWindowTitle(tr("Help"))
        self.resize(940, 640)
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search…"))
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self._find_next)
        self.search.textChanged.connect(self._find_first)
        left.addWidget(self.search)
        self.toc = QListWidget()
        self.toc.setFixedWidth(270)
        left.addWidget(self.toc, 1)
        pdf = QPushButton(tr("Save as PDF…"))
        pdf.setToolTip(tr("Print the help to a PDF file"))
        pdf.clicked.connect(self._save_pdf)
        left.addWidget(pdf)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.accept)
        left.addWidget(close)
        lay.addLayout(left)

        self.html = help_html()
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.document().setDefaultStyleSheet(
            f"body {{ color: {theme.TEXT}; }} a {{ color: {theme.ACCENT_HI}; }} "
            f"h1 {{ font-size: 24px; }} h2 {{ font-size: 18px; margin-top: 22px; }} "
            f"code, pre {{ background: {theme.PANEL}; color: {theme.TEXT}; }} "
            f"kbd {{ background: {theme.CARD_HI}; }} td {{ padding: 4px 10px 4px 0; vertical-align: top; }}")
        self.view.setHtml(self.html)
        lay.addWidget(self.view, 1)

        for anchor, title in sections(self.html):
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, anchor)
            self.toc.addItem(item)
        self.toc.currentItemChanged.connect(lambda cur, _prev: cur and self.view.scrollToAnchor(cur.data(Qt.UserRole)))
        if section:
            self.show_section(section)

    def show_section(self, anchor: str) -> None:
        for i in range(self.toc.count()):
            if self.toc.item(i).data(Qt.UserRole) == anchor:
                self.toc.setCurrentRow(i)
                return

    def _save_pdf(self) -> None:
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        path, _ = QFileDialog.getSaveFileName(self, tr("Save as PDF…"), "pylogi-help.pdf", "PDF (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            export_pdf(path)
        except OSError as e:
            QMessageBox.warning(self, tr("Error"), str(e))
            return
        QMessageBox.information(self, tr("Help"), f"{tr('Saved')}: {path}")

    def _find_first(self, text: str) -> None:
        self.view.moveCursor(self.view.textCursor().MoveOperation.Start)
        if text:
            self.view.find(text)

    def _find_next(self) -> None:
        if self.search.text() and not self.view.find(self.search.text()):
            self._find_first(self.search.text())               # wrap around
