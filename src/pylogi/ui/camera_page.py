# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Webcam page: live preview + camera controls with presets."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout,
                               QInputDialog, QLabel, QMenu, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QSlider,
                               QVBoxLayout, QWidget)

from ..core.camera import (AUTO_OF, BUILTIN, IMAGE, LABELS, LENS, auto_is_on,  # noqa: F401
                           auto_value, builtin_preset, find_roles, norm, plan_writes)
from ..core.config import Config
from ..core.webcam import CamControl
from ..i18n import N_, tr
from . import theme
from .widgets import attempt, debounced, muted
from .widgets import icon_card as _card

# -------------------------------------------------------------- preview
class Preview(QWidget):
    """Live picture of the camera; only holds the device while visible."""

    def __init__(self, dev):
        super().__init__()
        self.dev = dev
        self.image: QImage | None = None
        self.message = ""
        self.cam = None
        self.setMinimumSize(300, 190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def start(self) -> None:
        if self.cam is not None or not getattr(self.dev, "previewable", True):
            return
        if QApplication.platformName() == "offscreen":
            return
        try:
            from PySide6.QtMultimedia import (QCamera, QMediaCaptureSession,
                                              QMediaDevices, QVideoSink)
        except ImportError:
            self.message = tr("No preview available")
            return
        node = getattr(self.dev, "path", "").encode()
        name = self.dev.name.lower()
        pick = None
        for d in QMediaDevices.videoInputs():
            if (node and bytes(d.id()) == node) or name in d.description().lower() \
                    or d.description().lower() in name:
                pick = d
                break
        if pick is None:
            self.message = tr("No preview available")
            self.update()
            return
        self.cam = QCamera(pick, self)
        self.session = QMediaCaptureSession(self)
        self.session.setCamera(self.cam)
        self.sink = QVideoSink(self)
        self.session.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self._frame)
        self.cam.errorOccurred.connect(self._error)
        self.cam.start()

    def stop(self) -> None:
        if self.cam is not None:
            self.cam.stop()
            self.cam.deleteLater()
            self.session.deleteLater()
            self.sink.deleteLater()
            self.cam = None
        self.image = None
        self.update()

    def _frame(self, frame) -> None:
        img = frame.toImage()
        if not img.isNull():
            self.image = img
            self.update()

    def _error(self, *_a) -> None:
        self.message = tr("The camera is in use by another application.")
        self.stop()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor("#0b0b0c"))
        p.drawRoundedRect(r, 8, 8)
        if self.image is not None:
            s = self.image.size().scaled(int(r.width()) - 6, int(r.height()) - 6, Qt.KeepAspectRatio)
            target = QRectF(r.center().x() - s.width() / 2, r.center().y() - s.height() / 2,
                            s.width(), s.height())
            p.drawImage(target, self.image)
        elif self.message:
            p.setPen(QColor(theme.MUTED))
            p.drawText(r, Qt.AlignCenter | Qt.TextWordWrap, self.message)


# ---------------------------------------------------------------- rows
def _chip(text: str, checkable: bool = True) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("chipbtn")
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    return b


class Row(QWidget):
    """One control: label, slider + value (+ Auto chip)  /  chips for menus and toggles."""

    LABEL_W, VALUE_W, CHIP_W = 124, 52, 48

    def __init__(self, page: "CameraPage", role: str, c: CamControl, auto: CamControl | None):
        super().__init__()
        self.page, self.c, self.auto, self.role = page, c, auto, role
        self.slider: QSlider | None = None
        self.value: QLabel | None = None
        self.auto_chip: QPushButton | None = None
        self.chips: dict[int, QPushButton] = {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        label = QLabel(tr(LABELS.get(role, c.name)))
        label.setObjectName("muted")
        label.setStyleSheet("font-size:14px;")
        label.setFixedWidth(self.LABEL_W)
        label.setWordWrap(True)
        label.setToolTip(tr(LABELS.get(role, c.name)))
        lay.addWidget(label)
        self.binary = c.type == 2 or (c.type == 1 and c.maximum - c.minimum == 1)
        if c.type == 1 and not self.binary:
            self._slider(lay)
        elif self.binary:      # toggle (booleans and 0/1 integers)
            lay.addStretch(1)
            self.toggle = _chip("")
            self.toggle.toggled.connect(self._toggled)
            lay.addWidget(self.toggle)
        else:                  # menu -> chips
            lay.addStretch(1)
            for key, name in c.menu.items():
                if role == "flicker" and key == 0:
                    continue
                b = _chip(tr(name))
                b.clicked.connect(lambda _=False, k=key: self._pick(k))
                self.chips[key] = b
                lay.addWidget(b)
        if auto is not None:
            self.auto_chip = _chip(tr("Auto"))
            self.auto_chip.setFixedWidth(self.CHIP_W)
            self.auto_chip.toggled.connect(self._auto_toggled)
            lay.addWidget(self.auto_chip)
        elif self.slider is not None:
            lay.addSpacing(self.CHIP_W + 8)
        self.sync(c.value, None if auto is None else auto.value)

    def _slider(self, lay) -> None:
        c = self.c
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(c.minimum, c.maximum)
        self.slider.setSingleStep(c.step)
        self.slider.setPageStep(c.step * 10)
        self.value = QLabel()
        self.value.setObjectName("value")
        self.value.setFixedWidth(self.VALUE_W)
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.slider.valueChanged.connect(lambda v: self.value.setText(self._fmt(v)))
        debounced(self.slider, self._write)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.value)

    def _fmt(self, v: int) -> str:
        if self.role in ("pan", "tilt") and self.c.step % 3600 == 0:
            return f"{v // 3600}°"          # V4L2 pan/tilt are in arc seconds
        return str(v)

    # -- device writes
    def _write(self, v: int) -> None:
        v -= (v - self.c.minimum) % self.c.step
        attempt(self.page, self.page.dev.set_cam_control, self.c.id, v)

    def _toggled(self, on: bool) -> None:
        self.toggle.setText(tr("Enabled") if on else tr("Disabled"))
        if not self._syncing:
            attempt(self.page, self.page.dev.set_cam_control, self.c.id, self.c.minimum + int(on))

    def _pick(self, key: int) -> None:
        for k, b in self.chips.items():
            b.setChecked(k == key)
        attempt(self.page, self.page.dev.set_cam_control, self.c.id, key)

    def _auto_toggled(self, on: bool) -> None:
        if self._syncing:
            return
        if attempt(self.page, self.page.dev.set_cam_control, self.auto.id, auto_value(self.auto, on)):
            self.page.reload()          # drivers change dependent values when auto flips

    # -- state from the device
    _syncing = False

    def sync(self, value: int, auto_value_: int | None) -> None:
        self._syncing = True
        try:
            on = auto_value_ is not None and auto_is_on(self.auto, auto_value_)
            if self.slider is not None:
                self.slider.blockSignals(True)
                self.slider.setValue(value)
                self.slider.blockSignals(False)
                self.value.setText(self._fmt(value))
            elif self.binary:
                on_ = value != self.c.minimum
                self.toggle.setChecked(on_)
                self.toggle.setText(tr("Enabled") if on_ else tr("Disabled"))
            else:
                for k, b in self.chips.items():
                    b.setChecked(k == value)
            if self.auto_chip is not None:
                self.auto_chip.setChecked(on)
            active = self.c.writable and not on
            for w in (self.slider, self.value):
                if w is not None:
                    w.setEnabled(active)
        finally:
            self._syncing = False


# ----------------------------------------------------------------- page
class CameraPage(QWidget):
    def __init__(self, dev, cfg: Config):
        super().__init__()
        self.dev, self.cfg = dev, cfg
        self.rows: dict[str, Row] = {}
        self.controls: list[CamControl] = []
        self.roles: dict[str, CamControl] = {}
        root = QHBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(16)

        left, ll = _card("eye", N_("Camera"))
        self.preview = Preview(dev)
        ll.addWidget(self.preview)
        ll.addStretch(1)
        root.addWidget(left, 1, Qt.AlignTop)

        right, rl = _card("gear", N_("Camera controls"))
        right.setMinimumWidth(340)                          # flexible: 340..500 px, so narrow windows still fit
        right.setMaximumWidth(500)
        self.chips_row = QHBoxLayout()
        self.chips_row.setSpacing(6)
        rl.addLayout(self.chips_row)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self.inner = QWidget()
        self.form = QVBoxLayout(self.inner)
        self.form.setContentsMargins(0, 0, 6, 0)
        self.form.setSpacing(9)
        self.scroll.setWidget(self.inner)
        rl.addWidget(self.scroll, 1)
        reset = QPushButton(tr("Reset to defaults"))
        reset.clicked.connect(lambda: self.apply_preset("Default"))
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(reset)
        rl.addLayout(foot)
        root.addWidget(right, 2)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._build()

    # -- construction
    def _build(self) -> None:
        self.controls = self.dev.cam_controls()
        self.roles = find_roles(self.controls)
        while self.form.count():
            it = self.form.takeAt(0)
            if it.widget():
                it.widget().hide()
                it.widget().deleteLater()
        self.rows.clear()
        if not self.controls:
            self.form.addWidget(muted(tr("Not supported by this device")))
            return
        used: set[int] = set()
        for r in AUTO_OF.values():
            if r in self.roles:
                used.add(self.roles[r].id)
        lens = [r for r in LENS if r in self.roles]
        image = [r for r in IMAGE if r in self.roles]
        extras = [c for c in self.controls if c.id not in used and c not in self.roles.values()]
        for title, roles, more in ((N_("Lens"), lens, []), (N_("Image"), image, extras)):
            if not roles and not more:
                continue
            h = QLabel(tr(title))
            h.setObjectName("section")
            self.form.addWidget(h)
            for r in roles:
                self._add(r, self.roles[r], self.roles.get(AUTO_OF.get(r, "")))
            for c in more:
                self._add(f"x{c.id}", c, None)
        self.form.addStretch(1)
        self._presets()

    def _add(self, role: str, c: CamControl, auto) -> None:
        row = Row(self, role, c, auto)
        self.rows[role] = row
        self.form.addWidget(row)

    def reload(self) -> None:
        """Re-read the device and refresh every row (after auto-mode or preset changes)."""
        self.controls = self.dev.cam_controls()
        by_id = {c.id: c for c in self.controls}
        for row in self.rows.values():
            c = by_id.get(row.c.id)
            if c is None:
                continue
            row.c = c
            if row.auto is not None and row.auto.id in by_id:
                row.auto = by_id[row.auto.id]
            row.sync(c.value, None if row.auto is None else row.auto.value)

    # -- presets
    def _key(self) -> str:
        return f"cam:{self.dev.uid}"

    def _custom(self) -> dict:
        self.cfg.name_device(self.dev.uid, self.dev.name)      # its settings file is named after the camera
        return self.cfg.get(self._key(), {})

    def _presets(self) -> None:
        while self.chips_row.count():
            it = self.chips_row.takeAt(0)
            if it.widget():
                it.widget().hide()
                it.widget().deleteLater()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        current = self.cfg.get(f"campreset:{self.dev.uid}", "Default")
        for name in [*(str(b) for b in BUILTIN), *self._custom()]:
            custom = name not in BUILTIN
            b = _chip(name if custom else tr(name))
            b.setChecked(name == current)
            b.clicked.connect(lambda _=False, n=name: self.apply_preset(n))
            if custom:
                b.setContextMenuPolicy(Qt.CustomContextMenu)
                b.customContextMenuRequested.connect(lambda pos, n=name, w=b: self._chip_menu(n, w, pos))
            self.group.addButton(b)
            self.chips_row.addWidget(b)
        new = _chip("+ " + tr("New"), checkable=False)
        new.clicked.connect(self._new_preset)
        self.chips_row.addWidget(new)
        self.chips_row.addStretch(1)

    def apply_preset(self, name: str) -> None:
        values = builtin_preset(name, self.controls) if name in BUILTIN else self._custom().get(name, {})
        failed = []
        for c, v in plan_writes(values, self.controls):
            try:
                self.dev.set_cam_control(c.id, int(v))
            except OSError as e:
                failed.append(f"{tr(c.name)}: {e.strerror or e}")
        if failed:
            QMessageBox.warning(self, tr("Error"), "\n".join(failed))
        self.cfg.set(f"campreset:{self.dev.uid}", name)
        self.reload()
        self._presets()

    def _new_preset(self) -> None:
        name, ok = QInputDialog.getText(self, tr("New preset"), tr("Name"))
        name = name.strip()
        if not ok or not name or name in BUILTIN:
            return
        presets = self._custom()
        presets[name] = {c.name: c.value for c in self.dev.cam_controls() if c.writable}
        self.cfg.set(self._key(), presets)
        self.cfg.set(f"campreset:{self.dev.uid}", name)
        self._presets()

    def _chip_menu(self, name: str, widget: QWidget, pos: QPoint) -> None:
        m = QMenu(self)
        m.addAction(tr("Delete preset"), lambda: self._delete(name))
        m.exec(widget.mapToGlobal(pos))

    def _delete(self, name: str) -> None:
        presets = self._custom()
        presets.pop(name, None)
        self.cfg.set(self._key(), presets)
        if self.cfg.get(f"campreset:{self.dev.uid}") == name:
            self.cfg.set(f"campreset:{self.dev.uid}", "Default")
        self._presets()

    # -- the camera is only opened while this page is visible
    def showEvent(self, e):
        super().showEvent(e)
        self.preview.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.preview.stop()
