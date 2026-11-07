# Contributing to pyLogi

## Set up

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[hidapi]" ruff mypy coverage      # hidapi only matters on Windows / macOS
```

Run the app from the sources with `python run.py` (add `--demo` to use fake devices, no hardware needed).
On Linux you also need the udev rules, see the README.

## Before you open a pull request

CI runs the same commands:

```bash
ruff check .                      # lint (rules in pyproject.toml)
python scripts/lint.py            # unused imports / modules that do not compile
mypy                              # types for src/pylogi/core and src/pylogi/hidpp
python scripts/i18n_tool.py check # every string translated, placeholders match
python scripts/gen_docs.py --check
QT_QPA_PLATFORM=offscreen python -I scripts/run_tests.py
```

Coverage: `QT_QPA_PLATFORM=offscreen python -I scripts/run_tests.py --coverage && coverage report`.

## Code

- Qt widgets live in `src/pylogi/ui`, device logic in `src/pylogi/core`, the HID++ protocol in `src/pylogi/hidpp`.
  The UI must never block on device I/O: use `ui/background.py`.
- Devices are duck-typed: capabilities such as `cam_controls` exist only on some classes, ask `dev.has("webcam")` first.
- Do not swallow exceptions silently: log them with `core.debuglog.log("error", ...)`.
- Tests use the standard `unittest`; hardware is replaced by `core/demo.py` or a fake `Transport` (see `tests/test_hidpp.py`).

## Translations

User-visible strings go through `tr()` / `N_()`. After adding or changing one:

```bash
python scripts/i18n_tool.py update   # add the new keys (empty) to every locale file, then translate them
python scripts/i18n_tool.py check    # missing / unused strings, placeholder mismatches
```

`python scripts/gen_docs.py` regenerates the README tables and `THIRD_PARTY.md` (from `core/credits.py`);
CI fails if they are out of date.

## Releases

1. Bump `__version__` in `src/pylogi/__init__.py` and `version` in `pyproject.toml`; move the `[Unreleased]` entries in
   `CHANGELOG.md` under the new version.
2. Commit, then `git tag vX.Y.Z && git push --tags`. The *release* workflow builds the bundles and the RPM and publishes them.
3. Local builds: `python scripts/build_release.py` (PyInstaller) and `python scripts/build_rpm.py` (builds from `git HEAD`).
