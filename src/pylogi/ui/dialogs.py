# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Settings and Add-device dialogs."""
from __future__ import annotations

import subprocess

from PySide6.QtWidgets import (QComboBox, QDialog, QGridLayout, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton, QScrollArea, QSpinBox, QTabWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ..i18n import N_, available_languages, tr
from . import icons, theme
from .cards import ReceiverCard
from .widgets import Switch, muted


class SettingsDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle(tr("Settings"))
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(14)
        title = QLabel(tr("Settings"))
        title.setObjectName("title")
        lay.addWidget(title)
        outer = lay
        tabs = QTabWidget()
        outer.addWidget(tabs)
        pages = {}
        for key, label in (("general", N_("General")), ("startup", N_("Start-up and tray")),
                           ("notify", N_("Notifications")), ("advanced", N_("Advanced"))):
            page = QWidget()
            pl = QVBoxLayout(page)
            pl.setContentsMargins(4, 14, 4, 6)
            pl.setSpacing(12)
            tabs.addTab(page, tr(label))
            pages[key] = pl
        lay = pages["general"]

        grid = QGridLayout()
        grid.setVerticalSpacing(12)
        grid.addWidget(QLabel(tr("Language")), 0, 0)
        self.lang = QComboBox()
        self.lang.addItem(tr("Automatic"), "auto")
        for code, name in available_languages().items():
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(main.cfg.get("language", "auto"))))
        self.lang.activated.connect(self._language)
        grid.addWidget(self.lang, 0, 1)

        grid.addWidget(QLabel(tr("Appearance")), 1, 0)
        self.appearance = QComboBox()
        for key, label in (("system", N_("System")), ("light", N_("Light")), ("dark", N_("Dark"))):
            self.appearance.addItem(tr(label), key)
        self.appearance.setCurrentIndex(max(0, self.appearance.findData(main.cfg.get("appearance", "system"))))
        self.appearance.activated.connect(self._look)
        grid.addWidget(self.appearance, 1, 1)

        grid.addWidget(QLabel(tr("Theme")), 2, 0)
        self.theme = QComboBox()
        for tid, name in theme.available_themes().items():
            self.theme.addItem(tr(name), tid)
        self.theme.setCurrentIndex(max(0, self.theme.findData(main.cfg.get("theme", "default"))))
        self.theme.activated.connect(self._look)
        grid.addWidget(self.theme, 2, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        self.photos = Switch(tr("Show product photos"))
        self.photos.setChecked(bool(main.cfg.get("device_images", True)) and
                               bool(main.images and main.images.enabled))
        self.photos.toggled.connect(self._photos)
        lay.addWidget(self.photos)
        lay.addWidget(muted(tr("Photos are downloaded from the OpenLogi asset mirror and cached locally.")))
        lay = pages["startup"]
        self.autostart = Switch(tr("Start pyLogi with the desktop session"))
        from ..core import desktop
        self.autostart.setChecked(desktop.autostart_enabled())
        self.autostart.toggled.connect(desktop.set_autostart)
        lay.addWidget(self.autostart)
        self.service = None
        if desktop.service_available():
            self.service = Switch(tr("Run in the background as a service (systemd)"))
            self.service.setChecked(desktop.service_enabled())
            self.service.toggled.connect(self._service)
            lay.addWidget(self.service)
        self.launcher = Switch(tr("Show pyLogi in the application menu"))
        self.launcher.setChecked(desktop.launcher_installed())
        self.launcher.toggled.connect(desktop.set_launcher)
        lay.addWidget(self.launcher)
        if desktop.gnome_available():
            self.gnome = Switch(tr("GNOME helper extension (per-application profiles on Wayland)"))
            self.gnome.setChecked(desktop.gnome_extension_installed())
            self.gnome.toggled.connect(desktop.set_gnome_extension)
            lay.addWidget(self.gnome)
            lay.addWidget(muted(tr("After installing it, log out and in again so GNOME Shell loads it.")))
        self.tray = Switch(tr("Keep running in the tray when closed"))
        self.tray.setChecked(bool(main.cfg.get("close_to_tray", False)))
        self.tray.toggled.connect(lambda v: main.cfg.set("close_to_tray", v))
        lay.addWidget(self.tray)
        self.hidden = Switch(tr("Start hidden in the tray"))
        self.hidden.setChecked(bool(main.cfg.get("start_hidden", False)))
        self.hidden.toggled.connect(lambda v: main.cfg.set("start_hidden", v))
        lay.addWidget(self.hidden)
        self.splash = Switch(tr("Show the start-up screen"))
        self.splash.setChecked(bool(main.cfg.get("show_splash", True)))
        self.splash.toggled.connect(lambda v: main.cfg.set("show_splash", v))
        lay.addWidget(self.splash)
        self.mono = Switch(tr("Monochrome tray icon"))
        self.mono.setChecked(bool(main.cfg.get("tray_mono", False)))
        self.mono.toggled.connect(lambda v: (main.cfg.set("tray_mono", v), main.set_tray_icon()))
        lay.addWidget(self.mono)
        lay.addStretch(1)
        lay = pages["general"]
        self.remember = Switch(tr("Remember window size and position"))
        self.remember.setChecked(bool(main.cfg.get("remember_window", False)))
        self.remember.toggled.connect(lambda v: main.cfg.set("remember_window", v))
        lay.addWidget(self.remember)
        lay.addStretch(1)

        lay = pages["notify"]
        self.batt = Switch(tr("Notify when the battery is low"))
        self.batt.setChecked(bool(main.cfg.get("battery_alert", True)))
        lay.addWidget(self.batt)
        brow = QHBoxLayout()
        brow.addWidget(QLabel(tr("Battery level for the alert")))
        self.batt_pct = QSpinBox()
        self.batt_pct.setRange(5, 50)
        self.batt_pct.setSingleStep(5)
        self.batt_pct.setSuffix(" %")
        self.batt_pct.setValue(int(main.cfg.get("battery_alert_pct", 15)))
        self.batt_pct.setEnabled(self.batt.isChecked())
        self.batt_pct.valueChanged.connect(lambda v: main.cfg.set("battery_alert_pct", v))
        self.batt.toggled.connect(lambda v: (main.cfg.set("battery_alert", v), self.batt_pct.setEnabled(v)))
        brow.addStretch(1)
        brow.addWidget(self.batt_pct)
        lay.addLayout(brow)
        crow = QHBoxLayout()
        crow.addWidget(QLabel(tr("Battery level for the urgent alert")))
        self.batt_crit = QSpinBox()
        self.batt_crit.setRange(1, 25)
        self.batt_crit.setSuffix(" %")
        self.batt_crit.setValue(int(main.cfg.get("battery_alert_critical_pct", 5)))
        self.batt_crit.setEnabled(self.batt.isChecked())
        self.batt_crit.valueChanged.connect(lambda v: main.cfg.set("battery_alert_critical_pct", v))
        self.batt.toggled.connect(self.batt_crit.setEnabled)
        crow.addStretch(1)
        crow.addWidget(self.batt_crit)
        lay.addLayout(crow)
        self.conn = Switch(tr("Notify when a device connects or disconnects"))
        self.conn.setChecked(bool(main.cfg.get("notify_connection", False)))
        self.conn.toggled.connect(lambda v: main.cfg.set("notify_connection", v))
        lay.addWidget(self.conn)

        lay.addStretch(1)
        lay = pages["advanced"]
        self.debug = Switch(tr("Record a debug log"))
        self.debug.setChecked(bool(main.cfg.get("debug_log", False)))
        lay.addWidget(self.debug)
        self.debug_raw = Switch(tr("Include the raw HID++ traffic (large)"))
        self.debug_raw.setChecked(bool(main.cfg.get("debug_log_raw", False)))
        self.debug_raw.setEnabled(self.debug.isChecked())
        lay.addWidget(self.debug_raw)
        drow = QHBoxLayout()
        self.debug_info = muted("")
        drow.addWidget(self.debug_info, 1)
        self.debug_open = QPushButton(tr("Open the log folder"))
        self.debug_open.clicked.connect(self._open_log_folder)
        drow.addWidget(self.debug_open)
        lay.addLayout(drow)
        self.debug.toggled.connect(self._debug_changed)
        self.debug_raw.toggled.connect(self._debug_changed)
        self._debug_info()

        io = QHBoxLayout()
        exp = QPushButton(tr("Export settings…"))
        exp.clicked.connect(self._export)
        imp = QPushButton(tr("Import settings…"))
        imp.clicked.connect(self._import)
        io.addWidget(exp)
        io.addWidget(imp)
        lay.addLayout(io)

        from ..core import removal
        hidden = len(removal.hidden_keys(main.cfg))
        if hidden:
            hrow = QHBoxLayout()
            hrow.addWidget(QLabel(tr("Hidden devices") + f": {hidden}"))
            show = QPushButton(tr("Show all"))
            show.clicked.connect(lambda: (removal.unhide_all(main.cfg), main.refresh_home(), self.accept()))
            hrow.addStretch(1)
            hrow.addWidget(show)
            lay.addLayout(hrow)
        lay.addStretch(1)

        lay = outer
        row = QHBoxLayout()
        help_btn = QPushButton(tr("Help"))
        help_btn.clicked.connect(lambda: (self.accept(), main.show_help("settings")))
        about_btn = QPushButton(tr("About pyLogi"))
        about_btn.clicked.connect(lambda: (self.accept(), main.show_about("about")))
        row.addWidget(help_btn)
        row.addWidget(about_btn)
        row.addStretch(1)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self._tooltips(exp, imp)

    def _service(self, on: bool) -> None:
        from ..core import desktop
        try:
            desktop.set_service(on)
        except (OSError, subprocess.SubprocessError) as e:
            self.service.blockSignals(True)
            self.service.setChecked(not on)
            self.service.blockSignals(False)
            QMessageBox.warning(self, tr("Error"), str(e))
            return
        if on:                                              # the service replaces the autostart entry
            self.autostart.blockSignals(True)
            self.autostart.setChecked(False)
            self.autostart.blockSignals(False)

    def _tooltips(self, exp, imp) -> None:
        """Explain every setting on hover."""
        tips = [
            (self.lang, tr("Language of the interface. Automatic follows the system language.")),
            (self.appearance, tr("Light or dark look. System follows the operating system and changes with it.")),
            (self.theme, tr("Colour theme of the application. Some themes also change the accent colour.")),
            (self.photos, tr("Download product photos for your devices from the OpenLogi asset mirror and keep them in a local cache. Off = drawn icons only.")),
            (self.autostart, tr("Start pyLogi, minimised to the tray, when you log in to the desktop.")),
            (self.launcher, tr("Add or remove the pyLogi entry and icon in your application menu. No administrator rights needed.")),
            (self.tray, tr("Closing the window keeps pyLogi running in the tray so software actions and application profiles keep working.")),
            (self.hidden, tr("Start without showing a window or the start-up screen. Use the tray icon to open it.")),
            (self.splash, tr("Show the logo screen while the devices are detected. Off = the window just appears when ready.")),
            (self.mono, tr("Use a single-colour tray icon that blends in with the panel.")),
            (self.remember, tr("Reopen the window with the size and position it had. Off = it always opens at 1200×800.")),
            (self.batt, tr("Show a notification when a device battery falls to the chosen level.")),
            (self.batt_pct, tr("Battery level that triggers the first low-battery notification.")),
            (self.batt_crit, tr("Battery level that triggers the second, urgent notification.")),
            (self.conn, tr("Show a notification each time a device goes offline or comes back.")),
            (self.debug, tr("Write a debug log in the configuration folder to attach to a bug report. Serial numbers and your home folder are masked.")),
            (self.debug_raw, tr("Also record every HID++ report in a separate file. The file can become large.")),
            (self.debug_open, tr("Open the folder that contains the log files.")),
            (exp, tr("Save the whole configuration to a file, for example to move it to another computer.")),
            (imp, tr("Load a configuration file saved earlier with Export.")),
        ]
        if hasattr(self, "gnome"):
            tips.append((self.gnome, tr("Install or remove the GNOME Shell extension that tells pyLogi which application has the focus. Needed for per-application profiles on Wayland; log out and in after installing.")))
        if self.service is not None:
            tips.append((self.service, tr("Start pyLogi as a systemd user service with the graphical session, minimised to the tray. "
                                          "Diverted buttons, macros and application profiles keep working with the window closed, "
                                          "and pyLogi is restarted if it stops. It replaces 'Start with the desktop session'.")))
        for w, text in tips:
            w.setToolTip(text)

    def _debug_changed(self, _v: bool = False) -> None:
        """Start / stop the debug log at once and remember the choice for the next start."""
        from ..core import debuglog
        cfg = self.main.cfg
        cfg.set("debug_log", self.debug.isChecked())
        cfg.set("debug_log_raw", self.debug_raw.isChecked())
        self.debug_raw.setEnabled(self.debug.isChecked())
        if self.debug.isChecked():
            debuglog.LOG.enable(raw=self.debug_raw.isChecked())
            debuglog.LOG.devices(self.main.manager.devices, "when the log was switched on")
        else:
            debuglog.LOG.disable()
        self._debug_info()

    def _debug_info(self) -> None:
        from ..core import debuglog
        from ..core.config import config_dir
        path = debuglog.LOG.path if debuglog.LOG.enabled else config_dir() / "debug.log"
        text = tr("Recording to {path}") if debuglog.LOG.enabled else tr("Log file: {path}")
        self.debug_info.setText(text.format(path=str(path).replace(str(__import__("pathlib").Path.home()), "~")))

    def _open_log_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from ..core.config import config_dir
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(config_dir())))

    def _language(self, _i: int) -> None:
        self.main.set_ui_language(self.lang.currentData())
        self.reject()    # the main window is rebuilt; reopen to see the dialog translated

    def _export(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        from ..core.config import export_config
        from pathlib import Path
        f, _ = QFileDialog.getSaveFileName(self, tr("Export settings…"), "pylogi-settings.json", "JSON (*.json)")
        if f:
            export_config(self.main.cfg, Path(f))

    def _import(self) -> None:
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from ..core.config import import_config
        from pathlib import Path
        f, _ = QFileDialog.getOpenFileName(self, tr("Import settings…"), "", "JSON (*.json)")
        if not f:
            return
        try:
            import_config(self.main.cfg, Path(f))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, tr("Error"), str(e))
            return
        self.main.reload_after_import()
        self.accept()

    def _look(self, _i: int) -> None:
        self.main.set_appearance(self.appearance.currentData(), self.theme.currentData())

    def _photos(self, on: bool) -> None:
        self.main.cfg.set("device_images", on)
        if self.main.images:
            self.main.images.enabled = on
        self.main.refresh_home()


class ReceiversDialog(QDialog):
    """Every detected receiver with its details and the devices connected to it, as a tree."""

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle(tr("Receivers"))
        self.resize(640, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(12)
        title = QLabel(tr("Receivers"))
        title.setObjectName("title")
        lay.addWidget(title)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels([tr("Item"), tr("Value")])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setStretchLastSection(True)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        refresh = QPushButton(tr("Refresh"))
        refresh.clicked.connect(self._fill)
        expand = QPushButton(tr("Expand all"))
        expand.clicked.connect(self.tree.expandAll)
        collapse = QPushButton(tr("Collapse all"))
        collapse.clicked.connect(self.tree.collapseAll)
        for b in (refresh, expand, collapse):
            row.addWidget(b)
        row.addStretch(1)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self._fill()
        self.tree.expandToDepth(1)
        self.tree.resizeColumnToContents(0)

    @staticmethod
    def _node(parent, name: str, value: str = "") -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [name, value])
        return item

    def _fill(self) -> None:
        self.tree.clear()
        devices = list(self.main.manager.devices)
        receivers = [d for d in devices if d.kind == "receiver"]
        if not receivers:
            self._node(self.tree, tr("No receivers found"))
            return
        for r in receivers:
            kids = [d for d in devices if getattr(d, "receiver", None) is r]
            node = self._node(self.tree, r.name, (tr("1 device") if len(kids) == 1 else tr("{n} devices").format(n=len(kids))))
            node.setIcon(0, icons.icon("plug", theme.ICON, 16))
            rx = r.rx
            pid = (r.model_ids or [""])[0]
            for label, value in ((tr("Family"), rx.family),
                                 (tr("Product ID"), f"0x{pid.upper()}" if pid else "—"),
                                 (tr("Serial"), r.info.serial or "—"),
                                 (tr("Port"), str(r.uid).removesuffix(":rx")),
                                 (tr("Device slots"), str(rx.max_devices))):
                self._node(node, label, value)
            group = self._node(node, tr("Connected devices"), str(len(kids)))
            for d in sorted(kids, key=lambda x: x.slot):
                state = tr("Online") if d.online else tr("Offline")
                dn = self._node(group, f"{d.slot}: {d.name}", state)
                self._device_rows(dn, d)
            listed = {d.slot for d in kids}
            for text in r.slot_names:            # paired slots the receiver reports but that have no device object
                num = text.split(":", 1)[0]
                if num.isdigit() and int(num) not in listed:
                    self._node(group, text, tr("Offline"))

    def _device_rows(self, node, d) -> None:
        i = d.info
        rows = [(tr("Type"), tr(d.kind) if d.kind else "—"),
                (tr("Connection"), d.connection),
                (tr("Wireless product ID"), ", ".join(f"0x{m.upper()}" for m in d.model_ids) or "—")]
        if d.battery is not None and d.battery.percent is not None:
            rows.append((tr("Battery"), f"{d.battery.percent} %" + (" ⚡" if d.battery.charging else "")))
        for label, value in ((tr("Serial"), i.serial), (tr("Unit ID"), i.unit), (tr("Firmware"), i.firmware),
                             (tr("HID++ protocol"), i.protocol)):
            if value:
                rows.append((label, value))
        for k, v in i.extra.items():
            if k != "Features" and v:
                rows.append((tr(k), v))
        for label, value in rows:
            self._node(node, label, value)


class AddDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.setWindowTitle(tr("Add device"))
        self.resize(560, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(12)
        title = QLabel(tr("Add device"))
        title.setObjectName("title")
        lay.addWidget(title)
        lay.addWidget(muted(tr("Bluetooth devices are paired from the Bluetooth settings of the system.")))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        receivers = [d for d in main.manager.devices if d.kind == "receiver"]
        for r in receivers:
            v.addWidget(ReceiverCard(r))
        if not receivers:
            v.addWidget(muted(tr("No receivers found")))
        v.addStretch(1)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)
        row = QHBoxLayout()
        rescan = QPushButton(tr("Rescan"))
        rescan.clicked.connect(main.manager.rescan)
        row.addWidget(rescan)
        bt = QPushButton(tr("Bluetooth settings…"))
        bt.clicked.connect(self._bluetooth)
        row.addWidget(bt)
        row.addStretch(1)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

    def _bluetooth(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        if not open_bluetooth_settings():
            QMessageBox.information(self, tr("Add device"), tr("Open your system's Bluetooth settings to pair the device."))


def open_bluetooth_settings() -> bool:
    """Open the operating system's Bluetooth settings (Bluetooth pairing is done there)."""
    import os
    import shutil
    import subprocess
    import sys
    if sys.platform.startswith("win"):
        os.startfile("ms-settings:bluetooth")                      # type: ignore[attr-defined]
        return True
    if sys.platform == "darwin":
        subprocess.Popen(["open", "x-apple.systempreferences:com.apple.BluetoothSettings"])
        return True
    for cmd in (["gnome-control-center", "bluetooth"], ["kcmshell6", "kcm_bluetooth"],
                ["blueman-manager"], ["blueberry"]):
        if shutil.which(cmd[0]):
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return True
    return False


class RemoveDialog(QDialog):
    """Choose how to remove a device; destructive choices need an explicit confirmation."""

    def __init__(self, main, dev):
        super().__init__(main)
        from PySide6.QtWidgets import QButtonGroup, QRadioButton
        from ..core import removal
        self.main, self.dev, self.removal = main, dev, removal
        self.setWindowTitle(tr("Remove device"))
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(12)
        title = QLabel(f"{tr('Remove device')}: {dev.name}")
        title.setObjectName("title")
        title.setWordWrap(True)
        lay.addWidget(title)
        self.options = removal.options(dev)
        self.group = QButtonGroup(self)
        for i, o in enumerate(self.options):
            rb = QRadioButton(tr(o.label))
            rb.setChecked(o.key == self.removal.FORGET if len(self.options) > 2 else i == 0)
            self.group.addButton(rb, i)
            lay.addWidget(rb)
            lay.addWidget(muted(tr(o.detail)))
        self.confirm = Switch("")
        self.confirm.toggled.connect(self._update)
        lay.addWidget(self.confirm)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton(tr("Cancel"))
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton(tr("Remove"))
        self.ok.setObjectName("primary")
        self.ok.clicked.connect(self._go)
        row.addWidget(cancel)
        row.addWidget(self.ok)
        lay.addLayout(row)
        self.group.idClicked.connect(lambda _i: self._update())
        self._update()

    def _selected(self):
        return self.options[max(0, self.group.checkedId())]

    def _update(self) -> None:
        o = self._selected()
        self.confirm.setText(tr(o.confirm))
        self.confirm.setVisible(o.destructive)
        self.ok.setEnabled(not o.destructive or self.confirm.isChecked())

    def _go(self) -> None:
        o = self._selected()
        try:
            self.removal.apply(self.dev, o.key, self.main.cfg, self.main.manager, getattr(self.main, "settings", None))
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, tr("Error"), str(e))
            return
        self.accept()
