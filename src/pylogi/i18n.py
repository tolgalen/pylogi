# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Translations loaded from separate language files.

Source strings are English and double as lookup keys.  Each language lives in
its own JSON file::

    {"_meta": {"code": "es", "name": "Español"}, "strings": {"Devices": "Dispositivos"}}

Files are searched in ``<package>/locales`` and in ``<config dir>/locales``
(user files override or add languages, no code change needed).  Missing keys
fall back to the English text.  Use ``tr()`` to translate at run time and
``N_()`` to mark a string for extraction when it is translated later.
"""
from __future__ import annotations

import json
import locale
from pathlib import Path

PACKAGE_LOCALES = Path(__file__).with_name("locales")

_lang = "en"
_strings: dict[str, str] = {}
_catalog: dict[str, tuple[str, list[Path]]] = {}   # code -> (native name, files)


def user_locales_dir() -> Path:
    from .core.config import config_dir
    return config_dir() / "locales"


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def discover() -> dict[str, str]:
    """Scan the locale folders; returns {code: native name}."""
    _catalog.clear()
    dirs = [PACKAGE_LOCALES]
    try:
        dirs.append(user_locales_dir())
    except OSError:
        pass
    for d in dirs:
        for f in sorted(d.glob("*.json")):
            meta = _read(f).get("_meta", {})
            code = meta.get("code") or f.stem
            name = meta.get("name") or code
            old = _catalog.get(code)
            _catalog[code] = (name, (old[1] if old else []) + [f])
    if "en" not in _catalog:
        _catalog["en"] = ("English", [])
    return {c: n for c, (n, _) in sorted(_catalog.items(), key=lambda kv: kv[1][0].lower())}


def available_languages() -> dict[str, str]:
    return discover()


def system_language() -> str:
    code = (locale.getlocale()[0] or "en").replace("-", "_")
    langs = discover()
    for cand in (code, code[:2]):
        if cand in langs:
            return cand
    return "en"


def set_language(code: str | None = None) -> str:
    """Activate a language (None / "auto": follow the system). Returns the code used."""
    global _lang, _strings
    langs = discover()
    if not code or code == "auto":
        code = system_language()
    if code not in langs:
        code = "en"
    merged: dict[str, str] = {}
    for f in _catalog[code][1]:           # package file first, user file overrides
        merged.update({k: v for k, v in _read(f).get("strings", {}).items() if v})
    _lang, _strings = code, merged
    return code


def language() -> str:
    return _lang


def tr(text: str) -> str:
    return _strings.get(text, text)


def N_(text: str) -> str:
    """Mark a string for translation without translating it now."""
    return text
