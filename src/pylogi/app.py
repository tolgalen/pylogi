# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Application entry point."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from . import __license__, __version__
from .core.config import Config
from .i18n import available_languages, set_language


def build_parser() -> argparse.ArgumentParser:
    """The command-line interface (also used to generate the Help and README option tables)."""
    ap = argparse.ArgumentParser(prog="pylogi", description="Manage Logitech devices")
    ap.add_argument("--demo", action="store_true", help="use simulated devices")
    ap.add_argument("--lang", metavar="CODE", help="UI language code (see --languages), or auto")
    ap.add_argument("--languages", action="store_true", help="list available languages and exit")
    ap.add_argument("--offline", action="store_true",
                    help="never download product photos")
    ap.add_argument("--appearance", choices=["system", "light", "dark"], help="light, dark or follow the system")
    ap.add_argument("--theme", metavar="ID", help="named theme (see --themes)")
    ap.add_argument("--themes", action="store_true", help="list available themes and exit")
    ap.add_argument("--debug-log", action="store_true",
                    help="record a debug log (devices, events, errors) for this run; see Settings to keep it on")
    ap.add_argument("--trace-hid", metavar="FILE", help="log every HID++ report to FILE")
    ap.add_argument("--backup", action="store_true", help="save a full snapshot of every detected device and exit")
    ap.add_argument("--report", nargs="?", const="-", metavar="FILE",
                    help="write a read-only compatibility report (devices, capabilities, features) to FILE or stdout, and exit")
    ap.add_argument("--with-serials", action="store_true", help="include serial numbers in --report (left out by default)")
    ap.add_argument("--self-test", action="store_true",
                    help="write-verification on the real devices (backs them up first, restores afterwards)")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--list", action="store_true", help="print detected devices and exit")
    ap.add_argument("--json", action="store_true", help="machine-readable output for --list")
    ap.add_argument("--get", metavar="DEVICE", help="print the current settings of a device as JSON")
    ap.add_argument("--set", nargs="+", metavar=("DEVICE", "NAME=VALUE"),
                    help="change settings, e.g. --set 'MX Anywhere' dpi=1600 wheel-invert=on")
    ap.add_argument("--restore-deleted", action="store_true",
                    help="list again the devices removed with 'Delete from pyLogi'")
    ap.add_argument("--no-splash", action="store_true", help="do not show the start-up splash screen")
    ap.add_argument("--toggle", action="store_true",
                    help="show or hide the window of the running pyLogi and exit (bind it to a keyboard shortcut)")
    ap.add_argument("--minimized", action="store_true", help="start hidden in the system tray")
    ap.add_argument("--install-launcher", action="store_true", help="add pyLogi to the application menu")
    ap.add_argument("--autostart", choices=["on", "off"], help="start pyLogi with the desktop session")
    ap.add_argument("--install-gnome-extension", action="store_true",
                    help="install the helper extension that lets per-application profiles work on GNOME/Wayland")
    ap.add_argument("--export-help", metavar="FILE", help="write the help manual as a PDF file and exit")
    ap.add_argument("--license", action="store_true", help="print the licence notice and exit")
    ap.add_argument("--version", action="version",
                    version=f"pyLogi {__version__} ({__license__}, no warranty; see --license)")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    if args.license:
        from .core import credits
        print(f"pyLogi {__version__}\n{credits.COPYRIGHT}\n\n{credits.WARRANTY}\n\n{credits.TRADEMARKS}")
        return 0
    if args.languages:
        for code, name in available_languages().items():
            print(f"{code}\t{name}")
        return 0
    if args.themes:
        from .ui import theme as _t
        for tid, name in _t.available_themes().items():
            print(f"{tid}\t{name}")
        return 0
    if args.install_launcher or args.autostart or args.install_gnome_extension:
        from .core import desktop
        if args.install_launcher:
            for p in desktop.install_launcher():
                print("installed", p)
        if args.autostart:
            desktop.set_autostart(args.autostart == "on")
            print("autostart", args.autostart)
        if args.install_gnome_extension:
            print("installed", desktop.install_gnome_extension())
            print("Log out and in again, then run:  gnome-extensions enable " + desktop.EXT_UUID)
        return 0
    if args.restore_deleted:
        from .core import removal
        print(f"{removal.restore_deleted(Config())} device(s) will be listed again")
        return 0
    if args.lang and args.lang != "auto" and args.lang not in available_languages():
        ap.error(f"unknown language '{args.lang}' (see --languages)")

    app = QApplication(sys.argv[:1])
    app.setApplicationName("pyLogi")
    app.setDesktopFileName("pylogi")      # Wayland app_id: lets the dock match pylogi.desktop and show its icon
    from .ui.branding import app_icon
    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    cfg = Config()
    set_language(args.lang or cfg.get("language", "auto"))

    if args.demo:
        from .core.demo import DemoManager
        manager = DemoManager()
    else:
        from .core.manager import DeviceManager
        from .core.settings import DeviceSettings
        settings = DeviceSettings(cfg)
        manager = DeviceManager(cfg, settings=settings)

    if args.export_help:
        from .ui.help import export_pdf
        pages = export_pdf(args.export_help)
        print(f"{args.export_help}: {pages} pages")
        return 0
    from .core import debuglog
    if args.debug_log or cfg.get("debug_log", False):
        debuglog.LOG.enable(raw=bool(cfg.get("debug_log_raw", False)))
        if args.debug_log:
            print(f"debug log: {debuglog.LOG.path}", file=sys.stderr)
    if args.trace_hid:
        from .hidpp import trace
        trace.enable(args.trace_hid)
    if args.backup:
        from .core import backup
        manager.scan_once()
        manager.scan_once()
        for d in manager.devices:
            if d.kind != "receiver":
                print(backup.save(d))
        return 0
    if args.report:
        from .core import report
        manager.scan_once()
        manager.scan_once()
        text = report.to_text(report.build(manager.devices, args.with_serials))
        if args.report == "-":
            print(text)
        else:
            Path(args.report).write_text(text + "\n", encoding="utf-8")
            print(args.report)
        return 0
    if args.self_test:
        from .core import selftest
        manager.scan_once()
        manager.scan_once()
        if not args.yes and input("This writes temporary values to your devices and restores them "
                                  "(a backup is saved first). Continue? [y/N] ").lower() != "y":
            return 1
        results = selftest.run(manager.devices)
        bad = [r for r in results if r.status in (selftest.FAIL, selftest.ERROR)]
        print(f"\n{len(results)} checks: " + ", ".join(
            f"{s} {sum(r.status == s for r in results)}" for s in
            (selftest.PASS, selftest.FAIL, selftest.ERROR, selftest.UNCONFIRMED, selftest.SKIP)))
        return 1 if bad else 0
    if args.list or args.get or args.set:
        from . import cli
        if not args.demo:
            manager.scan_once()
            manager.scan_once()
        if args.list:
            cli.list_devices(manager.devices, args.json)
        elif args.get:
            print(json.dumps(cli.get(cli.find(manager.devices, args.get)), indent=1))
        else:
            dev = cli.find(manager.devices, args.set[0])
            if not dev.online:
                print(f"{dev.name} is offline", file=sys.stderr)
                return 1
            try:
                for item in cli.set_values(dev, args.set[1:], settings):
                    print("ok", item)
            except Exception as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
        for e in getattr(manager, "errors", []):
            print("warning:", e, file=sys.stderr)
        return 0

    from .ui import single_instance
    if args.toggle:
        if single_instance.notify_running(args.demo, b"toggle"):
            return 0
        print("pyLogi is not running.", file=sys.stderr)
        return 1
    if single_instance.notify_running(args.demo):          # already running: it shows its window, we exit
        print("pyLogi is already running; showing its window.", file=sys.stderr)
        return 0
    holder: dict = {}
    instance = single_instance.InstanceServer(  # noqa: F841 - listens from the start (even during the splash); keep it referenced
        lambda m: holder["win"].remote(m) if "win" in holder else None, args.demo)
    from .ui import theme
    from .ui.main_window import MainWindow
    theme.apply(app, args.appearance or cfg.get("appearance", "system"),
                args.theme or cfg.get("theme", "default"))
    splash = None
    hidden = args.minimized or bool(cfg.get("start_hidden", False))
    if not (args.no_splash or hidden or not cfg.get("show_splash", True)):
        from .i18n import tr
        from .ui.branding import SplashScreen
        splash = SplashScreen(__version__, tr("Starting…"))
        splash.show()
        app.processEvents()
    from .core.images import ImageProvider
    images = ImageProvider(enabled=not args.offline and cfg.get("device_images", False) is True)
    engine = None
    if not args.demo:
        from .core.actions import ActionEngine
        engine = ActionEngine(settings)       # software actions for diverted buttons
        engine.start_watching()
    win = MainWindow(manager, cfg, images, settings if not args.demo else None)
    holder["win"] = win

    def show_window() -> None:
        if not (hidden and win.tray.isVisible()):
            win.show()
        win.first_run(allow_download=not args.offline)        # the photo question, once the window is up

    if splash is not None:
        from .i18n import tr
        splash.set_message(tr("Detecting devices…"))
        ready, _finish = splash.finish_when_ready(win, show_window)
        manager.scanned.connect(ready)
    else:
        show_window()
    manager.start()
    code = app.exec()
    from .core import lighting_fx
    lighting_fx.HUB.stop_all()
    debuglog.LOG.disable()
    if engine is not None:
        engine.stop()
    return code
