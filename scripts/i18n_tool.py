#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Maintain the language files in src/pylogi/locales.

  i18n_tool.py extract          list every translatable string found in the sources
  i18n_tool.py check            report missing / unused / placeholder-mismatched strings
  i18n_tool.py update           add missing keys (empty value) to all locale files
  i18n_tool.py new CODE "Name"  create a new, empty locale file

A string is translatable when passed to tr("...") or N_("...") as a literal.
"""
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "pylogi"
LOC = SRC / "locales"
PLACEHOLDER = re.compile(r"\{[^}]*\}|%[sd]")


def extract() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for f in sorted(SRC.rglob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id in ("tr", "N_") and n.args
                    and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                found.setdefault(n.args[0].value, []).append(f"{f.relative_to(ROOT)}:{n.lineno}")
    return found


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data: dict) -> None:
    data["strings"] = dict(sorted(data["strings"].items(), key=lambda kv: kv[0].lower()))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "check"
    keys = extract()
    if cmd == "extract":
        for k in sorted(keys, key=str.lower):
            print(k)
        return 0
    if cmd == "new" and len(argv) == 4:
        p = LOC / f"{argv[2]}.json"
        save(p, {"_meta": {"code": argv[2], "name": argv[3]}, "strings": {k: "" for k in keys}})
        print("created", p)
        return 0
    bad = 0
    for f in sorted(LOC.glob("*.json")):
        data = load(f)
        if data["_meta"]["code"] == "en":
            continue
        strings = data["strings"]
        missing = [k for k in keys if not strings.get(k)]
        unused = [k for k in strings if k not in keys]
        mism = [k for k, v in strings.items() if v and sorted(PLACEHOLDER.findall(k)) != sorted(PLACEHOLDER.findall(v))]
        if cmd == "update":
            for k in missing:
                strings.setdefault(k, "")
            for k in unused:
                del strings[k]
            save(f, data)
        print(f"{f.name}: {len(keys) - len(missing)}/{len(keys)} translated, "
              f"{len(unused)} unused, {len(mism)} placeholder mismatches")
        if cmd == "check":
            for k in missing:
                print("  missing:", k)
            for k in mism:
                print("  placeholder mismatch:", k)
        bad += len(missing) + len(mism)
    return 1 if (cmd == "check" and bad) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
