#!/usr/bin/env bash
# Regenerate the engine's dependency lock files from the requirements files.
#
# The locks pin every package (including transitive ones) to an exact version
# with hashes, for every supported OS and Python version at once. The app's
# first-run setup and scripts/setup-dev.sh both install from them.
#
# Versions already pinned in the existing lock files are kept unless the
# requirements files rule them out, so a plain run only adds or removes
# packages. Extra arguments are passed to `uv pip compile`:
#
#   ./scripts/lock-deps.sh                              # sync locks with requirements*.txt
#   ./scripts/lock-deps.sh --upgrade-package litellm    # move one package forward
#   ./scripts/lock-deps.sh --upgrade                    # move everything forward
#
# After any change, run scripts/check-locks.sh and the engine test suite.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

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

cd "$REPO_ROOT/engine"

# --universal:       one lock valid on every OS / architecture
# --python-version:  the oldest Python the app accepts (MIN_PYTHON_MINOR in sidecar.rs)
# --no-build:        the app installs wheels only, so never pin an sdist-only release
COMPILE_ARGS=(
    --universal
    --python-version 3.10
    --generate-hashes
    --no-build
    --custom-compile-command "scripts/lock-deps.sh"
    --quiet
)

echo "── Locking core dependencies ──"
"$UV" pip compile requirements.txt "${COMPILE_ARGS[@]}" -o requirements.lock "$@"

# The ML lock is constrained by the core lock so packages present in both
# (numpy, tqdm, ...) get the same version. The ML install runs after the core
# install into the same venv and must not change what the core install put there.
echo "── Locking ML dependencies ──"
"$UV" pip compile requirements-ml.txt -c requirements.lock "${COMPILE_ARGS[@]}" -o requirements-ml.lock "$@"

# The dev lock is the core set plus the test tools, for scripts/setup-dev.sh and CI.
echo "── Locking dev dependencies ──"
"$UV" pip compile requirements-dev.txt -c requirements.lock "${COMPILE_ARGS[@]}" -o requirements-dev.lock "$@"

echo "  requirements.lock:     $(grep -cE '^[A-Za-z0-9_.-]+==' requirements.lock) packages"
echo "  requirements-ml.lock:  $(grep -cE '^[A-Za-z0-9_.-]+==' requirements-ml.lock) packages"
echo "  requirements-dev.lock: $(grep -cE '^[A-Za-z0-9_.-]+==' requirements-dev.lock) packages"
