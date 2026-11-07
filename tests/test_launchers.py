# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""The launchers (Linux/macOS shell script, Windows batch file) and the desktop integration that points to them."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pylogi.core import desktop  # noqa: E402

SH = ROOT / "pylogi.sh"
CMD = ROOT / "pylogi.cmd"


def system_python():
    """A Python that can import PySide6 by itself (not the project's own .venv), or None."""
    for name in ("python3", "python"):
        exe = shutil.which(name)
        if exe and subprocess.run([exe, "-c", "import PySide6"], capture_output=True).returncode == 0:
            return exe
    return None


class ShellLauncherTests(unittest.TestCase):
    def run_sh(self, *args, venv, **env):
        full = {**os.environ, "PYLOGI_VENV": str(venv), "QT_QPA_PLATFORM": "offscreen", **env}
        return subprocess.run([str(SH), *args], capture_output=True, text=True, env=full, timeout=120)

    def test_script_is_valid_and_executable(self):
        self.assertTrue(os.access(SH, os.X_OK))
        self.assertEqual(subprocess.run(["bash", "-n", str(SH)]).returncode, 0)
        self.assertEqual(subprocess.run(["bash", "-n", str(ROOT / "scripts/install-user.sh")]).returncode, 0)
        text = SH.read_text()
        for needle in ("PYLOGI_VENV", "PYLOGI_PYTHON", "PYLOGI_SYSTEM_SITE", "PYLOGI_NO_INSTALL", "requirements.txt",
                       "--setup", "--recreate", "--print-env", "PYLOGI_LAUNCHER", "PYTHONNOUSERSITE", "exec "):
            self.assertIn(needle, text)

    def test_plain_start_detaches_from_the_console_but_options_stay_in_the_foreground(self):
        text = SH.read_text()
        self.assertIn("PYLOGI_FOREGROUND", text)
        self.assertIn("setsid", text)
        self.assertIn("[ $# -eq 0 ]", text)                      # only a start without options detaches
        self.assertTrue(text.rstrip().endswith('exec "$VENV/bin/python" "$HERE/run.py" "$@"'))

    def test_builds_its_own_environment_and_runs_the_application_in_it(self):
        py = system_python()
        if py is None:
            self.skipTest("no interpreter with PySide6 to build an offline environment from")
        venv = Path(tempfile.mkdtemp()) / "env"
        opts = dict(PYLOGI_PYTHON=py, PYLOGI_SYSTEM_SITE="1", PYLOGI_NO_INSTALL="1")
        self.assertEqual(self.run_sh("--print-env", venv=venv, **opts).stdout.strip(), str(venv))
        self.assertFalse(venv.exists())                                   # printing does not create anything
        r = self.run_sh("--version", venv=venv, **opts)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pyLogi", r.stdout)
        self.assertIn("creating the virtual environment", r.stderr)
        self.assertTrue((venv / "bin" / "python").exists())
        out = subprocess.run([str(venv / "bin" / "python"), "-c", "import sys; print(sys.prefix)"],
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(Path(out).resolve(), venv.resolve())
        again = self.run_sh("--version", venv=venv, **opts)
        self.assertNotIn("creating", again.stderr)                        # reused, not rebuilt
        self.assertIn("pyLogi", again.stdout)

    def test_setup_and_recreate_exit_without_starting_the_app(self):
        py = system_python()
        if py is None:
            self.skipTest("no interpreter with PySide6")
        venv = Path(tempfile.mkdtemp()) / "env"
        opts = dict(PYLOGI_PYTHON=py, PYLOGI_SYSTEM_SITE="1", PYLOGI_NO_INSTALL="1")
        r = self.run_sh("--setup", venv=venv, **opts)
        self.assertEqual((r.returncode, "environment ready" in r.stderr), (0, True))
        marker = venv / "marker"
        marker.write_text("x")
        self.assertEqual(self.run_sh("--recreate", venv=venv, **opts).returncode, 0)
        self.assertFalse(marker.exists())                                 # rebuilt from scratch
        self.assertTrue((venv / "bin" / "python").exists())

    def test_symlink_in_the_path_still_finds_the_project(self):
        py = system_python()
        if py is None:
            self.skipTest("no interpreter with PySide6")
        link = Path(tempfile.mkdtemp()) / "pylogi"
        link.symlink_to(SH)
        venv = Path(tempfile.mkdtemp()) / "env"
        r = subprocess.run([str(link), "--version"], capture_output=True, text=True, timeout=120,
                           env={**os.environ, "PYLOGI_VENV": str(venv), "PYLOGI_PYTHON": py, "PYLOGI_SYSTEM_SITE": "1",
                                "PYLOGI_NO_INSTALL": "1", "QT_QPA_PLATFORM": "offscreen"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pyLogi", r.stdout)

    def test_failures_are_reported_and_leave_nothing_half_built(self):
        venv = Path(tempfile.mkdtemp()) / "env"
        r = self.run_sh("--setup", venv=venv, PYLOGI_PYTHON="/bin/false")
        self.assertEqual(r.returncode, 1)
        self.assertIn("could not create", r.stderr)
        self.assertFalse(venv.exists())
        r = self.run_sh("--setup", venv=venv, PYLOGI_PYTHON="/nonexistent/python")
        self.assertEqual(r.returncode, 1)


class WindowsLauncherTests(unittest.TestCase):
    """pylogi.cmd cannot run here, so its structure is checked statically."""

    def setUp(self):
        self.raw = CMD.read_bytes()
        self.lines = self.raw.decode("ascii").split("\r\n")

    def test_file_format(self):
        self.assertTrue(self.raw.isascii())                               # no encoding surprises in cmd.exe
        self.assertNotIn(b"\n", self.raw.replace(b"\r\n", b""))           # CRLF everywhere (labels need it)
        self.assertEqual(self.lines[0], "@echo off")
        self.assertIn("SPDX-License-Identifier: GPL-3.0-or-later", self.raw.decode())

    def test_no_multiline_blocks_so_paths_with_parentheses_are_safe(self):
        code = [l for l in self.lines if l.strip() and not l.lower().startswith("rem")]
        self.assertFalse([l for l in code if l.rstrip().endswith("(")], "multi-line ( ) block")
        self.assertFalse([l for l in code if l.strip() == ")" or l.strip().startswith(")")])

    def test_every_jump_has_a_target_and_subroutines_return(self):
        labels = {l[1:].split()[0].lower() for l in self.lines if l.startswith(":") and not l.startswith("::")}
        for line in self.lines:
            for target in re.findall(r"(?:goto|call)\s+:(\w+)", line, re.I):
                self.assertIn(target.lower(), labels, line)
        for must in ("setup", "findpython", "main", "ready", "gui", "printenv", "recreate", "verify", "deps"):
            self.assertIn(must, labels)
        text = "\n".join(self.lines)
        self.assertNotRegex(text, r"(?im)^\s*exit\s*$")                    # never closes the user's console

    def test_same_contract_as_the_shell_launcher(self):
        text = "\n".join(self.lines)
        for needle in ("PYLOGI_VENV", "PYLOGI_PYTHON", "PYLOGI_SYSTEM_SITE", "PYLOGI_NO_INSTALL", "requirements.txt",
                       "--setup", "--recreate", "--print-env", "PYLOGI_LAUNCHER", "PYTHONNOUSERSITE", "pythonw.exe",
                       "run.py", "import PySide6", "venv"):
            self.assertIn(needle, text)
        self.assertIn("py -3", text)                                       # the Windows launcher of Python

    def test_gui_options_start_without_a_console_and_cli_options_keep_it(self):
        text = "\n".join(self.lines)
        gui = re.findall(r'if /i "%~1"=="(--[\w-]+)" set "GUI=1"', text)
        from pylogi.app import build_parser
        window_options = {"--minimized", "--demo", "--no-splash", "--offline", "--lang", "--theme", "--appearance"}
        self.assertEqual(set(gui), window_options)
        known = {o for a in build_parser()._actions for o in a.option_strings}
        self.assertTrue(window_options <= known)                           # no stale option names
        # a console is needed for these: they print their result
        for cli in ("--list", "--get", "--set", "--backup", "--self-test", "--export-help", "--version", "--license"):
            self.assertNotIn(cli, gui)


class RequirementsTests(unittest.TestCase):
    def test_requirements_match_pyproject(self):
        lines = [l.split("#")[0].strip() for l in (ROOT / "requirements.txt").read_text().splitlines()]
        reqs = [l for l in lines if l]
        names = {re.split(r"[<>=;\s]", r)[0].lower() for r in reqs}
        self.assertEqual(names, {"pyside6-essentials", "pyside6-addons", "hidapi"})
        meta = (ROOT / "pyproject.toml").read_text().lower()
        for n in names:
            self.assertIn(n, meta)
        self.assertTrue(any(r.startswith("hidapi") and 'sys_platform != "linux"' in r for r in reqs))
        pyside = next(r for r in reqs if r.lower().startswith("pyside6"))
        self.assertNotIn("sys_platform", pyside)                           # Qt is needed everywhere

    def test_gitignore_keeps_the_environment_out(self):
        self.assertIn(".venv/", (ROOT / ".gitignore").read_text())

    def test_readme_documents_both_launchers(self):
        readme = (ROOT / "README.md").read_text()
        for needle in ("pylogi.sh", "pylogi.cmd", ".venv", "PYLOGI_VENV", "PYLOGI_PYTHON", "PYLOGI_SYSTEM_SITE",
                       "PYLOGI_NO_INSTALL", "--recreate", "scripts/install-user.sh"):
            self.assertIn(needle, readme)


class DesktopIntegrationTests(unittest.TestCase):
    def test_entries_start_the_launcher_not_the_system_python(self):
        home = tempfile.mkdtemp()
        env = {"XDG_DATA_HOME": f"{home}/data", "XDG_CONFIG_HOME": f"{home}/config", "PYLOGI_LAUNCHER": "/opt/pyLogi/pylogi.sh"}
        with mock.patch.dict(os.environ, env):
            entry = desktop.install_launcher()[0].read_text()
            self.assertIn("Exec=/opt/pyLogi/pylogi.sh\n", entry)
            desktop.set_autostart(True)
            self.assertIn("Exec=/opt/pyLogi/pylogi.sh --minimized\n", desktop.autostart_path().read_text())
        with mock.patch.dict(os.environ, {**env, "PYLOGI_LAUNCHER": "/opt/my apps/pylogi.sh"}):
            self.assertIn('Exec="/opt/my apps/pylogi.sh"', desktop._desktop_entry())     # spaces are quoted

    def test_without_the_launcher_it_falls_back_to_the_command(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PYLOGI_LAUNCHER", None)
            with mock.patch("pylogi.core.desktop.shutil.which", return_value=None):
                self.assertIn("-m pylogi", desktop._exec())

    def test_windows_shortcuts_use_the_launcher_and_the_ico(self):
        appdata = Path(tempfile.mkdtemp())
        env = {"APPDATA": str(appdata), "PYLOGI_LAUNCHER": r"C:\Apps\pyLogi\pylogi.cmd"}
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            script = cmd[-1]
            lnk = re.search(r"CreateShortcut\('([^']+)'\)", script).group(1)
            Path(lnk).write_bytes(b"lnk")                                  # what PowerShell would have created
            return mock.Mock(returncode=0)
        with mock.patch.object(sys, "platform", "win32"), mock.patch.dict(os.environ, env), \
                mock.patch("pylogi.core.desktop.subprocess.run", fake_run):
            menu = desktop.install_launcher()
            self.assertEqual([p.name for p in menu], ["pyLogi.lnk"])
            self.assertTrue(menu[0].exists())
            self.assertFalse(desktop.autostart_enabled())
            desktop.set_autostart(True)
            self.assertTrue(desktop.autostart_enabled())
            self.assertEqual(desktop.autostart_path().parent.name, "Startup")
            desktop.set_autostart(False)
            self.assertFalse(desktop.autostart_enabled())
        start_menu, autostart = calls
        self.assertEqual(start_menu[:3], ["powershell", "-NoProfile", "-NonInteractive"])
        self.assertIn(r"$s.TargetPath='C:\Apps\pyLogi\pylogi.cmd'", start_menu[-1])
        self.assertIn("pylogi.ico", start_menu[-1])
        self.assertIn("$s.Arguments=''", start_menu[-1])
        self.assertIn("$s.Arguments='--minimized'", autostart[-1])
        self.assertIn("$s.WindowStyle=7", autostart[-1])                   # minimised

    def test_quotes_in_paths_cannot_break_the_powershell_command(self):
        appdata = Path(tempfile.mkdtemp()) / "O'Brien"
        calls = []
        with mock.patch.object(sys, "platform", "win32"), mock.patch.dict(os.environ, {"APPDATA": str(appdata), "PYLOGI_LAUNCHER": "C:\\it's\\pylogi.cmd"}), \
                mock.patch("pylogi.core.desktop.subprocess.run", lambda cmd, **kw: calls.append(cmd) or mock.Mock()):
            desktop._make_shortcut(appdata / "x.lnk")
        script = calls[0][-1]
        self.assertIn("O''Brien", script)
        self.assertIn("it''s", script)


if __name__ == "__main__":
    unittest.main()
