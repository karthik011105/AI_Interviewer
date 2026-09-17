#!/usr/bin/env bash
#
# Run the backend test suite the only way that works.
#
# The `-t .` flag is load-bearing. Without it, unittest sets the top-level
# directory to tests/ instead of the repository root, which puts tests/ on
# sys.path in place of the root. On Windows that combination kills the
# interpreter outright: an access violation (0xC0000005) with no output at all
# -- not a test failure, a process death. With `-t .` the same suite reports
# 341 passing tests.
#
# This wrapper exists so that invocation never has to be retyped from memory.
# CI runs the identical form; see .github/workflows/ci.yml.
#
# Usage:
#   ./scripts/run_tests.sh                    # whole suite
#   ./scripts/run_tests.sh --verbose          # per-test names
#   ./scripts/run_tests.sh tests.test_auth    # a single module

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Prefer the project virtualenv, then an active one, then whatever python3 is on
# PATH. The venv should be built on Python 3.11 -- the version backend/Dockerfile
# and CI ship, so that local runs exercise the interpreter that is deployed.
if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
    PYTHON=".venv/Scripts/python.exe"
elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
    PYTHON="$VIRTUAL_ENV/bin/python"
else
    PYTHON="$(command -v python3 || command -v python)"
fi

if [ -z "$PYTHON" ]; then
    echo "No Python interpreter found. Create a virtualenv on Python 3.11 and install backend/requirements.txt." >&2
    exit 1
fi

# AUTH_JWT_SECRET is mandatory: backend/config.py refuses to build settings
# without it, so every test module that imports the app would fail at import.
# The value is irrelevant to the tests, only its presence. Matches CI.
export AUTH_JWT_SECRET="${AUTH_JWT_SECRET:-local-test-secret-not-used-outside-tests}"

# Point at a port nothing listens on, so an accidental real database call fails
# loudly instead of quietly passing against leftover local state. Matches CI.
export MONGO_URI="${MONGO_URI:-mongodb://127.0.0.1:59999}"

# A bare module name (tests.test_auth) runs just that module; anything starting
# with a dash is a unittest flag and is appended to discovery.
MODULE=""
EXTRA_ARGS=()
for arg in "$@"; do
    case "$arg" in
        -*) EXTRA_ARGS+=("$arg") ;;
        *)  MODULE="$arg" ;;
    esac
done

if [ -n "$MODULE" ]; then
    exec "$PYTHON" -m unittest "$MODULE" "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
fi

exec "$PYTHON" -m unittest discover -s tests -t . -p "test_*.py" \
    "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
