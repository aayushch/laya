#!/usr/bin/env bash
# Dev environment setup for Laya.
#
#   scripts/setup-dev.sh           one-time setup: venv, node_modules, n8n, ~/.laya dirs
#   scripts/setup-dev.sh --update  sync the installed Python, Node and Rust packages to the
#                                  committed lock files after a dependency change; n8n and
#                                  the ~/.laya directories are left alone
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

UPDATE=0
for arg in "$@"; do
    case "$arg" in
        --update) UPDATE=1 ;;
        -h|--help)
            sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown option: $arg (try --help)"; exit 1 ;;
    esac
done

if [ "$UPDATE" = 1 ]; then
    echo "=== Laya Dev Update (packages only) ==="
else
    echo "=== Laya Dev Setup ==="
fi

# Check prerequisites
echo "Checking prerequisites..."

command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found"; exit 1; }
command -v node >/dev/null 2>&1 || { echo "ERROR: node not found. Install Node.js 18+ from https://nodejs.org"; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "ERROR: npm not found (should come with Node.js)"; exit 1; }
command -v cargo >/dev/null 2>&1 || { echo "ERROR: cargo not found. Install Rust: https://rustup.rs"; exit 1; }

echo "  python3: $(python3 --version)"
echo "  node:    $(node --version)"
echo "  npm:     $(npm --version)"
echo "  cargo:   $(cargo --version)"

# Python venv
echo ""
echo "Setting up Python engine..."
cd "$REPO_ROOT/engine"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "  Created venv"
fi
source .venv/bin/activate
# The dev lock pins the same package versions the app installs for users
# (plus the test tools), so the test suite runs against what ships.
pip install -q --require-hashes -r requirements-dev.lock
echo "  Python deps installed from requirements-dev.lock (incl. test deps)"

# Node dependencies
echo ""
echo "Installing UI dependencies..."
cd "$REPO_ROOT/ui"
if [ "$UPDATE" = 1 ]; then
    # `npm ci` replaces node_modules with exactly what package-lock.json pins, so a
    # package that was removed or downgraded in the lock is removed or downgraded here too.
    npm ci --silent
    echo "  Node deps synced to package-lock.json"
else
    npm install --silent
    echo "  Node deps installed"
fi

# Rust crates: cargo compiles whatever Cargo.lock pins on the next build, so there is no
# installed state to sync. Download the crates now so the first `tauri dev` is fast, and
# fail if Cargo.lock is inconsistent with Cargo.toml instead of letting cargo rewrite it.
echo ""
echo "Fetching Rust crates pinned in Cargo.lock..."
cd "$REPO_ROOT/ui/src-tauri"
cargo fetch --locked
echo "  Rust crates fetched"

if [ "$UPDATE" = 1 ]; then
    echo ""
    echo "=== Update complete! ==="
    echo "Run: ./scripts/dev.sh"
    exit 0
fi

# Install n8n locally
echo ""
echo "Installing n8n into ~/.laya/n8n_module/..."
mkdir -p ~/.laya/n8n_module ~/.laya/n8n
# Point node-gyp at the engine venv Python (has setuptools for distutils shim)
# --allow-remote=all: npm 12 defaults allow-remote=none and refuses n8n's
# non-registry xlsx tarball (cdn.sheetjs.com) with EALLOWREMOTE. Harmless on
# npm 10/11 (npm 11 prints an "Unknown cli config" warning). See issue #18.
npm_config_python="$REPO_ROOT/engine/.venv/bin/python" npm install --prefix ~/.laya/n8n_module --allow-remote=all n8n@2.15.0
echo "  n8n installed"

# Create ~/.laya directories
echo ""
echo "Creating Laya data directories..."
mkdir -p ~/.laya/data ~/.laya/logs
echo "  ~/.laya/ ready"

echo ""
echo "=== Setup complete! ==="
echo "Run: ./scripts/dev.sh"
