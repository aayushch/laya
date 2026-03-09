#!/usr/bin/env bash
# Convert laya_dark.ico and laya_light.ico into all Tauri icon formats.
# Usage: ./scripts/update_icons.sh
#
# Requires Python 3 and Pillow. A temporary venv is created if needed.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ICONS_DIR="$REPO_ROOT/ui/src-tauri/icons"
DARK_ICO="$REPO_ROOT/laya_dark.ico"
LIGHT_ICO="$REPO_ROOT/laya_light.ico"
VENV_DIR="/tmp/laya_icon_conv"
ICONSET_DIR="/tmp/laya_icon.iconset"

# Validate source files exist
for f in "$DARK_ICO" "$LIGHT_ICO"; do
    if [ ! -f "$f" ]; then
        echo "ERROR: Missing $f"
        exit 1
    fi
done

# Ensure Pillow is available in a temp venv
if [ ! -f "$VENV_DIR/bin/python3" ]; then
    echo "Creating temp venv for Pillow..."
    python3 -m venv "$VENV_DIR"
fi
if ! "$VENV_DIR/bin/python3" -c "import PIL" 2>/dev/null; then
    echo "Installing Pillow..."
    "$VENV_DIR/bin/pip" install -q Pillow
fi

PYTHON="$VENV_DIR/bin/python3"

echo "Generating icons from $DARK_ICO..."

# Generate all PNG sizes + copy ico files
$PYTHON << PYEOF
from PIL import Image
import os, shutil

ICONS_DIR = "$ICONS_DIR"
DARK_ICO = "$DARK_ICO"
LIGHT_ICO = "$LIGHT_ICO"
ICONSET_DIR = "$ICONSET_DIR"

# Load largest frame from dark ico
ico = Image.open(DARK_ICO)
ico.size = (128, 128)
base = ico.copy().convert("RGBA")

def resize(img, size):
    return img.resize((size, size), Image.LANCZOS)

# Tauri PNGs
png_sizes = {
    "32x32.png": 32,
    "128x128.png": 128,
    "128x128@2x.png": 256,
    "icon.png": 512,
    "Square30x30Logo.png": 30,
    "Square44x44Logo.png": 44,
    "Square71x71Logo.png": 71,
    "Square89x89Logo.png": 89,
    "Square107x107Logo.png": 107,
    "Square142x142Logo.png": 142,
    "Square150x150Logo.png": 150,
    "Square284x284Logo.png": 284,
    "Square310x310Logo.png": 310,
    "StoreLogo.png": 50,
}

for name, size in png_sizes.items():
    resize(base, size).save(os.path.join(ICONS_DIR, name), "PNG")
    print(f"  {name} ({size}x{size})")

# Copy ico files
shutil.copy2(DARK_ICO, os.path.join(ICONS_DIR, "icon.ico"))
shutil.copy2(DARK_ICO, os.path.join(ICONS_DIR, "icon_dark.ico"))
shutil.copy2(LIGHT_ICO, os.path.join(ICONS_DIR, "icon_light.ico"))
print("  icon.ico (dark)")
print("  icon_dark.ico")
print("  icon_light.ico")

# Build macOS iconset
os.makedirs(ICONSET_DIR, exist_ok=True)
icns_sizes = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}
for name, size in icns_sizes.items():
    resize(base, size).save(os.path.join(ICONSET_DIR, name), "PNG")
print("  iconset prepared")
PYEOF

# Convert iconset to .icns (macOS only)
if command -v iconutil &>/dev/null; then
    iconutil -c icns "$ICONSET_DIR" -o "$ICONS_DIR/icon.icns"
    echo "  icon.icns"
else
    echo "  SKIP icon.icns (iconutil not available — not on macOS)"
fi

# Clean up temp iconset
rm -rf "$ICONSET_DIR"

# Clear macOS icon caches so changes show immediately
if [ "$(uname)" = "Darwin" ]; then
    echo ""
    echo "Clearing macOS icon caches..."
    sudo rm -rf /Library/Caches/com.apple.iconservices.store 2>/dev/null || true
    rm -rf ~/Library/Caches/com.apple.iconservices* 2>/dev/null || true
    killall Dock 2>/dev/null || true
fi

echo ""
echo "Done! Restart 'tauri dev' to see the new icons."
