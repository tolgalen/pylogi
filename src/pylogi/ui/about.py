# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""About dialog: description, licence, third-party components and a system report."""
from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QGuiApplication, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTabWidget,
                               QTextBrowser, QVBoxLayout, QWidget)

from .. import __copyright__, __license__, __version__
from ..core import credits, diagnostics
from ..i18n import N_, tr
from . import branding, theme

LICENSE_FILE = branding.DATA / "LICENSE"
TABS = ("about", "license", "components", "system")


def license_text() -> str:
    try:
        return LICENSE_FILE.read_text(encoding="utf-8")
    except OSError:
        return "GNU General Public License v3.0 or later: https://www.gnu.org/licenses/gpl-3.0.html"


def components_html() -> str:
    rows = []
    for c in credits.COMPONENTS:
        name = f'<a href="{c.url}">{escape(c.name)}</a>' if c.url else escape(c.name)
        rows.append(f"<p><b>{name}</b><br><i>{escape(c.license)}</i><br>{escape(c.role)}</p>")
    return "".join(rows) + f"<hr><p>{escape(credits.TRADEMARKS)}</p>"


class AboutDialog(QDialog):
    def __init__(self, main, tab: str = "about"):
        super().__init__(main)
        self.main = main
        self.setWindowTitle(tr("About pyLogi"))
        self.resize(640, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._about(), tr("About"))
        self.tabs.addTab(self._license(), tr("License"))
        self.tabs.addTab(self._components(), tr("Third-party components"))
        self.tabs.addTab(self._system(), tr("System"))
        self.tabs.setCurrentIndex(TABS.index(tab) if tab in TABS else 0)
        lay.addWidget(self.tabs)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

    # -- tabs ---------------------------------------------------------------
    def _about(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignTop)
        v.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(QPixmap.fromImage(branding.render_logo(192)).scaled(96, 96, Qt.KeepAspectRatio,
                                                                           Qt.SmoothTransformation))
        logo.setAlignment(Qt.AlignCenter)
        v.addWidget(logo)
        name = QLabel(f"py<span style='color:{theme.ACCENT}'>Logi</span>")
        name.setTextFormat(Qt.RichText)
        name.setStyleSheet("font-size:30px;font-weight:600;")
        name.setAlignment(Qt.AlignCenter)
        v.addWidget(name)
        for text, style in ((f"{tr('Version')} {__version__}", ""),
                            (tr(N_("Manage your Logitech mice, keyboards and webcams")), "color:%s;" % theme.MUTED),
                            (__copyright__, "color:%s;font-size:12px;" % theme.MUTED),
                            (tr("Licensed under the GNU General Public License, version 3 or later") +
                             f" ({__license__})", "font-size:12px;")):
            lb = QLabel(text)
            lb.setAlignment(Qt.AlignCenter)
            lb.setWordWrap(True)
            lb.setStyleSheet(style)
            v.addWidget(lb)
        warranty = QLabel(credits.WARRANTY.split("\n\n")[1])
        warranty.setWordWrap(True)
        warranty.setAlignment(Qt.AlignCenter)
        warranty.setStyleSheet(f"color:{theme.MUTED};font-size:11px;")
        v.addSpacing(8)
        v.addWidget(warranty)
        v.addStretch(1)
        link = QLabel(f"<a href='https://www.gnu.org/licenses/gpl-3.0.html'>{tr('GPL-3.0 on gnu.org')}</a>")
        link.setOpenExternalLinks(True)
        link.setAlignment(Qt.AlignCenter)
        v.addWidget(link)
        return w

    def _license(self) -> QWidget:
        edit = QPlainTextEdit(credits.WARRANTY + "\n\n" + "-" * 72 + "\n\n" + license_text())
        edit.setReadOnly(True)
        edit.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        edit.moveCursor(edit.textCursor().MoveOperation.Start)
        return edit

    def _components(self) -> QWidget:
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        view.setHtml(components_html())
        return view

    def _system(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(tr("Include this report when you ask for help. It contains no serial numbers.")))
        self.report = QPlainTextEdit(diagnostics.collect(getattr(self.main, "manager", None)))
        self.report.setReadOnly(True)
        self.report.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        v.addWidget(self.report)
        copy = QPushButton(tr("Copy"))
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.report.toPlainText()))
        v.addWidget(copy, 0, Qt.AlignRight)
        return w
