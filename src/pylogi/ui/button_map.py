# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Button editor: product photo with a callout per button + inspector panel.

Hotspot positions come from the product catalog (see core.images); devices
without that metadata fall back to a plain column of button cards.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget
)

from ..core import macros
from ..core.actions import ACTION_TYPES, GESTURE_DIRECTIONS, GESTURE_THRESHOLD, KEYCODES, MEDIA, NEEDS_DIVERT
from ..core.types import Control
from ..i18n import N_, tr
from . import icons, theme
from .widgets import Switch, attempt, muted

CARD_W, CARD_H, MIN_GAP = 168, 56, 10
_ICON_BY_CID = {0x52: "mouse", 0x50: "mouse", 0x51: "mouse", 0x53: "left", 0x56: "right",
                0xC4: "gauge", 0xD7: "gauge"}


def place_cards(slots: list[dict], img: QRectF, area: QRectF) -> dict[int, QRectF]:
    """Callout rectangles keyed by slot index: left/right of the photo, never overlapping."""
    rects: dict[int, QRectF] = {}
    for side in (-1, 1):
        group = sorted((i for i, s in enumerate(slots) if s["side"] == side),
                       key=lambda i: slots[i]["y"])
        if side < 0:
            x = max(area.left(), img.left() - CARD_W + img.width() * 0.05)
        else:
            x = min(area.right() - CARD_W, img.right() - img.width() * 0.05)
        prev_bottom = area.top()
        for i in group:
            y = img.top() + img.height() * slots[i]["y"] / 100 - CARD_H / 2
            y = max(y, prev_bottom + MIN_GAP, area.top())
            rects[i] = QRectF(x, y, CARD_W, CARD_H)
            prev_bottom = y + CARD_H
        overflow = prev_bottom - area.bottom()
        if overflow > 0 and group:             # shift the whole column up if it spills out
            for i in group:
                rects[i].translate(0, -overflow)
    return rects


class ButtonCard(QFrame):
    """Callout: small button name over the current action."""
    clicked = Signal(int)

    def __init__(self, cid: int, parent=None):
        super().__init__(parent)
        self.cid = cid
        self.setObjectName("btncard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(CARD_W, CARD_H)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 6, 10, 6)
        lay.setSpacing(1)
        self.title = QLabel()
        self.title.setObjectName("muted")
        self.title.setStyleSheet("font-size:11px;")
        row = QHBoxLayout()
        row.setSpacing(6)
        self.icon = QLabel()
        self.action = QLabel()
        self.action.setStyleSheet("font-size:14px;font-weight:500;")
        self.action.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        chev = QLabel()
        chev.setPixmap(icons.glyph("chevron", theme.MUTED, 12))
        row.addWidget(self.icon)
        row.addWidget(self.action, 1)
        row.addWidget(chev)
        lay.addWidget(self.title)
        lay.addLayout(row)

    def set_content(self, title: str, action: str, glyph: str, enabled: bool = True) -> None:
        self.title.setText(title)
        self._full = action
        self.action.setText(self.action.fontMetrics().elidedText(action, Qt.ElideRight, CARD_W - 74))
        self.action.setToolTip(action)
        self.icon.setPixmap(icons.glyph(glyph, theme.ICON, 16))
        self.setEnabled(enabled)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.cid)


class ButtonMap(QWidget):
    """Photo + hotspots + callout cards; emits selected(cid)."""
    selected = Signal(int)

    def __init__(self, controls: list[Control], photo, slots: list[dict] | None, summary=None):
        super().__init__()
        self.summary = summary          # callable(cid) -> (text, glyph) describing the current action
        self.controls = {c.cid: c for c in controls}
        self.photo = photo
        self.slots = [s for s in (slots or []) if s["cid"] in self.controls] if photo and slots else []
        self.cards: dict[int, ButtonCard] = {}
        self.current: int | None = None
        self._img = QRectF()
        self._rects: dict[int, QRectF] = {}
        self.setMinimumHeight(420)
        if self.slots:
            for s in self.slots:
                self._make_card(s["cid"])
        else:
            self.photo = None
            for c in controls:
                self._make_card(c.cid)
        self.refresh()

    def _make_card(self, cid: int) -> None:
        if cid in self.cards:
            return
        card = ButtonCard(cid, self)
        card.clicked.connect(self._select)
        self.cards[cid] = card

    # -- content
    def action_name(self, c: Control) -> str:
        target = self.controls.get(c.remap) if c.remap else None
        return tr(target.name if target else c.name)

    def refresh(self) -> None:
        for cid, card in self.cards.items():
            c = self.controls[cid]
            if self.summary:
                text, glyph = self.summary(cid)
            else:
                text, glyph = self.action_name(c), _ICON_BY_CID.get(c.remap or cid, "click")
            card.set_content(tr(c.name), text, glyph, c.reprogrammable)
            card.set_selected(cid == self.current)
        self.update()

    def _select(self, cid: int) -> None:
        self.current = cid
        self.refresh()
        self.selected.emit(cid)

    # -- geometry
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()

    def _relayout(self) -> None:
        area = QRectF(0, 0, self.width(), self.height()).adjusted(8, 8, -8, -8)
        if self.photo is None:         # fallback: one column of cards
            x = area.center().x() - CARD_W / 2
            for i, card in enumerate(self.cards.values()):
                card.move(int(x), int(area.top() + i * (CARD_H + MIN_GAP)))
            self.setMinimumHeight(int(len(self.cards) * (CARD_H + MIN_GAP)) + 20)
            return
        aspect = self.photo.width() / self.photo.height()
        inner_w = area.width() - 2 * (CARD_W - 0.05 * area.height() * aspect)
        h = min(area.height(), max(120.0, inner_w / aspect))
        w = h * aspect
        self._img = QRectF(area.center().x() - w / 2, area.top() + (area.height() - h) / 2, w, h)
        self._rects = place_cards(self.slots, self._img, area)
        for i, s in enumerate(self.slots):
            r = self._rects[i]
            self.cards[s["cid"]].move(int(r.x()), int(r.y()))
        self.update()

    def _dot(self, s: dict) -> QPointF:
        return QPointF(self._img.left() + self._img.width() * s["x"] / 100,
                       self._img.top() + self._img.height() * s["y"] / 100)

    def paintEvent(self, _):
        if self.photo is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(self._img.toRect(), self.photo)
        for i, s in enumerate(self.slots):
            dot, r = self._dot(s), self._rects.get(i)
            if r is None:
                continue
            sel = s["cid"] == self.current
            p.setPen(QPen(QColor(theme.ACCENT_HI if sel else theme.LINE), 1.4 if sel else 1))
            edge = QPointF(r.right() if s["side"] < 0 else r.left(), r.center().y())
            bend = QPointF(edge.x() + (14 if s["side"] < 0 else -14), dot.y())
            p.drawPolyline([dot, bend, edge])
            p.setBrush(QColor(theme.BG))
            p.setPen(QPen(QColor(theme.ACCENT_HI if sel else theme.ICON_DIM), 1.6))
            p.drawEllipse(dot, 5.5, 5.5)

    def mouseReleaseEvent(self, e):
        for s in self.slots:                       # dots are clickable too
            d = self._dot(s)
            if (e.position() - d).manhattanLength() < 12:
                self._select(s["cid"])
                return


class ScriptEdit(QPlainTextEdit):
    """Multi-line macro editor: `editingFinished` fires when it loses focus."""
    editingFinished = Signal()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.editingFinished.emit()


class Inspector(QWidget):
    """Right panel: choose what the selected button does (device remap or a software action)."""

    KEYS = list(KEYCODES)
    MOUSE_LABELS = {"left": N_("Left click"), "right": N_("Right click"), "middle": N_("Middle click"),
                    "back": N_("Back"), "forward": N_("Forward")}
    SCROLL_LABELS = {"up": N_("Scroll up"), "down": N_("Scroll down"), "left": N_("Scroll left"),
                     "right": N_("Scroll right")}
    MODS = [("ctrl", "Ctrl"), ("shift", "Shift"), ("alt", "Alt"), ("meta", "Win")]

    def __init__(self, dev, controls: list[Control], on_change, settings=None, engine=None, profile: str = ""):
        super().__init__()
        self.dev, self.controls, self.on_change = dev, {c.cid: c for c in controls}, on_change
        self.settings, self.engine, self.profile = settings, engine, profile
        self.cid: int | None = None
        self._loading = False
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(24, 22, 24, 20)
        self.lay.setSpacing(12)
        self.show_none()

    # -- helpers
    @staticmethod
    def _wipe(lay) -> None:
        """Remove (recursively) every widget and nested layout of a layout."""
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
            elif it.layout() is not None:
                Inspector._wipe(it.layout())

    def _reset(self) -> None:
        self._wipe(self.lay)
        t = QLabel(tr("Button inspector"))
        t.setObjectName("title")
        t.setStyleSheet("font-size:20px;")
        self.lay.addWidget(t)

    def show_none(self) -> None:
        self.cid = None
        self._reset()
        self.lay.addWidget(muted(tr("Select a button on the device to change its function.")))
        self.lay.addStretch(1)

    def _current(self) -> dict | None:
        if not self.settings:
            return None
        return self.settings.raw(self.dev, self.profile).get(self.cid)

    # -- editor
    def show_control(self, cid: int) -> None:
        c = self.controls[cid]
        self.cid = cid
        self._reset()
        name = QLabel(tr(c.name))
        name.setObjectName("h2")
        self.lay.addWidget(name)
        if not c.reprogrammable:
            self.lay.addWidget(muted(tr("This button cannot be reassigned.")))
            self.lay.addStretch(1)
            return
        cur = self._current()
        kinds = [("inherit", N_("Same as Default profile"))] if self.profile else []
        kinds += [(k, N_("Original function") if (k == "default" and self.profile) else l)
                  for k, l in ACTION_TYPES]
        self.lay.addWidget(QLabel(tr("Action")))
        self.type_box = QComboBox()
        for k, label in kinds:
            self.type_box.addItem(tr(label), k)
        current_kind = (cur or {}).get("type", "inherit" if self.profile else "default")
        self.type_box.setCurrentIndex(max(0, self.type_box.findData(current_kind)))
        self.type_box.activated.connect(lambda _i: self._type_changed())
        self.lay.addWidget(self.type_box)
        self.detail = QVBoxLayout()
        self.detail.setSpacing(8)
        self.lay.addLayout(self.detail)
        self.note = muted("")
        self.lay.addWidget(self.note)
        self.lay.addStretch(1)
        self._build_detail(current_kind, cur or {})

    def _type_changed(self) -> None:
        self._build_detail(self.type_box.currentData(), {})
        self._commit()

    def _clear_detail(self) -> None:
        self._wipe(self.detail)

    def _combo(self, items: list[tuple[str, object]], current) -> QComboBox:
        box = QComboBox()
        for label, data in items:
            box.addItem(tr(label), data)
        box.setCurrentIndex(max(0, box.findData(current)))
        box.activated.connect(lambda _i: self._commit())
        self.detail.addWidget(box)
        return box

    def _script_edit(self, text: str, placeholder: str, lines: int = 5) -> ScriptEdit:
        edit = ScriptEdit()
        edit.setPlainText(text)
        edit.setPlaceholderText(placeholder)
        edit.setTabChangesFocus(True)
        edit.setFixedHeight(edit.fontMetrics().lineSpacing() * lines + 18)
        edit.editingFinished.connect(self._commit)
        self.detail.addWidget(edit)
        return edit

    def _script_error(self, scripts: list[str]) -> str:
        for s in scripts:
            try:
                macros.parse(s)
            except macros.MacroError as e:
                return tr("Line {n}: {msg}").format(n=e.line, msg=e.message)
        return ""

    def _build_detail(self, kind: str, b: dict) -> None:
        self._clear_detail()
        self._widgets: dict[str, object] = {}
        w = self._widgets
        if kind == "remap":
            w["cid"] = self._combo([(o.name, o.cid) for o in self.controls.values() if o.cid != self.cid],
                                   b.get("cid"))
            self.note.setText(tr("Remaps last until the device is switched off."))
        elif kind == "key":
            grid = QGridLayout()
            grid.setHorizontalSpacing(18)
            w["mods"] = {}
            for i, (m, label) in enumerate(self.MODS):
                sw = Switch(label)
                sw.setChecked(m in b.get("mods", []))
                sw.toggled.connect(lambda _v: self._commit())
                grid.addWidget(sw, i // 2, i % 2)
                w["mods"][m] = sw
            self.detail.addLayout(grid)
            w["key"] = self._combo([(k, k) for k in self.KEYS], b.get("key", "A"))
            self.note.setText(tr("The shortcut is sent while pyLogi is running."))
        elif kind == "media":
            w["name"] = self._combo([(n, n) for n in MEDIA], b.get("name", "Play/Pause"))
        elif kind == "mouse":
            w["button"] = self._combo([(l, k) for k, l in self.MOUSE_LABELS.items()], b.get("button", "left"))
        elif kind == "scroll":
            w["dir"] = self._combo([(l, k) for k, l in self.SCROLL_LABELS.items()], b.get("dir", "down"))
        elif kind in ("text", "command"):
            edit = QLineEdit(b.get(kind if kind == "text" else "cmd", ""))
            edit.setPlaceholderText(tr("Text to type") if kind == "text" else tr("Command (no shell)"))
            edit.editingFinished.connect(self._commit)
            self.detail.addWidget(edit)
            w["edit"] = edit
            self.note.setText(tr("Typed with a US keyboard layout; punctuation may differ on other layouts.")
                              if kind == "text" else tr("Runs a program with its arguments, without a shell."))
        elif kind == "dpi":
            w["mode"] = self._combo([("Next DPI", "next"), ("Previous DPI", "prev")], b.get("mode", "next"))
        elif kind == "macro":
            w["script"] = self._script_edit(b.get("script", ""), tr("One step per line, e.g.:") + "\nkey ctrl+shift+T\nwait 200\ntext hello")
            self.note.setText(tr("Steps: key, text, media, click, scroll, run, wait. A number and x repeat a step (3x scroll down)."))
        elif kind == "gesture":
            self.note.setText(tr("Hold the button and move the mouse: each direction runs a macro. Leave empty to do nothing."))
            for d, label in (("click", N_("Click (no movement)")), ("up", N_("Up")), ("down", N_("Down")),
                             ("left", N_("Left")), ("right", N_("Right"))):
                self.detail.addWidget(QLabel(tr(label)))
                w[d] = self._script_edit(b.get(d, ""), "key ctrl+Tab", lines=2)
            row = QHBoxLayout()
            row.addWidget(QLabel(tr("Movement needed (counts)")))
            sp = QSpinBox()
            sp.setRange(10, 500)
            sp.setValue(int(b.get("threshold", GESTURE_THRESHOLD)))
            sp.editingFinished.connect(self._commit)
            row.addWidget(sp)
            self.detail.addLayout(row)
            w["threshold"] = sp
        elif kind == "none":
            self.note.setText(tr("The button does nothing."))
        elif kind == "default":
            self.note.setText(tr("The button keeps its original function."))
        else:
            self.note.setText("")
        if kind in NEEDS_DIVERT and self.engine is not None and not self.engine.injector.available:
            self.note.setText(self.note.text() + "\n" + tr("Software actions are not available:") + " " +
                              self.engine.injector.reason)

    _INVALID: dict = {"type": "invalid"}          # a script with an error: shown to the user, never saved

    def _read(self) -> dict | None:
        kind = self.type_box.currentData()
        w = self._widgets
        if kind == "inherit":
            return None
        if kind in ("default", "none"):
            return {"type": kind}
        if kind == "remap":
            return {"type": "remap", "cid": w["cid"].currentData() or 0}
        if kind == "key":
            return {"type": "key", "mods": [m for m, sw in w["mods"].items() if sw.isChecked()],
                    "key": w["key"].currentData()}
        if kind == "media":
            return {"type": "media", "name": w["name"].currentData()}
        if kind == "mouse":
            return {"type": "mouse", "button": w["button"].currentData()}
        if kind == "scroll":
            return {"type": "scroll", "dir": w["dir"].currentData()}
        if kind == "text":
            return {"type": "text", "text": w["edit"].text()}
        if kind == "command":
            return {"type": "command", "cmd": w["edit"].text()}
        if kind == "dpi":
            return {"type": "dpi", "mode": w["mode"].currentData()}
        if kind == "macro":
            script = w["script"].toPlainText()
            err = self._script_error([script])
            if err:
                self.note.setText(err)
                return self._INVALID
            return {"type": "macro", "script": script}
        if kind == "gesture":
            scripts = {d: w[d].toPlainText() for d in ("click", *GESTURE_DIRECTIONS)}
            err = self._script_error(list(scripts.values()))
            if err:
                self.note.setText(err)
                return self._INVALID
            return {"type": "gesture", "threshold": w["threshold"].value(), **scripts}
        return None

    def _commit(self) -> None:
        if self.cid is None or self.settings is None:
            return
        binding = self._read()
        if binding is self._INVALID:
            return
        if binding and binding["type"] == "remap" and not binding["cid"]:
            return
        self.settings.set_binding(self.dev, self.profile, self.cid, binding)
        active = self.engine.profile_for(self.dev) if self.engine else ""
        if self.profile == active:           # only the profile in force is written to the device
            attempt(self, self.settings.apply_buttons, self.dev)
        self.on_change()
