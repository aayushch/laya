#!/usr/bin/env bash
# Build Laya for distribution.
#
# Usage:
#   ./scripts/build.sh                              # Build for current platform
#   ./scripts/build.sh --target x86_64-apple-darwin  # Cross-compile for Intel Mac
#   ./scripts/build.sh --skip-engine                 # Skip engine bundling
#   ./scripts/build.sh --sign "Developer ID Application: ..."  # macOS signed build
#   ./scripts/build.sh --universal                   # Build universal (arm64 + x86_64) binary
#
# Prerequisites:
#   - Node.js + npm
#   - Rust/Cargo toolchain
#
# Output:
#   macOS:   ui/src-tauri/target/release/bundle/macos/Laya.app
#            ui/src-tauri/target/release/bundle/dmg/Laya_0.1.0_*.dmg
#   Windows: ui/src-tauri/target/release/bundle/msi/Laya_0.1.0_*.msi
#   Linux:   ui/src-tauri/target/release/bundle/appimage/Laya_0.1.0_*.AppImage

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKIP_ENGINE=false
CODESIGN_IDENTITY="${CODESIGN_IDENTITY:-}"
TARGET=""
UNIVERSAL=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-engine) SKIP_ENGINE=true; shift ;;
        --sign) CODESIGN_IDENTITY="$2"; shift 2 ;;
        --target) TARGET="$2"; shift 2 ;;
        --universal) UNIVERSAL=true; shift ;;
        *) shift ;;
    esac
done

export CODESIGN_IDENTITY

echo "=== Laya Build ==="
if [ -n "$TARGET" ]; then
    echo "  Target:   $TARGET"
fi
if [ "$UNIVERSAL" = true ]; then
    echo "  Target:   universal (aarch64 + x86_64)"
fi
if [ -n "$CODESIGN_IDENTITY" ]; then
    echo "  Signing:  $CODESIGN_IDENTITY"
fi
echo ""

# ── Step 1: Bundle engine Python source ──────────────────────────────
RESOURCES_DIR="$REPO_ROOT/ui/src-tauri/resources"
ENGINE_BUNDLE="$RESOURCES_DIR/engine"

if [ "$SKIP_ENGINE" = false ]; then
    echo "── Bundling engine source ──"

    # Clean and recreate
    rm -rf "$ENGINE_BUNDLE"
    mkdir -p "$ENGINE_BUNDLE"

    # Copy engine Python source (the actual application code)
    cp -R "$REPO_ROOT/engine/laya" "$ENGINE_BUNDLE/laya"

    # Copy requirements.txt (used at first-run to create user's venv)
    cp "$REPO_ROOT/engine/requirements.txt" "$ENGINE_BUNDLE/requirements.txt"

    # Remove any __pycache__ or .pyc files
    find "$ENGINE_BUNDLE" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find "$ENGINE_BUNDLE" -name "*.pyc" -delete 2>/dev/null || true

    echo "  Engine source: $ENGINE_BUNDLE/"
    echo "  $(find "$ENGINE_BUNDLE/laya" -name '*.py' | wc -l | tr -d ' ') Python files bundled"
    echo ""
else
    echo "── Skipping engine bundling (--skip-engine) ──"
    if [ ! -d "$ENGINE_BUNDLE/laya" ]; then
        echo "WARNING: No engine source at $ENGINE_BUNDLE/laya/"
        echo "  Run without --skip-engine first."
    fi
    echo ""
fi

# ── Step 2: Build Tauri app ──────────────────────────────────────────
echo "── Building Tauri app ──"
cd "$REPO_ROOT/ui"

if [ ! -d "node_modules" ]; then
    echo "  Installing npm dependencies..."
    npm install
fi

TAURI_ARGS=()
if [ -n "$TARGET" ]; then
    TAURI_ARGS+=(--target "$TARGET")
fi
if [ "$UNIVERSAL" = true ]; then
    TAURI_ARGS+=(--target universal-apple-darwin)
fi

echo "  Running tauri build ${TAURI_ARGS[*]:-}..."
npx tauri build "${TAURI_ARGS[@]+"${TAURI_ARGS[@]}"}"

echo ""
echo "=== Build Complete ==="
echo ""

# Show output location
BUNDLE_DIR="$REPO_ROOT/ui/src-tauri/target/release/bundle"
if [ -d "$BUNDLE_DIR/macos" ]; then
    echo "  macOS app:  $BUNDLE_DIR/macos/"
    ls "$BUNDLE_DIR/macos/" 2>/dev/null | sed 's/^/    /'
fi
if [ -d "$BUNDLE_DIR/dmg" ]; then
    echo "  macOS DMG:  $BUNDLE_DIR/dmg/"
    ls "$BUNDLE_DIR/dmg/" 2>/dev/null | sed 's/^/    /'
fi
if [ -d "$BUNDLE_DIR/msi" ]; then
    echo "  Windows:    $BUNDLE_DIR/msi/"
    ls "$BUNDLE_DIR/msi/" 2>/dev/null | sed 's/^/    /'
fi
if [ -d "$BUNDLE_DIR/nsis" ]; then
    echo "  Windows:    $BUNDLE_DIR/nsis/"
    ls "$BUNDLE_DIR/nsis/" 2>/dev/null | sed 's/^/    /'
fi
if [ -d "$BUNDLE_DIR/appimage" ]; then
    echo "  Linux:      $BUNDLE_DIR/appimage/"
    ls "$BUNDLE_DIR/appimage/" 2>/dev/null | sed 's/^/    /'
fi
if [ -d "$BUNDLE_DIR/deb" ]; then
    echo "  Linux deb:  $BUNDLE_DIR/deb/"
    ls "$BUNDLE_DIR/deb/" 2>/dev/null | sed 's/^/    /'
fi
