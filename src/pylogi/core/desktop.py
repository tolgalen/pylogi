# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Desktop integration: launcher entry, autostart and the GNOME Shell helper extension."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
EXT_UUID = "pylogi-active-window@pylogi.local"


def _exec() -> str:
    """Command line that starts this installation (the launcher, so the private environment is used)."""
    launcher = os.environ.get("PYLOGI_LAUNCHER")
    if launcher:
        return f'"{launcher}"' if " " in launcher else launcher
    if getattr(sys, "frozen", False):          # PyInstaller build: the executable is the app itself
        return f'"{sys.executable}"' if " " in sys.executable else sys.executable
    exe = shutil.which("pylogi")
    return exe if exe else f"{sys.executable} -m pylogi"


def _desktop_entry(extra: str = "") -> str:
    return ("[Desktop Entry]\nType=Application\nName=pyLogi\n"
            "Comment=Manage Logitech mice, keyboards and webcams\n"
            f"Exec={_exec()}{extra}\nIcon=pylogi\nTerminal=false\n"
            "Categories=Settings;HardwareSettings;\nStartupWMClass=pylogi\n")


def _windows() -> bool:
    return sys.platform.startswith("win")


def _win_programs() -> Path:
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _make_shortcut(lnk: Path, args: str = "", minimized: bool = False) -> None:
    """Create a Windows .lnk to the launcher (through PowerShell's WScript.Shell; no extra packages)."""
    launcher = os.environ.get("PYLOGI_LAUNCHER") or str(Path(sys.executable).with_name("pythonw.exe"))
    if not os.environ.get("PYLOGI_LAUNCHER"):
        args = f"-m pylogi {args}".strip()

    def q(text: str) -> str:
        return str(text).replace("'", "''")
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');$s.TargetPath='{target}';"
              "$s.Arguments='{args}';$s.WorkingDirectory='{wd}';$s.IconLocation='{icon}';"
              "$s.Description='pyLogi';$s.WindowStyle={style};$s.Save()").format(
        lnk=q(str(lnk)), target=q(launcher), args=q(args), wd=q(str(Path(launcher).parent)), icon=q(str(DATA / "pylogi.ico")),
        style=7 if minimized else 1)
    lnk.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True,
                   capture_output=True, timeout=30)


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var, Path.home() / default))


def install_launcher() -> list[Path]:
    """Application-menu entry + icon: XDG folders on Linux, a Start Menu shortcut on Windows."""
    if _windows():
        lnk = _win_programs() / "pyLogi.lnk"
        _make_shortcut(lnk)
        return [lnk]
    apps = _xdg("XDG_DATA_HOME", ".local/share") / "applications"
    icons = _xdg("XDG_DATA_HOME", ".local/share") / "icons/hicolor/scalable/apps"
    apps.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    entry, icon = apps / "pylogi.desktop", icons / "pylogi.svg"
    entry.write_text(_desktop_entry(), encoding="utf-8")
    shutil.copyfile(DATA / "pylogi.svg", icon)
    files = [entry, icon]
    png = DATA / "icons" / "pylogi-256.png"
    if png.exists():                                   # for shells / docks that do not read SVG icons
        d = _xdg("XDG_DATA_HOME", ".local/share") / "icons/hicolor/256x256/apps"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(png, d / "pylogi.png")
        files.append(d / "pylogi.png")
    return files


def launcher_paths() -> list[Path]:
    """Files that make up the application-menu entry."""
    if _windows():
        return [_win_programs() / "pyLogi.lnk"]
    data = _xdg("XDG_DATA_HOME", ".local/share")
    return [data / "applications/pylogi.desktop", data / "icons/hicolor/scalable/apps/pylogi.svg",
            data / "icons/hicolor/256x256/apps/pylogi.png"]


def launcher_installed() -> bool:
    return launcher_paths()[0].exists()


def set_launcher(on: bool) -> None:
    if on:
        install_launcher()
    else:
        for p in launcher_paths():
            p.unlink(missing_ok=True)


def autostart_path() -> Path:
    if _windows():
        return _win_programs() / "Startup" / "pyLogi.lnk"
    return _xdg("XDG_CONFIG_HOME", ".config") / "autostart" / "pylogi.desktop"


def autostart_enabled() -> bool:
    return autostart_path().exists()


def set_autostart(on: bool) -> None:
    p = autostart_path()
    if on and _windows():
        _make_shortcut(p, "--minimized", minimized=True)
    elif on:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_desktop_entry(" --minimized") + "X-GNOME-Autostart-enabled=true\n", encoding="utf-8")
    else:
        p.unlink(missing_ok=True)


SERVICE = "pylogi.service"


def service_unit(exe: str | None = None) -> str:
    """systemd user unit: pyLogi runs minimised in the tray for the whole graphical session, so diverted buttons,
    macros and per-application profiles keep working without opening the window, and it restarts if it dies."""
    return ("[Unit]\nDescription=pyLogi - Logitech device manager\n"
            "Documentation=https://github.com/tolgalen/pylogi\n"
            "PartOf=graphical-session.target\nAfter=graphical-session.target\n\n"
            f"[Service]\nType=simple\nExecStart={exe or _exec()} --minimized --no-splash\n"
            "Restart=on-failure\nRestartSec=3\n\n"
            "[Install]\nWantedBy=graphical-session.target\n")


def service_path() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "systemd" / "user" / SERVICE


def service_available() -> bool:
    """True where a systemd user session can be driven (Linux with systemctl)."""
    return sys.platform.startswith("linux") and shutil.which("systemctl") is not None


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=20)


def service_enabled() -> bool:
    if not service_available():
        return False
    try:
        return _systemctl("is-enabled", SERVICE).stdout.strip() == "enabled"
    except (OSError, subprocess.SubprocessError):
        return False


def set_service(on: bool) -> str:
    """Install + enable (or disable + remove) the user service; returns a short status text.

    The service replaces the autostart entry (both would start a second copy at login)."""
    if not service_available():
        raise OSError("systemd is not available")
    if on:
        p = service_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(service_unit(), encoding="utf-8")
        set_autostart(False)
        _systemctl("daemon-reload")
        r = _systemctl("enable", "--now", SERVICE)
        if r.returncode:
            raise OSError((r.stderr or r.stdout).strip() or "systemctl enable failed")
        return "enabled"
    _systemctl("disable", "--now", SERVICE)
    service_path().unlink(missing_ok=True)
    _systemctl("daemon-reload")
    return "disabled"


def install_gnome_extension() -> Path:
    """Copy the helper extension to the user's GNOME Shell folder (needs a log out/in to load)."""
    dest = _xdg("XDG_DATA_HOME", ".local/share") / "gnome-shell/extensions" / EXT_UUID
    shutil.copytree(DATA / "gnome-extension" / EXT_UUID, dest, dirs_exist_ok=True)
    return dest


def gnome_available() -> bool:
    """True on a GNOME Shell session (where the helper extension can be used)."""
    return sys.platform.startswith("linux") and "gnome" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


def _extension_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "gnome-shell/extensions" / EXT_UUID


def gnome_extension_installed() -> bool:
    return _extension_dir().exists()


def _gnome_ext_cmd(action: str) -> None:
    exe = shutil.which("gnome-extensions")
    if exe:
        subprocess.run([exe, action, EXT_UUID], capture_output=True, timeout=10)


def set_gnome_extension(on: bool) -> None:
    """Install + enable (or disable + remove) the helper extension; a fresh install needs a log out/in."""
    try:
        if on:
            install_gnome_extension()
            _gnome_ext_cmd("enable")
        else:
            _gnome_ext_cmd("disable")
            shutil.rmtree(_extension_dir(), ignore_errors=True)
    except (OSError, subprocess.SubprocessError):
        pass
