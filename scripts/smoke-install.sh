#!/usr/bin/env bash
# Install the engine's dependencies into a throwaway venv the way the app's
# first-run setup does, then check that the engine actually runs on them.
#
# This is the check a frozen dev venv cannot give: it fails when the packages
# a new user would receive today cannot import or run the engine.
#
# Usage:
#   ./scripts/smoke-install.sh [options]
#
#   --python VERSION    Python to build the venv on (default: 3.12, the managed runtime)
#   --installer NAME    uv (default) or pip, the app's two install paths
#   --source NAME       lock (default) installs the lock files;
#                       ranges resolves requirements*.txt, the app's path when a
#                       lock cannot be used on a machine
#   --ml                also install the ML packages and embed with the default model
#   --no-tests          skip the engine test suite
#   --keep              keep the venv and print its path

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE="$REPO_ROOT/engine"

PY_VERSION="3.12"
INSTALLER="uv"
SOURCE="lock"
WITH_ML=0
RUN_TESTS=1
KEEP=0

while [ $# -gt 0 ]; do
    case "$1" in
        --python) PY_VERSION="$2"; shift 2 ;;
        --installer) INSTALLER="$2"; shift 2 ;;
        --source) SOURCE="$2"; shift 2 ;;
        --ml) WITH_ML=1; shift ;;
        --no-tests) RUN_TESTS=0; shift ;;
        --keep) KEEP=1; shift ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

UV="${UV:-}"
if [ -z "$UV" ]; then
    if command -v uv >/dev/null 2>&1; then
        UV="uv"
    elif [ -x "$HOME/.laya/uv/uv" ]; then
        UV="$HOME/.laya/uv/uv"
    else
        echo "ERROR: uv not found. Install it from https://docs.astral.sh/uv/ or set UV=/path/to/uv" >&2
        exit 1
    fi
fi

WORK="$(mktemp -d)"
cleanup() {
    local status=$?
    if [ "$KEEP" = 1 ]; then
        echo "venv kept at $WORK/venv"
    else
        rm -rf "$WORK"
    fi
    exit "$status"
}
trap cleanup EXIT

VENV="$WORK/venv"
"$UV" venv --quiet --python "$PY_VERSION" $([ "$INSTALLER" = pip ] && echo --seed) "$VENV"
if [ -x "$VENV/bin/python" ]; then
    PY="$VENV/bin/python"
else
    PY="$VENV/Scripts/python.exe"
fi
echo "── $("$PY" --version), installer: $INSTALLER, source: $SOURCE ──"

# Same flags as uv_install_args / pip_install_args in ui/src-tauri/src/sidecar.rs.
install() {
    local file="$1" hashes=""
    case "$file" in *.lock) hashes="--require-hashes" ;; esac
    if [ "$INSTALLER" = uv ]; then
        "$UV" pip install --quiet -r "$file" --python "$PY" --only-binary :all: --color never $hashes
    else
        "$PY" -m pip install --quiet -r "$file" --only-binary :all: --progress-bar off $hashes
    fi
}

# Every package the lock pins for this environment is installed at that version.
verify_pins() {
    local lock="$1"
    "$PY" - "$lock" <<'PY'
import importlib.metadata as metadata
import re
import sys

from packaging.markers import Marker
from packaging.utils import canonicalize_name

wrong = []
checked = 0
for line in open(sys.argv[1], encoding="utf-8"):
    match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)\s*(?:;\s*(.*?))?\s*\\?$", line)
    if not match:
        continue
    name, version, marker = match.groups()
    if marker and not Marker(marker).evaluate():
        continue
    checked += 1
    try:
        installed = metadata.version(name)
    except metadata.PackageNotFoundError:
        installed = "missing"
    if installed != version:
        wrong.append(f"  {canonicalize_name(name)}: lock {version}, installed {installed}")
if wrong:
    print(f"{len(wrong)} package(s) differ from {sys.argv[1]}:")
    print("\n".join(wrong))
    sys.exit(1)
print(f"  {checked} packages match {sys.argv[1]}")
PY
}

cd "$ENGINE"

if [ "$SOURCE" = lock ]; then
    CORE=requirements.lock; ML=requirements-ml.lock
else
    CORE=requirements.txt; ML=requirements-ml.txt
fi

echo "── Installing core packages ──"
install "$CORE"
# `packaging` (needed by verify_pins) is part of the core set.
if [ "$SOURCE" = lock ]; then
    verify_pins requirements.lock
fi

echo "── Importing the engine ──"
# A throwaway HOME keeps the import from touching the real ~/.laya.
mkdir -p "$WORK/home"
HOME="$WORK/home" USERPROFILE="$WORK/home" "$PY" -c "import laya.main; print('  laya.main imported')"

echo "── Embedding with the built-in ONNX model ──"
"$PY" "$REPO_ROOT/scripts/check-embedding.py" default

if [ "$WITH_ML" = 1 ]; then
    echo "── Installing ML packages ──"
    install "$ML"
    if [ "$SOURCE" = lock ]; then
        verify_pins requirements-ml.lock
        # The ML install must leave the core set as the core lock pins it.
        verify_pins requirements.lock
    fi
    echo "── Embedding with the default model ──"
    "$PY" "$REPO_ROOT/scripts/check-embedding.py" nomic
fi

if [ "$RUN_TESTS" = 1 ]; then
    echo "── Running the engine test suite ──"
    # Test tools always come from the dev lock; with --source ranges only
    # pytest and its plugins are added so the resolved core set stays as is.
    if [ "$SOURCE" = lock ]; then
        install requirements-dev.lock
    else
        "$UV" pip install --quiet --python "$PY" pytest pytest-asyncio
    fi
    "$PY" -m pytest -q -p no:cacheprovider
fi

echo "── Smoke install passed ──"
