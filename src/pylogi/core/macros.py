# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
"""Macros: a small line-based script that chains the software actions.

One step per line; blank lines and ``# comments`` are ignored.  ``3x`` in front repeats a step.

    key ctrl+shift+T        keyboard shortcut (modifiers: ctrl shift alt meta)
    text Hello world        type text (US layout)
    media Play/Pause        media / browser key
    click left              mouse click (left right middle back forward)
    scroll down             scroll step (up down left right)
    run firefox --private   start a program (no shell)
    wait 250                pause in milliseconds
    3x scroll down          repeat a step

``parse`` turns a script into steps (dicts) and raises ``MacroError`` naming the bad line;
``format_script`` is its inverse.  Running the steps is the job of ``ActionEngine.run_macro``.
"""
from __future__ import annotations

import shlex

from .actions import KEYCODES, MEDIA, MODIFIERS, MOUSE

MAX_STEPS = 500               # after expanding repeats
MAX_WAIT_MS = 10_000          # one pause
MAX_TOTAL_MS = 60_000         # all pauses of one run
MAX_REPEAT = 100
DIRECTIONS = ("up", "down", "left", "right")
_KEYS = {k.lower(): k for k in KEYCODES}
_MEDIA = {k.lower(): k for k in MEDIA}


class MacroError(ValueError):
    def __init__(self, line: int, message: str):
        self.line, self.message = line, message
        super().__init__(f"line {line}: {message}")


def _parse_key(arg: str, n: int) -> dict:
    parts = [p.strip() for p in arg.split("+") if p.strip()]
    if not parts:
        raise MacroError(n, "key needs a shortcut, e.g. ctrl+shift+T")
    mods = [m.lower() for m in parts[:-1]]
    for m in mods:
        if m not in MODIFIERS:
            raise MacroError(n, f"unknown modifier '{m}' (ctrl, shift, alt, meta)")
    key = _KEYS.get(parts[-1].lower())
    if key is None:
        raise MacroError(n, f"unknown key '{parts[-1]}'")
    return {"op": "key", "mods": mods, "key": key}


def _parse_line(op: str, arg: str, n: int) -> dict:
    op = op.lower()
    if op == "key":
        return _parse_key(arg, n)
    if op == "text":
        if not arg:
            raise MacroError(n, "text needs something to type")
        return {"op": "text", "text": arg}
    if op == "media":
        name = _MEDIA.get(arg.lower())
        if name is None:
            raise MacroError(n, f"unknown media key '{arg}'")
        return {"op": "media", "name": name}
    if op == "click":
        if arg.lower() not in MOUSE:
            raise MacroError(n, f"unknown mouse button '{arg}' ({', '.join(MOUSE)})")
        return {"op": "click", "button": arg.lower()}
    if op == "scroll":
        if arg.lower() not in DIRECTIONS:
            raise MacroError(n, f"unknown direction '{arg}' ({', '.join(DIRECTIONS)})")
        return {"op": "scroll", "dir": arg.lower()}
    if op == "run":
        try:
            if not shlex.split(arg):
                raise ValueError
        except ValueError:
            raise MacroError(n, "run needs a program, e.g. run firefox --private") from None
        return {"op": "run", "cmd": arg}
    if op == "wait":
        try:
            ms = int(arg)
        except ValueError:
            raise MacroError(n, "wait needs milliseconds, e.g. wait 250") from None
        if not 0 <= ms <= MAX_WAIT_MS:
            raise MacroError(n, f"wait must be between 0 and {MAX_WAIT_MS} ms")
        return {"op": "wait", "ms": ms}
    raise MacroError(n, f"unknown step '{op}' (key, text, media, click, scroll, run, wait)")


def parse(script: str) -> list[dict]:
    steps: list[dict] = []
    total_wait = 0
    for n, raw in enumerate((script or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        count = 1
        head, _, rest = line.partition(" ")
        if head.lower().endswith("x") and head[:-1].isdigit():
            count = int(head[:-1])
            if not 1 <= count <= MAX_REPEAT:
                raise MacroError(n, f"repeat must be between 1 and {MAX_REPEAT}")
            line = rest.strip()
            if not line:
                raise MacroError(n, "nothing to repeat")
            head, _, rest = line.partition(" ")
        step = _parse_line(head, rest.strip(), n)
        if count > 1:
            step["times"] = count
        if step["op"] == "wait":
            total_wait += step["ms"] * count
            if total_wait > MAX_TOTAL_MS:
                raise MacroError(n, f"the pauses add up to more than {MAX_TOTAL_MS // 1000} s")
        steps.append(step)
    if sum(s.get("times", 1) for s in steps) > MAX_STEPS:
        raise MacroError(len(script.splitlines()), f"more than {MAX_STEPS} steps")
    return steps


def _format_step(s: dict) -> str:
    op = s["op"]
    if op == "key":
        return "key " + "+".join([*s.get("mods", []), s["key"]])
    if op == "wait":
        return f"wait {s['ms']}"
    arg = {"text": s.get("text"), "media": s.get("name"), "click": s.get("button"),
           "scroll": s.get("dir"), "run": s.get("cmd")}[op]
    return f"{op} {arg}"


def format_script(steps: list[dict]) -> str:
    return "\n".join((f"{s['times']}x " if s.get("times", 1) > 1 else "") + _format_step(s) for s in steps)


def describe(script: str, limit: int = 28) -> str:
    """One-line summary for the button card."""
    try:
        steps = parse(script)
    except MacroError:
        return (script or "").strip().splitlines()[0][:limit] if (script or "").strip() else ""
    text = " · ".join(format_script([s]) for s in steps)
    return text if len(text) <= limit else text[:limit - 1] + "…"
