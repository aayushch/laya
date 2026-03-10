#!/usr/bin/env bash
# Convert Laya.icon source into all Tauri icon formats.
# Usage: ./scripts/update_icons.sh
#
# Reads the PNG from Laya.icon/Assets/ and generates all sizes for Tauri.
# Requires Python 3 — Pillow is installed in a temp venv automatically.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ICONS_DIR="$REPO_ROOT/ui/src-tauri/icons"
ICON_SOURCE_DIR="$REPO_ROOT/Laya.icon/Assets"
VENV_DIR="/tmp/laya_icon_conv"
ICONSET_DIR="/tmp/laya_icon.iconset"

# Find the source PNG
SRC_PNG=$(find "$ICON_SOURCE_DIR" -name "*.png" -type f | head -1)
if [ -z "$SRC_PNG" ]; then
    echo "ERROR: No PNG found in $ICON_SOURCE_DIR"
    exit 1
fi
echo "Source: $SRC_PNG"

# Ensure Pillow is available
if [ ! -f "$VENV_DIR/bin/python3" ]; then
    echo "Creating temp venv for Pillow..."
    python3 -m venv "$VENV_DIR"
fi
if ! "$VENV_DIR/bin/python3" -c "import PIL" 2>/dev/null; then
    echo "Installing Pillow..."
    "$VENV_DIR/bin/pip" install -q Pillow
fi

PYTHON="$VENV_DIR/bin/python3"

echo "Generating icons..."

$PYTHON << PYEOF
from PIL import Image
import os, shutil

SRC = "$SRC_PNG"
ICONS_DIR = "$ICONS_DIR"
ICONSET_DIR = "$ICONSET_DIR"

base = Image.open(SRC).convert("RGBA")
print(f"  Source size: {base.size[0]}x{base.size[1]}")

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

# macOS dock icon with rounded corners (dev mode)
from PIL import ImageDraw
macos_size = 1024
radius = int(macos_size * 0.2237)
macos_img = resize(base, macos_size)
mask = Image.new("L", (macos_size, macos_size), 0)
draw = ImageDraw.Draw(mask)
draw.rounded_rectangle([(0, 0), (macos_size - 1, macos_size - 1)], radius=radius, fill=255)
result = Image.new("RGBA", (macos_size, macos_size), (0, 0, 0, 0))
result.paste(macos_img, mask=mask)
result.save(os.path.join(ICONS_DIR, "icon_macos.png"), "PNG")
print(f"  icon_macos.png ({macos_size}x{macos_size}, rounded)")

# Generate .ico (multi-size)
ico_sizes = [16, 32, 48, 64, 128, 256]
ico_images = [resize(base, s) for s in ico_sizes]
ico_images[0].save(
    os.path.join(ICONS_DIR, "icon.ico"),
    format="ICO",
    sizes=[(s, s) for s in ico_sizes],
    append_images=ico_images[1:],
)
print(f"  icon.ico ({', '.join(str(s) for s in ico_sizes)})")

# Build macOS iconset
shutil.rmtree(ICONSET_DIR, ignore_errors=True)
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

rm -rf "$ICONSET_DIR"

# Clear macOS icon caches
if [ "$(uname)" = "Darwin" ]; then
    echo ""
    echo "Clearing macOS icon caches..."
    sudo rm -rf /Library/Caches/com.apple.iconservices.store 2>/dev/null || true
    rm -rf ~/Library/Caches/com.apple.iconservices* 2>/dev/null || true
    killall Dock 2>/dev/null || true
fi

echo ""
echo "Done! Restart 'tauri dev' to see the new icons."
