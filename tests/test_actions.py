# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
import os
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from test_hidpp import FakeDevice  # noqa: E402
from pylogi.core import actions as A  # noqa: E402
from pylogi.core.config import Config  # noqa: E402
from pylogi.core.device import HidppDevice  # noqa: E402
from pylogi.core.settings import DeviceSettings  # noqa: E402
from pylogi.hidpp import features as ft  # noqa: E402
from pylogi.hidpp.constants import F  # noqa: E402
from pylogi.hidpp.protocol import Channel, Hidpp20Device  # noqa: E402

app = QApplication.instance() or QApplication([])


class RecordingInjector(A.Injector):
    available = True

    def __init__(self):
        self.events = []

    def key(self, code, down): self.events.append(("key", code, down))
    def rel(self, code, value): self.events.append(("rel", code, value))


class ReprogMouse(FakeDevice):
    """FakeDevice + REPROG_CONTROLS_V4 with divert/remap state (feature index 7)."""
    FEATS = dict(FakeDevice.FEATS)
    FEATS[F.REPROG_CONTROLS_V4] = 7
    CTRL = [(0x52, 0x0530), (0x53, 0x0530), (0x56, 0x0530)]      # middle, back, forward

    def __init__(self):
        super().__init__()
        self.state = {cid: [False, 0] for cid, _ in self.CTRL}     # divert, remap
        self.rawxy: dict[int, bool] = {}                            # raw-XY reporting set by the host

    def write(self, data):
        if data[2] == 7 and not self.silent:
            self.writes.append(data)
            fn, params = data[3] >> 4, data[4:]
            hdr = bytes([0x11, data[1], data[2], data[3]])
            if fn == 0:
                self.q.append(hdr + bytes([len(self.CTRL)]).ljust(16, b"\0"))
            elif fn == 1:
                cid, flags = self.CTRL[params[0]]
                self.q.append(hdr + struct.pack(">HHBBB", cid, cid, flags & 0xFF, 0, 0).ljust(16, b"\0"))
            elif fn == 2:
                cid = struct.unpack(">H", params[:2])[0]
                div, remap = self.state[cid]
                self.q.append(hdr + struct.pack(">HBH", cid, int(div), remap or cid).ljust(16, b"\0"))
            elif fn == 3:
                cid, flags, remap = struct.unpack(">HBH", params[:5])
                if flags & 2:
                    self.state[cid][0] = bool(flags & 1)
                if flags & 0x20:
                    self.rawxy[cid] = bool(flags & 0x10)
                self.state[cid][1] = 0 if remap == cid else remap
                self.q.append(hdr + params[:5].ljust(16, b"\0"))
            return
        super().write(data)

    def press(self, *cids):
        """Unsolicited diverted-buttons event listing the controls currently held."""
        body = b"".join(struct.pack(">H", c) for c in cids).ljust(8, b"\0")
        self.q.append(bytes([0x11, 1, 7, 0x00]) + body + bytes(8))


def make(tmp=None):
    t = ReprogMouse()
    dev = HidppDevice(Hidpp20Device(Channel(t), 1), "u", "Bolt")
    dev.refresh()
    dev.caps.add("buttons")
    cfg = Config(Path(tmp or tempfile.mkdtemp()) / "c.json")
    settings = DeviceSettings(cfg)
    inj = RecordingInjector()
    engine = A.ActionEngine(settings, inj, detector=type("D", (), {"available": False, "reason": "x", "current": lambda s: None})())
    return t, dev, settings, engine, inj


class ProtocolTests(unittest.TestCase):
    def test_divert_and_remap_keep_each_other(self):
        t, dev, *_ = make()
        d = dev.dev
        ft.set_control_reporting(d, 0x56, True, None)
        self.assertEqual(ft.get_control_reporting(d, 0x56), (True, 0))
        ft.set_control_reporting(d, 0x56, None, 0x53)             # remap must not clear the divert flag
        self.assertEqual(ft.get_control_reporting(d, 0x56), (True, 0x53))
        ft.set_control_reporting(d, 0x56, False, 0)
        self.assertEqual(ft.get_control_reporting(d, 0x56), (False, 0))

    def test_parse_diverted_event(self):
        rep = bytes([0x11, 1, 7, 0x00]) + struct.pack(">HHHH", 0x56, 0x53, 0, 0) + bytes(8)
        self.assertEqual(ft.parse_diverted(rep, 7), [0x56, 0x53])
        self.assertIsNone(ft.parse_diverted(rep, 8))                 # another feature
        self.assertIsNone(ft.parse_diverted(bytes([0x10, 1, 7, 0, 0, 0, 0]), 7))   # short report


class EngineTests(unittest.TestCase):
    def test_shortcut_is_pressed_and_released_in_order(self):
        t, dev, st, eng, inj = make()
        st.set_binding(dev, "", 0x56, {"type": "key", "mods": ["ctrl", "shift"], "key": "T"})
        eng.configure(dev)
        self.assertEqual(t.state[0x56], [True, 0])                    # diverted in the device
        self.assertEqual(t.state[0x53], [False, 0])                   # untouched buttons stay normal
        eng.handle_buttons(dev, {0x56})
        eng.handle_buttons(dev, set())
        ctrl, shift, tkey = A.MODIFIERS["ctrl"], A.MODIFIERS["shift"], A.KEYCODES["T"]
        self.assertEqual(inj.events, [("key", ctrl, True), ("key", shift, True), ("key", tkey, True),
                                      ("key", tkey, False), ("key", shift, False), ("key", ctrl, False)])

    def test_media_scroll_mouse_and_text(self):
        t, dev, st, eng, inj = make()
        st.set_binding(dev, "", 0x52, {"type": "media", "name": "Play/Pause"})
        st.set_binding(dev, "", 0x53, {"type": "mouse", "button": "middle"})
        st.set_binding(dev, "", 0x56, {"type": "text", "text": "Hi!"})
        eng.configure(dev)
        eng.handle_buttons(dev, {0x52}); eng.handle_buttons(dev, set())
        self.assertEqual(inj.events, [("key", 164, True), ("key", 164, False)])    # one tap, on press only
        inj.events.clear()
        eng.handle_buttons(dev, {0x53}); eng.handle_buttons(dev, set())
        self.assertEqual(inj.events, [("key", 0x112, True), ("key", 0x112, False)])  # held like a real button
        inj.events.clear()
        eng.handle_buttons(dev, {0x56})
        shift = A.MODIFIERS["shift"]
        self.assertEqual(inj.events[:4], [("key", shift, True), ("key", A.KEYCODES["H"], True),
                                          ("key", A.KEYCODES["H"], False), ("key", shift, False)])
        self.assertEqual(inj.events[-2:][0][0], "key")                                 # "!" = shift + 1
        self.assertIn(("key", 2, True), inj.events)

    def test_remap_binding_is_done_in_the_device_not_in_software(self):
        t, dev, st, eng, inj = make()
        st.set_binding(dev, "", 0x56, {"type": "remap", "cid": 0x53})
        eng.configure(dev)
        self.assertEqual(t.state[0x56], [False, 0x53])
        eng.handle_buttons(dev, {0x56})
        self.assertEqual(inj.events, [])

    def test_app_profiles_inherit_and_override(self):
        t, dev, st, eng, inj = make()
        st.set_binding(dev, "", 0x56, {"type": "key", "mods": [], "key": "F5"})
        st.set_binding(dev, "", 0x53, {"type": "key", "mods": [], "key": "F6"})
        st.add_profile(dev, "firefox")
        st.set_binding(dev, "firefox", 0x56, {"type": "key", "mods": ["alt"], "key": "Left"})
        st.set_binding(dev, "firefox", 0x53, {"type": "default"})          # original function in this app
        self.assertEqual(st.profiles(dev), ["", "firefox"])
        eng.configure(dev)
        eng.handle_buttons(dev, {0x56}); eng.handle_buttons(dev, set())
        self.assertEqual(inj.events[0], ("key", A.KEYCODES["F5"], True))
        inj.events.clear()
        eng.set_active_app("firefox")
        self.assertEqual(t.state[0x53], [False, 0])                         # back to normal for this app
        eng.handle_buttons(dev, {0x56}); eng.handle_buttons(dev, set())
        self.assertEqual(inj.events[0], ("key", A.MODIFIERS["alt"], True))
        eng.set_active_app("gedit")                                          # no such profile -> Default
        self.assertEqual(t.state[0x53], [True, 0])

    def test_managed_off_releases_everything(self):
        t, dev, st, eng, inj = make()
        st.set_binding(dev, "", 0x56, {"type": "key", "mods": [], "key": "F5"})
        eng.configure(dev)
        self.assertTrue(t.state[0x56][0])
        st.set_managed(dev, False)
        self.assertEqual(t.state[0x56], [False, 0])
        eng.configure(dev)                                                   # unmanaged: no writes
        self.assertEqual(t.state[0x56], [False, 0])

    def test_real_time_listener_receives_button_events(self):
        t, dev, st, eng, inj = make()
        st.set_binding(dev, "", 0x52, {"type": "media", "name": "Mute"})
        eng.configure(dev)
        t.press(0x52)
        deadline = time.time() + 2
        while not inj.events and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(inj.events, [("key", 113, True), ("key", 113, False)])
        dev.dev.ch.remove_listener(eng._on_report)


class RestoreOriginalTests(unittest.TestCase):
    def test_release_returns_buttons_to_the_state_they_were_found_in(self):
        t, dev, st, eng, inj = make()
        t.state[0x52] = [True, 0]            # another manager (OpenLogi / Solaar) had diverted it
        t.state[0x53] = [False, 0x56]        # ... and another one was remapped
        st.set_binding(dev, "", 0x52, {"type": "media", "name": "Mute"})
        st.set_binding(dev, "", 0x53, {"type": "key", "mods": [], "key": "F5"})
        st.set_binding(dev, "", 0x56, {"type": "key", "mods": [], "key": "F6"})
        eng.configure(dev)
        eng.configure(dev)                    # a second configure must not overwrite the remembered state
        self.assertEqual((t.state[0x52], t.state[0x53]), ([True, 0], [True, 0]))
        eng.release(dev)
        self.assertEqual(t.state[0x52], [True, 0])        # still diverted: it was before
        self.assertEqual(t.state[0x53], [False, 0x56])    # remap restored
        self.assertEqual(t.state[0x56], [False, 0])


class MiscTests(unittest.TestCase):
    def test_char_mapping(self):
        self.assertEqual(A.char_keys("a"), (30, 0))
        self.assertEqual(A.char_keys("A"), (30, 1))
        self.assertEqual(A.char_keys("5"), (6, 0))
        self.assertEqual(A.char_keys("?"), (53, 1))
        self.assertIsNone(A.char_keys("€"))

    def test_command_action_runs_without_a_shell(self):
        t, dev, st, eng, inj = make()
        d = Path(tempfile.mkdtemp())
        eng.perform(dev, {"type": "command", "cmd": f"touch {d / 'direct'}"}, True)
        eng.perform(dev, {"type": "command", "cmd": f"echo hi; touch {d / 'injected'}"}, True)
        for _ in range(100):
            if (d / "direct").exists():
                break
            time.sleep(0.02)
        time.sleep(0.2)
        self.assertTrue((d / "direct").exists())               # the program ran with its arguments
        self.assertFalse((d / "injected").exists())            # ';' is not interpreted: no shell

    def test_legacy_remaps_are_migrated(self):
        t, dev, st, *_ = make()
        st.cfg.set("dev:" + dev.device_key(), {"remaps": {"86": 83}})
        self.assertEqual(st.remaps(dev), {86: 83})

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux only: /dev/uinput")

    def test_uinput_device_is_created_when_available(self):
        try:
            inj = A.UInputInjector("pyLogi unit test")
        except OSError:
            self.skipTest("/dev/uinput not writable here")
        try:
            with open("/proc/bus/input/devices") as f:
                self.assertIn("pyLogi unit test", f.read())
        finally:
            inj.close()
        with open("/proc/bus/input/devices") as f:
            self.assertNotIn("pyLogi unit test", f.read())


if __name__ == "__main__":
    unittest.main()


class WheelAndPersistenceTests(unittest.TestCase):
    def _wheel_dev(self):
        class Wheel(ReprogMouse):
            FEATS = dict(ReprogMouse.FEATS)
            FEATS[F.HIRES_WHEEL] = 8
            def __init__(s): super().__init__(); s.mode = 0

            def write(s, data):
                if data[2] == 8:
                    fn = data[3] >> 4
                    hdr = bytes([0x11, data[1], data[2], data[3]])
                    if fn == 1:
                        s.q.append(hdr + bytes([s.mode]).ljust(16, b"\0"))
                    elif fn == 2:
                        s.mode = data[4]
                        s.q.append(hdr + bytes([s.mode]).ljust(16, b"\0"))
                    return
                super().write(data)
        t = Wheel()
        dev = HidppDevice(Hidpp20Device(Channel(t), 1), "w", "Bolt")
        dev.refresh()
        return t, dev

    def test_wheel_bits_and_divert_bit_preserved(self):
        t, dev = self._wheel_dev()
        t.mode = 0x01                                   # events diverted to software: must survive
        ft.write_wheel(dev.dev, hires=True)
        self.assertEqual((t.mode, ft.read_wheel(dev.dev)), (0x03, (True, False)))
        ft.write_wheel(dev.dev, invert=True)
        self.assertEqual((t.mode, ft.read_wheel(dev.dev)), (0x07, (True, True)))
        ft.write_wheel(dev.dev, hires=False, invert=False)
        self.assertEqual(t.mode, 0x01)
        self.assertIn("wheel", dev.caps)

    def test_wheel_and_key_colours_are_reapplied_on_reconnect(self):
        t, dev = self._wheel_dev()
        cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
        st = DeviceSettings(cfg)
        st.save(dev, "wheel", [True, True])
        dev.caps.add("keylight")
        sent = []
        dev.set_key_colors = lambda colors: sent.append(colors)
        st.save(dev, "key_colors", {"4": [255, 0, 0], "5": [0, 255, 0]})
        t.mode = 0                                      # the device forgot it
        st.apply(dev)
        self.assertEqual(ft.read_wheel(dev.dev), (True, True))
        self.assertEqual(sent, [{4: (255, 0, 0), 5: (0, 255, 0)}])

    def test_iso_layout_has_the_extra_keys_and_a_tall_enter(self):
        from pylogi.ui.keyboard_widget import KeyboardWidget, ROWS, iso_rows
        ansi = {k[1] for r in ROWS for k in r if k[1] is not None}
        iso = {k[1] for r in iso_rows() for k in r if k[1] is not None}
        self.assertEqual(iso - ansi, {0x32, 0x64})              # '#' and '<>'
        self.assertEqual(ansi - iso, {0x31})                    # no backslash key above Enter
        enter = [k for r in iso_rows() for k in r if k[1] == 0x28]
        self.assertEqual(len(enter), 1)
        self.assertEqual(enter[0][3], 2)                        # spans two rows
        widths = {round(sum(k[2] for k in r), 2) for r in iso_rows()[:6]}
        self.assertEqual(len(widths), len({round(sum(k[2] for k in r), 2) for r in ROWS[:6]}))
        kb = KeyboardWidget()
        kb.set_layout("iso")
        kb.resize(900, 300)
        kb._layout()
        self.assertEqual(len(kb._rects), len(iso))
        tall = [r for r, u, _ in kb._rects if u == 0x28][0]
        short = [r for r, u, _ in kb._rects if u == 0x2B][0]
        self.assertGreater(tall.height(), 1.8 * short.height())


class ProfilesPageLayoutTests(unittest.TestCase):
    def _page(self, device_index):
        import tempfile
        from pylogi.core.demo import DemoManager
        from pylogi.ui.device_page import DevicePage
        page = DevicePage(Config(Path(tempfile.mkdtemp()) / "c.json"), None)
        page.resize(1200, 800)
        dev = DemoManager().devices[device_index]
        page.show_device(dev)
        from pylogi.ui.cards import ProfilesCard
        card = page.ensure("profiles").findChild(ProfilesCard)
        page.show()
        app.processEvents()
        return page, card

    def test_the_button_table_uses_the_available_height(self):
        page, card = self._page(3)                              # keyboard with onboard profiles
        self.assertGreater(card.table.height(), 380)            # was a ~6-row box with an empty page below
        self.assertGreaterEqual(card.table.rowCount(), 15)
        self.assertLess(page.height() - (card.mapTo(page, card.rect().bottomLeft()).y()), 60)   # card reaches the bottom

    def test_dpi_rows_only_for_devices_with_a_sensor(self):
        page, kb = self._page(3)
        self.assertTrue(all(w.isHidden() for w in kb.dpi_widgets))
        self.assertFalse(kb.name.isHidden() or kb.rate.isHidden())
        page2, mouse = self._page(1)                            # gaming mouse
        self.assertTrue(not any(w.isHidden() for w in mouse.dpi_widgets))


    def test_keyboard_rows_are_called_keys_and_mouse_rows_buttons(self):
        page, kb = self._page(3)
        self.assertEqual(kb.table.item(0, 0).text(), "Key 1")
        page2, mouse = self._page(1)
        self.assertEqual(mouse.table.item(0, 0).text(), "Button 1")
        self.assertEqual(mouse.table.item(5, 0).text(), "G6")


class KeyboardDetectionTests(unittest.TestCase):
    G915 = bytes.fromhex("0000ffffffffffffffffffff0000feff")        # real answer of a G915 TKL (ISO, Spanish)

    @staticmethod
    def ids(payload: bytes) -> set[int]:
        return {i * 8 + b for i, byte in enumerate(payload[2:]) for b in range(8) if byte >> b & 1}

    def test_key_presence_is_read_from_the_per_key_lighting_info(self):
        class Kb(FakeDevice):
            FEATS = dict(FakeDevice.FEATS)
            FEATS[F.PER_KEY_LIGHTING_V2] = 7

            def write(s, data):
                if data[2] == 7 and data[3] >> 4 == 0:
                    s.q.append(bytes([0x11, data[1], data[2], data[3]]) + KeyboardDetectionTests.G915[:16])
                    return
                super().write(data)
        d = Hidpp20Device(Channel(Kb()), 1)
        ids = ft.read_key_presence(d)
        self.assertEqual(ids, self.ids(self.G915))
        self.assertEqual(ft.read_key_presence(Hidpp20Device(Channel(FakeDevice()), 1)), None)   # no per-key lighting

    def test_garbage_is_not_taken_for_a_key_bitmap(self):
        class Junk(FakeDevice):
            FEATS = dict(FakeDevice.FEATS)
            FEATS[F.PER_KEY_LIGHTING_V2] = 7

            def write(s, data):
                if data[2] == 7:
                    s.q.append(bytes([0x11, data[1], data[2], data[3]]) + bytes(16))
                    return
                super().write(data)
        self.assertIsNone(ft.read_key_presence(Hidpp20Device(Channel(Junk()), 1)))

    def test_detect_form(self):
        from pylogi.ui.keyboard_widget import detect_form
        self.assertEqual(detect_form(self.ids(self.G915), "G915 TKL"), ("iso", False))
        full_ansi = set(range(1, 97)) | set(range(104, 112))
        self.assertEqual(detect_form(full_ansi, "MX Keys"), ("ansi", True))
        self.assertEqual(detect_form(full_ansi | {97}, "K"), ("iso", True))
        self.assertEqual(detect_form(None, "G915 TKL LIGHTSPEED Wireless"), ("ansi", False))   # name fallback
        self.assertEqual(detect_form(None, "MX Keys S"), ("ansi", True))

    def test_tkl_iso_rows_have_no_numpad_and_the_iso_keys(self):
        from pylogi.hidpp.features import key_id
        from pylogi.ui.keyboard_widget import NUMPAD_USAGES, ROWS, build_rows
        present = self.ids(self.G915)
        rows = build_rows("iso", False, present)
        usages = {k[1] for r in rows for k in r if k[1] is not None}
        self.assertFalse(usages & NUMPAD_USAGES)
        self.assertTrue({0x64, 0x32, 0x28, 0x2C} <= usages)                       # '<>', '#', Enter, Space
        self.assertTrue(all(key_id(u) in present for u in usages))              # nothing the keyboard lacks
        self.assertTrue(all(r[-1][1] is not None for r in rows if r))            # no dangling gaps on the right
        width = lambda rs: max(sum(k[2] for k in r) for r in rs)
        self.assertLess(width(rows), width(ROWS) - 4)                            # the map really is narrower

    def test_lighting_card_follows_the_keyboard_and_the_user_can_override(self):
        import tempfile
        from pylogi.core.demo import DemoManager
        from pylogi.ui.cards import LightingCard
        from pylogi.ui.keyboard_widget import NUMPAD_USAGES
        dev = DemoManager().devices[3]                                           # demo G915 reports the real bitmap
        card = LightingCard(dev, Config(Path(tempfile.mkdtemp()) / "c.json"))
        self.assertEqual((card.auto_layout, card.auto_numpad), ("iso", False))
        self.assertIn("ISO", card.layout_box.itemText(0))
        self.assertFalse(set(card.kb.all_usages()) & NUMPAD_USAGES)
        n_auto = len(card.kb.all_usages())
        card.kb.rainbow()
        self.assertTrue(set(card.kb.colors) <= set(card.kb.all_usages()))        # nothing painted on keys that do not exist
        card.size_box.setCurrentIndex(card.size_box.findData("full"))
        card._layout_chosen(0)                                                    # manual override: all keys drawn
        self.assertTrue(set(card.kb.all_usages()) & NUMPAD_USAGES)
        self.assertGreater(len(card.kb.all_usages()), n_auto)
        self.assertEqual(card.settings._rec(dev).get("key_size"), "full")
        card2 = LightingCard(dev, card.cfg)                                       # remembered
        self.assertEqual(card2.size_box.currentData(), "full")


class ActiveProfileTests(unittest.TestCase):
    def test_onboard_reads_and_writes_the_current_profile_sector(self):
        from pylogi.hidpp.onboard import Onboard

        class Mem(FakeDevice):
            FEATS = dict(FakeDevice.FEATS)
            FEATS[F.ONBOARD_PROFILES] = 7
            current = 1

            def write(s, data):
                if data[2] == 7:
                    s.writes.append(data)
                    fn, params = data[3] >> 4, data[4:]
                    hdr = bytes([0x11, data[1], data[2], data[3]])
                    if fn == 0:      # description: memory 1, format 4, macros 1, 3 profiles, 3 oob, 15 buttons, 16 sectors of 255
                        s.q.append(hdr + bytes([1, 4, 1, 3, 3, 15, 16, 0, 255]).ljust(16, b"\0"))
                    elif fn == 4:
                        s.q.append(hdr + struct.pack(">H", s.current).ljust(16, b"\0"))
                    elif fn == 3:
                        s.current = struct.unpack(">H", params[:2])[0]
                        s.q.append(hdr + params[:2].ljust(16, b"\0"))
                    return
                super().write(data)
        t = Mem()
        ob = Onboard(Hidpp20Device(Channel(t), 1))
        self.assertEqual(ob.current_sector(), 1)
        ob.set_current_sector(3)
        self.assertEqual((t.current, ob.current_sector()), (3, 3))
        self.assertEqual(t.writes[-1][3] >> 4, 4)                       # read back after the write
        sets = [w for w in t.writes if w[2] == 7 and w[3] >> 4 == 3]
        self.assertEqual(sets[0][4:6], b"\x00\x03")                     # big-endian sector number

    def _card(self, index=1):
        from pylogi.core.demo import DemoManager
        from pylogi.ui.cards import ProfilesCard
        dev = DemoManager().devices[index]
        return dev, ProfilesCard(dev)

    def test_the_card_marks_the_active_profile_and_can_switch(self):
        dev, card = self._card()
        self.assertTrue(card.combo.itemText(0).startswith("● "))        # the device is using the first one
        self.assertFalse(card.combo.itemText(1).startswith("●"))
        self.assertFalse(card.use_btn.isEnabled())                       # the selected profile is already in use
        card.combo.setCurrentIndex(1)
        self.assertTrue(card.use_btn.isEnabled())
        card.use_btn.click()
        self.assertEqual(dev.active_profile(), card.profiles[1].sector)
        self.assertTrue(card.combo.itemText(1).startswith("● "))        # marker moved,
        self.assertFalse(card.combo.itemText(0).startswith("●"))
        self.assertEqual(card.combo.currentIndex(), 1)                   # and the selection stayed where it was
        self.assertFalse(card.use_btn.isEnabled())

    def test_switching_is_only_offered_in_onboard_mode(self):
        dev, card = self._card()
        card.combo.setCurrentIndex(1)
        self.assertTrue(card.use_btn.isEnabled())
        card.mode.setChecked(False)                                      # host mode: stored profiles are ignored
        self.assertFalse(card.use_btn.isEnabled())
        card.mode.setChecked(True)
        self.assertTrue(card.use_btn.isEnabled())


class ProfileColourTests(unittest.TestCase):
    def test_fixed_colour_patches_records_and_keeps_crc_valid(self):
        from pylogi.hidpp import onboard as ob
        raw = ob.seal(bytes(range(255)))
        out = ob.with_fixed_color(raw, (255, 0, 0))
        self.assertEqual(len(out), len(raw))
        for rec in ob.lighting_records(out):
            self.assertEqual(ob.fixed_color(rec), (255, 0, 0))
        self.assertEqual(out[:ob.O_LIGHT], raw[:ob.O_LIGHT])                   # name and buttons untouched
        self.assertEqual(ob.crc16(out[:-2]), int.from_bytes(out[-2:], "big"))

    def test_single_slot_only_changes_that_record(self):
        from pylogi.hidpp import onboard as ob
        raw = ob.seal(bytes(255))
        recs = ob.lighting_records(ob.with_fixed_color(raw, (1, 2, 3), slot=2))
        self.assertEqual([ob.fixed_color(r) for r in recs], [None, None, (1, 2, 3), None])


class ProfileColourReadTests(unittest.TestCase):
    def test_first_fixed_record_is_the_profile_colour(self):
        from pylogi.hidpp import onboard as ob
        raw = bytearray(255)
        raw[ob.O_LIGHT:ob.O_LIGHT + 11] = bytes.fromhex("0c010000000000206400" + "00")
        raw[ob.O_LIGHT + 11:ob.O_LIGHT + 22] = bytes.fromhex("0121bffc02000000000000")
        cols = [ob.fixed_color(r) for r in ob.lighting_records(bytes(raw))]
        self.assertIsNone(cols[0])
        self.assertEqual(next(c for c in cols if c), (0x21, 0xBF, 0xFC))


class FlowLayoutTests(unittest.TestCase):
    def test_buttons_wrap_instead_of_being_cut_off(self):
        from PySide6.QtWidgets import QApplication, QPushButton, QWidget
        from pylogi.ui.widgets import FlowLayout
        app = QApplication.instance() or QApplication([])
        for width in (1200, 600, 250):
            box = QWidget()
            lay = FlowLayout(box)
            for t in ("Apply to selected", "Apply to all", "Rainbow", "Load colour from profile…", "Save preset…"):
                lay.addWidget(QPushButton(t))
            box.resize(width, lay.heightForWidth(width))
            lay.setGeometry(box.rect())
            kids = box.findChildren(QPushButton)
            self.assertTrue(all(k.geometry().right() < width for k in kids), width)
        self.assertGreater(lay.heightForWidth(250), lay.heightForWidth(1200))


class KeyboardWidgetSizeTests(unittest.TestCase):
    def test_keys_use_the_whole_width_and_the_height_follows(self):
        from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
        from pylogi.ui.keyboard_widget import KeyboardWidget
        app = QApplication.instance() or QApplication([])
        kb = KeyboardWidget()
        kb.set_layout("iso", False, None)
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(kb)
        heights = []
        for width in (900, 1400):
            box.resize(width, lay.heightForWidth(width))
            lay.setGeometry(box.rect())
            kb._layout()
            self.assertGreater(max(r.right() for r, _, _ in kb._rects) / kb.width(), .97)
            heights.append(kb.height())
        self.assertGreater(heights[1], heights[0])


class NarrowWindowTests(unittest.TestCase):
    def test_no_device_section_forces_a_wide_window(self):
        import tempfile
        from PySide6.QtWidgets import QScrollArea
        from pylogi.core.demo import DemoManager
        from pylogi.ui.device_page import DevicePage
        page = DevicePage(Config(Path(tempfile.mkdtemp()) / "c.json"), None)
        page.resize(1000, 800)
        worst = 0
        for dev in DemoManager().devices:
            page.show_device(dev)
            for key in page._sections(dev):
                page.ensure(key)
                page._go(key)
                app.processEvents()
                w = page.stack.currentWidget()
                inner = w.widget() if isinstance(w, QScrollArea) else w
                worst = max(worst, inner.minimumSizeHint().width())
        self.assertLess(worst, 800)          # the camera page used to ask for 1022 px
