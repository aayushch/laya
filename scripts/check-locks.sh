#!/usr/bin/env bash
# Verify that the engine lock files can be installed on every OS / Python
# combination the app supports, without installing anything.
#
# A universal lock pins one version per package, but does not guarantee that
# the pinned version publishes a wheel for each platform. The app installs
# wheels only, so a pin with no wheel for (say) Intel macOS fails setup there.
# This resolves each lock against every target with the same flags the app
# uses and reports the combinations that cannot be satisfied.
#
# Usage:
#   ./scripts/check-locks.sh            # full matrix
#   ./scripts/check-locks.sh -v         # also print uv's error for each failure

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERBOSE=0
[ "${1:-}" = "-v" ] && VERBOSE=1

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

# Targets the release workflow builds for (.github/workflows/release.yml), as
# "label|uv platform|minimum macOS". The OS floors are the oldest systems the
# locks are expected to install on: glibc 2.28 and macOS 13. macOS 14 is listed
# separately because some wheels exist only from macOS 14 on.
TARGETS=(
    "windows-x64|x86_64-pc-windows-msvc|"
    "linux-x64|x86_64-manylinux_2_28|"
    "macos13-arm64|aarch64-apple-darwin|13.0"
    "macos14-arm64|aarch64-apple-darwin|14.0"
    "macos13-x64|x86_64-apple-darwin|13.0"
)
# MIN_PYTHON_MINOR..MAX_PYTHON_MINOR in ui/src-tauri/src/sidecar.rs.
PYTHONS=(3.10 3.11 3.12 3.13 3.14)

# Combinations for which no installable wheel set exists, as "lock:label:python"
# (python may be "*"). On these the app's setup falls back to resolving the
# requirements file, and the optional ML install may be skipped.
#   - onnxruntime publishes Python 3.14 wheels only for macOS 14+ on Apple
#     Silicon, and none for Intel macOS.
#   - torch publishes its Python 3.14 wheel for macOS 14+ only.
#   - torch stopped publishing Intel macOS wheels after 2.2.2, which is older
#     than the transformers version the engine needs.
EXPECTED_UNAVAILABLE=(
    "core:macos13-arm64:3.14"
    "core:macos13-x64:3.14"
    "ml:macos13-arm64:3.14"
    "ml:macos13-x64:*"
)

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
"$UV" venv --quiet "$WORK/venv" || exit 1
PROBE_PY="$WORK/venv/bin/python"
[ -x "$PROBE_PY" ] || PROBE_PY="$WORK/venv/Scripts/python.exe"

cd "$REPO_ROOT/engine"

failures=0

check() {
    local lock="$1" plat="$2" macos="$3" py="$4"
    MACOSX_DEPLOYMENT_TARGET="$macos" "$UV" pip install --dry-run \
        --python "$PROBE_PY" \
        --python-platform "$plat" --python-version "$py" \
        --only-binary :all: --require-hashes \
        --color never \
        -r "$lock" >"$WORK/out.txt" 2>&1
}

expected_unavailable() {
    local kind="$1" label="$2" py="$3" e
    for e in "${EXPECTED_UNAVAILABLE[@]}"; do
        [ "$e" = "$kind:$label:$py" ] || [ "$e" = "$kind:$label:*" ] && return 0
    done
    return 1
}

# Prints the cell text for one lock on one target and counts problems.
cell() {
    local kind="$1" lock="$2" label="$3" plat="$4" macos="$5" py="$6" text
    if check "$lock" "$plat" "$macos" "$py"; then
        if expected_unavailable "$kind" "$label" "$py"; then
            # Keep the list honest: a combination that installs must not stay listed.
            text="FAIL (listed as unavailable but installs)"
            failures=$((failures + 1))
        else
            text="ok"
        fi
    elif expected_unavailable "$kind" "$label" "$py"; then
        text="unavailable (expected)"
    else
        text="FAIL"
        failures=$((failures + 1))
        [ "$VERBOSE" = 1 ] && sed 's/^/    /' "$WORK/out.txt" >&2
    fi
    # A yanked release still installs from an exact pin, but was withdrawn by
    # its maintainers for a reason; the lock must not ship one.
    if grep -q "is yanked" "$WORK/out.txt"; then
        text="$text (yanked pin)"
        failures=$((failures + 1))
        [ "$VERBOSE" = 1 ] && grep "is yanked" "$WORK/out.txt" | sed 's/^/    /' >&2
    fi
    CELL="$text"
}

printf '%-16s %-7s %-26s %s\n' "target" "python" "core" "ml"
for target in "${TARGETS[@]}"; do
    IFS='|' read -r label plat macos <<<"$target"
    for py in "${PYTHONS[@]}"; do
        cell core requirements.lock "$label" "$plat" "$macos" "$py"; core="$CELL"
        cell ml requirements-ml.lock "$label" "$plat" "$macos" "$py"; ml="$CELL"
        printf '%-16s %-7s %-26s %s\n' "$label" "$py" "$core" "$ml"
    done
done

if [ "$failures" -gt 0 ]; then
    echo ""
    echo "$failures problem(s). Re-run with -v for details."
    exit 1
fi
echo ""
echo "Both lock files install on every supported target."
