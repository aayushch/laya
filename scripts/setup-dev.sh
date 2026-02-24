#!/usr/bin/env bash
# One-time dev environment setup for Laya
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Laya Dev Setup ==="

# Check prerequisites
echo "Checking prerequisites..."

command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found"; exit 1; }
command -v node >/dev/null 2>&1 || { echo "ERROR: node not found"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "ERROR: docker not found"; exit 1; }
command -v cargo >/dev/null 2>&1 || { echo "ERROR: cargo not found. Install Rust: https://rustup.rs"; exit 1; }

echo "  python3: $(python3 --version)"
echo "  node:    $(node --version)"
echo "  docker:  $(docker --version)"
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

# Pull n8n Docker image
echo ""
echo "Pulling n8n Docker image..."
docker pull n8nio/n8n:latest
echo "  n8n image ready"

# Create ~/.laya directories
echo ""
echo "Creating Laya data directories..."
mkdir -p ~/.laya/data ~/.laya/logs
echo "  ~/.laya/ ready"

echo ""
echo "=== Setup complete! ==="
echo "Run: ./scripts/dev.sh"
