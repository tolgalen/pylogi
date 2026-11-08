#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 pyLogi contributors
#
# pyLogi launcher for Linux.  The application always runs in its own virtual
# environment (.venv next to this script), which is created and filled on first use.
#
#   ./pylogi.sh                      start the application detached (the console closes / the prompt returns;
#                                    output goes to <config dir>/pylogi/pylogi.log)
#   ./pylogi.sh [pyLogi options]     run in the foreground with any option of `pylogi --help`
#   ./pylogi.sh --setup              create / update the environment and exit
#   ./pylogi.sh --recreate           delete the environment, build it again and exit
#   ./pylogi.sh --print-env          print the environment folder and exit
#
# Environment variables:
#   PYLOGI_VENV         folder of the virtual environment (default: <this folder>/.venv)
#   PYLOGI_PYTHON       Python interpreter used to create it (default: first python3 >= 3.10 found)
#   PYLOGI_SYSTEM_SITE  1 = let the environment see the system's Python packages (e.g. a distro PySide6)
#   PYLOGI_NO_INSTALL   1 = do not run pip (offline use, or dependencies provided by the system)
#   PYLOGI_FOREGROUND   1 = do not detach when started without options (keeps the console attached, for debugging)
set -euo pipefail

# resolve symlinks, so that a link in ~/.local/bin works
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
HERE="$(cd -P "$(dirname "$SOURCE")" && pwd)"
VENV="${PYLOGI_VENV:-$HERE/.venv}"
REQ="$HERE/requirements.txt"
STAMP="$VENV/.pylogi-requirements"

say() { printf 'pyLogi: %s\n' "$*" >&2; }

find_python() {
    if [ -n "${PYLOGI_PYTHON:-}" ]; then
        echo "$PYLOGI_PYTHON"
        return 0
    fi
    local c
    for c in python3 python3.13 python3.12 python3.11 python3.10 python; do
        if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
            command -v "$c"
            return 0
        fi
    done
    return 1
}

setup_env() {
    if [ ! -x "$VENV/bin/python" ]; then
        local py args=()
        py="$(find_python)" || { say "Python 3.10 or newer is required (or set PYLOGI_PYTHON)"; exit 1; }
        say "creating the virtual environment in $VENV ($("$py" --version 2>&1))"
        [ -n "${PYLOGI_SYSTEM_SITE:-}" ] && args+=(--system-site-packages)
        "$py" -m venv ${args[@]+"${args[@]}"} "$VENV" ||
            { say "could not create the environment (Debian/Ubuntu: sudo apt install python3-venv)"; rm -rf "$VENV"; exit 1; }
    fi
    if [ -z "${PYLOGI_NO_INSTALL:-}" ]; then
        # reinstall when requirements.txt or the interpreter changes
        local want
        want="$("$VENV/bin/python" -c 'import hashlib, sys
print(hashlib.sha256(open(sys.argv[1], "rb").read() + sys.version.encode()).hexdigest())' "$REQ")"
        if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$want" ]; then
            say "installing the dependencies (needs an internet connection the first time)"
            "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$REQ" ||
                { say "pip could not install the dependencies"; exit 1; }
            echo "$want" > "$STAMP"
        fi
    fi
    "$VENV/bin/python" -c 'import PySide6' 2>/dev/null ||
        { say "PySide6 is not available in $VENV (run '$0 --recreate')"; exit 1; }
}

case "${1:-}" in
    --print-env) echo "$VENV"; exit 0 ;;
    --recreate)  rm -rf "$VENV"; setup_env; say "environment ready: $VENV"; exit 0 ;;
    --setup)     setup_env; say "environment ready: $VENV"; exit 0 ;;
esac

setup_env
export PYLOGI_LAUNCHER="$HERE/pylogi.sh"      # used by --install-launcher / --autostart so they start this environment
[ -z "${PYLOGI_SYSTEM_SITE:-}" ] && export PYTHONNOUSERSITE=1    # nothing outside the environment leaks in
if [ $# -eq 0 ] && [ -z "${PYLOGI_FOREGROUND:-}" ]; then
    LOGDIR="${XDG_CONFIG_HOME:-$HOME/.config}/pylogi"
    mkdir -p "$LOGDIR"
    if command -v setsid >/dev/null 2>&1; then
        setsid "$VENV/bin/python" "$HERE/run.py" >>"$LOGDIR/pylogi.log" 2>&1 </dev/null &
    else
        nohup "$VENV/bin/python" "$HERE/run.py" >>"$LOGDIR/pylogi.log" 2>&1 </dev/null &
    fi
    disown 2>/dev/null || true
    exit 0
fi
exec "$VENV/bin/python" "$HERE/run.py" "$@"
