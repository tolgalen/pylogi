#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Tiny linter (no dependencies): unused imports, and modules that fail to compile."""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def unused_imports(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: dict[str, int] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                imported[(a.asname or a.name).split(".")[0]] = n.lineno
        elif isinstance(n, ast.ImportFrom) and n.module != "__future__":
            for a in n.names:
                imported[a.asname or a.name] = n.lineno
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.value.id for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    src = path.read_text(encoding="utf-8").splitlines()
    out = []
    for name, line in imported.items():
        if name not in used and "noqa" not in src[line - 1] and not path.name == "__init__.py":
            # names listed in __all__ or re-exported through a string are rare here; keep it simple
            out.append((line, name))
    return sorted(out)


def main() -> int:
    bad = 0
    for p in sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "tests").glob("*.py")) + sorted((ROOT / "scripts").glob("*.py")):
        try:
            for line, name in unused_imports(p):
                print(f"{p.relative_to(ROOT)}:{line}: unused import {name}")
                bad += 1
        except SyntaxError as e:
            print(f"{p.relative_to(ROOT)}: {e}")
            bad += 1
    print(f"{bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
