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
pip install -q -r requirements.txt
echo "  Python deps installed"

# Node dependencies
echo ""
echo "Installing UI dependencies..."
cd "$REPO_ROOT/ui"
npm install --silent
echo "  Node deps installed"

# Install n8n locally
echo ""
echo "Installing n8n into ~/.laya/n8n_module/..."
mkdir -p ~/.laya/n8n_module ~/.laya/n8n
# Point node-gyp at the engine venv Python (has setuptools for distutils shim)
npm_config_python="$REPO_ROOT/engine/.venv/bin/python" npm install --prefix ~/.laya/n8n_module n8n@2.15.0
echo "  n8n installed"

# Create ~/.laya directories
echo ""
echo "Creating Laya data directories..."
mkdir -p ~/.laya/data ~/.laya/logs
echo "  ~/.laya/ ready"

echo ""
echo "=== Setup complete! ==="
echo "Run: ./scripts/dev.sh"
