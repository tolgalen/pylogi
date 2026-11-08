# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Webcam: saved settings, presets, rules by application, video modes, viewing aids, CLI and tray."""
from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from pylogi import cli  # noqa: E402
from pylogi.core import camera as C  # noqa: E402
from pylogi.core import camera_rules, webcam  # noqa: E402
from pylogi.core.config import Config  # noqa: E402
from pylogi.core.demo import DemoWebcam  # noqa: E402
from pylogi.core.settings import DeviceSettings  # noqa: E402

APP = QApplication.instance() or QApplication([])


def fresh():
    cfg = Config(Path(tempfile.mkdtemp()) / "c.json")
    return cfg, DeviceSettings(cfg), DemoWebcam()


def value(cam, name):
    return next(c.value for c in cam.cam_controls() if c.name == name)


class V4L2Structures(unittest.TestCase):
    def test_sizes_and_request_numbers_match_the_kernel(self):
        self.assertEqual(ctypes.sizeof(webcam._FmtDesc), 64)
        self.assertEqual(ctypes.sizeof(webcam._FrmSizeEnum), 44)
        self.assertEqual(ctypes.sizeof(webcam._FrmIvalEnum), 52)
        self.assertEqual(ctypes.sizeof(webcam._Format), 208)
        self.assertEqual(ctypes.sizeof(webcam._StreamParm), 204)
        self.assertEqual(webcam.VIDIOC_ENUM_FMT, 0xC0405602)
        self.assertEqual(webcam.VIDIOC_G_FMT, 0xC0D05604)
        self.assertEqual(webcam.VIDIOC_S_FMT, 0xC0D05605)
        self.assertEqual(webcam.VIDIOC_S_PARM, 0xC0CC5616)
        self.assertEqual(webcam.VIDIOC_ENUM_FRAMESIZES, 0xC02C564A)
        self.assertEqual(webcam.VIDIOC_ENUM_FRAMEINTERVALS, 0xC034564B)

    def test_fourcc_roundtrip(self):
        self.assertEqual(webcam.fourcc(webcam.fourcc_code("MJPG")), "MJPG")
        self.assertEqual(webcam.fourcc(webcam.fourcc_code("YUYV")), "YUYV")

    def fake_ioctl(self):
        state = {"set": []}

        def ioctl(fd, req, arg):
            if req == webcam.VIDIOC_ENUM_FMT:
                if arg.index > 0:
                    raise OSError(22, "end")
                arg.pixelformat = webcam.fourcc_code("MJPG")
            elif req == webcam.VIDIOC_ENUM_FRAMESIZES:
                if arg.index > 1:
                    raise OSError(22, "end")
                arg.type = webcam.FRMSIZE_DISCRETE
                arg.width, arg.height = ((1920, 1080), (1280, 720))[arg.index]
            elif req == webcam.VIDIOC_ENUM_FRAMEINTERVALS:
                if arg.index > 1:
                    raise OSError(22, "end")
                arg.type = 1
                arg.numerator, arg.denominator = 1, (60, 30)[arg.index]
            elif req == webcam.VIDIOC_G_FMT:
                arg.fmt.pix.width, arg.fmt.pix.height = 1280, 720
                arg.fmt.pix.pixelformat = webcam.fourcc_code("MJPG")
            elif req == webcam.VIDIOC_G_PARM:
                arg.parm.capture.numerator, arg.parm.capture.denominator = 1, 30
            elif req in (webcam.VIDIOC_S_FMT, webcam.VIDIOC_S_PARM):
                state["set"].append((req, bytes(arg)))
            return 0
        return ioctl, state

    def test_modes_and_current_mode_are_read(self):
        ioctl, _ = self.fake_ioctl()
        cam = webcam.V4L2Camera("/dev/video0")
        with mock.patch.object(webcam.os, "open", return_value=9), mock.patch.object(webcam.os, "close"), \
                mock.patch.object(webcam.fcntl, "ioctl", side_effect=ioctl):
            modes = cam.modes()
            self.assertEqual([(m.fmt, m.size, m.fps) for m in modes],
                             [("MJPG", "1920x1080", [60, 30]), ("MJPG", "1280x720", [60, 30])])
            self.assertEqual(cam.mode(), ("MJPG", 1280, 720, 30))

    def test_set_mode_writes_format_then_rate(self):
        ioctl, state = self.fake_ioctl()
        cam = webcam.V4L2Camera("/dev/video0")
        with mock.patch.object(webcam.os, "open", return_value=9), mock.patch.object(webcam.os, "close"), \
                mock.patch.object(webcam.fcntl, "ioctl", side_effect=ioctl):
            cam.set_mode("MJPG", 1920, 1080, 60)
        self.assertEqual([r for r, _ in state["set"]], [webcam.VIDIOC_S_FMT, webcam.VIDIOC_S_PARM])


class ModeText(unittest.TestCase):
    def test_parse_and_format(self):
        self.assertEqual(C.parse_mode("mjpg:1280x720@30"), ("MJPG", 1280, 720, 30))
        self.assertEqual(C.parse_mode("YUYV 640x480"), ("YUYV", 640, 480, 0))
        self.assertEqual(C.mode_text(("MJPG", 1280, 720, 30)), "MJPG 1280x720 @30")
        with self.assertRaises(ValueError):
            C.parse_mode("big")


class SavedSettings(unittest.TestCase):
    def test_controls_and_mode_come_back_when_the_camera_is_plugged_in(self):
        cfg, st, cam = fresh()
        cam.set_cam_control(9, 250)                       # zoom
        cam.set_video_mode("MJPG", 1280, 720, 60)
        st.remember_camera(cam)
        st.remember_camera_mode(cam, cam.video_mode())
        cam.set_cam_control(9, 100)                       # "unplugged": the camera forgets
        cam.set_video_mode("YUYV", 640, 480, 30)
        st.apply(cam)
        self.assertEqual(value(cam, "Zoom, Absolute"), 250)
        self.assertEqual(cam.video_mode(), ("MJPG", 1280, 720, 60))

    def test_nothing_is_written_when_the_camera_is_not_managed(self):
        cfg, st, cam = fresh()
        cam.set_cam_control(9, 250)
        st.remember_camera(cam)
        st.set_managed(cam, False)
        cam.set_cam_control(9, 100)
        st.apply(cam)
        self.assertEqual(value(cam, "Zoom, Absolute"), 100)

    def test_buttons_are_not_stored_as_values(self):
        _, _, cam = fresh()
        self.assertNotIn("Pan/Tilt Reset", C.snapshot(cam.cam_controls()))

    def test_manager_restores_a_camera_when_it_appears(self):
        from pylogi.core.manager import DeviceManager
        cfg, st, cam = fresh()
        cam.set_cam_control(9, 300)
        st.remember_camera(cam)
        cam.set_cam_control(9, 100)
        mgr = DeviceManager(cfg, settings=st)
        with mock.patch("pylogi.core.webcam.find_logitech_webcams", return_value=[cam]):
            mgr._publish()
            self.assertEqual(value(cam, "Zoom, Absolute"), 300)
            cam.set_cam_control(9, 120)
            mgr._publish()                                  # still plugged in: not written again
            self.assertEqual(value(cam, "Zoom, Absolute"), 120)
        with mock.patch("pylogi.core.webcam.find_logitech_webcams", return_value=[]):
            mgr._publish()
        with mock.patch("pylogi.core.webcam.find_logitech_webcams", return_value=[cam]):
            mgr._publish()                                  # plugged in again
            self.assertEqual(value(cam, "Zoom, Absolute"), 300)

    def test_settings_of_the_old_dev_node_identifier_are_carried_over(self):
        cfg, st, cam = fresh()
        cam.usb_path, cam.legacy_key = "1-2.3", "camera:046d:0000:/dev/video0"
        cfg.set("dev:camera:046d:0000:/dev/video0", {"camera": {"Brightness": 77}})
        self.assertEqual(st.camera(cam), {"Brightness": 77})
        self.assertIsNone(cfg.get("dev:camera:046d:0000:/dev/video0"))

    def test_identity_follows_the_usb_port_not_the_node(self):
        _, _, cam = fresh()
        cam.usb_path = "1-2.3"
        self.assertTrue(cam.device_key().endswith(":1-2.3"))


class Presets(unittest.TestCase):
    def test_a_builtin_preset_can_be_customised_and_restored(self):
        cfg, _, cam = fresh()
        original = C.resolve_preset(cfg, cam.uid, "Streaming", cam.cam_controls())
        cfg.set(f"camover:{cam.uid}", {"Streaming": {"Brightness": 10}})
        self.assertEqual(C.resolve_preset(cfg, cam.uid, "Streaming", cam.cam_controls()), {"Brightness": 10})
        cfg.set(f"camover:{cam.uid}", {})
        self.assertEqual(C.resolve_preset(cfg, cam.uid, "Streaming", cam.cam_controls()), original)

    def test_apply_preset_selects_it_and_reports_nothing_wrong(self):
        cfg, _, cam = fresh()
        self.assertEqual(C.apply_preset(cam, cfg, "Streaming"), [])
        self.assertEqual(cfg.get(f"campreset:{cam.uid}"), "Streaming")
        self.assertEqual(value(cam, "Zoom, Absolute"), 100)


class Rules(unittest.TestCase):
    def test_preset_follows_the_application_and_the_old_one_comes_back(self):
        cfg, st, cam = fresh()
        mgr = mock.Mock(devices=[cam])
        camera_rules.set_rule(cfg, cam, "Zoom", "Streaming")
        cfg.set(f"cam:{cam.uid}", {"Mine": {"Brightness": 40}})
        cfg.set(f"campreset:{cam.uid}", "Mine")
        cam.set_cam_control(1, 40)
        auto = camera_rules.CameraAutomation(mgr, cfg, st)
        self.assertTrue(auto.wants_app())
        auto("zoom")
        self.assertEqual(value(cam, "Saturation"), C._frac(next(c for c in cam.cam_controls() if c.name == "Saturation"), .55))
        self.assertEqual(cfg.get(f"campreset:{cam.uid}"), "Mine")              # a rule does not change the selection
        auto("firefox")                                                         # focus moves elsewhere
        self.assertEqual(value(cam, "Brightness"), 40)
        self.assertEqual(cfg.get(f"campreset:{cam.uid}"), "Mine")

    def test_watcher_keeps_running_only_while_rules_exist(self):
        cfg, st, cam = fresh()
        auto = camera_rules.CameraAutomation(mock.Mock(devices=[cam]), cfg, st)
        self.assertFalse(auto.wants_app())
        camera_rules.set_rule(cfg, cam, "zoom", "Video call")
        self.assertTrue(auto.wants_app())
        camera_rules.set_rule(cfg, cam, "zoom", None)
        self.assertFalse(auto.wants_app())

    def test_engine_notifies_listeners(self):
        from pylogi.core.actions import ActionEngine
        eng = ActionEngine(mock.Mock(profiles=lambda d: [""]), injector=mock.Mock(), detector=mock.Mock(available=False))
        seen = []
        eng.app_listeners.append(seen.append)
        eng.set_active_app("zoom")
        self.assertEqual(seen, ["zoom"])


class PageTests(unittest.TestCase):
    def page(self):
        from pylogi.ui.camera_page import CameraPage
        cfg, st, cam = fresh()
        return CameraPage(cam, cfg, st), cfg, st, cam

    def test_button_and_format_rows_exist(self):
        page, _, _, cam = self.page()
        self.assertTrue(any(r.c.is_button for r in page.rows.values()))
        self.assertTrue(hasattr(page, "format_row"))
        self.assertEqual(page.format_row.fmt.currentData(), "MJPG")
        self.assertEqual(page.format_row.fps.currentData(), 30)

    def test_changing_a_control_is_remembered(self):
        page, _, st, cam = self.page()
        page.rows["zoom"]._write(300)
        self.assertEqual(st.camera(cam)["Zoom, Absolute"], 300)

    def test_applying_a_format_is_remembered(self):
        page, _, st, cam = self.page()
        page.format_row.fmt.setCurrentIndex(page.format_row.fmt.findData("YUYV"))
        page.format_row._apply()
        self.assertEqual(cam.video_mode(), ("YUYV", 640, 480, 30))
        self.assertEqual(st.camera_mode(cam), ["YUYV", 640, 480, 30])

    def test_run_button_centres_pan_and_tilt(self):
        page, _, _, cam = self.page()
        cam.set_cam_control(10, 7200)
        next(r for r in page.rows.values() if r.c.is_button)._run()
        self.assertEqual(value(cam, "Pan (absolute)"), 0)

    def test_presets_can_be_customised_renamed_and_deleted(self):
        page, cfg, _, cam = self.page()
        cam.set_cam_control(1, 33)
        page._update("Streaming")
        self.assertEqual(cfg.get(f"camover:{cam.uid}")["Streaming"]["Brightness"], 33)
        page._restore("Streaming")
        self.assertNotIn("Streaming", cfg.get(f"camover:{cam.uid}"))
        cfg.set(f"cam:{cam.uid}", {"A": {"Brightness": 5}, "B": {"Brightness": 6}})
        cfg.set(f"campreset:{cam.uid}", "A")
        camera_rules.set_rule(cfg, cam, "zoom", "A")
        from PySide6.QtWidgets import QInputDialog
        with mock.patch.object(QInputDialog, "getText", return_value=("Work", True)):
            page._rename("A")
        self.assertEqual(list(cfg.get(f"cam:{cam.uid}")), ["Work", "B"])
        self.assertEqual(cfg.get(f"campreset:{cam.uid}"), "Work")
        self.assertEqual(camera_rules.rules(cfg, cam), {"zoom": "Work"})
        page._delete("Work")
        self.assertEqual(list(cfg.get(f"cam:{cam.uid}")), ["B"])

    def test_auto_adjust_locks_the_values(self):
        page, _, st, cam = self.page()
        page.SETTLE_MS = 0
        page.auto_adjust()
        page._lock_auto()
        self.assertEqual(value(cam, "White Balance, Automatic"), 0)
        self.assertEqual(value(cam, "Focus, Automatic Continuous"), 0)
        self.assertEqual(value(cam, "Auto Exposure"), 1)
        self.assertEqual(st.camera(cam)["Auto Exposure"], 1)

    def test_viewing_aids_are_saved(self):
        page, cfg, _, _ = self.page()
        page._aid("grid", True)
        self.assertTrue(page.preview.grid)
        self.assertEqual(cfg.get("campreview"), {"grid": True})

    def test_histogram_of_a_flat_picture_has_one_bar(self):
        from PySide6.QtGui import QColor, QImage
        from pylogi.ui.camera_page import Preview
        img = QImage(64, 36, QImage.Format_RGB32)
        img.fill(QColor(200, 200, 200))
        h = Preview.luminance_histogram(img)
        self.assertEqual(sum(1 for v in h if v > 0), 1)
        self.assertEqual(max(h), 1.0)

    def test_overlays_paint_without_errors(self):
        from PySide6.QtGui import QColor, QImage
        page, _, _, _ = self.page()
        img = QImage(64, 36, QImage.Format_RGB32)
        img.fill(QColor(10, 120, 200))
        pv = page.preview
        pv.image, pv.grid, pv.mirror, pv.histogram = img, True, True, True
        pv.resize(300, 200)
        self.assertFalse(pv.grab().isNull())

    def test_rules_dialog_adds_and_removes(self):
        from PySide6.QtWidgets import QInputDialog
        from pylogi.ui.camera_page import AppsDialog
        page, cfg, _, cam = self.page()
        dlg = AppsDialog(page)
        with mock.patch.object(QInputDialog, "getText", return_value=("Zoom", True)), \
                mock.patch.object(QInputDialog, "getItem", return_value=("Video call", True)):
            dlg._add()
        self.assertEqual(camera_rules.rules(cfg, cam), {"zoom": "Video call"})
        self.assertEqual(dlg.table.rowCount(), 1)
        dlg.table.selectRow(0)
        dlg._remove()
        self.assertEqual(camera_rules.rules(cfg, cam), {})


class CommandLine(unittest.TestCase):
    def test_preset_and_video_mode(self):
        cfg, st, cam = fresh()
        cli.set_values(cam, ["preset=Streaming", "video-mode=YUYV:640x480@15"], st)
        self.assertEqual(cfg.get(f"campreset:{cam.uid}"), "Streaming")
        self.assertEqual(cam.video_mode(), ("YUYV", 640, 480, 15))
        self.assertEqual(cli.get(cam, ["preset", "video-mode"], st), {"preset": "Streaming", "video-mode": "YUYV 640x480 @15"})
        with self.assertRaises(SystemExit):
            cli.set_values(cam, ["preset=Nope"], st)
        with self.assertRaises(SystemExit):
            cli.set_values(cam, ["video-mode=huge"], st)

    def test_camera_controls_written_from_the_command_line_are_remembered(self):
        _, st, cam = fresh()
        cli.set_values(cam, ["camera.brightness=90"], st)
        self.assertEqual(st.camera(cam)["Brightness"], 90)


class TrayTests(unittest.TestCase):
    def test_camera_presets_in_the_tray_menu(self):
        from PySide6.QtWidgets import QMenu
        from pylogi.ui import tray_menu
        cfg, st, cam = fresh()
        cfg.set(f"cam:{cam.uid}", {"Mine": {"Brightness": 40}})
        menu = QMenu()
        self.assertEqual(tray_menu.fill(menu, [cam], cfg=cfg, settings=st), 1)
        sub = menu.actions()[0].menu()
        labels = [a.text() for a in sub.actions() if not a.isSeparator()]
        self.assertTrue({"Default", "Streaming", "Video call", "Mine"} <= set(labels), labels)
        next(a for a in sub.actions() if a.text() == "Mine").trigger()
        self.assertEqual(value(cam, "Brightness"), 40)
        self.assertEqual(cfg.get(f"campreset:{cam.uid}"), "Mine")
        self.assertEqual(tray_menu.fill(QMenu(), [cam]), 0)             # without a configuration there is nothing to show


if __name__ == "__main__":
    unittest.main()
