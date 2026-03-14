#!/usr/bin/env bash
# Build Laya for distribution.
#
# Usage:
#   ./scripts/build.sh                              # Build for current platform
#   ./scripts/build.sh --skip-engine                 # Skip engine build (use existing binary)
#   ./scripts/build.sh --sign "Developer ID Application: ..."  # macOS signed build
#
# Prerequisites:
#   - Python 3 venv at engine/.venv with PyInstaller installed
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

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-engine) SKIP_ENGINE=true; shift ;;
        --sign) CODESIGN_IDENTITY="$2"; shift 2 ;;
        *) shift ;;
    esac
done

export CODESIGN_IDENTITY

# Detect target triple for sidecar naming
detect_target_triple() {
    local arch os
    arch="$(uname -m)"
    os="$(uname -s)"

    case "$arch" in
        x86_64)  arch="x86_64" ;;
        arm64|aarch64) arch="aarch64" ;;
        *) echo "Unsupported architecture: $arch" >&2; exit 1 ;;
    esac

    case "$os" in
        Darwin) echo "${arch}-apple-darwin" ;;
        Linux)  echo "${arch}-unknown-linux-gnu" ;;
        MINGW*|MSYS*|CYGWIN*) echo "${arch}-pc-windows-msvc" ;;
        *) echo "Unsupported OS: $os" >&2; exit 1 ;;
    esac
}

TARGET_TRIPLE="$(detect_target_triple)"
BINARIES_DIR="$REPO_ROOT/ui/src-tauri/binaries"

echo "=== Laya Build ==="
echo "  Platform: $TARGET_TRIPLE"
if [ -n "$CODESIGN_IDENTITY" ]; then
    echo "  Signing:  $CODESIGN_IDENTITY"
fi
echo ""

# ── Step 1: Build Python engine with PyInstaller ──────────────────────
if [ "$SKIP_ENGINE" = false ]; then
    echo "── Building engine binary ──"

    cd "$REPO_ROOT/engine"
    source .venv/bin/activate

    # Ensure PyInstaller is available
    if ! command -v pyinstaller &>/dev/null; then
        echo "  Installing PyInstaller..."
        pip install pyinstaller -q
    fi

    echo "  Running PyInstaller..."
    pyinstaller laya-engine.spec \
        --distpath dist \
        --workpath build \
        --noconfirm \
        --clean

    # Copy binary to Tauri binaries dir with target-triple suffix
    mkdir -p "$BINARIES_DIR"
    if [ -f "dist/laya-engine" ]; then
        cp "dist/laya-engine" "$BINARIES_DIR/laya-engine-$TARGET_TRIPLE"
        echo "  Engine binary: $BINARIES_DIR/laya-engine-$TARGET_TRIPLE"
    elif [ -f "dist/laya-engine.exe" ]; then
        cp "dist/laya-engine.exe" "$BINARIES_DIR/laya-engine-$TARGET_TRIPLE.exe"
        echo "  Engine binary: $BINARIES_DIR/laya-engine-$TARGET_TRIPLE.exe"
    else
        echo "ERROR: PyInstaller did not produce expected output"
        ls -la dist/ 2>/dev/null || echo "  dist/ directory not found"
        exit 1
    fi

    echo "  Engine build complete"
    echo ""
else
    echo "── Skipping engine build (--skip-engine) ──"
    if [ ! -f "$BINARIES_DIR/laya-engine-$TARGET_TRIPLE" ] && \
       [ ! -f "$BINARIES_DIR/laya-engine-$TARGET_TRIPLE.exe" ]; then
        echo "WARNING: No engine binary found at $BINARIES_DIR/laya-engine-$TARGET_TRIPLE"
        echo "  The Tauri build may fail. Run without --skip-engine first."
    fi
    echo ""
fi

# ── Step 2: Build Tauri app ──────────────────────────────────────────
echo "── Building Tauri app ──"
cd "$REPO_ROOT/ui"

# npm run build is invoked by Tauri's beforeBuildCommand, but install deps first
if [ ! -d "node_modules" ]; then
    echo "  Installing npm dependencies..."
    npm install
fi

echo "  Running tauri build..."
npx tauri build

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
