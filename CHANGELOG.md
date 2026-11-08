# Changelog

All notable changes to pyLogi. The format follows [Keep a Changelog](https://keepachangelog.com/);
the release workflow copies the section of the tagged version into the GitHub release notes.

## [Unreleased]

## [0.3.2] - 2026-11-08

### Added
- Webcams: every change is remembered and written again when the camera is plugged in or the computer wakes up
  (control values and video mode); the chosen preset is no longer lost on restart.
- Webcams: video format (pixel format, size, frame rate) through V4L2; command-line `video-mode=` and `preset=`;
  the presets of each camera in the tray menu.
- Webcams: built-in presets can be customised and restored; saved presets can be updated, renamed and deleted.
- Webcams: presets that switch on while an application has the focus (same window detection as the mouse profiles).
- Webcams: *Auto-adjust and lock*; grid, mirror and histogram over the preview; action buttons (`Pan/Tilt Reset`) and
  integer menus from the driver are shown.
- Webcams without a serial number are identified by their USB port instead of `/dev/videoN`, so settings follow the camera
  (existing ones are carried over).

### Added
- Debian / Ubuntu package (`scripts/build_deb.py`, built on Ubuntu 22.04 in the release workflow): a self-contained `.deb` for
  Debian 12/13 and Ubuntu 22.04 or later, with menu entry, icons, udev rules and the user service. Installed and started in
  containers of all four releases.

## [0.3.1] - 2026-11-08

### Added
- Background service: `pylogi --service on|off` (and a switch in Settings) installs a systemd user service that keeps pyLogi
  running minimised for the whole graphical session and restarts it if it stops; the RPM also ships the unit.
- Per-application profiles on Wayland without the GNOME extension: the focused window is read from Sway (`swaymsg`),
  Hyprland (`hyprctl`), Niri (`niri`) and KDE Plasma (`kdotool`). Parsers are tested; not tried on those compositors.
- Command line: `--get DEVICE dpi battery ...` prints only the named settings; boolean settings accept `toggle`;
  new `thumb-invert` setting.
- Receivers: pairing a Unifying / Lightspeed / Nano device now shows progress and reports the new device; *Unpair* asks which
  device to remove (by name) and confirms. The firmware versions of HID++ 2.0 devices are shown on the device page.

### Changed
- The "Offline" notice on the device page is drawn in red (text and border), in every theme.

## [0.3.0] - 2026-11-07

### Added
- Macros: a new button action that runs a script of steps (`key`, `text`, `media`, `click`, `scroll`, `run`, `wait`, repeats
  with `3x`), validated line by line in the editor (`core/macros.py`).
- Gestures: hold a button and move the mouse; each direction (and a plain click) runs its own macro. Uses the raw-XY
  reporting of `REPROG_CONTROLS_V4`; not verified on hardware.
- Thumb wheel (`THUMB_WHEEL`): invert it, or use it for horizontal scroll, zoom, volume or tab switching; not verified on hardware.
- Input injection on Windows (`SendInput`) and macOS (Quartz, through ctypes) in `core/injectors.py`; not verified on real systems.
- Fedora package: `python scripts/build_rpm.py` builds a `noarch` RPM and SRPM on the distribution's own PySide6
  (`packaging/rpm/pylogi.spec`), including menu entry, icons and udev rules.
- Quality tooling: `ruff` and `mypy` (core and hidpp) configured in `pyproject.toml` and run in CI; coverage report in CI.
- Tests for the self-test, backups, image downloads, uevent parsing and listener-thread shutdown (`tests/test_quality.py`).
- CI: Python 3.14, Windows and macOS test jobs (informational for now), an RPM build on Fedora.
- Release workflow: pushing a `vX.Y.Z` tag builds the bundles and the RPM and publishes a GitHub release.
- `CONTRIBUTING.md`.

### Changed
- `ui/cards.py` is now the package `ui/cards/` (one module per card); import paths are unchanged.
- Errors that were silently swallowed (profile re-apply, control restore, DPI step, listeners, self-test clean-up…) are
  now written to the debug log.
- Image downloads: response size limit, and a PNG signature check when the catalog gives no checksum.
- `build_rpm.py` warns when the working tree has changes that are not in `HEAD` (the RPM is built from `HEAD`).

### Fixed
- Desktop entries no longer list two main categories (`Utility` removed); `desktop-file-validate` is clean.
- `Channel.close()` could close the HID handle while the listener thread was still reading from it.
- Files opened without an explicit encoding (`uevent`, HID trace, and every JSON / desktop-entry read and write): on Windows
  they used cp1252 and broke with accented text.
- Windows: `fcntl` (Linux-only) was imported at module level by `core/actions.py` and `core/webcam.py`, so the app could not start.

## [0.2.0]

- Settings tabs and tooltips, receivers tree, Bolt pairing, launcher and GNOME extension switches.
- Fixes: Bolt pairing freeze, M650 photo match, per-device custom picture option.
- Real screenshots in the README.
