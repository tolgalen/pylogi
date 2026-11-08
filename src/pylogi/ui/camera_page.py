# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Webcam page: live preview + camera controls with presets."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QDialog, QFrame,
                               QHBoxLayout, QInputDialog, QLabel, QMenu, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QSlider,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import camera_rules, debuglog
from ..core.camera import (AUTO_OF, BUILTIN, IMAGE, LABELS, LENS, apply_values,  # noqa: F401
                           auto_is_on, auto_value, builtin_preset, find_roles, mode_text, norm,
                           plan_writes, preset_names, resolve_preset, snapshot)
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
        self.grid = self.mirror = self.histogram = False          # viewing aids; they never touch the camera
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

    @staticmethod
    def luminance_histogram(img: QImage, bins: int = 64) -> list[float]:
        """Share of pixels per brightness band (0..1), from a small copy of the picture."""
        small = img.scaled(64, 36, Qt.IgnoreAspectRatio, Qt.FastTransformation).convertToFormat(QImage.Format_Grayscale8)
        counts = [0] * bins
        for y in range(small.height()):
            line = small.constScanLine(y)
            for x in range(small.width()):
                counts[line[x] * bins // 256] += 1
        top = max(counts) or 1
        return [c / top for c in counts]

    def _draw_histogram(self, p: QPainter, img: QImage, target: QRectF) -> None:
        h = self.luminance_histogram(img)
        w, ht = 150.0, 48.0
        box = QRectF(target.left() + 8, target.bottom() - ht - 8, w, ht)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 140))
        p.drawRect(box)
        p.setBrush(QColor(255, 255, 255, 190))
        bar = w / len(h)
        for i, v in enumerate(h):
            p.drawRect(QRectF(box.left() + i * bar, box.bottom() - v * (ht - 4), max(bar - 0.5, 0.5), v * (ht - 4)))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor("#0b0b0c"))
        p.drawRoundedRect(r, 8, 8)
        if self.image is not None:
            img = self.image.mirrored(True, False) if self.mirror else self.image
            s = img.size().scaled(int(r.width()) - 6, int(r.height()) - 6, Qt.KeepAspectRatio)
            target = QRectF(r.center().x() - s.width() / 2, r.center().y() - s.height() / 2,
                            s.width(), s.height())
            p.drawImage(target, img)
            if self.grid:                                     # rule of thirds
                p.setPen(QPen(QColor(255, 255, 255, 110), 1))
                for i in (1, 2):
                    x = target.left() + target.width() * i / 3
                    y = target.top() + target.height() * i / 3
                    p.drawLine(int(x), int(target.top()), int(x), int(target.bottom()))
                    p.drawLine(int(target.left()), int(y), int(target.right()), int(y))
            if self.histogram:
                self._draw_histogram(p, img, target)
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
        if c.is_button:        # an action, such as "Pan/Tilt Reset"
            lay.addStretch(1)
            run = _chip(tr("Run"), checkable=False)
            run.clicked.connect(self._run)
            lay.addWidget(run)
        elif c.type == 1 and not self.binary:
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
        if attempt(self.page, self.page.dev.set_cam_control, self.c.id, v):
            self.page.remember()

    def _toggled(self, on: bool) -> None:
        self.toggle.setText(tr("Enabled") if on else tr("Disabled"))
        if not self._syncing and attempt(self.page, self.page.dev.set_cam_control, self.c.id, self.c.minimum + int(on)):
            self.page.remember()

    def _pick(self, key: int) -> None:
        for k, b in self.chips.items():
            b.setChecked(k == key)
        if attempt(self.page, self.page.dev.set_cam_control, self.c.id, key):
            self.page.remember()

    def _run(self) -> None:
        if attempt(self.page, self.page.dev.set_cam_control, self.c.id, 1):
            self.page.reload()
            self.page.remember()

    def _auto_toggled(self, on: bool) -> None:
        if self._syncing:
            return
        if attempt(self.page, self.page.dev.set_cam_control, self.auto.id, auto_value(self.auto, on)):
            self.page.reload()          # drivers change dependent values when auto flips
            self.page.remember()

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
class FormatRow(QWidget):
    """Pixel format, size and frame rate of the camera (V4L2).  Applying stops the preview: a stream in use cannot change."""

    def __init__(self, page: "CameraPage", modes):
        super().__init__()
        self.page, self.modes = page, modes
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.fmt, self.size, self.fps = QComboBox(), QComboBox(), QComboBox()
        for fmt in dict.fromkeys(m.fmt for m in modes):
            self.fmt.addItem(fmt, fmt)
        for w in (self.fmt, self.size, self.fps):
            lay.addWidget(w, 1)
        self.apply = QPushButton(tr("Apply"))
        self.apply.clicked.connect(self._apply)
        lay.addWidget(self.apply)
        self.fmt.currentIndexChanged.connect(self._fill_sizes)
        self.size.currentIndexChanged.connect(self._fill_rates)
        cur = page.current_mode()
        if cur:
            self.fmt.setCurrentIndex(max(0, self.fmt.findData(cur[0])))
        self._fill_sizes()
        if cur:
            self.size.setCurrentIndex(max(0, self.size.findData((cur[1], cur[2]))))
            self.fps.setCurrentIndex(max(0, self.fps.findData(cur[3])))

    def _fill_sizes(self) -> None:
        self.size.blockSignals(True)
        self.size.clear()
        sizes = sorted({(m.width, m.height) for m in self.modes if m.fmt == self.fmt.currentData()},
                       key=lambda s: -s[0] * s[1])
        for w, h in sizes:
            self.size.addItem(f"{w}×{h}", (w, h))
        self.size.blockSignals(False)
        self._fill_rates()

    def _fill_rates(self) -> None:
        self.fps.clear()
        size = self.size.currentData()
        for m in self.modes:
            if size and m.fmt == self.fmt.currentData() and (m.width, m.height) == tuple(size):
                for r in m.fps:
                    self.fps.addItem(f"{r} fps", r)

    def _apply(self) -> None:
        size = self.size.currentData()
        if not size:
            return
        self.page.set_mode((self.fmt.currentData(), size[0], size[1], self.fps.currentData() or 0))


class AppsDialog(QDialog):
    """Presets that switch on by themselves while an application has the focus."""

    def __init__(self, page: "CameraPage"):
        super().__init__(page)
        self.page = page
        self.setWindowTitle(tr("Presets by application"))
        self.setMinimumSize(420, 300)
        lay = QVBoxLayout(self)
        lay.addWidget(muted(tr("The preset is applied while the application has the focus and the previous one comes back afterwards. "
                               "Applications are named by their window class, for example zoom or firefox.")))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([tr("Application"), tr("Preset")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        add = QPushButton("+ " + tr("Add application"))
        add.clicked.connect(self._add)
        rem = QPushButton(tr("Remove"))
        rem.clicked.connect(self._remove)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.accept)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self._load()

    def _load(self) -> None:
        rules = camera_rules.rules(self.page.cfg, self.page.dev)
        self.table.setRowCount(len(rules))
        for i, (app, preset) in enumerate(sorted(rules.items())):
            self.table.setItem(i, 0, QTableWidgetItem(app))
            self.table.setItem(i, 1, QTableWidgetItem(tr(preset) if preset in BUILTIN else preset))
            self.table.item(i, 1).setData(Qt.UserRole, preset)

    def _add(self) -> None:
        app, ok = QInputDialog.getText(self, tr("Add application"), tr("Window class of the application"))
        app = app.strip().lower()
        if not ok or not app:
            return
        names = preset_names(self.page.cfg, self.page.dev.uid)
        shown = [tr(n) if n in BUILTIN else n for n in names]
        choice, ok = QInputDialog.getItem(self, tr("Preset"), tr("Preset to apply"), shown, 0, False)
        if ok:
            camera_rules.set_rule(self.page.cfg, self.page.dev, app, names[shown.index(choice)])
            self._load()

    def _remove(self) -> None:
        for idx in self.table.selectionModel().selectedRows():
            camera_rules.set_rule(self.page.cfg, self.page.dev, self.table.item(idx.row(), 0).text(), None)
        self._load()


class CameraPage(QWidget):
    def __init__(self, dev, cfg: Config, settings=None):
        super().__init__()
        self.dev, self.cfg, self.settings = dev, cfg, settings
        self._migrate_keys()
        self.rows: dict[str, Row] = {}
        self.controls: list[CamControl] = []
        self.roles: dict[str, CamControl] = {}
        root = QHBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(16)

        left, ll = _card("eye", N_("Camera"))
        self.preview = Preview(dev)
        ll.addWidget(self.preview)
        aids = QHBoxLayout()
        aids.setSpacing(6)
        saved = cfg.get("campreview", {})
        for attr, label in (("grid", N_("Grid")), ("mirror", N_("Mirror")), ("histogram", N_("Histogram"))):
            b = _chip(tr(label))
            on = bool(saved.get(attr, False))
            b.setChecked(on)
            setattr(self.preview, attr, on)
            b.toggled.connect(lambda v, a=attr: self._aid(a, v))
            aids.addWidget(b)
        aids.addStretch(1)
        ll.addLayout(aids)
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
        self.auto_btn = QPushButton(tr("Auto-adjust and lock"))
        self.auto_btn.setToolTip(tr("Lets the camera choose exposure, focus and white balance for a moment, then keeps those values."))
        self.auto_btn.clicked.connect(self.auto_adjust)
        apps = QPushButton(tr("Presets by application…"))
        apps.clicked.connect(lambda: AppsDialog(self).exec())
        foot = QHBoxLayout()                       # two rows: three buttons do not fit side by side in a narrow window
        foot.addWidget(self.auto_btn)
        foot.addStretch(1)
        foot.addWidget(reset)
        rl.addLayout(foot)
        foot2 = QHBoxLayout()
        foot2.addWidget(apps)
        foot2.addStretch(1)
        rl.addLayout(foot2)
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
        modes = self._modes()
        if modes:
            h = QLabel(tr("Video format"))
            h.setObjectName("section")
            self.form.addWidget(h)
            self.format_row = FormatRow(self, modes)
            self.form.addWidget(self.format_row)
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

    # -- stored state
    def _migrate_keys(self) -> None:
        """Presets saved under the old /dev/videoN-based identifier move to the stable one."""
        old = getattr(self.dev, "legacy_uid", "")
        if not old or old == self.dev.uid:
            return
        for prefix in ("cam", "campreset", "camrules", "camover"):
            if self.cfg.get(f"{prefix}:{self.dev.uid}") is None:
                val = self.cfg.pop(f"{prefix}:{old}")
                if val is not None:
                    self.cfg.set(f"{prefix}:{self.dev.uid}", val)

    def remember(self) -> None:
        """Keep what the camera is set to, so it is written again when the camera is plugged in."""
        if self.settings is not None:
            try:
                self.settings.remember_camera(self.dev)
            except Exception as e:                      # a failed read must not break the page
                debuglog.log("error", f"camera: remembering the settings: {e!r}")

    def _aid(self, attr: str, on: bool) -> None:
        setattr(self.preview, attr, on)
        saved = dict(self.cfg.get("campreview", {}))
        saved[attr] = on
        self.cfg.set("campreview", saved)
        self.preview.update()

    # -- video format
    def _modes(self):
        try:
            return self.dev.video_modes()
        except (OSError, AttributeError):
            return []

    def current_mode(self):
        try:
            return self.dev.video_mode()
        except (OSError, AttributeError):
            return None

    def set_mode(self, mode) -> None:
        running = self.preview.cam is not None
        if running:
            self.preview.stop()                  # a stream in use cannot change its format
        try:
            ok = attempt(self, self.dev.set_video_mode, *mode)
        finally:
            if running:
                self.preview.start()
        if ok and self.settings is not None:
            self.settings.remember_camera_mode(self.dev, mode)

    # -- presets
    def _key(self) -> str:
        return f"cam:{self.dev.uid}"

    def _custom(self) -> dict:
        self.cfg.name_device(self.dev.uid, self.dev.name)      # its settings file is named after the camera
        return self.cfg.get(self._key(), {})

    def _overrides(self) -> dict:
        return self.cfg.get(f"camover:{self.dev.uid}", {})

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
            mine = name in self._overrides()
            b = _chip(name if custom else tr(name) + (" *" if mine else ""))
            b.setChecked(name == current)
            b.clicked.connect(lambda _=False, n=name: self.apply_preset(n))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos, n=name, w=b: self._chip_menu(n, w, pos))
            self.group.addButton(b)
            self.chips_row.addWidget(b)
        new = _chip("+ " + tr("New"), checkable=False)
        new.clicked.connect(self._new_preset)
        self.chips_row.addWidget(new)
        self.chips_row.addStretch(1)

    def apply_preset(self, name: str) -> None:
        values = resolve_preset(self.cfg, self.dev.uid, name, self.controls)
        failed = apply_values(self.dev, values)
        if failed:
            QMessageBox.warning(self, tr("Error"), "\n".join(failed))
        self.cfg.set(f"campreset:{self.dev.uid}", name)
        self.reload()
        self.remember()
        self._presets()

    def _new_preset(self) -> None:
        name, ok = QInputDialog.getText(self, tr("New preset"), tr("Name"))
        name = name.strip()
        if not ok or not name or name in BUILTIN:
            return
        presets = self._custom()
        presets[name] = snapshot(self.dev.cam_controls())
        self.cfg.set(self._key(), presets)
        self.cfg.set(f"campreset:{self.dev.uid}", name)
        self._presets()

    def _chip_menu(self, name: str, widget: QWidget, pos: QPoint) -> None:
        m = QMenu(self)
        if name in BUILTIN:
            m.addAction(tr("Customise with the current values"), lambda: self._update(name))
            if name in self._overrides():
                m.addAction(tr("Restore the original"), lambda: self._restore(name))
        else:
            m.addAction(tr("Update with the current values"), lambda: self._update(name))
            m.addAction(tr("Rename…"), lambda: self._rename(name))
            m.addAction(tr("Delete preset"), lambda: self._delete(name))
        m.exec(widget.mapToGlobal(pos))

    def _update(self, name: str) -> None:
        values = snapshot(self.dev.cam_controls())
        if name in BUILTIN:
            over = dict(self._overrides())
            over[name] = values
            self.cfg.set(f"camover:{self.dev.uid}", over)
        else:
            presets = self._custom()
            presets[name] = values
            self.cfg.set(self._key(), presets)
        self._presets()

    def _restore(self, name: str) -> None:
        over = dict(self._overrides())
        over.pop(name, None)
        self.cfg.set(f"camover:{self.dev.uid}", over)
        self._presets()

    def _rename(self, name: str) -> None:
        new, ok = QInputDialog.getText(self, tr("Rename…"), tr("Name"), text=name)
        new = new.strip()
        presets = self._custom()
        if not ok or not new or new == name or new in BUILTIN or new in presets:
            return
        presets = {(new if k == name else k): v for k, v in presets.items()}      # keeps the order
        self.cfg.set(self._key(), presets)
        if self.cfg.get(f"campreset:{self.dev.uid}") == name:
            self.cfg.set(f"campreset:{self.dev.uid}", new)
        rules = camera_rules.rules(self.cfg, self.dev)
        for app, preset in rules.items():
            if preset == name:
                camera_rules.set_rule(self.cfg, self.dev, app, new)
        self._presets()

    def _delete(self, name: str) -> None:
        presets = self._custom()
        presets.pop(name, None)
        self.cfg.set(self._key(), presets)
        if self.cfg.get(f"campreset:{self.dev.uid}") == name:
            self.cfg.set(f"campreset:{self.dev.uid}", "Default")
        self._presets()

    # -- let the camera choose, then keep its choice
    def auto_adjust(self) -> None:
        autos = {a: self.roles[a] for a in AUTO_OF.values() if a in self.roles}
        if not autos:
            QMessageBox.information(self, tr("Camera"), tr("Not supported by this device"))
            return
        failed = apply_values(self.dev, {c.name: auto_value(c, True) for c in autos.values()})
        if failed:
            QMessageBox.warning(self, tr("Error"), "\n".join(failed))
            return
        self.reload()
        self.auto_btn.setEnabled(False)
        QTimer.singleShot(self.SETTLE_MS, self._lock_auto)

    SETTLE_MS = 1800

    def _lock_auto(self) -> None:
        self.auto_btn.setEnabled(True)
        controls = self.dev.cam_controls()
        roles = find_roles(controls)
        values = {roles[a].name: auto_value(roles[a], False) for a in AUTO_OF.values() if a in roles}
        values.update({roles[m].name: roles[m].value for m, a in AUTO_OF.items() if m in roles and a in roles})
        failed = apply_values(self.dev, values)
        if failed:
            QMessageBox.warning(self, tr("Error"), "\n".join(failed))
        self.reload()
        self.remember()

    # -- the camera is only opened while this page is visible
    def showEvent(self, e):
        super().showEvent(e)
        self.preview.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.preview.stop()
