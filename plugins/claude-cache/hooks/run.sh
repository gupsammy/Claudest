#!/usr/bin/env bash
# Cross-platform hook runner: converts MSYS2/Git Bash POSIX paths
# for native python.exe and resolves the Python interpreter.
set -e

TARGET="${CLAUDE_PLUGIN_ROOT}/hooks/${1:?run.sh requires a script filename as \$1}"

# On Git Bash (MSYS2), convert POSIX path to mixed-mode Windows path
# so native python.exe can resolve it. No-ops on Linux/Mac (no cygpath).
if command -v cygpath >/dev/null 2>&1; then
    TARGET="$(cygpath -m "$TARGET")"
fi

# Prefer python3, fall back to python (Windows default).
# Uses proper if/else — not `&& ... ||` which double-executes on failure.
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$TARGET"
else
    exec python "$TARGET"
fi
