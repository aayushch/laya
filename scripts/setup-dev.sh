#!/usr/bin/env bash
# One-time dev environment setup for Laya
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Laya Dev Setup ==="

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
npm install --silent
echo "  Node deps installed"

# Managed Node.js for n8n
# n8n's native addon isolated-vm (7.x, pulled in by @n8n/expression-runtime)
# only compiles against Node 22/24 V8 headers: Node 26 removed APIs it uses
# (PropertyCallbackInfo::This, Object::GetIsolate, ...), so `npm install n8n`
# fails outright on a newer system Node. Install the same pinned Node the
# desktop app provisions (NODE_VERSION in ui/src-tauri/src/runtime.rs) into
# ~/.laya/node/. It is NOT added to the user's PATH; n8n.rs find_node()
# prefers this managed install, so n8n's addons are built and run by the
# same Node version (a mismatch fails with NODE_MODULE_VERSION errors).
echo ""
NODE_VERSION="$(sed -n 's/^const NODE_VERSION: &str = "\(.*\)";/\1/p' "$REPO_ROOT/ui/src-tauri/src/runtime.rs")"
[ -n "$NODE_VERSION" ] || { echo "ERROR: could not read NODE_VERSION from ui/src-tauri/src/runtime.rs"; exit 1; }
NODE_DIR="$HOME/.laya/node"
echo "Setting up managed Node.js $NODE_VERSION for n8n..."
if [ -x "$NODE_DIR/bin/node" ] && [ "$(cat "$NODE_DIR/.version" 2>/dev/null)" = "$NODE_VERSION" ]; then
    echo "  Already installed at $NODE_DIR"
else
    case "$(uname -s)-$(uname -m)" in
        Darwin-arm64)               NODE_PLAT="darwin-arm64" ;;
        Darwin-x86_64)              NODE_PLAT="darwin-x64" ;;
        Linux-x86_64)               NODE_PLAT="linux-x64" ;;
        Linux-aarch64|Linux-arm64)  NODE_PLAT="linux-arm64" ;;
        *) echo "ERROR: unsupported platform for managed Node: $(uname -s)/$(uname -m)"; exit 1 ;;
    esac
    NODE_TARBALL="node-v${NODE_VERSION}-${NODE_PLAT}.tar.gz"
    NODE_URL="https://nodejs.org/dist/v${NODE_VERSION}"
    mkdir -p ~/.laya/runtimes
    NODE_STAGING="$(mktemp -d ~/.laya/runtimes/staging-node.XXXXXX)"
    curl -fsSL "$NODE_URL/$NODE_TARBALL" -o "$NODE_STAGING/$NODE_TARBALL"
    EXPECTED_SHA="$(curl -fsSL "$NODE_URL/SHASUMS256.txt" | awk -v f="$NODE_TARBALL" '$2 == f {print $1}')"
    ACTUAL_SHA="$(shasum -a 256 "$NODE_STAGING/$NODE_TARBALL" | awk '{print $1}')"
    if [ -z "$EXPECTED_SHA" ] || [ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]; then
        echo "ERROR: checksum mismatch for $NODE_TARBALL (expected '$EXPECTED_SHA', got '$ACTUAL_SHA')"
        rm -rf "$NODE_STAGING"
        exit 1
    fi
    tar -xzf "$NODE_STAGING/$NODE_TARBALL" -C "$NODE_STAGING"
    rm -rf "$NODE_DIR"
    mv "$NODE_STAGING/node-v${NODE_VERSION}-${NODE_PLAT}" "$NODE_DIR"
    # Same marker the app writes; runtime.rs skips re-downloading when it matches.
    printf '%s' "$NODE_VERSION" > "$NODE_DIR/.version"
    rm -rf "$NODE_STAGING"
    echo "  Installed to $NODE_DIR"
fi

# Install n8n locally
echo ""
echo "Installing n8n into ~/.laya/n8n_module/..."
mkdir -p ~/.laya/n8n_module ~/.laya/n8n
# Managed Node first on PATH so npm, its lifecycle scripts and node-gyp all
# build against the managed Node's headers, not the system Node's.
# Point node-gyp at the engine venv Python (has setuptools for distutils shim)
# --allow-remote=all: npm 12 defaults allow-remote=none and refuses n8n's
# non-registry xlsx tarball (cdn.sheetjs.com) with EALLOWREMOTE. Harmless on
# npm 10/11 (npm 11 prints an "Unknown cli config" warning). See issue #18.
PATH="$NODE_DIR/bin:$PATH" npm_config_python="$REPO_ROOT/engine/.venv/bin/python" \
    "$NODE_DIR/bin/npm" install --prefix ~/.laya/n8n_module --allow-remote=all n8n@2.15.0
echo "  n8n installed"

# Create ~/.laya directories
echo ""
echo "Creating Laya data directories..."
mkdir -p ~/.laya/data ~/.laya/logs
echo "  ~/.laya/ ready"

echo ""
echo "=== Setup complete! ==="
echo "Run: ./scripts/dev.sh"
