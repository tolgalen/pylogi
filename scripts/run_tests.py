#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Run the test suite and leave the process without Qt's interpreter-exit teardown.

Destroying Qt widgets while Python finalises sometimes crashes (SIGSEGV / SIGABRT) *after* every test has
passed, which would turn a green run red.  The exit status here is the real test result.

    python -I scripts/run_tests.py
    python -I scripts/run_tests.py --coverage      # also writes .coverage (pip install coverage)
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
cov = None
if "--coverage" in sys.argv:
    import coverage
    cov = coverage.Coverage(config_file=str(ROOT / "pyproject.toml"))
    cov.start()
suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT / "tests"))
result = unittest.TextTestRunner(verbosity=1).run(suite)
if cov is not None:
    cov.stop()
    cov.save()
sys.stdout.flush()
sys.stderr.flush()
os._exit(0 if result.wasSuccessful() else 1)
