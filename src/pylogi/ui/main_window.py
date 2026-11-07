# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Main window: device overview (home) and per-device page."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QIcon, QKeySequence
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMessageBox,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget
)

from ..core.config import Config
from ..core.device import Device
from ..i18n import set_language, tr
from . import theme
from .about import AboutDialog
from .dialogs import AddDialog, ReceiversDialog, RemoveDialog, SettingsDialog
from .help import HelpDialog
from .device_page import DevicePage
from .home import HomePage


def _clear(layout) -> None:
    while layout.count():
        it = layout.takeAt(0)
        if it.widget():
            w = it.widget()
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif it.layout():
            _clear(it.layout())


class MainWindow(QMainWindow):
    def __init__(self, manager, cfg: Config, images=None, settings=None):
        super().__init__()
        self.manager, self.cfg, self.images, self.settings = manager, cfg, images, settings
        self.setWindowTitle("pyLogi")
        self.resize(1200, 800)
        self._restore_geometry()
        self.current: Device | None = None
        self._warned: set[str] = set()
        self._critical: set[str] = set()
        self._online: dict[str, bool] | None = None
        self.tray = QSystemTrayIcon(self._tray_icon(), self)
        QApplication.instance().aboutToQuit.connect(self._save_geometry)
        self._build_tray()
        self._build_ui()
        f1 = QAction(self)                                    # F1 opens the help from anywhere in the window
        f1.setShortcut(QKeySequence(QKeySequence.HelpContents))
        f1.triggered.connect(lambda: self.show_help())
        self.addAction(f1)
        manager.changed.connect(self._refresh, Qt.QueuedConnection)
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._scheme_changed)
        QGuiApplication.instance().applicationStateChanged.connect(self._app_state)
        if images:
            images.ready.connect(self._images_ready, Qt.QueuedConnection)
        self._refresh()

    def _tray_icon(self) -> QIcon:
        """The application icon, or the single-colour glyph when 'Monochrome tray icon' is on."""
        if self.cfg.get("tray_mono", False):
            from .branding import DATA
            mono = QIcon(str(DATA / "pylogi-tray.svg"))
            if not mono.isNull():
                return mono
        from .branding import DATA
        big = QIcon(str(DATA / "pylogi-tray-color.svg"))        # same icon with a larger G, easier to see at 24 px
        if not big.isNull():
            return big
        icon = QApplication.windowIcon()
        return icon if not icon.isNull() else QIcon(theme.device_icon("mouse", 64))

    def set_tray_icon(self) -> None:
        self.tray.setIcon(self._tray_icon())

    def _restore_geometry(self) -> None:
        """Size and position of the last session, only when 'Remember window size and position' is on."""
        if not self.cfg.get("remember_window", False):
            return
        from PySide6.QtCore import QByteArray
        data = self.cfg.get("window_geometry", "")
        if isinstance(data, str) and data:
            self.restoreGeometry(QByteArray.fromBase64(data.encode("ascii")))

    def _save_geometry(self) -> None:
        if self.cfg.get("remember_window", False) and self.isVisible() and not self.isMaximized():
            self.cfg.set("window_geometry", bytes(self.saveGeometry().toBase64()).decode("ascii"))

    def remote(self, message: str) -> None:
        """A later launch of pyLogi (or 'pylogi --toggle') talking to this instance."""
        self.toggle_visible() if message == "toggle" else self.bring_to_front()

    def _build_tray(self) -> None:
        self._tray_menu = QMenu()
        self._tray_sig = None
        self._fill_tray()
        self._tray_menu.aboutToShow.connect(self._fill_tray)      # profiles / hosts may have changed
        self.tray.setContextMenu(self._tray_menu)
        if not getattr(self, "_tray_connected", False):
            self.tray.activated.connect(self._tray_activated)
            self._tray_connected = True
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _tray_activated(self, reason) -> None:
        """Left/double click on the tray icon toggles the window between shown and hidden."""
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick, QSystemTrayIcon.MiddleClick):
            self.toggle_visible()

    def bring_to_front(self) -> None:
        """Show the window whatever its state (used when pyLogi is launched a second time)."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_visible(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self._save_geometry()
            self.hide()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def _fill_tray(self) -> None:
        """Devices with something to switch first, then the fixed entries."""
        from . import tray_menu
        menu = self._tray_menu
        menu.clear()
        if tray_menu.fill(menu, self.manager.devices, lambda m: self.manager._err(f"tray: {m}")):
            menu.addSeparator()
        showing = self.isVisible() and not self.isMinimized()
        show = QAction(tr("Hide") if showing else tr("Show"), menu)
        show.triggered.connect(self.toggle_visible)
        report = QAction(tr("Save compatibility report…"), menu)
        report.triggered.connect(self.save_report)
        about = QAction(tr("About pyLogi"), menu)
        about.triggered.connect(lambda: (self.show(), self.show_about("about")))
        quit_ = QAction(tr("Quit"), menu)
        quit_.triggered.connect(QApplication.quit)
        for act in (show, report, about, quit_):
            menu.addAction(act)

    def save_report(self) -> None:
        """Write the read-only compatibility report (no serial numbers) where the user chooses."""
        from PySide6.QtWidgets import QFileDialog
        from ..core import report
        f, _ = QFileDialog.getSaveFileName(self, tr("Save compatibility report…"), "pylogi-report.json", "JSON (*.json)")
        if f:
            Path(f).write_text(report.to_text(report.build(self.manager.devices)) + "\n", encoding="utf-8")

    def _build_ui(self) -> None:
        """(Re)build every translated widget; called again on a language change."""
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        v.addWidget(self.stack)
        self.home = HomePage(self.images, self.cfg)
        self.page = DevicePage(self.cfg, self.images, self.settings)
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.page)
        self.home.open_device.connect(self._open)
        self.home.rescan_requested.connect(self.manager.rescan)
        self.home.remove_requested.connect(self.remove_device)
        self.home.help_requested.connect(self.show_help)
        self.home.about_requested.connect(self.show_about)
        self.home.picture_requested.connect(self.choose_picture)
        self.home.settings_requested.connect(lambda: SettingsDialog(self).exec())
        self.home.add_requested.connect(lambda: AddDialog(self).exec())
        self.home.receivers_requested.connect(lambda: ReceiversDialog(self).exec())
        self.page.history = getattr(self.manager, "history", None)
        self.page.back.connect(self._home)
        self.page.settings_requested.connect(lambda: SettingsDialog(self).exec())
        self.page.remove_requested.connect(self.remove_device)

    def show_help(self, section: str | None = None) -> None:
        HelpDialog(self, section if isinstance(section, str) else None).exec()

    def show_about(self, tab: str = "about") -> None:
        AboutDialog(self, tab).exec()

    def remove_device(self, dev: Device) -> None:
        if RemoveDialog(self, dev).exec():
            self.manager.rescan()                  # forces a re-read: an unpaired slot disappears
            self._home()

    def choose_picture(self, dev: Device, reset: bool) -> None:
        """Use a picture of your own for a device (or go back to the catalog photo)."""
        pictures = dict(self.cfg.get("pictures", {}))
        key = dev.device_key()
        if reset:
            pictures.pop(key, None)
        else:
            f, _ = QFileDialog.getOpenFileName(self, tr("Choose picture…"), "",
                                               "Images (*.png *.jpg *.jpeg *.webp *.svg)")
            if not f:
                return
            from ..core.config import config_dir
            import shutil
            folder = config_dir() / "pictures"
            folder.mkdir(exist_ok=True)
            safe = "".join(c if c.isalnum() else "_" for c in key)
            dest = folder / f"{safe}{Path(f).suffix.lower()}"
            shutil.copyfile(f, dest)
            pictures[key] = str(dest)
        self.cfg.set("pictures", pictures)
        if self.images:
            self.images.custom = pictures
            self.images._pix.clear()
        self.refresh_home()

    def first_run(self, allow_download: bool = True) -> None:
        """Ask once whether product photos may be downloaded (nothing is fetched before the answer)."""
        if self.images:
            self.images.custom = dict(self.cfg.get("pictures", {}))
        if not allow_download or not self.images or self.cfg.get("device_images") is not None:
            return
        QTimer.singleShot(400, self._ask_photos)

    def _ask_photos(self) -> None:
        yes = QMessageBox.question(
            self, tr("Product photos"),
            tr("pyLogi can show a photo of each device. The photos are downloaded from the OpenLogi asset "
               "mirror (assets.openlogi.org) and kept on this computer; only the files of your devices' "
               "models are requested and nothing about you is sent. Download them?")) == QMessageBox.Yes
        self.cfg.set("device_images", yes)
        if self.images:
            self.images.enabled = yes
        self.refresh_home()

    def reload_after_import(self) -> None:
        """Apply an imported configuration: language, theme, photos, saved device settings."""
        set_language(self.cfg.get("language", "auto"))
        theme.apply(QApplication.instance(), self.cfg.get("appearance", "system"), self.cfg.get("theme", "default"))
        if self.images:
            self.images.enabled = self.cfg.get("device_images") is True
            self.images.custom = dict(self.cfg.get("pictures", {}))
        QTimer.singleShot(0, self._retranslate)

    # -- language / images
    def set_ui_language(self, code: str) -> None:
        self.cfg.set("language", code)
        set_language(code)
        QTimer.singleShot(0, self._retranslate)

    def set_appearance(self, appearance: str, theme_id: str) -> None:
        """Change light/dark/system and the named theme; the UI is rebuilt with the new colours."""
        self.cfg.set("appearance", appearance)
        self.cfg.set("theme", theme_id)
        theme.apply(QApplication.instance(), appearance, theme_id)
        QTimer.singleShot(0, self._retranslate)

    def _app_state(self, state) -> None:
        if hasattr(self.manager, "set_active"):
            self.manager.set_active(state == Qt.ApplicationState.ApplicationActive)

    def _scheme_changed(self, *_a) -> None:
        """The OS switched between light and dark: follow it when the appearance is 'system'."""
        if self.cfg.get("appearance", "system") == "system":
            theme.apply(QApplication.instance(), "system", self.cfg.get("theme", "default"))
            QTimer.singleShot(0, self._retranslate)

    def _retranslate(self) -> None:
        cur = self.current
        self.centralWidget().deleteLater()
        self._build_ui()
        self._build_tray()
        self._refresh()
        if cur is not None:
            self._open(cur)

    def refresh_home(self) -> None:
        for d in self.manager.devices:             # settings files are named after their device
            self.cfg.name_device(d.device_key(), d.name)
            self.cfg.name_device(d.uid, d.name)
        self.home.populate(self.manager.devices, self.manager.errors)

    def _images_ready(self) -> None:
        if self.current is not None:
            self.page.images_ready()
        else:
            self.refresh_home()

    # -- model updates
    def _refresh(self) -> None:
        devs = self.manager.devices
        if self.images:                       # warm the button-map assets before the page is opened
            for d in devs:
                if d.online and d.has("buttons"):
                    self.images.buttons_layout(d)
        if self.stack.currentWidget() is self.home or self.current is None:
            self.refresh_home()
        elif all(d.uid != self.current.uid for d in devs):
            self._home()      # the open device vanished
        sig = tuple(sorted((d.uid, d.online) for d in devs))
        if sig != getattr(self, "_tray_sig", None):           # a device came or went: rebuild the tray entries
            self._tray_sig = sig
            self._fill_tray()
        self._battery_alerts(devs)
        self._connection_alerts(devs)

    def _battery_alerts(self, devs) -> None:
        """One notification when a device reaches the low level and another at the critical level."""
        if not self.cfg.get("battery_alert", True):
            return
        limit = int(self.cfg.get("battery_alert_pct", 15))
        crit = min(int(self.cfg.get("battery_alert_critical_pct", 5)), limit)
        for d in devs:
            if not d.battery or d.battery.percent is None:
                continue
            pct = d.battery.percent
            if d.battery.charging:                                  # plugged in: warn again next time it drains
                self._warned.discard(d.uid)
                self._critical.discard(d.uid)
                continue
            if pct <= crit and d.uid not in self._critical:
                self._critical.add(d.uid)
                self._warned.add(d.uid)
                self.tray.showMessage(tr("Battery critically low"), f"{d.name}: {pct}%",
                                      QSystemTrayIcon.Critical)
            elif pct <= limit and d.uid not in self._warned:
                self._warned.add(d.uid)
                self.tray.showMessage(tr("Low battery"), f"{d.name}: {pct}%")
            if pct > min(100, limit + 10):                          # recovered: re-arm both
                self._warned.discard(d.uid)
                self._critical.discard(d.uid)
            elif pct > crit + 5:
                self._critical.discard(d.uid)

    def _connection_alerts(self, devs) -> None:
        """Notify when a device goes offline or comes back (off by default; the first scan is silent)."""
        now = {d.uid: bool(d.online) for d in devs if d.kind != "receiver"}
        before, self._online = self._online, now
        if before is None or not self.cfg.get("notify_connection", False):
            return
        for d in devs:
            if d.uid in before and d.uid in now and before[d.uid] != now[d.uid]:
                self.tray.showMessage(tr("Device connected") if now[d.uid] else tr("Device disconnected"), d.name)

    def _open(self, dev: Device) -> None:
        self.current = dev
        self.page.show_device(dev)
        self.stack.setCurrentWidget(self.page)

    def _home(self) -> None:
        self.current = None
        self.stack.setCurrentWidget(self.home)
        self.refresh_home()

    def closeEvent(self, e):
        self._save_geometry()
        if self.tray.isVisible() and self.cfg.get("close_to_tray", False):
            e.ignore()
            self.hide()
        else:
            self.manager.stop()
            e.accept()
