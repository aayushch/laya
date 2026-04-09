#!/usr/bin/env bash
# Bundle engine Python source and n8n workflows into Tauri resources.
# Used by both build.sh (local builds) and CI (GitHub Actions).
#
# Usage:
#   ./scripts/bundle-engine.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RESOURCES_DIR="$REPO_ROOT/ui/src-tauri/resources"
ENGINE_BUNDLE="$RESOURCES_DIR/engine"

echo "── Bundling engine source ──"

# Clean and recreate
rm -rf "$ENGINE_BUNDLE"
mkdir -p "$ENGINE_BUNDLE"
touch "$ENGINE_BUNDLE/.gitkeep"

# Copy engine Python source (the actual application code)
cp -R "$REPO_ROOT/engine/laya" "$ENGINE_BUNDLE/laya"

# Copy requirements files (used at first-run to create user's venv)
cp "$REPO_ROOT/engine/requirements.txt" "$ENGINE_BUNDLE/requirements.txt"
cp "$REPO_ROOT/engine/requirements-ml.txt" "$ENGINE_BUNDLE/requirements-ml.txt"

# Copy n8n workflows (imported into n8n on first run)
if [ -d "$REPO_ROOT/n8n/workflows" ]; then
    cp -R "$REPO_ROOT/n8n/workflows" "$ENGINE_BUNDLE/n8n_workflows"
    echo "  $(ls "$ENGINE_BUNDLE/n8n_workflows" | wc -l | tr -d ' ') n8n workflows bundled"
fi

# Remove any __pycache__ or .pyc files (avoids macOS code signature invalidation)
find "$ENGINE_BUNDLE" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$ENGINE_BUNDLE" -name "*.pyc" -delete 2>/dev/null || true

echo "  Engine source: $ENGINE_BUNDLE/"
echo "  $(find "$ENGINE_BUNDLE/laya" -name '*.py' | wc -l | tr -d ' ') Python files bundled"
