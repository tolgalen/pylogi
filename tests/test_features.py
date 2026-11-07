# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Macros, gestures, thumb wheel and the Windows / macOS input injectors."""
import ctypes
import os
import struct
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import test_actions as TA  # noqa: E402
from test_actions import RecordingInjector, make  # noqa: E402

from pylogi.core import actions as A  # noqa: E402
from pylogi.core import injectors, macros  # noqa: E402
from pylogi.core.device import HidppDevice  # noqa: E402
from pylogi.hidpp import features as ft  # noqa: E402
from pylogi.hidpp.constants import F  # noqa: E402
from pylogi.hidpp.protocol import Channel, Hidpp20Device  # noqa: E402


def wait_for(cond, seconds=3.0):
    end = time.time() + seconds
    while not cond() and time.time() < end:
        time.sleep(0.01)
    return cond()


class MacroParsing(unittest.TestCase):
    def test_every_step_roundtrips(self):
        script = "key ctrl+shift+T\n3x scroll down\nwait 200\ntext hi there\nmedia Play/Pause\nclick back\nrun firefox -p x"
        steps = macros.parse(script)
        self.assertEqual(macros.format_script(steps), script)
        self.assertEqual(steps[1], {"op": "scroll", "dir": "down", "times": 3})

    def test_comments_blank_lines_and_case_are_ignored(self):
        steps = macros.parse("# note\n\nKEY CTRL+c\n")
        self.assertEqual(steps, [{"op": "key", "mods": ["ctrl"], "key": "C"}])

    def test_errors_name_the_line(self):
        for bad, line in (("key", 1), ("ok\nkey foo+a", 1), ("key a\nwait x", 2), ("key a\nkey a\nwait 99999", 3),
                          ("0x key a", 1), ("101x key a", 1), ("scroll sideways", 1), ("jump", 1), ("click joystick", 1),
                          ("media nope", 1), ("run", 1), ("3x", 1)):
            with self.assertRaises(macros.MacroError, msg=bad) as cm:
                macros.parse(bad)
            if bad != "ok\nkey foo+a":
                self.assertEqual(cm.exception.line, line, bad)

    def test_limits(self):
        with self.assertRaises(macros.MacroError):
            macros.parse("\n".join(["100x wait 10000"] * 2))          # too much waiting
        with self.assertRaises(macros.MacroError):
            macros.parse("\n".join(["100x key a"] * 6))               # too many steps
        self.assertEqual(macros.parse(""), [])

    def test_describe(self):
        self.assertEqual(macros.describe("key ctrl+c\nwait 5"), "key ctrl+C · wait 5")
        self.assertTrue(macros.describe("text " + "x" * 80).endswith("…"))
        self.assertEqual(macros.describe("nonsense here"), "nonsense here")


class MacroExecution(unittest.TestCase):
    def test_macro_binding_runs_its_steps_in_order(self):
        t, dev, st, eng, inj = make()
        st.set_binding(dev, "", 0x52, {"type": "macro", "script": "key ctrl+C\nwait 10\n2x scroll down\nmedia Mute"})
        eng.configure(dev)
        self.assertTrue(t.state[0x52][0])                                   # diverted to software
        eng.handle_buttons(dev, {0x52})
        self.assertTrue(wait_for(lambda: ("key", 113, False) in inj.events))
        self.assertEqual(inj.events, [("key", 29, True), ("key", 46, True), ("key", 46, False), ("key", 29, False),
                                      ("rel", 8, -1), ("rel", 8, -1), ("key", 113, True), ("key", 113, False)])

    def test_same_macro_does_not_overlap_but_can_run_again_afterwards(self):
        t, dev, st, eng, inj = make()
        script = "wait 150\nmedia Mute"
        self.assertTrue(eng.run_macro(dev, script))
        self.assertFalse(eng.run_macro(dev, script))                        # still running
        self.assertTrue(wait_for(lambda: bool(inj.events)))
        self.assertTrue(wait_for(lambda: not eng._macros))
        self.assertTrue(eng.run_macro(dev, "media Mute"))

    def test_invalid_script_is_refused_and_logged(self):
        t, dev, st, eng, inj = make()
        with mock.patch("pylogi.core.actions._debuglog.log") as log:
            self.assertFalse(eng.run_macro(dev, "explode"))
        self.assertTrue(log.called)
        self.assertEqual(inj.events, [])

    def test_stopping_the_engine_cancels_a_running_macro(self):
        t, dev, st, eng, inj = make()
        eng.run_macro(dev, "wait 5000\nmedia Mute")
        time.sleep(0.05)
        eng._stop.set()
        self.assertTrue(wait_for(lambda: not eng._macros, 2))
        self.assertEqual(inj.events, [])

    def test_macro_pressed_and_released_fires_once(self):
        t, dev, st, eng, inj = make()
        st.set_binding(dev, "", 0x52, {"type": "macro", "script": "media Mute"})
        eng.configure(dev)
        eng.handle_buttons(dev, {0x52})
        eng.handle_buttons(dev, set())
        self.assertTrue(wait_for(lambda: len(inj.events) >= 2 and not eng._macros))
        self.assertEqual(len(inj.events), 2)


def raw_xy(dx, dy, idx=7):
    return bytes([0x11, 1, idx, 0x10]) + struct.pack(">hh", dx, dy) + bytes(12)


class Gestures(unittest.TestCase):
    GESTURE = {"type": "gesture", "threshold": 40, "click": "media Mute", "up": "media Play/Pause",
               "down": "media Stop", "left": "media Previous track", "right": "media Next track"}

    def test_direction_from_movement(self):
        d = A.ActionEngine.gesture_direction
        self.assertEqual(d(5, -3), "click")
        self.assertEqual(d(80, 10), "right")
        self.assertEqual(d(-80, 10), "left")
        self.assertEqual(d(10, 80), "down")
        self.assertEqual(d(10, -80), "up")
        self.assertEqual(d(60, 60), "right")                 # ties go to the horizontal axis
        self.assertEqual(d(30, 0, threshold=20), "right")

    def test_parse_raw_xy_notification(self):
        self.assertEqual(ft.parse_raw_xy(raw_xy(-7, 12), 7), (-7, 12))
        self.assertIsNone(ft.parse_raw_xy(raw_xy(1, 1), 9))
        self.assertIsNone(ft.parse_raw_xy(bytes([0x11, 1, 7, 0x00]) + bytes(16), 7))      # a button event

    def test_gesture_button_is_diverted_with_raw_xy_and_restored(self):
        t, dev, st, eng, inj = make()
        st.set_binding(dev, "", 0x53, self.GESTURE)
        st.set_binding(dev, "", 0x52, {"type": "media", "name": "Mute"})
        eng.configure(dev)
        self.assertEqual((t.state[0x53][0], t.rawxy.get(0x53)), (True, True))
        self.assertNotIn(0x52, t.rawxy)                                   # ordinary actions never touch raw XY
        st.set_binding(dev, "", 0x53, {"type": "media", "name": "Stop"})  # no longer a gesture
        eng.configure(dev)
        self.assertEqual(t.rawxy[0x53], False)
        st.set_binding(dev, "", 0x53, self.GESTURE)
        eng.configure(dev)
        eng.release(dev)
        self.assertEqual((t.state[0x53][0], t.rawxy[0x53]), (False, False))

    def _gesture(self, moves):
        t, dev, st, eng, inj = make()
        st.set_binding(dev, "", 0x53, self.GESTURE)
        eng.configure(dev)
        eng.handle_buttons(dev, {0x53})
        for dx, dy in moves:
            eng.on_raw_xy(dev, dx, dy)
        eng.handle_buttons(dev, set())
        wait_for(lambda: bool(inj.events) and not eng._macros)
        return [e[1] for e in inj.events if e[2]]

    def test_each_direction_runs_its_macro(self):
        K = A.MEDIA
        self.assertEqual(self._gesture([(30, 2), (25, -1)]), [K["Next track"]])
        self.assertEqual(self._gesture([(-60, 0)]), [K["Previous track"]])
        self.assertEqual(self._gesture([(0, -70)]), [K["Play/Pause"]])
        self.assertEqual(self._gesture([(5, 80)]), [K["Stop"]])
        self.assertEqual(self._gesture([(3, 4)]), [K["Mute"]])            # barely moved: a click
        self.assertEqual(self._gesture([]), [K["Mute"]])

    def test_movement_without_a_held_gesture_button_is_ignored(self):
        t, dev, st, eng, inj = make()
        eng.on_raw_xy(dev, 500, 0)
        self.assertEqual(eng._gesture, {})

    def test_a_second_gesture_starts_from_zero(self):
        t, dev, st, eng, inj = make()
        st.set_binding(dev, "", 0x53, self.GESTURE)
        eng.configure(dev)
        eng.handle_buttons(dev, {0x53})
        eng.on_raw_xy(dev, 90, 0)
        eng.handle_buttons(dev, set())
        wait_for(lambda: not eng._macros)
        eng.handle_buttons(dev, {0x53})
        self.assertEqual((eng._gesture[dev.uid]["dx"], eng._gesture[dev.uid]["dy"]), (0, 0))

    def test_raw_xy_reports_reach_the_engine_through_the_listener(self):
        t, dev, st, eng, inj = make()
        st.set_binding(dev, "", 0x53, self.GESTURE)
        eng.configure(dev)
        t.press(0x53)
        t.q.append(raw_xy(90, 0))
        self.assertTrue(wait_for(lambda: eng._gesture.get(dev.uid, {}).get("dx") == 90))
        dev.dev.ch.remove_listener(eng._on_report)


class ThumbMouse(TA.ReprogMouse):
    FEATS = dict(TA.ReprogMouse.FEATS)
    FEATS[F.THUMB_WHEEL] = 8

    def __init__(self):
        super().__init__()
        self.thumb = [0, 0]                                   # diverted, inverted

    def write(self, data):
        if data[2] == 8 and not self.silent:
            fn, params = data[3] >> 4, data[4:]
            hdr = bytes([0x11, data[1], data[2], data[3]])
            if fn == 1:
                self.q.append(hdr + bytes(self.thumb).ljust(16, b"\0"))
            elif fn == 2:
                self.thumb = [params[0], params[1]]
                self.q.append(hdr + params[:2].ljust(16, b"\0"))
            return
        super().write(data)

    def rotate(self, n):
        self.q.append(bytes([0x11, 1, 8, 0x00]) + struct.pack(">h", n) + bytes(12))


def make_thumb():
    import tempfile
    from pathlib import Path

    from pylogi.core.config import Config
    from pylogi.core.settings import DeviceSettings
    t = ThumbMouse()
    dev = HidppDevice(Hidpp20Device(Channel(t), 1), "u", "Bolt")
    dev.refresh()
    dev.caps.update({"buttons", "thumbwheel"})
    st = DeviceSettings(Config(Path(tempfile.mkdtemp()) / "c.json"))
    inj = RecordingInjector()
    eng = A.ActionEngine(st, inj, detector=type("D", (), {"available": False, "reason": "x", "current": lambda s: None})())
    return t, dev, st, eng, inj


class ThumbWheel(unittest.TestCase):
    def test_read_and_write_keep_the_other_field(self):
        t, dev, *_ = make_thumb()
        self.assertEqual(dev.thumb(), (False, False))
        dev.set_thumb(invert=True)
        self.assertEqual(t.thumb, [0, 1])
        dev.set_thumb(diverted=True)
        self.assertEqual((t.thumb, dev.thumb()), ([1, 1], (True, True)))

    def test_parse_notification(self):
        self.assertEqual(ft.parse_thumb(bytes([0x11, 1, 8, 0]) + struct.pack(">h", -5) + bytes(12), 8), -5)
        self.assertIsNone(ft.parse_thumb(bytes([0x11, 1, 7, 0]) + bytes(16), 8))

    def test_mode_is_saved_and_applied_to_the_device(self):
        t, dev, st, eng, inj = make_thumb()
        self.assertEqual(st.thumb(dev), ("native", False))
        st.save_thumb(dev, "zoom", True)
        eng.configure_thumb(dev)
        self.assertEqual(t.thumb, [1, 1])
        st.save_thumb(dev, "native")
        eng.configure_thumb(dev)
        self.assertEqual(t.thumb, [0, 1])                    # back to the system, direction kept
        dev.dev.ch.remove_listener(eng._on_report)

    def test_rotation_in_each_mode(self):
        K = A.KEYCODES
        cases = {
            "hscroll": [("rel", 6, 1)] * 2,
            "zoom": [("key", 29, True), ("rel", 8, 1), ("key", 29, False)] * 2,
            "volume": [("key", 115, True), ("key", 115, False)] * 2,
            "tabs": [("key", 29, True), ("key", K["Page Down"], True), ("key", K["Page Down"], False), ("key", 29, False)] * 2,
        }
        for mode, expected in cases.items():
            t, dev, st, eng, inj = make_thumb()
            st.save_thumb(dev, mode)
            eng.on_thumb(dev, A.THUMB_STEP * 2)
            self.assertEqual(inj.events, expected, mode)

    def test_negative_rotation_and_native_mode(self):
        t, dev, st, eng, inj = make_thumb()
        st.save_thumb(dev, "hscroll")
        eng.on_thumb(dev, -A.THUMB_STEP)
        self.assertEqual(inj.events, [("rel", 6, -1)])
        st.save_thumb(dev, "native")
        eng.on_thumb(dev, 99)
        self.assertEqual(len(inj.events), 1)

    def test_small_rotations_accumulate_and_a_reversal_restarts(self):
        t, dev, st, eng, inj = make_thumb()
        st.save_thumb(dev, "hscroll")
        for _ in range(A.THUMB_STEP):
            eng.on_thumb(dev, 1)
        self.assertEqual(inj.events, [("rel", 6, 1)])
        eng.on_thumb(dev, 1)
        eng.on_thumb(dev, -1)                                # reversed before a full step: nothing sent
        self.assertEqual(len(inj.events), 1)

    def test_notifications_reach_the_engine(self):
        t, dev, st, eng, inj = make_thumb()
        st.save_thumb(dev, "hscroll")
        eng.configure_thumb(dev)
        t.rotate(A.THUMB_STEP)
        self.assertTrue(wait_for(lambda: inj.events == [("rel", 6, 1)]))
        dev.dev.ch.remove_listener(eng._on_report)

    def test_settings_apply_restores_the_mode_on_reconnect(self):
        t, dev, st, eng, inj = make_thumb()
        st.engine = eng
        st.save_thumb(dev, "volume", True)
        st.apply(dev)
        self.assertEqual(t.thumb, [1, 1])
        dev.dev.ch.remove_listener(eng._on_report)


class FakeUser32:
    def __init__(self):
        self.sent = []

    def SendInput(self, n, ptr, size):
        self.sent.append(ptr._obj)
        return n


class WindowsInjector(unittest.TestCase):
    def test_every_action_code_has_a_virtual_key(self):
        codes = set(A.KEYCODES.values()) | set(A.MODIFIERS.values()) | set(A.MEDIA.values())
        self.assertEqual(sorted(codes - injectors._VK.keys()), [])

    def test_keys_and_buttons_and_wheel(self):
        u = FakeUser32()
        inj = injectors.WindowsInjector(u)
        inj.key(A.KEYCODES["A"], True)
        inj.key(A.KEYCODES["A"], False)
        inj.key(A.KEYCODES["Left"], True)
        inj.key(A.MOUSE["right"], True)
        inj.key(A.MOUSE["back"], False)
        inj.rel(8, -1)
        inj.rel(6, 2)
        inj.key(99999, True)                                            # unknown code: ignored
        k1, k2, k3, m1, m2, w1, w2 = u.sent
        self.assertEqual((k1.type, k1.ki.wVk, k1.ki.dwFlags), (1, ord("A"), 0))
        self.assertEqual(k2.ki.dwFlags, 0x0002)
        self.assertEqual(k3.ki.dwFlags, 0x0001)                         # arrows are extended keys
        self.assertEqual((m1.type, m1.mi.dwFlags), (0, 0x0008))
        self.assertEqual((m2.mi.dwFlags, m2.mi.mouseData), (0x0100, 1))
        self.assertEqual((w1.mi.dwFlags, ctypes.c_int32(w1.mi.mouseData).value), (0x0800, -120))
        self.assertEqual((w2.mi.dwFlags, w2.mi.mouseData), (0x1000, 240))


class MacInjector(unittest.TestCase):
    def test_every_letter_digit_modifier_and_edit_key_has_a_mac_code(self):
        needed = {c for k, c in A.KEYCODES.items() if k not in ("F6", "F7", "F8", "F9", "F10", "Insert", "Print Screen")}
        needed |= set(A.MODIFIERS.values())
        self.assertEqual(sorted(needed - injectors._MAC.keys()), [])

    def test_posts_events_and_frees_them(self):
        q = mock.MagicMock()
        q.CGEventGetLocation.return_value = injectors.CGPoint(10, 20)
        inj = injectors.MacInjector(q)
        inj.key(A.KEYCODES["A"], True)
        q.CGEventCreateKeyboardEvent.assert_called_with(None, 0, True)
        inj.key(A.MOUSE["left"], True)
        self.assertEqual(q.CGEventCreateMouseEvent.call_args[0][1], 1)
        inj.key(A.MOUSE["left"], False)
        self.assertEqual(q.CGEventCreateMouseEvent.call_args[0][1], 2)
        inj.rel(8, -1)
        q.CGEventCreateScrollWheelEvent.assert_called_with(None, 1, 2, -1, 0)
        inj.rel(6, 3)
        q.CGEventCreateScrollWheelEvent.assert_called_with(None, 1, 2, 0, 3)
        self.assertEqual(q.CGEventPost.call_count, q.CFRelease.call_count)
        before = q.CGEventPost.call_count
        inj.key(A.MEDIA["Mute"], True)                                  # media keys: not supported, nothing posted
        self.assertEqual(q.CGEventPost.call_count, before)

    def test_platform_choice(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertIsNone(injectors.platform_injector())
        with mock.patch.object(sys, "platform", "darwin"), \
                mock.patch("pylogi.core.injectors.MacInjector", side_effect=OSError("no lib")):
            inj = injectors.platform_injector()
            self.assertFalse(inj.available)
            self.assertIn("no lib", inj.reason)

    def test_default_injector_uses_the_platform_backend_off_linux(self):
        fake = RecordingInjector()
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch("pylogi.core.injectors.platform_injector", return_value=fake):
            self.assertIs(A.default_injector(), fake)


if __name__ == "__main__":
    unittest.main()


class FeatureUiTests(unittest.TestCase):
    def _page(self):
        import tempfile
        from pathlib import Path

        from PySide6.QtWidgets import QApplication

        from pylogi.core.config import Config
        from pylogi.core.demo import DemoManager
        from pylogi.core.settings import DeviceSettings
        from pylogi.ui.device_page import DevicePage
        self.app = QApplication.instance() or QApplication([])
        cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        st = DeviceSettings(cfg)
        eng = A.ActionEngine(st, RecordingInjector(), detector=type("D", (), {"available": False, "reason": "x", "current": lambda s: None})())
        st.engine = None                       # demo devices have no HID channel for the engine to listen on
        dev = DemoManager().devices[0]
        page = DevicePage(cfg, None, st)
        page.show_device(dev)
        return page, dev, st, eng

    def _choose(self, bp, cid, kind):
        bp.map._select(cid)
        insp = bp.inspector
        insp.type_box.setCurrentIndex(insp.type_box.findData(kind))
        insp._type_changed()
        return insp

    def test_macro_is_saved_only_when_valid(self):
        page, dev, st, eng = self._page()
        bp = page.ensure("buttons")
        insp = self._choose(bp, 0xC3, "macro")
        insp._widgets["script"].setPlainText("key ctrl+shift+T\nwait 100")
        insp._commit()
        self.assertEqual(st.effective(dev)[0xC3], {"type": "macro", "script": "key ctrl+shift+T\nwait 100"})
        insp._widgets["script"].setPlainText("key ctrl+shift+T\nexplode")
        insp._commit()
        self.assertEqual(st.effective(dev)[0xC3]["script"], "key ctrl+shift+T\nwait 100")      # not overwritten
        self.assertIn("Line 2", insp.note.text())
        self.assertEqual(bp._summary(0xC3)[0], "key ctrl+shift+T · wait 100")

    def test_gesture_editor_saves_all_directions_and_threshold(self):
        page, dev, st, eng = self._page()
        bp = page.ensure("buttons")
        insp = self._choose(bp, 0xC3, "gesture")
        w = insp._widgets
        w["up"].setPlainText("key ctrl+Tab")
        w["left"].setPlainText("media Previous track")
        w["threshold"].setValue(80)
        insp._commit()
        b = st.effective(dev)[0xC3]
        self.assertEqual((b["type"], b["threshold"], b["up"], b["left"], b["click"], b["down"]),
                         ("gesture", 80, "key ctrl+Tab", "media Previous track", "", ""))
        w["right"].setPlainText("key nonsense")
        insp._commit()
        self.assertEqual(st.effective(dev)[0xC3]["right"], "")                  # the invalid script was not saved
        self.assertEqual(bp._summary(0xC3)[0], "Gesture")

    def test_gesture_and_macro_are_offered_for_every_button(self):
        page, dev, st, eng = self._page()
        bp = page.ensure("buttons")
        bp.map._select(0xC3)
        kinds = {bp.inspector.type_box.itemData(i) for i in range(bp.inspector.type_box.count())}
        self.assertTrue({"macro", "gesture"} <= kinds)

    def test_thumb_wheel_controls_save_mode_and_direction(self):
        page, dev, st, eng = self._page()
        from pylogi.ui.cards import PointerCard
        card = page.ensure("pointer").findChild(PointerCard)
        self.assertTrue(hasattr(card, "thumb_mode"))
        self.assertEqual(card.thumb_mode.currentData(), "native")
        card.thumb_inv.setChecked(True)
        self.assertEqual(st.thumb(dev), ("native", True))
        self.assertEqual(dev.thumb(), (False, True))
        st.engine = eng
        eng.register = lambda d: None           # no HID channel on a demo device
        card.thumb_mode.setCurrentIndex(card.thumb_mode.findData("zoom"))
        card._set_thumb()
        self.assertEqual(st.thumb(dev), ("zoom", True))
        self.assertEqual(dev.thumb(), (True, True))


class OfflinePill(unittest.TestCase):
    @staticmethod
    def _red_pixels(widget) -> int:
        img = widget.grab().toImage()
        n = 0
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                if c.red() > c.green() + 60 and c.red() > c.blue() + 60:
                    n += 1
        return n

    def test_no_connection_notice_is_red_and_connected_is_not(self):
        from PySide6.QtWidgets import QApplication

        from pylogi.core.demo import DemoManager
        from pylogi.ui import theme
        from pylogi.ui.details import status_pill
        app = QApplication.instance() or QApplication([])
        dev = DemoManager().devices[0]
        for mode in ("dark", "light"):
            theme.apply(app, mode, "default")
            dev.online = True
            ok = status_pill(dev)
            ok.show()
            ok.ensurePolished()
            dev.online = False
            off = status_pill(dev)
            off.show()
            off.ensurePolished()
            self.assertFalse(ok.property("offline"))
            self.assertTrue(off.property("offline"))
            self.assertGreater(self._red_pixels(off), 20, mode)          # the border and the text are red
            self.assertLess(self._red_pixels(ok), self._red_pixels(off), mode)
        theme.apply(app, "dark", "default")
