# Changelog

All notable changes to pyLogi. The format follows [Keep a Changelog](https://keepachangelog.com/);
the release workflow copies the section of the tagged version into the GitHub release notes.

## [Unreleased]

### Added
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
- `Channel.close()` could close the HID handle while the listener thread was still reading from it.
- Files opened without an explicit encoding (`uevent`, HID trace, and every JSON / desktop-entry read and write): on Windows
  they used cp1252 and broke with accented text.
- Windows: `fcntl` (Linux-only) was imported at module level by `core/actions.py` and `core/webcam.py`, so the app could not start.

## [0.2.0]

- Settings tabs and tooltips, receivers tree, Bolt pairing, launcher and GNOME extension switches.
- Fixes: Bolt pairing freeze, M650 photo match, per-device custom picture option.
- Real screenshots in the README.
